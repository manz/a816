"""Link-time expressions as data: the RPN of an operand, a `.assert` or an
alias, stored in the object file and evaluated by the linker.

An expression is a flat list of `ExprOp` records in evaluation order, so the
object codec stores it as columns and the linker evaluates it without a
scanner, a parser or a regex over names. A name is a `SYMBOL` node carrying
its binding: `LOCAL` resolves in the object that wrote it, `GLOBAL` in the
merged symbol map. Operators share their semantics with codegen
(`unary_value` / `binary_value`), so compile time and link time agree.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from enum import Enum


class ExprOpCode(Enum):
    CONST = 0
    SYMBOL = 1
    STRING = 2
    NEG = 10
    NOT = 11
    LOW = 12
    HIGH = 13
    ADD = 20
    SUB = 21
    MUL = 22
    DIV = 23
    MOD = 24
    AND = 25
    OR = 26
    XOR = 27
    SHR = 28
    SHL = 29
    GE = 30
    LE = 31
    LT = 32
    GT = 33
    EQ = 34
    NE = 35


class Binding(Enum):
    GLOBAL = 0
    LOCAL = 1


_UNARY = {"-": ExprOpCode.NEG, "~": ExprOpCode.NOT, "<": ExprOpCode.LOW, ">": ExprOpCode.HIGH}
_BINARY = {
    "+": ExprOpCode.ADD,
    "-": ExprOpCode.SUB,
    "*": ExprOpCode.MUL,
    "/": ExprOpCode.DIV,
    "%": ExprOpCode.MOD,
    "&": ExprOpCode.AND,
    "|": ExprOpCode.OR,
    "^": ExprOpCode.XOR,
    ">>": ExprOpCode.SHR,
    "<<": ExprOpCode.SHL,
    ">=": ExprOpCode.GE,
    "<=": ExprOpCode.LE,
    "<": ExprOpCode.LT,
    ">": ExprOpCode.GT,
    "==": ExprOpCode.EQ,
    "!=": ExprOpCode.NE,
}
_UNARY_TEXT = {code: text for text, code in _UNARY.items()}
_BINARY_TEXT = {code: text for text, code in _BINARY.items()}


@dataclass(frozen=True)
class ExprOp:
    """One RPN step. `value` is a `CONST`'s number or a `SYMBOL`'s `Binding`
    value; `name` is a `SYMBOL`'s name or a `STRING`'s text."""

    op: ExprOpCode
    value: int = 0
    name: str = ""


class UnresolvedLinkName(Exception):
    """A `SYMBOL` the lookup has no value for."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


Lookup = Callable[[str, Binding], int | str]


_INT64 = range(-(1 << 63), 1 << 63)


