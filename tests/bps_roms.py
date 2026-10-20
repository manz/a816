"""Seeded ROMs for the BPS tests, shared with the script that made the Flips fixture."""

from __future__ import annotations

import random

SOURCE_SIZE = 0x10000


def source_rom() -> bytes:
    return random.Random(816).randbytes(SOURCE_SIZE)


def delta_target() -> bytes:
    """A hack Flips encodes with every action: moved, repeated and new bytes."""
    source = source_rom()
    target = bytearray(source)
    target[0x2000:0x2000] = b"inserted"  # shifts the rest: SourceCopy
    target[0x4000:0x4400] = source[0x8000:0x8400]  # bytes from elsewhere in the source
    target[0x6000:0x6800] = b"ABCD" * 0x200  # a repeated run: TargetCopy
    target += random.Random(65816).randbytes(0x400)  # past the source
    return bytes(target)
