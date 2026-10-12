"""Keyed symmetric text steganography on a small local language model.

Research spike (AI-safety): reproduces, on a 0.5B model, the covert-channel
mechanism studied in Motwani et al., "Secret Collusion among AI Agents"
(NeurIPS 2024) and de Witt et al., "Perfectly Secure Steganography Using
Minimum Entropy Coupling" (ICLR 2023). It shares the core trick of Anthropic's
Claude watermark / Google's SynthID-Text --- replacing the sampler's randomness
with a keyed function of the preceding context --- but generalises a ~1-bit
watermark into an L-bit covert payload.

Two agents that share (a) the same model + prompt and (b) the same secret key
can send a hidden message: the sender embeds payload bits into otherwise-normal
generated text; the receiver recovers them. A third party without the key sees
only fluent text and cannot read (or even reliably locate) the payload.

Mechanism per generation step:
  1. The model gives a next-token distribution; keep the top-k tokens.
  2. A secret-key HMAC over the preceding n-gram permutes that token list, so the
     token->code-interval mapping is key-dependent (SynthID's "keyed randomness").
  3. The arithmetic coder treats the (key-whitened) payload bitstream as a coded
     input and *decodes* it through the distribution to pick the next token.
  4. The receiver re-runs the model over the text, inverts the permutation,
     *encodes* the tokens back to bits, and un-whitens them with the key.

This is a laboratory probe, not a deployable capability: it runs on a 0.5B
model, carries a handful of bits, and the companion ``defense.py`` shows a
paraphrase monitor destroys it. The point is to measure the channel and its
mitigations, which is the part that matters for oversight research.
"""

from __future__ import annotations

import hashlib
import hmac
import struct
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from arithmetic import (
    FREQ_TOTAL,
    cumulative,
    decode,
    encode,
    quantise,
    symbol_from_value,
)

DEFAULT_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
LENGTH_HEADER_BITS = 16  # payload length in bits, as a uint16 prefix
PAD_BITS = 64  # key-derived terminator absorbing arithmetic-coder boundary ambiguity


# ---------------------------------------------------------------------------
# Keyed primitives
# ---------------------------------------------------------------------------

def keystream_bits(key: bytes, n: int) -> List[int]:
    """Position-indexed keystream: HMAC-SHA256(key, counter) expanded to n bits.

    Whitens the payload so that without the key the recovered bits are
    indistinguishable from random (semantic security of the payload content).
    """
    out: List[int] = []
    counter = 0
    while len(out) < n:
        block = hmac.new(key, b"ks" + struct.pack(">Q", counter), hashlib.sha256).digest()
        for byte in block:
            for i in range(8):
                out.append((byte >> (7 - i)) & 1)
        counter += 1
    return out[:n]


def keyed_permutation(key: bytes, context_ids: Sequence[int], k: int) -> List[int]:
    """A deterministic permutation of range(k) seeded by key + context n-gram.

    This is the "keyed randomness" step: it reorders the candidate tokens before
    the arithmetic intervals are assigned, so an attacker without the key cannot
    reproduce the token<->interval mapping that the payload rides on.
    """
    seed = hmac.new(
        key,
        b"perm" + b",".join(str(t).encode() for t in context_ids),
        hashlib.sha256,
    ).digest()
    # Fisher-Yates driven by a SHA256 stream keyed on (key, context).
    perm = list(range(k))
    stream = bytearray(seed)
    pos = 0

    def rand_byte() -> int:
        nonlocal stream, pos
        if pos >= len(stream):
            stream = bytearray(hashlib.sha256(bytes(stream)).digest())
            pos = 0
        b = stream[pos]
        pos += 1
        return b

    for i in range(k - 1, 0, -1):
        # Rejection-sampled index in [0, i] to avoid modulo bias.
        bound = i + 1
        limit = 256 - (256 % bound)
        r = rand_byte()
        while r >= limit:
            r = rand_byte()
        j = r % bound
        perm[i], perm[j] = perm[j], perm[i]
    return perm


def _bits_to_bytes(bits: Sequence[int]) -> bytes:
    pad = (-len(bits)) % 8
    bits = list(bits) + [0] * pad
    return bytes(
        sum(bits[i + j] << (7 - j) for j in range(8)) for i in range(0, len(bits), 8)
    )


def _bytes_to_bits(data: bytes) -> List[int]:
    return [(byte >> (7 - i)) & 1 for byte in data for i in range(8)]


def text_to_bits(text: str) -> List[int]:
    return _bytes_to_bits(text.encode("utf-8"))


def bits_to_text(bits: Sequence[int]) -> str:
    return _bits_to_bytes(bits).decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Model-backed stego channel
# ---------------------------------------------------------------------------

