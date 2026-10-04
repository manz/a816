"""Cartridge boards from the vendored ares `boards.bml`, and `board = ...` in a816.toml."""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.boards import ARES_REVISION, BoardRegion, _expand_names, boards
from a816.config import load_a816_toml
from a816.cpu.mapping import BsnesRegion, parse_bml_address
from a816.exceptions import A816ConfigError
from a816.module_builder import build_with_imports


def test_every_board_name_is_expanded() -> None:
    assert len(boards()) == 148


def test_revision_is_pinned() -> None:
    assert len(ARES_REVISION) == 40


@pytest.mark.parametrize(
    ("name", "expanded"),
    [
        ("SHVC-1A3M-(10,20)", ["SHVC-1A3M-10", "SHVC-1A3M-20"]),
        ("SHVC-2A0N-01#A", ["SHVC-2A0N-01#A"]),
        ("SHVC-1C0N", ["SHVC-1C0N"]),
    ],
)
def test_variant_names_expand(name: str, expanded: list[str]) -> None:
    assert _expand_names(name) == expanded


def test_lorom_sram_board_has_rom_and_sram_in_shared_banks() -> None:
    assert boards()["SHVC-1A3M-30"] == (
        BoardRegion("00-7d,80-ff:8000-ffff", mask=0x8000),
        BoardRegion("70-7d,f0-ff:0000-7fff", mask=0x8000, writable=True),
    )


def test_exhirom_board_offsets_its_low_banks() -> None:
    rom = [r for r in boards()["SHVC-LJ3M-01"] if not r.writable]
    assert [(r.address, r.mask, r.base) for r in rom] == [
        ("00-3f:8000-ffff", 0, 0x400000),
        ("40-7d:0000-ffff", 0, 0x400000),
        ("80-bf:8000-ffff", 0xC00000, 0),
        ("c0-ff:0000-ffff", 0xC00000, 0),
    ]


def test_coprocessor_program_rom_comes_from_its_mcu_window() -> None:
    rom = [r.address for r in boards()["SHVC-1L5B-20"] if not r.writable]
    assert rom == ["00-3f,80-bf:8000-ffff", "c0-ff:0000-ffff"]


def test_slots_are_not_board_memory() -> None:
    assert boards()["BANDAI-PT-923"] == (BoardRegion("00-1f,80-9f:8000-ffff", mask=0x8000),)


def test_every_region_address_parses() -> None:
    for name, regions in boards().items():
        for region in regions:
            parse_bml_address(region.address)  # raises on a malformed spec
            assert region.mask >= 0, name


def _rom_regions() -> list[tuple[str, BsnesRegion]]:
    out = []
    for name, regions in boards().items():
        for r in regions:
            if not r.writable:
                ranges, windows = parse_bml_address(r.address)
                out.append((name, BsnesRegion(ranges, windows, r.mask, r.base, 0x800000, False)))
    return out


def test_every_board_rom_region_round_trips() -> None:
    """physical -> logical -> physical is exact and stays in the caller's bank range, for every board."""
    for name, region in _rom_regions():
        lo, hi = region.windows[0][0], region.windows[-1][1]
        for bank_lo, bank_hi in region.ranges:
            for logical in (bank_lo << 16 | lo, bank_hi << 16 | hi):
                physical = region.physical_address(logical)
                assert physical is not None
                back = region.logical_address(physical, near=logical)
                assert region.physical_address(back) == physical, f"{name} ${logical:06X}"
                assert bank_lo <= back >> 16 <= bank_hi, f"{name} ${logical:06X} -> ${back:06X}"


def _load(tmp_path: Path, body: str):  # type: ignore[no-untyped-def]
    cfg = tmp_path / "a816.toml"
    cfg.write_text(body, encoding="utf-8")
    return load_a816_toml(cfg)


def _error_code(tmp_path: Path, body: str) -> str:
    with pytest.raises(A816ConfigError) as info:
        _load(tmp_path, body)
    return info.value.code.code


def test_board_expands_to_its_regions_plus_wram(tmp_path: Path) -> None:
    config = _load(tmp_path, 'board = "SHVC-1A3M-30"\nrom_size = 0x400000\n')
    assert [(m.identifier, m.address, m.writeable, m.rom_size) for m in config.bus_map] == [
        ("board.0", "00-7d,80-ff:8000-ffff", False, 0x400000),
        ("board.1", "70-7d,f0-ff:0000-7fff", True, 0),
        ("board.wram", "7e-7f:0000-ffff", True, 0),
    ]


def test_board_regions_come_before_map_tables(tmp_path: Path) -> None:
    body = 'board = "SHVC-1A3M-30"\nrom_size = 0x400000\n[map.9]\naddress = "60-6f:0000-7fff"\nwritable = true\n'
    assert _load(tmp_path, body).bus_map[-1].identifier == "9"


def test_board_with_rom_requires_rom_size(tmp_path: Path) -> None:
    assert _error_code(tmp_path, 'board = "SHVC-1A3M-30"\n') == "E0506"


def test_unknown_board_is_rejected(tmp_path: Path) -> None:
    assert _error_code(tmp_path, 'board = "SHVC-NOPE"\n') == "E0509"


def test_unknown_board_suggests_close_names(tmp_path: Path) -> None:
    with pytest.raises(A816ConfigError) as info:
        _load(tmp_path, 'board = "SHVC-1A3M-31"\n')
    assert "SHVC-1A3M-30" in info.value.message


def test_board_must_be_a_string(tmp_path: Path) -> None:
    assert _error_code(tmp_path, "board = 1\n") == "E0509"


def test_mapper_excludes_board(tmp_path: Path) -> None:
    assert _error_code(tmp_path, 'mapper = "lorom"\nboard = "SHVC-1A3M-30"\n') == "E0507"


def _ips_records(path: Path) -> list[tuple[int, bytes]]:
    data = path.read_bytes()
    out, i = [], 5
    while data[i : i + 3] != b"EOF":
        offset, size = int.from_bytes(data[i : i + 3], "big"), int.from_bytes(data[i + 3 : i + 5], "big")
        out.append((offset, data[i + 5 : i + 5 + size]))
        i += 5 + size
    return out


_SOURCE = ".alloc at 0x708000 {\n    rts\n}\n.alloc at 0x818000 {\n    nop\n}\n"
_HAND_WRITTEN = """rom_size = 0x400000
[map.1]
address = "00-7d,80-ff:8000-ffff"
mask = 0x8000
[map.2]
address = "70-7d,f0-ff:0000-7fff"
mask = 0x8000
writable = true
"""


def _build(root: Path, toml: str) -> list[tuple[int, bytes]]:
    root.mkdir()
    (root / "a816.toml").write_text(toml, encoding="utf-8")
    (root / "main.s").write_text(_SOURCE, encoding="utf-8")
    result = build_with_imports(root / "main.s", root / "out.ips", output_dir=root / "obj")
    assert result.exit_code == 0, result.diagnostics
    return _ips_records(root / "out.ips")


def test_board_build_matches_the_hand_written_regions(tmp_path: Path) -> None:
    by_board = _build(tmp_path / "board", 'board = "SHVC-1A3M-30"\nrom_size = 0x400000\n')
    assert by_board == _build(tmp_path / "hand", _HAND_WRITTEN)
    assert sorted(by_board) == [(0x008000, b"\xea"), (0x380000, b"\x60")]
