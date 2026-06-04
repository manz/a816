import struct
import typing
import warnings

from a816.cpu.types import AddressingMode, RomType, ValueSize
from a816.exceptions import MissingOperandError
from a816.protocols import OpcodeProtocol, ValueNodeProtocol

if typing.TYPE_CHECKING:  # pragma: nocover
    from a816.symbols import Resolver

# Re-export types for backward compatibility
__all__ = [
    "AddressingMode",
    "RomType",
    "ValueSize",
    "OpcodeProtocol",
    "NoOpcodeForOperandSize",
    "snes_opcode_table",
    "rom_to_snes",
    "snes_to_rom",
    "get_opcodes_with_addressing",
    "guess_value_size",
]


class NoOpcodeForOperandSize(Exception):
    """Raised when an opcode doesn't support the requested operand size.

    This is an internal exception that gets caught and converted to a
    more informative OperandSizeError with opcode context.
    """

    pass


class OpcodeWithoutOperand(OpcodeProtocol):
    def __init__(self, opcode: int) -> None:
        self.opcode = opcode

    def emit(
        self,
        value_node: "ValueNodeProtocol | None",
        resolver: "Resolver",
        size: ValueSize | None = None,
    ) -> bytes:
        return struct.pack("B", self.opcode)

    def supposed_length(
        self,
        value_node: "ValueNodeProtocol | None",
        size: ValueSize | None = None,
        resolver: "Resolver | None" = None,
    ) -> int:
        return 1


class RelativeJumpOpcode(OpcodeWithoutOperand):
    # Offset width of the PC-relative displacement. 1 byte for the 8-bit
    # branches (`bra`, `bcc`, …); `RelativeLongJumpOpcode` overrides to 2
    # for `brl`. The displacement is measured from the instruction *after*
    # the branch, i.e. `dest - pc - (1 + OFFSET_BYTES)`.
    OFFSET_BYTES = 1
    _PACK = "b"
    _RANGE = "signed 8-bit range (-128 to 127)"

    def _relative_delta(self, value_node: "ValueNodeProtocol", resolver: "Resolver") -> int:
        value = value_node.get_value()
        # Use duck typing: ExpressionNode has 'expression' attribute, ValueNode does not
        if hasattr(value_node, "expression"):
            physical_destination = resolver.get_bus().get_address(value).physical
            if physical_destination is None:
                raise RuntimeError(
                    f"Cannot compute relative jump: target address {value:#x} "
                    "has no physical mapping (RAM addresses not supported)"
                )
            return physical_destination - resolver.pc - (1 + self.OFFSET_BYTES)
        return value

    def emit(
        self,
        value_node: "ValueNodeProtocol | None",
        resolver: "Resolver",
        size: ValueSize | None = None,
    ) -> bytes:
        if value_node is None:
            raise MissingOperandError("branch")
        delta = self._relative_delta(value_node, resolver)
        try:
            return super().emit(value_node, resolver, size) + struct.pack(self._PACK, delta)
        except struct.error as e:
            raise RuntimeError(f"Branch target out of range: offset {delta} exceeds {self._RANGE}") from e

    def supposed_length(
        self,
        value_node: "ValueNodeProtocol | None",
        size: ValueSize | None = None,
        resolver: "Resolver | None" = None,
    ) -> int:
        return 1 + self.OFFSET_BYTES


class RelativeLongJumpOpcode(RelativeJumpOpcode):
    """`brl` ($82): unconditional PC-relative branch with a 16-bit signed
    displacement (±32 KB). The link-invariant long-jump primitive: unlike
    `jmp.w`, it stays relative and never consults the symbol table."""

    OFFSET_BYTES = 2
    _PACK = "<h"
    _RANGE = "signed 16-bit range (-32768 to 32767)"


