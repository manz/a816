import struct
import typing
import warnings
from enum import Enum
from typing import Dict, List, Optional, Protocol, Union

if typing.TYPE_CHECKING:  # pragma: nocover
    from a816.parse.nodes import ValueNodeProtocol
    from a816.symbols import Resolver


class NoOpcodeForOperandSize(Exception):
    pass


class AddressingMode(Enum):
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


class OpcodeProtocol(Protocol):
    def emit(
        self, value_node: "Optional[ValueNodeProtocol]", resolver: "Resolver", size: Optional[str] = None
    ) -> bytes:
        """"""

    def supposed_length(self, value_node: "Optional[ValueNodeProtocol]", size: Optional[str] = None) -> int:
        """"""

    def get_opcode_byte(self, value_size: str) -> int:
        """"""

    def guess_value_size(self, value_node: "ValueNodeProtocol", size: Optional[str]) -> str:
        """"""


class BaseOpcode(OpcodeProtocol):
    def __init__(self, opcode: int) -> None:
        self.opcode = opcode

    def emit(
        self, value_node: "Optional[ValueNodeProtocol]", resolver: "Resolver", size: Optional[str] = None
    ) -> bytes:
        return struct.pack("B", self.opcode)

    def get_opcode_byte(self, value_size: str) -> int:
        return self.opcode

    def supposed_length(self, value_node: "Optional[ValueNodeProtocol]", size: Optional[str] = None) -> int:
        return 1

    def guess_value_size(self, value_node: "ValueNodeProtocol", size: Optional[str]) -> str:
        return ""


class RelativeJumpOpcode(BaseOpcode):
    def emit(
        self, value_node: "Optional[ValueNodeProtocol]", resolver: "Resolver", size: Optional[str] = None
    ) -> bytes:
        if value_node is None:
            raise RuntimeError("Nope.")
        value = value_node.get_value()
        from a816.parse.nodes import ExpressionNode

        if isinstance(value_node, ExpressionNode):
            pc = resolver.pc
            physical_destination = resolver.get_bus().get_address(value).physical

            if physical_destination is None:
                raise RuntimeError("Jumping from ram is not supported.")

            delta = physical_destination - pc
            delta -= 2
        else:
            delta = value
        try:
            return super(RelativeJumpOpcode, self).emit(value_node, resolver, size) + struct.pack("b", delta)
        except struct.error:
            print(value_node)
            raise

    def supposed_length(self, value_node: "Optional[ValueNodeProtocol]", size: Optional[str] = None) -> int:
        return 2


class Opcode(OpcodeProtocol):
    def __init__(self, opcode_def: List[Optional[int]], is_a: bool = False, is_x: bool = False):
        self.opcode_def = opcode_def
        self.is_a = is_a
        self.is_x = is_x
        self.size_opcode_map: Dict[str, int] = {"b": 0, "w": 1, "l": 2}

    def guess_value_size(self, value_node: "ValueNodeProtocol", size: Optional[str]) -> str:
        if value_node is None:
            raise RuntimeError("Nope.")
        if size:
            return size
        else:
            return value_node.get_operand_size()

    def emit_value(self, value_node: "ValueNodeProtocol", size: str) -> bytes:
        value = value_node.get_value()
        if size == "b":
            return struct.pack("B", value & 0xFF)
        elif size == "w":
            return struct.pack("<H", value & 0xFFFF)
        elif size == "l":
            return struct.pack("<HB", value & 0xFFFF, value >> 16)
        else:
            raise RuntimeError(f"Unknown Size {size}")

    def supposed_length(self, value_node: "Optional[ValueNodeProtocol]", size: Optional[str] = None) -> int:
        if value_node is None:
            raise RuntimeError("Nope.")
        value_size = self.guess_value_size(value_node, size)
        return 2 + self.size_opcode_map[value_size]

    def get_opcode_byte(self, value_size: str) -> int:
        try:
            opcode_byte = self.opcode_def[self.size_opcode_map[value_size]]
        except IndexError:
            raise NoOpcodeForOperandSize()
        else:
            if opcode_byte is None:
                raise NoOpcodeForOperandSize()
            return opcode_byte

    def emit(
        self, value_node: "Optional[ValueNodeProtocol]", resolver: "Resolver", size: Optional[str] = None
    ) -> bytes:
        if value_node is None:
            raise RuntimeError("Nope.")
        value_size = self.guess_value_size(value_node, size)
        opcode_byte = self.get_opcode_byte(value_size)

        operand_bytes = self.emit_value(value_node, value_size)
        node_bytes = struct.pack("B", opcode_byte) + operand_bytes
        return node_bytes


OpcodeDef = Union[OpcodeProtocol, Dict[str, OpcodeProtocol]]

