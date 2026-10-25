"""SpcOpcodeNode: emit one assembled SPC700 instruction."""

from __future__ import annotations

from dataclasses import dataclass

from a816.cpu.mapping import Address
from a816.cpu.spc700 import Encoding, candidates, choose, encode_operand, opcode_byte
from a816.error_codes import (
    E_CODEGEN_BAD_ADDRESSING_MODE,
    E_CODEGEN_BRANCH_RANGE,
    E_CODEGEN_UNDECIDABLE_SIZE,
    E_SYMBOL_NOT_A_VALUE,
)
from a816.exceptions import SymbolNotDefined
from a816.parse.nodes.errors import NodeError, undefined_symbol_error
from a816.parse.nodes.expr import ExpressionNode
from a816.parse.tokens import Token
from a816.protocols import NodeBase
from a816.symbols import Resolver


@dataclass(frozen=True)
class SpcOperandNode:
    shape: str
    value: ExpressionNode | None = None
    bit: ExpressionNode | None = None


class SpcOpcodeNode(NodeBase):
    def __init__(
        self, opcode: str, operands: tuple[SpcOperandNode, ...], *, file_info: Token, resolver: Resolver
    ) -> None:
        self.opcode = opcode
        self.operands = operands
        self.file_info = file_info
        self.resolver = resolver
        self._found = candidates(opcode, tuple(operand.shape for operand in operands))
        if not self._found:
            raise NodeError(
                f"no SPC700 encoding for `{self._source()}`",
                file_info,
                code=str(E_CODEGEN_BAD_ADDRESSING_MODE),
            )
        # Length picked on a label pass from a value not placed yet.
        self._provisional_length: int | None = None

    def _source(self) -> str:
        return " ".join((self.opcode, ", ".join(operand.shape for operand in self.operands)))

    def _evaluate(self, node: ExpressionNode | None) -> int | None:
        if node is None:
            return None
        try:
            value = node.get_value()
        except SymbolNotDefined as undefined:
            raise undefined_symbol_error(undefined, self.file_info, self.resolver.current_scope) from undefined
        if not isinstance(value, int):
            raise NodeError(f"`{self.opcode}` operand is not a number", self.file_info, code=str(E_SYMBOL_NOT_A_VALUE))
        return value

    def _values(self) -> tuple[tuple[int | None, ...], tuple[int | None, ...]]:
        values = tuple(self._evaluate(operand.value) for operand in self.operands)
        bits = tuple(self._evaluate(operand.bit) for operand in self.operands)
        return values, bits

    def _encoding(self, values: tuple[int | None, ...]) -> Encoding:
        encoding = choose(self._found, values)
        assert encoding is not None
        return encoding

    def pc_after(self, current_pc: Address) -> Address:
        resolver = self.resolver
        resolver.provisional_label_value = current_pc.logical_value
        resolver.provisional_label_used = False
        try:
            values, _ = self._values()
        finally:
            resolver.provisional_label_value = None
        length = self._encoding(values).length
        if resolver.provisional_label_used and self._provisional_length is None:
            self._provisional_length = length
        return current_pc + length

    def emit(self, current_pc: Address) -> bytes:
        values, bits = self._values()
        encoding = self._encoding(values)
        if self._provisional_length not in (None, encoding.length):
            raise NodeError(
                f"`{self.opcode}` was sized before its operand was placed, and the operand changed its size",
                self.file_info,
                code=str(E_CODEGEN_UNDECIDABLE_SIZE),
                hint="write `!` before the operand to force the absolute form",
            )
        pc_after = current_pc.logical_value + encoding.length
        emitted = bytearray([opcode_byte(encoding, bits, values)])
        for index in encoding.byte_order():
            value = values[index]
            assert value is not None
            pattern = encoding.patterns[index]
            if pattern == "rel" and not -0x80 <= value - pc_after <= 0x7F:
                raise NodeError(
                    f"branch target is {value - pc_after} bytes away (-128..127)",
                    self.file_info,
                    code=str(E_CODEGEN_BRANCH_RANGE),
                    hint="branch over a `jmp` to reach a distant target",
                )
            emitted += encode_operand(pattern, value, bits[index], pc_after)
        return bytes(emitted)

    def __str__(self) -> str:
        return f"SpcOpcodeNode({self._source()})"