def guess_value_size(
    value_node: "ValueNodeProtocol",
    size: ValueSize | None,
    resolver: "Resolver | None" = None,
    is_a: bool = False,
    is_x: bool = False,
) -> ValueSize:
    if value_node is None:
        raise MissingOperandError("opcode")
    if size:
        return size

    # Use resolver state for A/X register operations when no explicit size
    if resolver is not None:
        if is_a and resolver.a_size == 16:
            return "w"
        if is_x and resolver.i_size == 16:
            return "w"

    return value_node.get_operand_size()


class Opcode(OpcodeProtocol):
    def __init__(self, opcode_def: list[int | None], is_a: bool = False, is_x: bool = False, alias: bool = False):
        self.opcode_def = opcode_def
        self.is_a = is_a
        self.is_x = is_x
        # `alias=True` marks an encode-only convenience entry (e.g. `jsl` ≡
        # `jsr.l`). The disassembler's derived byte-to-instruction map skips
        # these so each opcode byte decodes to exactly one mnemonic.
        self.alias = alias
        self.size_opcode_map: dict[str, int] = {"b": 0, "w": 1, "l": 2}

    def emit_value(self, value_node: "ValueNodeProtocol", size: ValueSize) -> bytes:
        value = value_node.get_value()

        # Check if this is an ExpressionNode with a deferred expression from external symbols
        # Use duck typing: check for resolver attribute (ExpressionNode has it, ValueNode doesn't)
        if hasattr(value_node, "resolver") and hasattr(value_node, "_deferred_expression"):
            # Generate expression relocation instead of regular relocation
            resolver = value_node.resolver
            if resolver.context.is_object_mode:
                # Determine size in bytes
                if size == "b":
                    size_bytes = 1
                elif size == "w":
                    size_bytes = 2
                else:
                    size_bytes = 3
                # Operand lands one byte past the opcode in the current section.
                writer = resolver.context.object_writer
                if writer is not None:
                    current_offset = writer.relocation_offset(pending_block_bytes=1)
                    writer.add_expression_relocation(current_offset, value_node._deferred_expression, size_bytes)

        # Address-form operands keep their masks: SNES code routinely
        # writes `jsr.w label` where `label` is a full 24-bit address
        # but only the low 16 bits matter (PB stays in the current
        # bank). Stripping the bank byte here is the standard idiom,
        # not a footgun.
        #
        # Immediate-form overflow IS a footgun, so it's caught in
        # `OpcodeNode.emit` (immediate addressing only) before this
        # method runs.
        if size == "b":
            return struct.pack("B", value & 0xFF)
        elif size == "w":
            return struct.pack("<H", value & 0xFFFF)
        elif size == "l":
            return struct.pack("<HB", value & 0xFFFF, value >> 16)
        return b""

    def supposed_length(
        self,
        value_node: "ValueNodeProtocol | None",
        size: ValueSize | None = None,
        resolver: "Resolver | None" = None,
    ) -> int:
        if value_node is None:
            raise MissingOperandError(f"opcode (def: {self.opcode_def})")

        value_size = guess_value_size(value_node, size, resolver, self.is_a, self.is_x)
        return 2 + self.size_opcode_map[value_size]

    def get_opcode_byte(self, value_size: str) -> int:
        try:
            opcode_byte = self.opcode_def[self.size_opcode_map[value_size]]
        except IndexError as e:
            raise NoOpcodeForOperandSize() from e
        else:
            if opcode_byte is None:
                raise NoOpcodeForOperandSize()
            return opcode_byte

    def emit(
        self,
        value_node: "ValueNodeProtocol | None",
        resolver: "Resolver",
        size: ValueSize | None = None,
    ) -> bytes:
        if value_node is None:
            raise MissingOperandError(f"opcode (def: {self.opcode_def})")

        value_size = guess_value_size(value_node, size, resolver, self.is_a, self.is_x)
        opcode_byte = self.get_opcode_byte(value_size)

        operand_bytes = self.emit_value(value_node, value_size)
        node_bytes = struct.pack("B", opcode_byte) + operand_bytes
        return node_bytes