@dataclass(frozen=True)
class LinkExpr:
    ops: tuple[ExprOp, ...]

    def __post_init__(self) -> None:
        """Refuse an op list that is not one expression: an operator short of
        operands, values left over, a constant the object format cannot hold."""
        depth = 0
        for op in self.ops:
            if op.op is ExprOpCode.CONST and op.value not in _INT64:
                raise ValueError(f"constant {op.value:#x} does not fit a link expression")
            arity = 1 if op.op in _UNARY_TEXT else 2 if op.op in _BINARY_TEXT else 0
            if depth < arity:
                raise ValueError(f"malformed link expression: `{op.op.name}` lacks an operand")
            depth += 1 - arity
        if depth != 1:
            raise ValueError(f"malformed link expression: {len(self.ops)} ops leave {depth} values")

    @classmethod
    def parse(cls, text: str) -> LinkExpr:
        """The expression `text` writes: numbers, names, strings and the
        assembler's operators. Every name is `GLOBAL`."""
        from a816.parse.ast.expression import _ordered, eval_number, expr_to_ast
        from a816.parse.ast.nodes import BinOp, Term, UnaryOp
        from a816.parse.tokens import TokenType

        ops: list[ExprOp] = []
        for node in _ordered(expr_to_ast(text)):
            token = node.token
            if type(node) is UnaryOp:
                ops.append(ExprOp(_UNARY[token.value]))
            elif type(node) is BinOp:
                ops.append(ExprOp(_BINARY[token.value]))
            elif type(node) is not Term:
                raise ValueError(f"`{node.to_canonical()}` cannot appear in a link-time expression")
            elif token.type == TokenType.NUMBER:
                ops.append(ExprOp(ExprOpCode.CONST, eval_number(token.value)))
            elif token.type == TokenType.QUOTED_STRING:
                ops.append(ExprOp(ExprOpCode.STRING, name=token.value[1:-1]))
            else:
                ops.append(ExprOp(ExprOpCode.SYMBOL, Binding.GLOBAL.value, token.value))
        return cls(tuple(ops))

    def names(self) -> set[str]:
        return {op.name for op in self.ops if op.op is ExprOpCode.SYMBOL}

    def localize(self, local_names: Collection[str]) -> LinkExpr:
        """The same expression with each name in `local_names` bound `LOCAL`;
        other names keep their binding."""
        return LinkExpr(
            tuple(
                ExprOp(op.op, Binding.LOCAL.value, op.name)
                if op.op is ExprOpCode.SYMBOL and op.name in local_names
                else op
                for op in self.ops
            )
        )

    def substitute(self, replacements: Mapping[str, LinkExpr]) -> LinkExpr:
        """The expression with each named `SYMBOL` replaced by its expression."""
        ops: list[ExprOp] = []
        for op in self.ops:
            replacement = replacements.get(op.name) if op.op is ExprOpCode.SYMBOL else None
            ops.extend(replacement.ops if replacement is not None else (op,))
        return LinkExpr(tuple(ops))

    def evaluate_value(self, lookup: Lookup) -> int | str:
        """The expression's value, names read through `lookup`; raises
        `UnresolvedLinkName` for a name it lacks and `ExprEvalError` for an
        operator mistake (division by zero, a string where a number goes)."""
        from a816.parse.ast.expression import binary_value, unary_value

        stack: list[int | str] = []
        for op in self.ops:
            if op.op is ExprOpCode.CONST:
                stack.append(op.value)
            elif op.op is ExprOpCode.STRING:
                stack.append(op.name)
            elif op.op is ExprOpCode.SYMBOL:
                stack.append(lookup(op.name, Binding(op.value)))
            elif op.op in _UNARY_TEXT:
                stack.append(unary_value(_UNARY_TEXT[op.op], stack.pop()))
            else:
                right = stack.pop()
                stack.append(binary_value(_BINARY_TEXT[op.op], stack.pop(), right))
        return stack[0]

    def evaluate(self, lookup: Lookup) -> int:
        from a816.error_codes import E_CODEGEN_MISMATCHED_TYPES
        from a816.parse.ast.expression import ExprEvalError

        value = self.evaluate_value(lookup)
        if not isinstance(value, int):
            raise ExprEvalError(f"`{self}` is a string, not a number", E_CODEGEN_MISMATCHED_TYPES)
        return value

    def __str__(self) -> str:
        """Infix text that parses back to the same expression: every
        operation is parenthesised but the outermost."""
        stack: list[str] = []
        for op in self.ops:
            if op.op is ExprOpCode.CONST:
                stack.append(_number(op.value) if op.value >= 0 else f"(-{_number(-op.value)})")
            elif op.op is ExprOpCode.STRING:
                stack.append(f'"{op.name}"')
            elif op.op is ExprOpCode.SYMBOL:
                stack.append(op.name)
            elif op.op in _UNARY_TEXT:
                stack.append(f"({_UNARY_TEXT[op.op]}{stack.pop()})")
            else:
                right = stack.pop()
                stack.append(f"({stack.pop()} {_BINARY_TEXT[op.op]} {right})")
        text = stack[-1] if stack else ""
        return text[1:-1] if _wrapped(text) else text


def _number(value: int) -> str:
    """A digit as written, anything larger in hex as addresses are."""
    return str(value) if value < 10 else f"{value:#x}"


def _wrapped(text: str) -> bool:
    """Whether the outer parentheses of `text` enclose all of it."""
    if not (text.startswith("(") and text.endswith(")")):
        return False
    depth = 0
    for index, char in enumerate(text):
        depth += {"(": 1, ")": -1}.get(char, 0)
        if depth == 0 and index < len(text) - 1:
            return False
    return True


def symbol(name: str, binding: Binding = Binding.GLOBAL) -> LinkExpr:
    return LinkExpr((ExprOp(ExprOpCode.SYMBOL, binding.value, name),))


class RelocKind(Enum):
    """What the linker checks before it writes an expression's value."""

    VALUE = 0
    """Masked to the field width: data, `.w` / `.l` operands."""
    PC_RELATIVE = 1
    """A branch offset: the value minus the operand's end, signed to its width."""
    BYTE_IMMEDIATE = 2
    """`lda.b #x`: -0x100..0xFF, as at compile time."""
    DIRECT_PAGE = 3
    """`lda.b x`: an address the direct page reaches."""
    DIRECT_POINTER = 4
    """`lda (x),y`: a pointer the direct page reaches."""
    STACK_OFFSET = 5
    """`lda x,s`: an offset 0..0xFF from S."""


@dataclass(frozen=True)
class ExprRelocation:
    """`size` bytes at `offset` in a section filled with `expr` at link."""

    offset: int
    size: int
    kind: RelocKind
    expr: LinkExpr
