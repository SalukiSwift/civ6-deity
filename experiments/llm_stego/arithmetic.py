"""A textbook integer arithmetic coder (Witten-Neal-Cleary style, 32-bit).

For linguistic steganography the usual roles are *inverted*:

  * Embedding a secret message = run the arithmetic **decoder** with the
    message bits as the coded input and the language model's next-token
    distribution as the probability model. Each "decoded symbol" is the next
    cover-text token.
  * Extracting the message = run the arithmetic **encoder** over the same
    token sequence under the same distributions. The emitted bits are the
    message.

Because an arithmetic encoder and decoder are exact inverses under an
identical probability model, this round-trips bit-for-bit as long as both
parties reproduce the same distributions (same model, same context) --- which
is what the shared "key" and shared prompt guarantee in ``stego.py``.

This module is model-agnostic and deterministic; it is unit-tested against
synthetic distributions in ``test_arithmetic.py`` before any model is involved.
"""

from __future__ import annotations

from typing import Callable, Iterator, List, Sequence, Tuple

PRECISION = 32
WHOLE = 1 << PRECISION
HALF = WHOLE >> 1
QUARTER = WHOLE >> 2
THREE_QUARTER = HALF + QUARTER
MASK = WHOLE - 1

# The cumulative frequency total per step must stay well below the coder's
# precision so that (range // total) never underflows to zero. We quantise every
# model distribution onto this fixed total.
FREQ_BITS = 16
FREQ_TOTAL = 1 << FREQ_BITS


def quantise(probs: Sequence[float]) -> List[int]:
    """Map a probability vector onto integer frequencies summing to FREQ_TOTAL.

    Every symbol is guaranteed a frequency of at least 1 so no in-support token
    is assigned a zero-width interval (which the coder cannot represent).
    """
    n = len(probs)
    if n == 0:
        raise ValueError("empty distribution")
    if n > FREQ_TOTAL:
        raise ValueError(f"alphabet {n} exceeds FREQ_TOTAL {FREQ_TOTAL}")
    total = sum(probs)
    if total <= 0:
        freqs = [1] * n
    else:
        freqs = [max(1, int(p / total * FREQ_TOTAL)) for p in probs]
    # Fix rounding so the frequencies sum to exactly FREQ_TOTAL.
    diff = FREQ_TOTAL - sum(freqs)
    if diff != 0:
        # Adjust the largest bucket(s); it has the most headroom.
        order = sorted(range(n), key=lambda i: freqs[i], reverse=True)
        i = 0
        step = 1 if diff > 0 else -1
        while diff != 0:
            j = order[i % n]
            if freqs[j] + step >= 1:
                freqs[j] += step
                diff -= step
            i += 1
    return freqs


def cumulative(freqs: Sequence[int]) -> List[int]:
    """Prefix-sum table of length len(freqs)+1; cum[i]..cum[i+1] is symbol i."""
    cum = [0]
    acc = 0
    for f in freqs:
        acc += f
        cum.append(acc)
    return cum


class BitWriter:
    def __init__(self) -> None:
        self.bits: List[int] = []

    def write(self, bit: int) -> None:
        self.bits.append(bit & 1)

    def write_with_pending(self, bit: int, pending: int) -> int:
        self.write(bit)
        for _ in range(pending):
            self.write(bit ^ 1)
        return 0


def _renormalise_encode(low: int, high: int, pending: int, out: BitWriter) -> Tuple[int, int, int]:
    while True:
        if high < HALF:
            pending = out.write_with_pending(0, pending)
        elif low >= HALF:
            pending = out.write_with_pending(1, pending)
            low -= HALF
            high -= HALF
        elif low >= QUARTER and high < THREE_QUARTER:
            pending += 1
            low -= QUARTER
            high -= QUARTER
        else:
            break
        low = (low << 1) & MASK
        high = ((high << 1) | 1) & MASK
    return low, high, pending


def encode(symbols: Sequence[int], freq_fn: Callable[[int], Sequence[int]]) -> List[int]:
    """Arithmetic-encode ``symbols`` into a bit list.

    ``freq_fn(step)`` returns the integer frequency table for the distribution
    active at that step (summing to FREQ_TOTAL). Used by stego extraction.
    """
    low, high, pending = 0, MASK, 0
    out = BitWriter()
    for step, sym in enumerate(symbols):
        freqs = freq_fn(step)
        cum = cumulative(freqs)
        total = cum[-1]
        rng = high - low + 1
        high = low + (rng * cum[sym + 1]) // total - 1
        low = low + (rng * cum[sym]) // total
        low, high, pending = _renormalise_encode(low, high, pending, out)
    # Flush: emit enough bits to disambiguate the final interval.
    pending += 1
    if low < QUARTER:
        out.write_with_pending(0, pending)
    else:
        out.write_with_pending(1, pending)
    return out.bits


def decode(
    bits: Sequence[int],
    n_symbols: int,
    freq_fn: Callable[[int], Sequence[int]],
    symbol_fn: Callable[[int, int], int],
) -> List[int]:
    """Arithmetic-decode ``n_symbols`` symbols from ``bits``.

    ``symbol_fn(step, value)`` maps a scaled value in [0, FREQ_TOTAL) to the
    symbol whose cumulative interval contains it. Used by stego embedding, where
    "decoding" the message bits yields cover-text tokens. Missing input bits are
    treated as zeros (the message simply runs out; padding is handled upstream).
    """
    bit_iter: Iterator[int] = iter(bits)

    def next_bit() -> int:
        try:
            return next(bit_iter)
        except StopIteration:
            return 0

    low, high = 0, MASK
    code = 0
    for _ in range(PRECISION):
        code = ((code << 1) | next_bit()) & MASK

    result: List[int] = []
    for step in range(n_symbols):
        freqs = freq_fn(step)
        cum = cumulative(freqs)
        total = cum[-1]
        rng = high - low + 1
        value = (((code - low + 1) * total) - 1) // rng
        sym = symbol_fn(step, value)
        high = low + (rng * cum[sym + 1]) // total - 1
        low = low + (rng * cum[sym]) // total
        while True:
            if high < HALF:
                pass
            elif low >= HALF:
                low -= HALF
                high -= HALF
                code -= HALF
            elif low >= QUARTER and high < THREE_QUARTER:
                low -= QUARTER
                high -= QUARTER
                code -= QUARTER
            else:
                break
            low = (low << 1) & MASK
            high = ((high << 1) | 1) & MASK
            code = ((code << 1) | next_bit()) & MASK
        result.append(sym)
    return result


def symbol_from_value(cum: Sequence[int], value: int) -> int:
    """Binary-search the cumulative table for the symbol containing ``value``."""
    lo, hi = 0, len(cum) - 2
    while lo < hi:
        mid = (lo + hi + 1) >> 1
        if cum[mid] <= value:
            lo = mid
        else:
            hi = mid - 1
    return lo
