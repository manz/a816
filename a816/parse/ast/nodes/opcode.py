"""`OpcodeAstNode` + addressing-mode index map."""

from __future__ import annotations

import warnings
from typing import Any

from a816.cpu.cpu_65c816 import AddressingMode, ValueSize
from a816.parse.ast.nodes.base import AstNode, ExpressionAstNode
from a816.parse.tokens import Token


class OpcodeAstNode(AstNode):
    def __init__(
        self,
        *,
        addressing_mode: AddressingMode,
        opcode: str,
        value_size: ValueSize | None,
        operand: ExpressionAstNode | None,
        index: str | None,
        file_info: Token,
        operand2: ExpressionAstNode | None = None,
    ):
        super().__init__("opcode", file_info)
        self.addressing_mode = addressing_mode
        self.opcode = opcode
        self.value_size = value_size
        self.operand = operand
        # Second operand for block-move (`mvn src, dst`); None otherwise.
        self.operand2 = operand2
        self.index = index

    @property
    def opcode_value(self) -> tuple[str, ValueSize] | str:
        warnings.warn(
            "Use opcode and value_size fields instead of opcode_value composite field.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._repr_opcode_value

    @property
    def _repr_opcode_value(self) -> tuple[str, ValueSize] | str:
        if self.value_size:
            return self.opcode, self.value_size
        else:
            return self.opcode

    def to_representation(self) -> tuple[Any, ...]:
        return (
            self.kind,
            self.addressing_mode,
            self._repr_opcode_value,
            self.operand.to_representation()[0] if self.operand else None,
            self.index,
        )

    def to_canonical(self) -> str:
        # Build opcode with size specifier
        result = self.opcode
        if self.value_size:
            result += f".{self.value_size}"

        # Add operand if present
        if self.operand:
            operand_str = self.operand.to_canonical()
            if self.index:
                operand_str += f",{self.index}"
            result += f" {operand_str}"

        return result


index_map = {
    AddressingMode.indirect: AddressingMode.indirect_indexed,
    AddressingMode.indirect_long: AddressingMode.indirect_indexed_long,
    AddressingMode.direct: AddressingMode.direct_indexed,
    AddressingMode.dp_or_sr_indirect_indexed: AddressingMode.stack_indexed_indirect_indexed,
}
