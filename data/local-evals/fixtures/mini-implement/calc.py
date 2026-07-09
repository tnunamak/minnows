"""Tiny calculator — used as local-eval implement.standard fixture."""

def add(a: int, b: int) -> int:
    # BUG: missing return (oracle: tests must pass after agent fix)
    a + b


def mul(a: int, b: int) -> int:
    return a * b