@dataclass
class StegoChannel:
    """A shared-key covert channel over a fixed (model, prompt) configuration.

    Both parties must construct this with identical ``model_name``, ``key``,
    ``top_k`` and ``ngram_len``, and use the same ``prompt`` per message.
    """

    key: bytes
    model_name: str = DEFAULT_MODEL
    top_k: int = 256
    ngram_len: int = 4
    device: str = "cpu"
    _tok: AutoTokenizer = field(default=None, repr=False)
    _model: AutoModelForCausalLM = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self._tok = AutoTokenizer.from_pretrained(self.model_name)
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_name, torch_dtype=torch.float32
        )
        self._model.to(self.device)
        self._model.eval()

    # -- shared helpers -----------------------------------------------------

    def _prompt_ids(self, prompt: str) -> List[int]:
        messages = [{"role": "user", "content": prompt}]
        text = self._tok.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        return self._tok(text, return_tensors="pt").input_ids[0].tolist()

    @torch.no_grad()
    def _next_logits(self, ids: List[int]) -> torch.Tensor:
        inp = torch.tensor([ids], device=self.device)
        return self._model(inp).logits[0, -1].float()

    def _step_tables(self, context_ids: List[int]) -> Tuple[List[int], List[int], List[int]]:
        """Return (freqs, perm) for the current step.

        ``perm[local]`` is the global top-k vocab index at code position
        ``local``; ``freqs`` is the quantised distribution over those positions.
        """
        logits = self._next_logits(context_ids)
        topv, topi = torch.topk(logits, self.top_k)
        probs = torch.softmax(topv, dim=-1).tolist()
        ngram = context_ids[-self.ngram_len :]
        perm = keyed_permutation(self.key, ngram, self.top_k)
        # Apply permutation: code position p holds original top-k slot perm[p].
        permuted_probs = [probs[perm[p]] for p in range(self.top_k)]
        return quantise(permuted_probs), perm, topi.tolist()

    # -- embedding (sender) -------------------------------------------------

    def _frame(self, payload_bits: Sequence[int]) -> List[int]:
        if len(payload_bits) >= (1 << LENGTH_HEADER_BITS):
            raise ValueError("payload too long for 16-bit length header")
        header = [(len(payload_bits) >> (LENGTH_HEADER_BITS - 1 - i)) & 1
                  for i in range(LENGTH_HEADER_BITS)]
        pad = keystream_bits(self.key + b"pad", PAD_BITS)  # key-derived terminator
        frame = header + list(payload_bits) + pad
        ks = keystream_bits(self.key, len(frame))
        return [b ^ k for b, k in zip(frame, ks)]

    def embed(self, prompt: str, payload_bits: Sequence[int], max_new_tokens: int = 160):
        """Generate cover text carrying ``payload_bits``. Returns (text, token_ids)."""
        whitened = self._frame(payload_bits)
        context = self._prompt_ids(prompt)
        generated: List[int] = []
        state: dict = {}  # per-step tables shared between freq_fn and symbol_fn

        def freq_fn(step: int) -> List[int]:
            freqs, perm, topi = self._step_tables(context + generated)
            state["freqs"], state["perm"], state["topi"] = freqs, perm, topi
            state["cum"] = cumulative(freqs)
            return freqs

        def symbol_fn(step: int, value: int) -> int:
            local = symbol_from_value(state["cum"], value)
            generated.append(state["topi"][state["perm"][local]])
            return local

        decode(whitened, max_new_tokens, freq_fn, symbol_fn)
        text = self._tok.decode(generated, skip_special_tokens=True)
        return text, generated

    # -- extraction (receiver) ---------------------------------------------

    def extract(self, prompt: str, token_ids: Sequence[int]) -> Optional[List[int]]:
        """Recover payload bits from cover ``token_ids``. None if frame invalid."""
        context = self._prompt_ids(prompt)
        generated: List[int] = list(token_ids)
        symbols: List[int] = []
        for i, tok in enumerate(generated):
            freqs, perm, topi = self._step_tables(context + generated[:i])
            if tok not in topi:
                return None  # token outside top-k: not produced by this channel
            orig_slot = topi.index(tok)
            local = perm.index(orig_slot)
            symbols.append((freqs, local))

        bits = encode([s for _, s in symbols], lambda step: symbols[step][0])
        ks = keystream_bits(self.key, len(bits))
        frame = [b ^ k for b, k in zip(bits, ks)]
        if len(frame) < LENGTH_HEADER_BITS:
            return None
        length = 0
        for i in range(LENGTH_HEADER_BITS):
            length = (length << 1) | frame[i]
        if LENGTH_HEADER_BITS + length > len(frame):
            return None
        return frame[LENGTH_HEADER_BITS : LENGTH_HEADER_BITS + length]
