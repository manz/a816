"""CPU type definitions for 65c816 assembler.

This module contains enums and type aliases used throughout the assembler.
Extracting these to a separate module eliminates circular dependencies
between cpu_65c816.py, symbols.py, and nodes.py.
"""

from enum import Enum
from typing import Literal


class RomType(Enum):
    """ROM memory mapping type for SNES cartridges."""

    low_rom = 0
    low_rom_2 = 1
    high_rom = 2


class AddressingMode(Enum):
    """65c816 CPU addressing modes."""

    none = 0
    immediate = 1
    direct = 2
    direct_indexed = 3
    indirect = 4
    indirect_indexed = 5
    indirect_long = 6
    indirect_indexed_long = 7
    dp_or_sr_indirect_indexed = 8
    stack_indexed_indirect_indexed = 9


# Type alias for operand size hints
ValueSize = Literal["b", "w", "l", ""]
