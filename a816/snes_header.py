"""The SNES header checksum, which only the patched ROM can give.

The cartridge header sits at bus `$00:FFC0`; its checksum complement and
checksum (little-endian words at `$00:FFDC` and `$00:FFDE`) hold the 16-bit
sum of every ROM byte. A patch built without the base ROM cannot know it,
so sealing, which has the base, writes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from a816.config import A816Config, discover_a816_config
from a816.cpu.mapping import Bus
from a816.exceptions import UnmappedBankError
from a816.mappers import map_on_bus

HEADER_BUS_ADDRESS = 0x00FFC0
CHECKSUM_FIELDS = 0x1C  # complement, then checksum, from the header start
# Complement + checksum always add up to 0x1FE; these stand in while summing.
_NEUTRAL_FIELDS = b"\xff\xff\x00\x00"


@dataclass(frozen=True)
class Cartridge:
    """What sealing needs from `a816.toml`: the ROM size and where the checksum fields are."""

    rom_size: int
    checksum_offset: int

    @classmethod
    def of(cls, config: A816Config | None) -> Cartridge | None:
        """The project's cartridge, or None when it declares no ROM (no `board` / `[map.N]`)."""
        if config is None:
            return None
        rom_size = max((m.rom_size for m in config.bus_map if not m.writeable), default=0)
        if not rom_size:
            return None
        bus = Bus("seal")
        for mapping in config.bus_map:
            map_on_bus(bus, mapping)
        try:
            header = bus.get_address(HEADER_BUS_ADDRESS).physical
        except UnmappedBankError:
            return None
        if header is None:
            return None
        return cls(rom_size, header + CHECKSUM_FIELDS)


def cartridge_at(start: Path) -> Cartridge | None:
    return Cartridge.of(discover_a816_config(start))


def _sum_mirrored(data: bytes) -> tuple[int, int]:
    """The byte sum of `data` mirrored up to a power of two, and that length.

    A ROM that is not a power of two long (3 MB, 6 MB) repeats its last
    part on the bus; the checksum counts it as many times.
    """
    size = len(data)
    if size & (size - 1) == 0:
        return sum(data), size
    half = 1 << (size.bit_length() - 1)
    rest, rest_size = _sum_mirrored(data[half:])
    return sum(data[:half]) + rest * (half // rest_size), half * 2


def checksum(rom: bytes, cartridge: Cartridge) -> int:
    """The header checksum of `rom` padded to `rom_size`, whatever its checksum fields hold."""
    padded = bytearray(rom.ljust(cartridge.rom_size, b"\0"))
    at = cartridge.checksum_offset
    padded[at : at + 4] = _NEUTRAL_FIELDS
    return _sum_mirrored(bytes(padded))[0] & 0xFFFF


def checksum_fields(rom: bytes, cartridge: Cartridge) -> bytes:
    """The four bytes at `$00:FFDC`: complement, then checksum."""
    total = checksum(rom, cartridge)
    return (total ^ 0xFFFF).to_bytes(2, "little") + total.to_bytes(2, "little")


def with_checksum(rom: bytes, cartridge: Cartridge) -> bytes:
    """`rom` padded to `rom_size`, its checksum fields written."""
    fixed = bytearray(rom.ljust(cartridge.rom_size, b"\0"))
    at = cartridge.checksum_offset
    fixed[at : at + 4] = checksum_fields(rom, cartridge)
    return bytes(fixed)
