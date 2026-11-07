#!/usr/bin/env python3
"""Fictional example hook for hook-doctor's fixtures.

Pretend guard: deny a shell command that asks for the
danger word. This is the correct shape — when it wants to
stop the call it exits 2, so hook-doctor finds the blocking
signal and raises no EXIT_CODE_TRAP for it.
"""
import sys


def main():
    payload = sys.stdin.read()
    if "danger" in payload:
        print("denied: dangerous command", file=sys.stderr)
        sys.exit(2)  # exit 2 = block; stderr is the reason
    sys.exit(0)


if __name__ == "__main__":
    main()