snes_opcode_table: Dict[str, Dict[AddressingMode, OpcodeDef]] = {
    "nop": {AddressingMode.none: BaseOpcode(0xEA)},
    "rep": {AddressingMode.immediate: Opcode([0xC2])},
    "cpx": {AddressingMode.immediate: Opcode([0xE0, 0xE0], is_x=True), AddressingMode.direct: Opcode([0xE4, 0xEC])},
    "cpy": {AddressingMode.immediate: Opcode([0xC0, 0xC0], is_x=True), AddressingMode.direct: Opcode([0xC4, 0xCC])},
    "dec": {
        AddressingMode.none: BaseOpcode(0x3A),
        AddressingMode.direct: Opcode([0xC6, 0xCE]),
        AddressingMode.direct_indexed: {"x": Opcode([0xD6, 0xDE])},
    },
    "lda": {
        AddressingMode.immediate: Opcode([0xA9, 0xA9], is_a=True),
        AddressingMode.direct: Opcode([0xA5, 0xAD, 0xAF], is_a=True),
        AddressingMode.direct_indexed: {
            "x": Opcode([0xB5, 0xBD, 0xBF], is_a=True),
            "y": Opcode([None, 0xB9, None], is_a=True),
            "s": Opcode([0xA3]),
        },
        AddressingMode.indirect_indexed_long: {"y": Opcode([0xB7])},
        AddressingMode.indirect_indexed: {"y": Opcode([0xB1])},
        AddressingMode.indirect_long: Opcode([0xA7]),
        AddressingMode.indirect: Opcode([0xB2]),
        AddressingMode.dp_or_sr_indirect_indexed: {
            "x": Opcode([0xA1]),
        },
        AddressingMode.stack_indexed_indirect_indexed: {"y": Opcode([0xB3])},
    },
    "ora": {
        AddressingMode.immediate: Opcode([0x09, 0xA9], is_a=True),
        AddressingMode.direct: Opcode([0x05, 0x0D, 0x0F], is_a=True),
        AddressingMode.direct_indexed: {
            "x": Opcode([None, 0x1D, 0x1F], is_a=True),
            "y": Opcode([None, 0x19, None], is_a=True),
        },
        AddressingMode.indirect_indexed_long: {"y": Opcode([0x17])},
    },
    "eor": {
        AddressingMode.immediate: Opcode([0x49]),
        AddressingMode.direct: Opcode([0x45, 0x4D, 0x4F]),
        AddressingMode.direct_indexed: {
            "x": Opcode([None, 0x5D, 0x5F]),
            "y": Opcode([None, 0x59, None]),
            "s": Opcode([0x43, None, None]),
        },
        AddressingMode.indirect: Opcode([0x52]),
        AddressingMode.indirect_long: Opcode([0x47]),
        AddressingMode.indirect_indexed: {"y": Opcode([0x51])},
        AddressingMode.indirect_indexed_long: {"y": Opcode([0x57])},
        AddressingMode.dp_or_sr_indirect_indexed: {"x": Opcode([0x41])},
        AddressingMode.stack_indexed_indirect_indexed: {"y": Opcode([0x53])},
    },
    "ldx": {
        AddressingMode.immediate: Opcode([0xA2, 0xA2], is_x=True),
        AddressingMode.direct: Opcode([0xA6, 0xAE], is_x=True),
        AddressingMode.direct_indexed: {"y": Opcode([0xB6, 0xBE], is_x=True)},
    },
    "ldy": {
        AddressingMode.immediate: Opcode([0xA0, 0xA0], is_x=True),
        AddressingMode.direct: Opcode([0xA4, 0xAC], is_x=True),
        AddressingMode.direct_indexed: {"x": Opcode([0xB4, 0xBC], is_x=True)},
    },
    "lsr": {
        AddressingMode.none: BaseOpcode(0x4A),
        AddressingMode.direct: Opcode([0x46, 0x4E]),
        AddressingMode.direct_indexed: {"x": Opcode([0x56, 0x5E])},
    },
    "jsr": {AddressingMode.direct: Opcode([None, 0x20, 0x22])},
    "jmp": {
        AddressingMode.direct: Opcode([None, 0x4C, 0x5C]),
        AddressingMode.indirect: Opcode([None, 0x6C, None]),
        AddressingMode.indirect_long: Opcode([None, 0xDC, None]),
    },
    "inc": {
        AddressingMode.none: BaseOpcode(0x1A),
        AddressingMode.direct: Opcode([0xE6, 0xEE]),
        AddressingMode.direct_indexed: {"x": Opcode([0xF6, 0xFE])},
    },
    "inx": {AddressingMode.none: BaseOpcode(0xE8)},
    "iny": {AddressingMode.none: BaseOpcode(0xC8)},
    "dex": {AddressingMode.none: BaseOpcode(0xCA)},
    "dey": {AddressingMode.none: BaseOpcode(0x88)},
    "adc": {
        AddressingMode.immediate: Opcode([0x69, 0x69], is_a=True),
        AddressingMode.direct: Opcode([0x65, 0x6D, 0x6F]),
        AddressingMode.direct_indexed: {
            "x": Opcode([0x75, 0x7D, 0x7F]),
            "y": Opcode([None, 0x79, None]),
            "s": Opcode([0x63]),
        },
        AddressingMode.indirect: Opcode([0x72]),
        AddressingMode.indirect_indexed: {"y": Opcode([0x71])},
        AddressingMode.indirect_long: Opcode([0x67]),
        AddressingMode.indirect_indexed_long: {"y": Opcode([0x77])},
    },
    "and": {
        AddressingMode.immediate: Opcode([0x29, 0x29], is_a=True),
        AddressingMode.direct: Opcode([0x25, 0x2D, 0x2F]),
        AddressingMode.direct_indexed: {"x": Opcode([0x35, 0x3D, 0x3F]), "y": Opcode([None, 0x39, None])},
        AddressingMode.indirect: Opcode([0x32]),
        AddressingMode.indirect_indexed: {"y": Opcode([0x31])},
        AddressingMode.indirect_long: Opcode([0x27]),
        AddressingMode.indirect_indexed_long: {"y": Opcode([0x37])},
    },
    "asl": {
        AddressingMode.none: BaseOpcode(0x0A),
        AddressingMode.direct: Opcode([0x06, 0x0E]),
        AddressingMode.direct_indexed: {"x": Opcode([0x16, 0x1E])},
    },
    "bcc": {AddressingMode.direct: RelativeJumpOpcode(0x90)},
    "bcs": {AddressingMode.direct: RelativeJumpOpcode(0xB0)},
    "beq": {AddressingMode.direct: RelativeJumpOpcode(0xF0)},
    "bit": {
        AddressingMode.immediate: Opcode([0x89, 0x89], is_a=True),
        AddressingMode.direct: Opcode([0x24, 0x2C]),
        AddressingMode.direct_indexed: {"x": Opcode([0x34, 0x3C])},
    },
    "bmi": {AddressingMode.direct: RelativeJumpOpcode(0x30)},
    "bne": {AddressingMode.direct: RelativeJumpOpcode(0xD0)},
    "bpl": {AddressingMode.direct: RelativeJumpOpcode(0x10)},
    "bra": {AddressingMode.direct: RelativeJumpOpcode(0x80)},
    "brk": {AddressingMode.none: BaseOpcode(0x00)},
    "clc": {AddressingMode.none: BaseOpcode(0x18)},
    "cmp": {
        AddressingMode.immediate: Opcode([0xC9, 0xC9], is_a=True),
        AddressingMode.direct: Opcode([0xC5, 0xCD, 0xCF], is_a=True),
        AddressingMode.direct_indexed: {
            "x": Opcode([None, 0xDD, 0xDF], is_a=True),
            "y": Opcode([None, 0xD9, None], is_a=True),
        },
    },
    "pea": {AddressingMode.direct: Opcode([None, 0xF4])},
    "pei": {AddressingMode.indirect: Opcode([0xD4])},
    "pha": {AddressingMode.none: BaseOpcode(0x48)},
    "pla": {AddressingMode.none: BaseOpcode(0x68)},
    "phy": {AddressingMode.none: BaseOpcode(0x5A)},
    "ply": {AddressingMode.none: BaseOpcode(0x7A)},
    "phx": {AddressingMode.none: BaseOpcode(0xDA)},
    "plx": {AddressingMode.none: BaseOpcode(0xFA)},
    "php": {AddressingMode.none: BaseOpcode(0x08)},
    "plp": {AddressingMode.none: BaseOpcode(0x28)},
    "phb": {AddressingMode.none: BaseOpcode(0x8B)},
    "plb": {AddressingMode.none: BaseOpcode(0xAB)},
    "phd": {AddressingMode.none: BaseOpcode(0x0B)},
    "pld": {AddressingMode.none: BaseOpcode(0x2B)},
    "phk": {
        AddressingMode.none: BaseOpcode(0x4B),
    },
    "rol": {
        AddressingMode.none: BaseOpcode(0x2A),
        AddressingMode.direct: Opcode([0x26, 0x2E]),
        AddressingMode.direct_indexed: {"x": Opcode([0x36, 0x3E])},
    },
    "ror": {
        AddressingMode.none: BaseOpcode(0x6A),
        AddressingMode.direct: Opcode([0x66, 0x6E]),
        AddressingMode.direct_indexed: {"x": Opcode([0x76, 0x7E])},
    },
    "rti": {AddressingMode.none: BaseOpcode(0x40)},
    "rtl": {AddressingMode.none: BaseOpcode(0x6B)},
    "rts": {AddressingMode.none: BaseOpcode(0x60)},
    "sbc": {
        AddressingMode.immediate: Opcode([0xE9, 0xE9], is_a=True),
        AddressingMode.direct: Opcode([0xE5, 0xED, 0xEF]),
        AddressingMode.direct_indexed: {"x": Opcode([0xF5, 0xFD, 0xFF]), "y": Opcode([None, 0xF9])},
        AddressingMode.indirect: Opcode([0xF2]),
        AddressingMode.indirect_indexed: {"y": Opcode([0xF1])},
        AddressingMode.indirect_indexed_long: {"y": Opcode([0xF7])},
    },
    "sec": {AddressingMode.none: BaseOpcode(0x38)},
    "sed": {AddressingMode.none: BaseOpcode(0xF8)},
    "sei": {AddressingMode.none: BaseOpcode(0x78)},
    "sep": {AddressingMode.immediate: Opcode([0xE2])},
    "sta": {
        AddressingMode.direct: Opcode([0x85, 0x8D, 0x8F]),
        AddressingMode.indirect_long: Opcode([0x87]),
        AddressingMode.indirect: Opcode([0x92]),
        AddressingMode.indirect_indexed: {"y": Opcode([0x91])},
        AddressingMode.indirect_indexed_long: {"y": Opcode([0x97])},
        AddressingMode.direct_indexed: {
            "x": Opcode([0x95, 0x9D, 0x9F]),
            "y": Opcode([None, 0x99, None]),
            "s": Opcode([0x83]),
        },
    },
    "stx": {AddressingMode.direct: Opcode([0x86, 0x8E]), AddressingMode.direct_indexed: {"y": Opcode([0x96])}},
    "sty": {AddressingMode.direct: Opcode([0x84, 0x8C]), AddressingMode.direct_indexed: {"x": Opcode([0x94])}},
    "stz": {AddressingMode.direct: Opcode([0x64, 0x9C]), AddressingMode.direct_indexed: {"x": Opcode([0x74, 0x9E])}},
    "stp": {AddressingMode.none: BaseOpcode(0xDB)},
    "tax": {AddressingMode.none: BaseOpcode(0xAA)},
    "tay": {AddressingMode.none: BaseOpcode(0xA8)},
    "tcd": {AddressingMode.none: BaseOpcode(0x5B)},
    "tcs": {AddressingMode.none: BaseOpcode(0x1B)},
    "tdc": {AddressingMode.none: BaseOpcode(0x7B)},
    "trb": {AddressingMode.direct: Opcode([0x14, 0x1C])},
    "tsb": {AddressingMode.direct: Opcode([0x04, 0x0C])},
    "tsc": {AddressingMode.none: BaseOpcode(0x3B)},
    "tsx": {AddressingMode.none: BaseOpcode(0xBA)},
    "txa": {AddressingMode.none: BaseOpcode(0x8A)},
    "txs": {AddressingMode.none: BaseOpcode(0x9A)},
    "txy": {AddressingMode.none: BaseOpcode(0x9B)},
    "tya": {AddressingMode.none: BaseOpcode(0x98)},
    "tyx": {AddressingMode.none: BaseOpcode(0xBB)},
    "wai": {AddressingMode.none: BaseOpcode(0xCB)},
    "xba": {AddressingMode.none: BaseOpcode(0xEB)},
    "xce": {AddressingMode.none: BaseOpcode(0xFB)},
}


