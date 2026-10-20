"""Sealing writes the SNES header checksum: `a816 bps seal`, `a816 ips seal` and `a816 ci seal`."""

from __future__ import annotations

import zlib
from pathlib import Path

import pytest

from a816 import bps, ips
from a816.bps_cli import bps_main
from a816.ci import ci_main
from a816.cli import _dispatch_subcommand
from a816.ips import IpsError
from a816.ips_cli import ips_main
from a816.seal import seal_bps, seal_ips
from a816.snes_header import Cartridge, checksum
from tests.bps_roms import SOURCE_SIZE, source_rom
from tests.ci_fakes import FakeRun, actions_in

CART = Cartridge(rom_size=SOURCE_SIZE, checksum_offset=0x7FDC)
TOML = f'board = "SHVC-1A0N-30"\nrom_size = {SOURCE_SIZE:#x}\n'


def _unsealed(metadata: str = "") -> bytes:
    source = source_rom()
    return bps.encode_linear(
        [(0x100, b"hack")],
        source_size=SOURCE_SIZE,
        source_crc32=zlib.crc32(source),
        target_size=SOURCE_SIZE,
        metadata=metadata,
    )


def _ips() -> bytes:
    return b"PATCH" + ips.record_bytes(0x100, b"hack") + b"EOF"


def _ff6_records(rom: bytes, rom_size: int, at: int) -> bytes:
    """ff6's tools/checksum.py, the oracle: pad record, then complement + checksum."""
    padded = bytearray(rom.ljust(rom_size, b"\0"))
    padded[at : at + 4] = b"\xff\xff\x00\x00"
    total = sum(padded) & 0xFFFF
    pad = (rom_size - 1).to_bytes(3, "big") + b"\x00\x01\x00" if len(rom) < rom_size else b""
    fields = (total ^ 0xFFFF).to_bytes(2, "little") + total.to_bytes(2, "little")
    return pad + at.to_bytes(3, "big") + len(fields).to_bytes(2, "big") + fields


def _checksum_ok(rom: bytes) -> bool:
    total = int.from_bytes(rom[0x7FDE:0x7FE0], "little")
    return total == checksum(rom, CART) and int.from_bytes(rom[0x7FDC:0x7FDE], "little") == total ^ 0xFFFF


def test_without_a_cartridge_bps_seal_only_fills_the_crc() -> None:
    assert seal_bps(_unsealed(), source_rom(), None) == bps.seal(_unsealed(), source_rom())


def test_a_sealed_bps_carries_the_checksum() -> None:
    assert _checksum_ok(bps.apply(seal_bps(_unsealed(), source_rom(), CART), source_rom()))


def test_the_sealed_bps_keeps_the_patch() -> None:
    target = bps.apply(seal_bps(_unsealed(), source_rom(), CART), source_rom())
    assert target[0x100:0x104] == b"hack"


def test_the_sealed_bps_keeps_its_metadata() -> None:
    sealed = seal_bps(_unsealed('<patch v="1"/>'), source_rom(), CART)
    assert bps.info(sealed).metadata == '<patch v="1"/>'


def test_a_right_checksum_keeps_the_patch_as_is() -> None:
    once = seal_bps(_unsealed(), source_rom(), CART)
    target = bps.apply(once, source_rom())
    fixed = bps.encode_linear(
        bps.diff_blocks(source_rom(), target),
        source_size=SOURCE_SIZE,
        source_crc32=zlib.crc32(source_rom()),
        target_size=SOURCE_SIZE,
    )
    assert seal_bps(fixed, source_rom(), CART) == bps.seal(fixed, source_rom())


def test_diff_blocks_rebuild_the_target() -> None:
    source = source_rom()
    target = bytearray(source)
    target[5:9] = b"abcd"
    target[0x3000] ^= 0xFF
    target += b"\x00\x00tail"
    blocks = bps.diff_blocks(source, bytes(target))
    patch = bps.encode_linear(blocks, source_size=SOURCE_SIZE, source_crc32=zlib.crc32(source), target_size=len(target))
    assert bps.apply(patch, source, unsealed=True) == bytes(target)


def test_diff_blocks_cover_only_the_differences() -> None:
    target = bytearray(source_rom())
    target[0x2000:0x2002] = b"\x00\x00" if target[0x2000:0x2002] != b"\x00\x00" else b"\x01\x01"
    assert [address for address, _ in bps.diff_blocks(source_rom(), bytes(target))] == [0x2000]


def _changed_at(*offsets: int) -> bytes:
    target = bytearray(bytes(0x100))
    for offset in offsets:
        target[offset] = 1
    return bytes(target)


def test_diff_blocks_merge_runs_a_few_bytes_apart() -> None:
    assert bps.diff_blocks(bytes(0x100), _changed_at(0x10, 0x15)) == [(0x10, _changed_at(0x10, 0x15)[0x10:0x16])]


