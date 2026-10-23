"""Deprecated: see `katsuji.script.formulas`, whose `long_pointer(base)` takes a bus address in place of
`long_low_rom_pointer`'s LoROM offset."""

import struct
from collections.abc import Callable

from katsuji.script.formulas import base_relative_16bits_pointer_formula

from a816.cpu.cpu_65c816 import RomType, rom_to_snes

__all__ = ["base_relative_16bits_pointer_formula", "long_low_rom_pointer"]


def long_low_rom_pointer(base: int) -> Callable[[int], bytes]:
    def inner_func(pointer: int) -> bytes:
        snes_address = rom_to_snes(pointer + base, RomType.low_rom)
        return struct.pack("<HB", snes_address & 0xFFFF, snes_address >> 16)

    return inner_func
