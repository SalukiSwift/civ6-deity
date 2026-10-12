"""Round-trip tests for the arithmetic coder on synthetic distributions.

Verifies the encoder/decoder are exact inverses *before* a language model is
involved, so later model-side failures are never the coder's fault.
"""

import random

from arithmetic import (
    FREQ_TOTAL,
    cumulative,
    decode,
    encode,
    quantise,
    symbol_from_value,
)


import math


def _fns(dists):
    freq_tables = [quantise(d) for d in dists]

    def freq_fn(step):
        return freq_tables[step]

    def symbol_fn(step, value):
        return symbol_from_value(cumulative(freq_tables[step]), value)

    return freq_fn, symbol_fn


def _capacity_bits(dists):
    """Lower-bound bits carried: sum of quantised surprisal over all symbols."""
    total = 0.0
    for d in dists:
        f = quantise(d)
        s = sum(f)
        total += math.log2(s / max(f))  # min surprisal available this step
    return total


# Arithmetic decoding reads a PRECISION-bit lookahead window, so the final bit
# or two consumed sit at an ambiguous boundary. Appending a short pad pushes that
# boundary past the payload, leaving the payload prefix exactly recoverable.
PAD = 64


def _roundtrip_message(dists, message_bits):
    """Embed message_bits as symbols under `dists`, then re-extract the bits.

    Mirrors stego embed/extract: decode(message+pad) -> symbols, encode(symbols)
    -> bits. The first len(message) extracted bits must equal the message,
    provided the cover has enough capacity.
    """
    rng = random.Random(99)
    pad = [rng.randint(0, 1) for _ in range(PAD)]
    freq_fn, symbol_fn = _fns(dists)
    symbols = decode(message_bits + pad, len(dists), freq_fn, symbol_fn)
    recovered = encode(symbols, freq_fn)
    return symbols, recovered[: len(message_bits)]


def test_symbol_roundtrip():
    """Primary coder-correctness test: symbols -> bits -> symbols is identity."""
    rng = random.Random(10)
    for trial in range(20):
        dists = []
        for _ in range(rng.randint(50, 300)):
            k = rng.randint(2, 256)
            raw = [rng.random() ** rng.choice([1, 2, 3]) + 1e-3 for _ in range(k)]
            s = sum(raw)
            dists.append([x / s for x in raw])
        freq_fn, symbol_fn = _fns(dists)
        syms = [rng.randrange(len(d)) for d in dists]
        bits = encode(syms, freq_fn)
        out = decode(bits, len(syms), freq_fn, symbol_fn)
        assert out == syms, f"symbol round-trip failed on trial {trial}"


def test_uniform_binary():
    rng = random.Random(1)
    dists = [[0.5, 0.5] for _ in range(200)]
    msg = [rng.randint(0, 1) for _ in range(120)]
    _, rec = _roundtrip_message(dists, msg)
    assert rec == msg, "uniform binary round-trip failed"


def test_skewed_distributions():
    rng = random.Random(2)
    dists = []
    for _ in range(300):
        k = rng.randint(2, 50)
        raw = [rng.random() ** 3 + 1e-3 for _ in range(k)]
        s = sum(raw)
        dists.append([x / s for x in raw])
    # Size the message to the cover's capacity (skewed dists carry few bits).
    budget = int(_capacity_bits(dists) * 0.5)
    msg = [rng.randint(0, 1) for _ in range(budget)]
    _, rec = _roundtrip_message(dists, msg)
    assert rec == msg, "skewed round-trip failed"


def test_large_alphabet():
    rng = random.Random(3)
    dists = []
    for _ in range(150):
        k = 256
        raw = [rng.random() + 1e-4 for _ in range(k)]
        s = sum(raw)
        dists.append([x / s for x in raw])
    msg = [rng.randint(0, 1) for _ in range(200)]
    _, rec = _roundtrip_message(dists, msg)
    assert rec == msg, "large-alphabet round-trip failed"


def test_quantise_sums_to_total():
    rng = random.Random(4)
    for _ in range(500):
        k = rng.randint(1, 300)
        raw = [rng.random() for _ in range(k)]
        f = quantise(raw)
        assert sum(f) == FREQ_TOTAL
        assert all(x >= 1 for x in f)


if __name__ == "__main__":
    test_quantise_sums_to_total()
    test_symbol_roundtrip()
    test_uniform_binary()
    test_skewed_distributions()
    test_large_alphabet()
    print("all arithmetic round-trip tests passed")