def test_diff_blocks_split_runs_further_apart() -> None:
    assert [a for a, _ in bps.diff_blocks(bytes(0x100), _changed_at(0x10, 0x16))] == [0x10, 0x16]


def test_an_ips_seal_matches_ff6_s_checksum_tool() -> None:
    base = source_rom()[: SOURCE_SIZE // 2]
    patch = _ips()
    expected = patch[:-3] + _ff6_records(ips.apply(patch, base), SOURCE_SIZE, 0x7FDC) + b"EOF"
    assert seal_ips(patch, base, CART) == expected


def test_an_ips_seal_without_padding_needed_adds_only_the_checksum() -> None:
    sealed = seal_ips(_ips(), source_rom(), CART)
    assert len(ips.parse(sealed).records) == 2


def test_sealing_an_ips_twice_changes_nothing() -> None:
    once = seal_ips(_ips(), source_rom(), CART)
    assert seal_ips(once, source_rom(), CART) == once


def test_a_sealed_ips_carries_the_checksum() -> None:
    assert _checksum_ok(ips.apply(seal_ips(_ips(), source_rom(), CART), source_rom()))


def test_a_headered_base_is_refused() -> None:
    with pytest.raises(IpsError, match="copier header"):
        seal_ips(_ips(), bytes(0x8200), CART)


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "a816.toml").write_text(TOML, encoding="utf-8")
    (tmp_path / "base.sfc").write_bytes(source_rom())
    monkeypatch.chdir(tmp_path)
    return tmp_path / "base.sfc"


def test_bps_seal_fixes_the_checksum_in_a_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _project(tmp_path, monkeypatch)
    (tmp_path / "x.bps.unsealed").write_bytes(_unsealed())
    bps_main(["seal", "--source", str(base), "x.bps.unsealed"])
    assert _checksum_ok(bps.apply((tmp_path / "x.bps").read_bytes(), source_rom()))


def test_bps_seal_no_checksum_only_fills_the_crc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _project(tmp_path, monkeypatch)
    (tmp_path / "x.bps.unsealed").write_bytes(_unsealed())
    bps_main(["seal", "--no-checksum", "--source", str(base), "x.bps.unsealed"])
    assert (tmp_path / "x.bps").read_bytes() == bps.seal(_unsealed(), source_rom())


def test_ips_seal_writes_in_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _project(tmp_path, monkeypatch)
    (tmp_path / "x.ips").write_bytes(_ips())
    ips_main(["seal", "--source", str(base), "x.ips"])
    assert _checksum_ok(ips.apply((tmp_path / "x.ips").read_bytes(), source_rom()))


def test_ips_seal_can_write_elsewhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _project(tmp_path, monkeypatch)
    (tmp_path / "x.ips").write_bytes(_ips())
    ips_main(["seal", "--source", str(base), "x.ips", "-o", "sealed.ips"])
    assert ((tmp_path / "x.ips").read_bytes(), (tmp_path / "sealed.ips").is_file()) == (_ips(), True)


def test_ips_seal_needs_a_cartridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "x.ips").write_bytes(_ips())
    (tmp_path / "base.sfc").write_bytes(source_rom())
    status = ips_main(["seal", "--source", "base.sfc", "x.ips"])
    assert (status, "E0519" in capsys.readouterr().err) == (1, True)


def test_ips_seal_reports_a_malformed_patch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _project(tmp_path, monkeypatch)
    (tmp_path / "x.ips").write_bytes(b"junk")
    assert (ips_main(["seal", "--source", str(base), "x.ips"]), "E0517" in capsys.readouterr().err) == (1, True)


def test_ci_seal_seals_an_ips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _project(tmp_path, monkeypatch)
    (tmp_path / "x.ips").write_bytes(_ips())
    ci_main(["seal", "--rom", str(base), "--patches", "x.ips"], actions_in(tmp_path), FakeRun())
    assert _checksum_ok(ips.apply((tmp_path / "x.ips").read_bytes(), source_rom()))


def test_ci_seal_no_checksum_leaves_the_bps_crc_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _project(tmp_path, monkeypatch)
    (tmp_path / "x.bps.unsealed").write_bytes(_unsealed())
    argv = ["seal", "--rom", str(base), "--patches", "x.bps.unsealed", "--no-checksum"]
    ci_main(argv, actions_in(tmp_path), FakeRun())
    assert (tmp_path / "x.bps").read_bytes() == bps.seal(_unsealed(), source_rom())


def test_ci_seal_needs_a_cartridge_for_an_ips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "x.ips").write_bytes(_ips())
    (tmp_path / "base.sfc").write_bytes(source_rom())
    assert ci_main(["seal", "--rom", "base.sfc", "--patches", "x.ips"], actions_in(tmp_path), FakeRun()) == 1


def test_a816_dispatches_ips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _project(tmp_path, monkeypatch)
    (tmp_path / "x.ips").write_bytes(_ips())
    assert _dispatch_subcommand(["ips", "seal", "--source", str(base), "x.ips"]) == 0
