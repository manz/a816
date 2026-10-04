"""bsnes-semantics bus regions: `reduce`/`mirror`, BML address specs, and the (bank, address) bus.

Regions below are copied from ares' `boards.bml` (LoROM, HiROM, ExHiROM,
SA-1-style 0x408000 masks, BS-X 0xe08000 masks, SRAM windows) so the math is
checked against the shapes real boards use.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.cpu.mapping import BsnesRegion, Bus, mirror, parse_bml_address, reduce
from a816.exceptions import UnmappedBankError
from a816.mappers import map_on_bus
from a816.module_builder import build_with_imports
from a816.object_file import BusMapping


def _region(address: str, mask: int = 0, base: int = 0, size: int = 0, writable: bool = False) -> BsnesRegion:
    ranges, windows = parse_bml_address(address)
    return BsnesRegion(ranges, windows, mask, base, size, writable)


@pytest.mark.parametrize(
    ("addr", "mask", "expected"),
    [
        (0x018000, 0x8000, 0x008000),  # LoROM: A15 removed
        (0x808000, 0x8000, 0x400000),
        (0xC12345, 0, 0xC12345),  # no mask: full address
        (0x808000, 0xC00000, 0x008000),  # ExHiROM upper banks: bits 22-23 removed
        (0x208000, 0xE08000, 0x000000),  # BS-X: bits 15, 21-23 removed
    ],
)
def test_reduce_removes_mask_bits(addr: int, mask: int, expected: int) -> None:
    assert reduce(addr, mask) == expected


@pytest.mark.parametrize(
    ("addr", "size", "expected"),
    [
        (0x408000, 0x200000, 0x008000),  # power of two: plain wrap
        (0x1FFFFF, 0x200000, 0x1FFFFF),
        (0x300000, 0x300000, 0x200000),  # 3 MB image: upper MB mirrors the last MB
        (0x0, 0, 0),
    ],
)
def test_mirror_folds_into_size(addr: int, size: int, expected: int) -> None:
    assert mirror(addr, size) == expected


def test_parse_bml_address_splits_banks_and_window() -> None:
    assert parse_bml_address("00-3f,80-bf:8000-ffff") == ([(0x00, 0x3F), (0x80, 0xBF)], [(0x8000, 0xFFFF)])


def test_parse_bml_address_splits_several_windows() -> None:
    assert parse_bml_address("00-3f:6000-6bff,7000-7bff") == ([(0x00, 0x3F)], [(0x6000, 0x6BFF), (0x7000, 0x7BFF)])


def test_parse_bml_address_accepts_single_bank() -> None:
    assert parse_bml_address("70:0000-7fff") == ([(0x70, 0x70)], [(0x0000, 0x7FFF)])


@pytest.mark.parametrize("spec", ["00-3f", "zz:0000-ffff", "3f-00:0000-ffff", "00-3f:8000-1ffff"])
def test_parse_bml_address_rejects_malformed(spec: str) -> None:
    with pytest.raises(ValueError):
        parse_bml_address(spec)


@pytest.mark.parametrize(
    ("region", "logical", "physical"),
    [
        (_region("00-7d,80-ff:8000-ffff", 0x8000, size=0x200000), 0x018000, 0x008000),
        (_region("00-7d,80-ff:8000-ffff", 0x8000, size=0x200000), 0x818000, 0x008000),
        (_region("40-7d,c0-ff:0000-ffff", size=0x400000), 0xC12345, 0x012345),
        (_region("00-3f:8000-ffff", base=0x400000, size=0x800000), 0x008000, 0x408000),
        (_region("40-7d:0000-ffff", base=0x400000, size=0x800000), 0x400000, 0x400000),
        (_region("c0-ff:0000-ffff", 0xC00000, size=0x800000), 0xC00000, 0x000000),
    ],
)
def test_region_physical_matches_bsnes(region: BsnesRegion, logical: int, physical: int) -> None:
    assert region.physical_address(logical) == physical


def test_writable_region_has_no_file_offset() -> None:
    assert _region("70-7d:0000-7fff", 0x8000, writable=True).physical_address(0x700000) is None


_BOARD_REGIONS = [
    _region("00-7d,80-ff:8000-ffff", 0x8000, size=0x200000),
    _region("00-7d,80-ff:8000-ffff", 0x8000, size=0x400000),
    _region("40-7d,c0-ff:0000-ffff", size=0x400000),
    _region("00-3f,80-bf:8000-ffff", size=0x400000),
    _region("00-3f:8000-ffff", base=0x400000, size=0x800000),
    _region("40-7d:0000-ffff", base=0x400000, size=0x800000),
    _region("80-bf:8000-ffff", 0xC00000, size=0x800000),
    _region("c0-ff:0000-ffff", 0xC00000, size=0x800000),
    _region("00-3f,80-bf:8000-ffff", 0x408000, size=0x400000),
    _region("20-3f:8000-ffff", 0xE08000, base=0x100000, size=0x300000),
]


def _samples(region: BsnesRegion) -> list[int]:
    return [
        bank << 16 | addr
        for bank_lo, bank_hi in region.ranges
        for bank in {bank_lo, (bank_lo + bank_hi) // 2, bank_hi}
        for lo, hi in region.windows
        for addr in {lo, (lo + hi) // 2, hi}
    ]


@pytest.mark.parametrize("region", _BOARD_REGIONS, ids=lambda r: f"{r.ranges}:{r.windows}/{r.mask:x}")
def test_inverse_round_trips_within_the_callers_bank_range(region: BsnesRegion) -> None:
    for logical in _samples(region):
        physical = region.physical_address(logical)
        assert physical is not None
        back = region.logical_address(physical, near=logical)
        assert region.physical_address(back) == physical, f"${logical:06X}"
        same_range = [r for r in region.ranges if r[0] <= logical >> 16 <= r[1]]
        assert same_range[0][0] <= back >> 16 <= same_range[0][1], f"${logical:06X} -> ${back:06X}"


def test_inverse_without_near_uses_the_first_bank_range() -> None:
    region = _region("00-7d,80-ff:8000-ffff", 0x8000, size=0x200000)
    assert region.logical_address(0x008000) == 0x018000


def test_inverse_rejects_unreachable_offsets() -> None:
    region = _region("00-3f:8000-ffff", base=0x400000, size=0x800000)
    with pytest.raises(ValueError):
        region.logical_address(0x000000)


def _board_1a3m() -> Bus:
    """SHVC-1A3M: LoROM with SRAM in the low half of banks $70-$7D."""
    bus = Bus("1A3M")
    map_on_bus(bus, BusMapping.bml("1", "00-7d,80-ff:8000-ffff", mask=0x8000, rom_size=0x400000))
    map_on_bus(bus, BusMapping.bml("2", "70-7d,f0-ff:0000-7fff", mask=0x8000, writeable=True))
    return bus


def test_rom_and_sram_share_banks() -> None:
    bus = _board_1a3m()
    assert (bus.get_address(0x708000).physical, bus.get_address(0x700000).writable) == (0x380000, True)


def test_address_outside_every_window_is_unmapped() -> None:
    bus = _board_1a3m()
    with pytest.raises(UnmappedBankError):
        bus.get_address(0x004000)


def test_adding_across_a_bank_stays_in_the_mirror_range() -> None:
    assert (_board_1a3m().get_address(0x80FFFF) + 1).logical_value == 0x818000


def test_unmap_drops_the_region_windows() -> None:
    bus = _board_1a3m()
    bus.unmap("2")
    assert bus.get_address(0x708000).physical == 0x380000
    with pytest.raises(UnmappedBankError):
        bus.get_address(0x700000)


def test_get_mapping_for_bank_returns_the_last_declared_window() -> None:
    assert _board_1a3m().get_mapping_for_bank(0x70).writable is True


_ROM_AND_SRAM_TOML = """rom_size = 0x400000
[map.1]
address = "00-7d,80-ff:8000-ffff"
mask = 0x8000
[map.2]
address = "70-7d,f0-ff:0000-7fff"
mask = 0x8000
writable = true
"""


def _ips_records(path: Path) -> list[tuple[int, bytes]]:
    data = path.read_bytes()
    out, i = [], 5
    while data[i : i + 3] != b"EOF":
        offset, size = int.from_bytes(data[i : i + 3], "big"), int.from_bytes(data[i + 3 : i + 5], "big")
        out.append((offset, data[i + 5 : i + 5 + size]))
        i += 5 + size
    return out


def test_toml_board_places_rom_code_in_an_sram_bank(tmp_path: Path) -> None:
    (tmp_path / "a816.toml").write_text(_ROM_AND_SRAM_TOML, encoding="utf-8")
    (tmp_path / "main.s").write_text(".alloc at 0x708000 {\n    rts\n}\n", encoding="utf-8")
    result = build_with_imports(tmp_path / "main.s", tmp_path / "out.ips", output_dir=tmp_path / "obj")
    assert result.exit_code == 0, result.diagnostics
    assert _ips_records(tmp_path / "out.ips") == [(0x380000, b"\x60")]


def test_unsized_region_does_not_fold() -> None:
    assert _region("c0-ff:0000-ffff", base=0x10).physical_address(0xC00000) == 0xC00010


def test_unsized_region_inverse_gives_up_past_24_bits() -> None:
    region = _region("c0-ff:0000-ffff")
    with pytest.raises(ValueError):
        region.logical_address(0x000000)