class RomType(Enum):
    low_rom = 0
    low_rom_2 = 1
    high_rom = 2


def rom_to_snes(address: int, mode: RomType) -> int:
    warnings.warn("Kept for compatibility, see Address class for more information.", DeprecationWarning)
    if mode == RomType.low_rom:
        bank = int(address / 0x8000)
        remainder = (address % 0x8000) + 0x8000
        snes_address = bank << 16 | remainder
    elif mode == RomType.low_rom_2:
        bank = int(address / 0x8000)
        bank += 0x80
        remainder = (address % 0x8000) + 0x8000
        snes_address = bank << 16 | remainder
    else:
        snes_address = address + 0xC00000

    return snes_address


def snes_to_rom(address: int) -> int:
    """Legacy mapping"""
    warnings.warn("Kept for compatibility, see Address class for more information.", DeprecationWarning)

    if address >= 0xC00000:
        rom_address = address - 0xC00000
    elif address >= 0x808000:
        bank = address >> 16
        bank -= 0x80
        rom_address = bank * 0x8000 + (address & 0x7FFF)
    else:
        bank = address >> 16
        rom_address = bank * 0x8000 + (address & 0x7FFF)

    return rom_address


def get_opcodes_with_addressing(addressing_mode: AddressingMode) -> List[str]:
    def filter_func(k: str) -> bool:
        keys = snes_opcode_table[k].keys()
        return addressing_mode in keys

    return list(filter(filter_func, snes_opcode_table.keys()))