class LongOpcode(Opcode):
    """Always emits the 24-bit long form, regardless of inferred operand size.

    `jsl` / `jml` are inherently 3-byte-operand instructions (long call / long
    jump). Encoding them as a plain size-indexed `Opcode` would infer width from
    the target value, so a sub-bank target (`jsl $1234`) would raise
    `NoOpcodeForOperandSize`. Pinning the operand to `l` keeps them long.
    """

    def __init__(self, opcode: int, alias: bool = False) -> None:
        super().__init__([None, None, opcode], alias=alias)

    def emit(
        self,
        value_node: "ValueNodeProtocol | None",
        resolver: "Resolver",
        size: ValueSize | None = None,
    ) -> bytes:
        return super().emit(value_node, resolver, "l")

    def supposed_length(
        self,
        value_node: "ValueNodeProtocol | None",
        size: ValueSize | None = None,
        resolver: "Resolver | None" = None,
    ) -> int:
        return super().supposed_length(value_node, "l", resolver)


class BlockMoveOpcode(OpcodeProtocol):
    """`mvn` / `mvp` block move: two bank operands.

    Source order is `mvn srcbank, destbank`, but the encoded operand bytes are
    REVERSED: `opcode, destbank, srcbank`. Each operand contributes its low 8
    bits (the bank byte). Driven by `OpcodeNode` via `emit_block_move` because
    the generic single-operand `emit` path can't carry two value nodes.
    """

    def __init__(self, opcode: int) -> None:
        self.opcode = opcode

    def emit_block_move(
        self,
        src_node: "ValueNodeProtocol",
        dest_node: "ValueNodeProtocol",
        resolver: "Resolver",
    ) -> bytes:
        del resolver
        src = src_node.get_value() & 0xFF
        dest = dest_node.get_value() & 0xFF
        return struct.pack("BBB", self.opcode, dest, src)

    def emit(
        self,
        value_node: "ValueNodeProtocol | None",
        resolver: "Resolver",
        size: ValueSize | None = None,
    ) -> bytes:
        raise NoOpcodeForOperandSize()  # block move is routed via emit_block_move

    def supposed_length(
        self,
        value_node: "ValueNodeProtocol | None",
        size: ValueSize | None = None,
        resolver: "Resolver | None" = None,
    ) -> int:
        return 3


OpcodeDef = OpcodeProtocol | dict[str, OpcodeProtocol]

