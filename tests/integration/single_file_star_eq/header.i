"""Shared header — feature flags + constants used by patches.

Splices into every source via `.include`. No emit, just symbol
defs + macros."""

DEBUG := 0
ENABLE_FEATURE := 1
SHARED_TILE := 0x42
