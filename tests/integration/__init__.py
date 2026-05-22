"""End-to-end integration tests.

Each test assembles a real `.s` source, loads the result into the
`kintsuki` SNES emulator, runs a handful of frames, and asserts on
emulator-side state (VRAM tile bytes, CGRAM palette, framebuffer
content). Catches regressions that pure-Python unit tests miss —
codegen output drift, DMA emit ordering, IPS patch application,
`.alloc`/`.relocate` placement, etc.

`kintsuki` is a hard dev dependency; CI installs the wheel, so tests
run everywhere rather than skipping when the native lib is absent.
"""
