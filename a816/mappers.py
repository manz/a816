"""Fixed cartridge mappers: the default bus layouts behind `-m low/low2/high`.

`MAPPERS` holds the exact region lists of those default buses.
`MAPPER_BOARDS` only remembers which ares board the removed a816.toml
`mapper = "lorom"/"hirom"` meant, so its error can name the replacement.
"""

from __future__ import annotations

from a816.cpu.mapping import BsnesRegion, Bus, parse_bml_address
from a816.object_file import BusMapping

_WRAM = BusMapping("2", (0x7E, 0x7F), (0x0000, 0xFFFF), 0x1_0000, writeable=True)

MAPPERS: dict[str, tuple[BusMapping, ...]] = {
    "lorom": (
        BusMapping("1", (0x00, 0x6F), (0x8000, 0xFFFF), 0x8000, mirror_bank_range=(0x80, 0xCF)),
        _WRAM,
    ),
    "hirom": (
        BusMapping("1", (0x40, 0x7F), (0x0000, 0xFFFF), 0x1_0000, mirror_bank_range=(0xC0, 0xFF)),
        _WRAM,
    ),
}

#: Removed a816.toml `mapper = ...` -> the ares board to write instead.
MAPPER_BOARDS: dict[str, str] = {"lorom": "SHVC-1A0N-30", "hirom": "SHVC-1J0N-20"}

#: `-m` spelling -> mapper preset. `low2` addresses the same LoROM bus
#: through its $80+ mirror, so it agrees with `lorom`.
CLI_MAPPERS: dict[str, str] = {"low": "lorom", "low2": "lorom", "high": "hirom"}


def map_on_bus(bus: Bus, mapping: BusMapping) -> None:
    """Declare one region on ``bus``: bsnes semantics for a BML region, legacy stride for a `.map`."""
    if mapping.address is not None:
        ranges, windows = parse_bml_address(mapping.address)
        region = BsnesRegion(ranges, windows, mapping.mask, mapping.base, mapping.rom_size, mapping.writeable)
        bus.map_region(mapping.identifier, region)
    else:
        bus.map(
            mapping.identifier,
            mapping.bank_range,
            mapping.addr_range,
            mapping.mask,
            writeable=mapping.writeable,
            mirror_bank_range=mapping.mirror_bank_range,
        )
    bus.declared[mapping.identifier] = mapping.shape()


def build_mapper_bus(name: str, mapper: str) -> Bus:
    """A read-only bus holding the ``mapper`` preset's regions."""
    bus = Bus(name)
    for mapping in MAPPERS[mapper]:
        map_on_bus(bus, mapping)
    bus.editable = False
    return bus
