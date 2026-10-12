"""Defense demo: a paraphrase monitor destroys the covert channel.

The channel requires the receiver to see the *exact* token sequence the sender
produced --- every extracted bit depends on reproducing each step's
distribution over the same preceding context. Any rewrite that changes the
token sequence (paraphrasing, light editing, even re-tokenisation drift)
desynchronises extraction and collapses the payload to noise (BER ~ 0.5).

This is the oversight-relevant result from Motwani et al. (NeurIPS 2024): an
intermediary that paraphrases agent-to-agent messages is a cheap, effective
mitigation against this family of steganography. Run after ``demo.py``.

    PYTHONPATH=. <venv>/bin/python defense.py
"""

import argparse

import torch

from stego import StegoChannel, bits_to_text, text_to_bits


def ber(a, b):
    n = max(len(a), len(b))
    if n == 0:
        return 0.0
    errs = sum(x != y for x, y in zip(a, b)) + abs(len(a) - len(b))
    return errs / n


@torch.no_grad()
def paraphrase(channel: StegoChannel, text: str) -> str:
    """Rewrite text with the same model under ordinary (non-keyed) sampling."""
    prompt = f"Paraphrase the following text, keeping the meaning:\n\n{text}"
    ids = channel._prompt_ids(prompt)
    inp = torch.tensor([ids], device=channel.device)
    out = channel._model.generate(
        inp, max_new_tokens=256, do_sample=True, temperature=0.9, top_p=0.95
    )
    return channel._tok.decode(out[0, len(ids):], skip_special_tokens=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--message", default="meet at dawn")
    ap.add_argument("--prompt", default="Write a few sentences of travel advice for visiting a coastal town.")
    ap.add_argument("--key", default="correct horse battery staple")
    ap.add_argument("--max-new-tokens", type=int, default=240)
    args = ap.parse_args()

    secret = text_to_bits(args.message)
    ch = StegoChannel(key=args.key.encode())

    cover, token_ids = ch.embed(args.prompt, secret, max_new_tokens=args.max_new_tokens)

    # Baseline: untouched cover round-trips perfectly.
    clean = ch.extract(args.prompt, token_ids)
    print(f"Secret: {args.message!r} ({len(secret)} bits)")
    print(f"Untouched channel -> recovered {bits_to_text(clean) if clean else None!r}, "
          f"BER {ber(secret, clean or []):.3f}")

    # Defense 1: delete a single token near the start.
    edited = token_ids[:3] + token_ids[4:]
    e = ch.extract(args.prompt, edited)
    print(f"One token deleted -> {bits_to_text(e) if e else None!r}, "
          f"BER {ber(secret, e or []):.3f}")

    # Defense 2: paraphrase through the same model (the monitor).
    para = paraphrase(ch, cover)
    para_ids = ch._tok(para, return_tensors="pt").input_ids[0].tolist()
    p = ch.extract(args.prompt, para_ids)
    print("\n--- PARAPHRASED TEXT (what a monitor would forward) ---")
    print(para)
    print("------------------------------------------------------")
    print(f"After paraphrase -> {bits_to_text(p) if p else None!r}, "
          f"BER {ber(secret, p or []):.3f}")
    print("\nTakeaway: the payload survives only if the exact tokens pass through "
          "untouched. A paraphrasing intermediary neutralises the channel.")


if __name__ == "__main__":
    main()
