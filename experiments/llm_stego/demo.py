"""End-to-end demo: embed a secret message, recover it with the same key,
show that a wrong key fails, and report the bit error rate.

Run (from this directory):
    PYTHONPATH=. <venv>/bin/python demo.py
"""

import argparse

from stego import StegoChannel, bits_to_text, text_to_bits


def ber(a, b):
    n = min(len(a), len(b))
    if n == 0:
        return 1.0
    errs = sum(x != y for x, y in zip(a[:n], b[:n])) + abs(len(a) - len(b))
    return errs / max(len(a), len(b))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--message", default="meet at dawn")
    ap.add_argument("--prompt", default="Write a few sentences of travel advice for visiting a coastal town.")
    ap.add_argument("--key", default="correct horse battery staple")
    ap.add_argument("--wrong-key", default="attacker guess")
    ap.add_argument("--max-new-tokens", type=int, default=200)
    args = ap.parse_args()

    secret = text_to_bits(args.message)
    print(f"Secret message : {args.message!r}  ({len(secret)} bits)")

    print("\nLoading model (sender)...")
    sender = StegoChannel(key=args.key.encode())
    print("Embedding into cover text...")
    cover, token_ids = sender.embed(args.prompt, secret, max_new_tokens=args.max_new_tokens)
    print("\n--- COVER TEXT (looks like ordinary model output) ---")
    print(cover)
    print("-----------------------------------------------------")
    print(f"(cover is {len(token_ids)} tokens)")

    print("\nReceiver with the CORRECT key extracts...")
    receiver = StegoChannel(key=args.key.encode())
    recovered = receiver.extract(args.prompt, token_ids)
    if recovered is None:
        print("  frame invalid (extraction failed)")
    else:
        print(f"  recovered bits : {len(recovered)}")
        print(f"  recovered text : {bits_to_text(recovered)!r}")
        print(f"  bit error rate : {ber(secret, recovered):.3f}")
        print(f"  EXACT MATCH    : {recovered == secret}")

    print("\nEavesdropper with a WRONG key extracts...")
    attacker = StegoChannel(key=args.wrong_key.encode())
    stolen = attacker.extract(args.prompt, token_ids)
    if stolen is None:
        print("  frame invalid (nothing legible recovered)")
    else:
        print(f"  recovered bits : {len(stolen)}")
        print(f"  recovered text : {bits_to_text(stolen)!r}")
        print(f"  bit error rate vs secret : {ber(secret, stolen):.3f}")


if __name__ == "__main__":
    main()
