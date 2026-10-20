"""The SNES header checksum sealing writes, located through the project's cartridge."""

from __future__ import annotations

from pathlib import Path

from a816.config import load_a816_toml
from a816.snes_header import Cartridge, _sum_mirrored, cartridge_at, checksum, checksum_fields, with_checksum

LOROM = Cartridge(rom_size=0x80000, checksum_offset=0x7FDC)


def _cartridge(tmp_path: Path, toml: str) -> Cartridge | None:
    (tmp_path / "a816.toml").write_text(toml, encoding="utf-8")
    return Cartridge.of(load_a816_toml(tmp_path / "a816.toml"))


def test_a_lorom_header_is_at_7fc0(tmp_path: Path) -> None:
    assert _cartridge(tmp_path, 'board = "SHVC-1A0N-30"\nrom_size = 0x80000\n') == LOROM


def test_a_hirom_header_is_at_ffc0(tmp_path: Path) -> None:
    cartridge = _cartridge(tmp_path, 'board = "SHVC-1J0N-20"\nrom_size = 0x400000\n')
    assert cartridge == Cartridge(rom_size=0x400000, checksum_offset=0xFFDC)


def test_a_map_table_locates_the_header(tmp_path: Path) -> None:
    toml = 'rom_size = 0x100000\n[map.1]\naddress = "00-3f,80-bf:8000-ffff"\nmask = 0x8000\n'
    assert _cartridge(tmp_path, toml) == Cartridge(rom_size=0x100000, checksum_offset=0x7FDC)


def test_no_rom_region_is_no_cartridge(tmp_path: Path) -> None:
    assert _cartridge(tmp_path, 'entrypoint = "main.s"\n') is None


def test_an_unmapped_header_is_no_cartridge(tmp_path: Path) -> None:
    toml = 'rom_size = 0x100000\n[map.1]\naddress = "40-7d:0000-ffff"\n'
    assert _cartridge(tmp_path, toml) is None


def test_a_header_outside_the_rom_is_no_cartridge(tmp_path: Path) -> None:
    toml = 'rom_size = 0x100000\n[map.1]\naddress = "40-7d:0000-ffff"\n[map.2]\naddress = "00:8000-ffff"\nwritable = true\n'
    assert _cartridge(tmp_path, toml) is None


def test_no_config_is_no_cartridge() -> None:
    assert Cartridge.of(None) is None


def test_the_cartridge_is_found_above_a_path(tmp_path: Path) -> None:
    (tmp_path / "a816.toml").write_text('board = "SHVC-1A0N-30"\nrom_size = 0x80000\n', encoding="utf-8")
    (tmp_path / "src").mkdir()
    assert cartridge_at(tmp_path / "src") == LOROM


def test_a_power_of_two_sums_once() -> None:
    assert _sum_mirrored(bytes([1, 2, 3, 4])) == (10, 4)


def test_a_tail_is_mirrored_up_to_a_power_of_two() -> None:
    assert _sum_mirrored(bytes([1, 2, 3])) == (1 + 2 + 3 + 3, 4)


def test_a_three_part_tail_mirrors_twice() -> None:
    # 6 bytes: 4 summed once, the 2-byte tail mirrored to 4.
    assert _sum_mirrored(bytes([1, 1, 1, 1, 5, 6])) == (4 + (5 + 6) * 2, 8)


def test_a_blank_rom_sums_to_its_neutral_fields() -> None:
    assert checksum(bytes(0x80000), LOROM) == 0x1FE


def test_the_old_fields_do_not_count() -> None:
    rom = bytearray(0x80000)
    rom[0x7FDC:0x7FE0] = b"\x12\x34\x56\x78"
    assert checksum(bytes(rom), LOROM) == 0x1FE


def test_a_short_rom_is_padded_with_zeros() -> None:
    assert checksum(b"\x01", LOROM) == 0x1FF


def test_the_fields_are_complement_then_checksum() -> None:
    assert checksum_fields(bytes(0x80000), LOROM) == b"\x01\xfe\xfe\x01"


def test_with_checksum_writes_the_fields() -> None:
    assert with_checksum(bytes(0x80000), LOROM)[0x7FDC:0x7FE0] == b"\x01\xfe\xfe\x01"


def test_with_checksum_pads_to_rom_size() -> None:
    assert len(with_checksum(bytes(0x10000), LOROM)) == 0x80000
