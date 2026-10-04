"""Fixed cartridge mappers: the bus layouts behind `-m` and `mapper = ...`.

Each preset is the exact region list of the matching default bus, so
`mapper = "lorom"` in `a816.toml` and `-m low` describe the same bus.
Only layouts `a816.cpu.mapping.Mapping` can express are listed: a
region's physical offset always starts at zero, so a mapper that splits
one ROM image across two bank windows (ExHiROM) has no preset.
"""

from __future__ import annotations

from a816.cpu.mapping import Bus
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

#: `-m` spelling -> mapper preset. `low2` addresses the same LoROM bus
#: through its $80+ mirror, so it agrees with `lorom`.
CLI_MAPPERS: dict[str, str] = {"low": "lorom", "low2": "lorom", "high": "hirom"}

#: Mapper preset -> the `-m` spelling it implies when `-m` is absent.
MAPPER_CLI_FLAGS: dict[str, str] = {"lorom": "low", "hirom": "high"}


def map_on_bus(bus: Bus, mapping: BusMapping) -> None:
    """Declare one region on ``bus``."""
    bus.map(
        mapping.identifier,
        mapping.bank_range,
        mapping.addr_range,
        mapping.mask,
        writeable=mapping.writeable,
        mirror_bank_range=mapping.mirror_bank_range,
    )


def build_mapper_bus(name: str, mapper: str) -> Bus:
    """A read-only bus holding the ``mapper`` preset's regions."""
    bus = Bus(name)
    for mapping in MAPPERS[mapper]:
        map_on_bus(bus, mapping)
    bus.editable = False
    return bus
