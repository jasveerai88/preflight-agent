#!/usr/bin/env python3
"""Fictional example hook for hook-doctor's fixtures.

Pretend guard: refuse a write that looks like it contains a
secret. It signals its verdict with exit 1 — which stops
nothing: every code other than the blocking one is treated
as a non-blocking error and the write goes ahead anyway.
hook-doctor flags exactly this shape as the EXIT_CODE_TRAP:
guard intent with no blocking signal behind it.
"""
import sys


def main():
    payload = sys.stdin.read()
    if "SECRET" in payload:
        print("this write looks like it holds a secret",
              file=sys.stderr)
        sys.exit(1)  # the trap: a non-blocking error
    sys.exit(0)


if __name__ == "__main__":
    main()