snes_opcode_table: dict[str, dict[AddressingMode, OpcodeDef]] = {
    "nop": {AddressingMode.none: OpcodeWithoutOperand(0xEA)},
    "rep": {AddressingMode.immediate: Opcode([0xC2])},
    "cpx": {
        AddressingMode.immediate: Opcode([0xE0, 0xE0], is_x=True),
        AddressingMode.direct: Opcode([0xE4, 0xEC]),
    },
    "cpy": {
        AddressingMode.immediate: Opcode([0xC0, 0xC0], is_x=True),
        AddressingMode.direct: Opcode([0xC4, 0xCC]),
    },
    "dec": {
        AddressingMode.none: OpcodeWithoutOperand(0x3A),
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
        AddressingMode.immediate: Opcode([0x09, 0x09], is_a=True),
        AddressingMode.direct: Opcode([0x05, 0x0D, 0x0F], is_a=True),
        AddressingMode.direct_indexed: {
            "x": Opcode([0x15, 0x1D, 0x1F], is_a=True),
            "y": Opcode([None, 0x19, None], is_a=True),
            "s": Opcode([0x03]),
        },
        AddressingMode.indirect: Opcode([0x12]),
        AddressingMode.indirect_long: Opcode([0x07]),
        AddressingMode.indirect_indexed: {"y": Opcode([0x11])},
        AddressingMode.indirect_indexed_long: {"y": Opcode([0x17])},
        AddressingMode.dp_or_sr_indirect_indexed: {"x": Opcode([0x01])},
        AddressingMode.stack_indexed_indirect_indexed: {"y": Opcode([0x13])},
    },
    "eor": {
        AddressingMode.immediate: Opcode([0x49, 0x49], is_a=True),
        AddressingMode.direct: Opcode([0x45, 0x4D, 0x4F]),
        AddressingMode.direct_indexed: {
            "x": Opcode([0x55, 0x5D, 0x5F]),
            "y": Opcode([None, 0x59, None]),
            "s": Opcode([0x43]),
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
        AddressingMode.none: OpcodeWithoutOperand(0x4A),
        AddressingMode.direct: Opcode([0x46, 0x4E]),
        AddressingMode.direct_indexed: {"x": Opcode([0x56, 0x5E])},
    },
    "jsr": {
        AddressingMode.direct: Opcode([None, 0x20, 0x22]),
        AddressingMode.dp_or_sr_indirect_indexed: Opcode([None, 0xFC]),
    },
    "jmp": {
        AddressingMode.direct: Opcode([None, 0x4C, 0x5C]),
        AddressingMode.indirect: Opcode([None, 0x6C, None]),
        AddressingMode.indirect_long: Opcode([None, 0xDC, None]),
        AddressingMode.dp_or_sr_indirect_indexed: Opcode([None, 0x7C, None]),
    },
    # jsl / jml: encode-only aliases for `jsr.l` (0x22) / `jmp.l` (0x5C).
    # alias=True keeps them out of the disassembler's derived table.
    "jsl": {AddressingMode.direct: LongOpcode(0x22, alias=True)},
    "jml": {AddressingMode.direct: LongOpcode(0x5C, alias=True)},
    "inc": {
        AddressingMode.none: OpcodeWithoutOperand(0x1A),
        AddressingMode.direct: Opcode([0xE6, 0xEE]),
        AddressingMode.direct_indexed: {"x": Opcode([0xF6, 0xFE])},
    },
    "inx": {AddressingMode.none: OpcodeWithoutOperand(0xE8)},
    "iny": {AddressingMode.none: OpcodeWithoutOperand(0xC8)},
    "dex": {AddressingMode.none: OpcodeWithoutOperand(0xCA)},
    "dey": {AddressingMode.none: OpcodeWithoutOperand(0x88)},
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
        AddressingMode.dp_or_sr_indirect_indexed: {"x": Opcode([0x61])},
        AddressingMode.stack_indexed_indirect_indexed: {"y": Opcode([0x73])},
    },
    "and": {
        AddressingMode.immediate: Opcode([0x29, 0x29], is_a=True),
        AddressingMode.direct: Opcode([0x25, 0x2D, 0x2F]),
        AddressingMode.direct_indexed: {
            "x": Opcode([0x35, 0x3D, 0x3F]),
            "y": Opcode([None, 0x39, None]),
            "s": Opcode([0x23]),
        },
        AddressingMode.indirect: Opcode([0x32]),
        AddressingMode.indirect_indexed: {"y": Opcode([0x31])},
        AddressingMode.indirect_long: Opcode([0x27]),
        AddressingMode.indirect_indexed_long: {"y": Opcode([0x37])},
        AddressingMode.dp_or_sr_indirect_indexed: {"x": Opcode([0x21])},
        AddressingMode.stack_indexed_indirect_indexed: {"y": Opcode([0x33])},
    },
    "asl": {
        AddressingMode.none: OpcodeWithoutOperand(0x0A),
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
    "brl": {AddressingMode.direct: RelativeLongJumpOpcode(0x82)},
    "bvc": {AddressingMode.direct: RelativeJumpOpcode(0x50)},
    "bvs": {AddressingMode.direct: RelativeJumpOpcode(0x70)},
    # BRK / COP / WDM are 2-byte instructions on 65816: opcode + a
    # signature byte the handler reads off the stack (PC pushed is the
    # instruction + 2). Force callers to supply the signature so the
    # second byte is never silently whatever happens to follow in ROM.
    "brk": {AddressingMode.immediate: Opcode([0x00])},
    "cop": {AddressingMode.immediate: Opcode([0x02])},
    "wdm": {AddressingMode.immediate: Opcode([0x42])},
    "mvn": {AddressingMode.block_move: BlockMoveOpcode(0x54)},
    "mvp": {AddressingMode.block_move: BlockMoveOpcode(0x44)},
    "clc": {AddressingMode.none: OpcodeWithoutOperand(0x18)},
    "cld": {AddressingMode.none: OpcodeWithoutOperand(0xD8)},
    "cli": {AddressingMode.none: OpcodeWithoutOperand(0x58)},
    "clv": {AddressingMode.none: OpcodeWithoutOperand(0xB8)},
    "cmp": {
        AddressingMode.immediate: Opcode([0xC9, 0xC9], is_a=True),
        AddressingMode.direct: Opcode([0xC5, 0xCD, 0xCF], is_a=True),
        AddressingMode.direct_indexed: {
            "x": Opcode([0xD5, 0xDD, 0xDF], is_a=True),
            "y": Opcode([None, 0xD9, None], is_a=True),
            "s": Opcode([0xC3]),
        },
        AddressingMode.indirect: Opcode([0xD2]),
        AddressingMode.indirect_long: Opcode([0xC7]),
        AddressingMode.indirect_indexed: {"y": Opcode([0xD1])},
        AddressingMode.indirect_indexed_long: {"y": Opcode([0xD7])},
        AddressingMode.dp_or_sr_indirect_indexed: {"x": Opcode([0xC1])},
        AddressingMode.stack_indexed_indirect_indexed: {"y": Opcode([0xD3])},
    },
    "pea": {AddressingMode.direct: Opcode([None, 0xF4])},
    "pei": {AddressingMode.indirect: Opcode([0xD4])},
    "per": {AddressingMode.direct: RelativeLongJumpOpcode(0x62)},
    "pha": {AddressingMode.none: OpcodeWithoutOperand(0x48)},
    "pla": {AddressingMode.none: OpcodeWithoutOperand(0x68)},
    "phy": {AddressingMode.none: OpcodeWithoutOperand(0x5A)},
    "ply": {AddressingMode.none: OpcodeWithoutOperand(0x7A)},
    "phx": {AddressingMode.none: OpcodeWithoutOperand(0xDA)},
    "plx": {AddressingMode.none: OpcodeWithoutOperand(0xFA)},
    "php": {AddressingMode.none: OpcodeWithoutOperand(0x08)},
    "plp": {AddressingMode.none: OpcodeWithoutOperand(0x28)},
    "phb": {AddressingMode.none: OpcodeWithoutOperand(0x8B)},
    "plb": {AddressingMode.none: OpcodeWithoutOperand(0xAB)},
    "phd": {AddressingMode.none: OpcodeWithoutOperand(0x0B)},
    "pld": {AddressingMode.none: OpcodeWithoutOperand(0x2B)},
    "phk": {
        AddressingMode.none: OpcodeWithoutOperand(0x4B),
    },
    "rol": {
        AddressingMode.none: OpcodeWithoutOperand(0x2A),
        AddressingMode.direct: Opcode([0x26, 0x2E]),
        AddressingMode.direct_indexed: {"x": Opcode([0x36, 0x3E])},
    },
    "ror": {
        AddressingMode.none: OpcodeWithoutOperand(0x6A),
        AddressingMode.direct: Opcode([0x66, 0x6E]),
        AddressingMode.direct_indexed: {"x": Opcode([0x76, 0x7E])},
    },
    "rti": {AddressingMode.none: OpcodeWithoutOperand(0x40)},
    "rtl": {AddressingMode.none: OpcodeWithoutOperand(0x6B)},
    "rts": {AddressingMode.none: OpcodeWithoutOperand(0x60)},
    "sbc": {
        AddressingMode.immediate: Opcode([0xE9, 0xE9], is_a=True),
        AddressingMode.direct: Opcode([0xE5, 0xED, 0xEF]),
        AddressingMode.direct_indexed: {
            "x": Opcode([0xF5, 0xFD, 0xFF]),
            "y": Opcode([None, 0xF9]),
            "s": Opcode([0xE3]),
        },
        AddressingMode.indirect: Opcode([0xF2]),
        AddressingMode.indirect_long: Opcode([0xE7]),
        AddressingMode.indirect_indexed: {"y": Opcode([0xF1])},
        AddressingMode.indirect_indexed_long: {"y": Opcode([0xF7])},
        AddressingMode.dp_or_sr_indirect_indexed: {"x": Opcode([0xE1])},
        AddressingMode.stack_indexed_indirect_indexed: {"y": Opcode([0xF3])},
    },
    "sec": {AddressingMode.none: OpcodeWithoutOperand(0x38)},
    "sed": {AddressingMode.none: OpcodeWithoutOperand(0xF8)},
    "sei": {AddressingMode.none: OpcodeWithoutOperand(0x78)},
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
        AddressingMode.dp_or_sr_indirect_indexed: {"x": Opcode([0x81])},
        AddressingMode.stack_indexed_indirect_indexed: {"y": Opcode([0x93])},
    },
    "stx": {
        AddressingMode.direct: Opcode([0x86, 0x8E]),
        AddressingMode.direct_indexed: {"y": Opcode([0x96])},
    },
    "sty": {
        AddressingMode.direct: Opcode([0x84, 0x8C]),
        AddressingMode.direct_indexed: {"x": Opcode([0x94])},
    },
    "stz": {
        AddressingMode.direct: Opcode([0x64, 0x9C]),
        AddressingMode.direct_indexed: {"x": Opcode([0x74, 0x9E])},
    },
    "stp": {AddressingMode.none: OpcodeWithoutOperand(0xDB)},
    "tax": {AddressingMode.none: OpcodeWithoutOperand(0xAA)},
    "tay": {AddressingMode.none: OpcodeWithoutOperand(0xA8)},
    "tcd": {AddressingMode.none: OpcodeWithoutOperand(0x5B)},
    "tcs": {AddressingMode.none: OpcodeWithoutOperand(0x1B)},
    "tdc": {AddressingMode.none: OpcodeWithoutOperand(0x7B)},
    "trb": {AddressingMode.direct: Opcode([0x14, 0x1C])},
    "tsb": {AddressingMode.direct: Opcode([0x04, 0x0C])},
    "tsc": {AddressingMode.none: OpcodeWithoutOperand(0x3B)},
    "tsx": {AddressingMode.none: OpcodeWithoutOperand(0xBA)},
    "txa": {AddressingMode.none: OpcodeWithoutOperand(0x8A)},
    "txs": {AddressingMode.none: OpcodeWithoutOperand(0x9A)},
    "txy": {AddressingMode.none: OpcodeWithoutOperand(0x9B)},
    "tya": {AddressingMode.none: OpcodeWithoutOperand(0x98)},
    "tyx": {AddressingMode.none: OpcodeWithoutOperand(0xBB)},
    "wai": {AddressingMode.none: OpcodeWithoutOperand(0xCB)},
    "xba": {AddressingMode.none: OpcodeWithoutOperand(0xEB)},
    "xce": {AddressingMode.none: OpcodeWithoutOperand(0xFB)},
}


def rom_to_snes(address: int, mode: RomType) -> int:
    warnings.warn("Kept for compatibility, see Address class for more information.", DeprecationWarning, stacklevel=2)
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
    warnings.warn("Kept for compatibility, see Address class for more information.", DeprecationWarning, stacklevel=2)

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


def get_opcodes_with_addressing(addressing_mode: AddressingMode) -> list[str]:
    def filter_func(k: str) -> bool:
        keys = snes_opcode_table[k].keys()
        return addressing_mode in keys

    return list(filter(filter_func, snes_opcode_table.keys()))
