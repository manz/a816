"""`SpcOpcodeAstNode`: one SPC700 instruction and its operand shapes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from a816.parse.ast.nodes.base import AstNode, ExpressionAstNode
from a816.parse.tokens import Token

# How each shape is written, `{}` standing for the expression.
_SHAPE_SYNTAX = {
    "imm": "#{}",
    "mem": "{}",
    "mem+x": "{}+x",
    "mem+y": "{}+y",
    "abs": "!{}",
    "abs+x": "!{}+x",
    "abs+y": "!{}+y",
    "[dp+x]": "[{}+x]",
    "[dp]+y": "[{}]+y",
    "[abs+x]": "[!{}+x]",
}


@dataclass(frozen=True)
class SpcOperand:
    """`shape` is a register (`a`, `ya`, `(x)+`) or a memory form (`mem`, `abs+x`, `mem.bit`)."""

    shape: str
    expression: ExpressionAstNode | None = None
    bit: ExpressionAstNode | None = None

    def to_canonical(self) -> str:
        if self.expression is None:
            return self.shape
        text = self.expression.to_canonical()
        if self.shape in ("mem.bit", "/mem.bit"):
            assert self.bit is not None
            prefix = "/" if self.shape == "/mem.bit" else ""
            return f"{prefix}{text}.{self.bit.to_canonical()}"
        return _SHAPE_SYNTAX[self.shape].format(text)


class SpcOpcodeAstNode(AstNode):
    def __init__(self, *, opcode: str, operands: tuple[SpcOperand, ...], file_info: Token) -> None:
        super().__init__("spc_opcode", file_info)
        self.opcode: Final = opcode
        self.operands: Final = operands

    def to_representation(self) -> tuple[Any, ...]:
        return (self.kind, self.opcode, tuple(operand.to_canonical() for operand in self.operands))

    def to_canonical(self) -> str:
        if not self.operands:
            return self.opcode
        return f"{self.opcode} {', '.join(operand.to_canonical() for operand in self.operands)}"
