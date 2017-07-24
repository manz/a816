import struct
from a816.cpu.cpu_65c816 import rom_to_snes, RomType


def base_relative_16bits_pointer_formula(base):
    return lambda v: int(v[0]) + int(v[1] << 8) + base


def long_low_rom_pointer(base):
    def inner_func(pointer):
        snes_address = rom_to_snes(pointer + base, RomType.low_rom)
        return struct.pack('<HB', snes_address & 0xFFFF, snes_address >> 16)

    return inner_func
