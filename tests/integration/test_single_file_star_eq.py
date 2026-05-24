"""Regression: Bahamut-Lagoon-shape single-file ROM patches.

Pattern: one main.s with `*=` directives and `.include`d patch
files (no `.import` modules). Every byte goes through direct
mode + multi-pass resolver; cross-include label references
resolve via the shared resolver.

This is the canonical "ROM patch" shape that must keep working
forever — sections refactor + import-chain-dies doctrine
target the OTHER shape (modular code with module-owned
placement). Hobbyist projects that started as single-file
disassembly hijacks ship through this pipeline and shouldn't
need a structural rewrite to track a816 master.

Bisect against ff4-modules surfaced two commits that regressed
the related `*= / .import` chain pattern:
- `292e93f` (a24) added `.import` source-dedup
- `1efe8aa` (a28) added prelude-skip-precompile

Neither of those changes should touch the `.include`-only path
this test exercises — `.include` is text-splice, no module
boundary, no dedup question. The test pins that contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.module_builder import build_with_imports_direct

HERE = Path(__file__).parent
SRC_DIR = HERE / "single_file_star_eq"


def _build(tmp_path: Path, *, overlap_mode: str = "error") -> bytes:
    out = tmp_path / "out.ips"
    result = build_with_imports_direct(
        main_source=SRC_DIR / "main.s",
        output_file=out,
        output_format="ips",
        module_paths=[SRC_DIR],
        output_dir=tmp_path / "obj",
        include_paths=[SRC_DIR],
        overlap_mode=overlap_mode,
    )
    assert result.exit_code == 0, f"build returned {result.exit_code}"
    return out.read_bytes()


def _ips_records(data: bytes) -> list[tuple[int, bytes]]:
    assert data[:5] == b"PATCH"
    out: list[tuple[int, bytes]] = []
    pos = 5
    while data[pos : pos + 3] != b"EOF":
        offset = int.from_bytes(data[pos : pos + 3], "big")
        pos += 3
        length = int.from_bytes(data[pos : pos + 2], "big")
        pos += 2
        if length == 0:
            rle_len = int.from_bytes(data[pos : pos + 2], "big")
            pos += 2
            byte = data[pos]
            pos += 1
            payload = bytes([byte]) * rle_len
        else:
            payload = data[pos : pos + length]
            pos += length
        out.append((offset, payload))
    return out


@pytest.fixture
def ips_bytes(tmp_path: Path) -> bytes:
    return _build(tmp_path)


class TestBahamutLagoonShape:
    """Single-file `.include` + `*=` direct-mode ROM patch."""

    def test_build_succeeds(self, ips_bytes: bytes) -> None:
        assert ips_bytes[:5] == b"PATCH"
        assert ips_bytes[-3:] == b"EOF"

    def test_all_pinned_sites_emit(self, ips_bytes: bytes) -> None:
        """Patches land at $00:8000, $00:8100, $00:8200, $01:8000,
        $00:FFC0. LoROM physical: 0x0000, 0x0100, 0x0200, 0x8000,
        0x7FC0."""
        offsets = {off for off, _ in _ips_records(ips_bytes)}
        expected = {0x0000, 0x0100, 0x0200, 0x8000, 0x7FC0}
        missing = expected - offsets
        assert not missing, f"missing pinned sites at physical offsets: {missing}"

    def test_cross_include_label_resolves(self, ips_bytes: bytes) -> None:
        """`boot_hook` at $00:8000 does `jsr.l patch_a_entry`.
        patch_a_entry is defined in `patches_a.s` at $00:8100.
        Operand of JSL at physical 0x0001 should be 0x008100."""
        records = {off: payload for off, payload in _ips_records(ips_bytes)}
        boot = records[0x0000]
        assert boot[0] == 0x22, f"boot_hook missing JSL opcode (got 0x{boot[0]:02x})"
        target = int.from_bytes(boot[1:4], "little")
        assert target == 0x008100, f"jsr.l patch_a_entry resolved to 0x{target:06x}, expected 0x008100"

    def test_cross_bank_label_resolves(self, ips_bytes: bytes) -> None:
        """`patch_b_entry` at $01:8000 does `jsr.l patch_a_entry`
        which lives in bank 0. Linker must resolve across banks."""
        records = {off: payload for off, payload in _ips_records(ips_bytes)}
        bank1 = records[0x8000]
        assert bank1[0] == 0x22
        target = int.from_bytes(bank1[1:4], "little")
        assert target == 0x008100, f"cross-bank JSL resolved to 0x{target:06x}, expected 0x008100"

    def test_no_silent_overlap(self, tmp_path: Path) -> None:
        """`overlap_mode="error"` (current default) succeeds: no two
        `*=` sites in this shape write the same bytes."""
        _build(tmp_path, overlap_mode="error")  # raises if overlap

    def test_header_bytes_at_ffc0(self, ips_bytes: bytes) -> None:
        records = {off: payload for off, payload in _ips_records(ips_bytes)}
        header = records[0x7FC0]
        assert header.startswith(b"BAHAMUT TEST SHAPE")
