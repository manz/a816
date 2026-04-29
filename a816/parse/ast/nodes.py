import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from a816.cpu.cpu_65c816 import AddressingMode, ValueSize
from a816.parse.tokens import Token


class AstNode(ABC):
    kind: str

    def __init__(self, kind: str, file_info: Token, docstring: str | None = None) -> None:
        self.kind = kind
        self.file_info = file_info
        self.docstring = docstring

    @abstractmethod
    def to_representation(self) -> tuple[Any, ...]:
        """Returns the tuple representation of the node."""

    def to_canonical(self) -> str:
        """Returns the canonical representation of the node."""
        return f"# {self.kind} node (to_canonical not implemented)"


@dataclass
class ExprNode:
    token: Token

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, ExprNode):
            return False

        return self.token == other.token


class BinOp(ExprNode):
    """Represents a Binary expression operation"""


class UnaryOp(ExprNode):
    """Represents a Unary expression operation"""


class Term(ExprNode):
    """Represents a expression term"""


class Parenthesis(ExprNode):
    """Represents a Parenthesis expression"""


class ExpressionAstNode(AstNode):
    tokens: list[ExprNode]

    def __init__(self, tokens: list[ExprNode]) -> None:
        super().__init__("expression", tokens[0].token)
        self.tokens = tokens

    def to_representation(self) -> tuple[Any, ...]:
        return (" ".join([expr_node.token.value for expr_node in self.tokens]),)

    def to_canonical(self) -> str:
        return " ".join([expr_node.token.value for expr_node in self.tokens])


class BlockAstNode(AstNode):
    body: list[AstNode]

    def __init__(self, body: list[AstNode], file_info: Token) -> None:
        super().__init__("block", file_info)
        self.body = body

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, list(node.to_representation() for node in self.body)

    def to_canonical(self) -> str:
        return f"{{\n{'\n'.join([node.to_canonical() for node in self.body])}}}\n"


class CompoundAstNode(AstNode):
    body: list[AstNode]

    def __init__(self, body: list[AstNode], file_info: Token):
        super().__init__("compound", file_info)
        self.body = body

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, list(node.to_representation() for node in self.body)

    def to_canonical(self) -> str:
        return "\n".join([node.to_canonical() for node in self.body])


class LabelAstNode(AstNode):
    label: str

    def __init__(self, label: str, file_info: Token) -> None:
        super().__init__("label", file_info)
        self.label = label

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.label

    def to_canonical(self) -> str:
        return f"{self.label}:"


class TextAstNode(AstNode):
    text: str

    def __init__(self, text: str, file_info: Token) -> None:
        super().__init__("text", file_info)
        self.text = text

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.text

    def to_canonical(self) -> str:
        return f'.text "{self.text}"'


class AsciiAstNode(AstNode):
    text: str

    def __init__(self, text: str, file_info: Token) -> None:
        super().__init__("ascii", file_info)
        self.text = text

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.text

    def to_canonical(self) -> str:
        return f'.ascii "{self.text}"'


class CommentAstNode(AstNode):
    comment: str

    def __init__(self, comment: str, file_info: Token) -> None:
        super().__init__("comment", file_info)
        self.comment = comment

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.comment

    def to_canonical(self) -> str:
        return self.comment


class DocstringAstNode(AstNode):
    def __init__(self, text: str, file_info: Token) -> None:
        super().__init__("docstring", file_info)
        self.text = text

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.text

    def to_canonical(self) -> str:
        if '"""' in self.text and "'''" not in self.text:
            return f"'''{self.text}'''"
        if '"""' in self.text:
            escaped = self.text.replace('"""', '\\"""')
            return f'"""{escaped}"""'
        return f'"""{self.text}"""'


class ScopeAstNode(AstNode):
    name: str
    body: BlockAstNode

    def __init__(self, name: str, body: Any, file_info: Token, docstring: str | None = None) -> None:
        super().__init__("scope", file_info, docstring)
        self.name = name
        self.body = body

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.name, self.body.to_representation()

    def to_canonical(self) -> str:
        return f".scope {self.name}:\n{{{self.body.to_canonical()}}}\n"


class CodePositionAstNode(AstNode):
    def __init__(self, expression: ExpressionAstNode, file_info: Token):
        super().__init__("star_eq", file_info)
        self.expression = expression

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.expression.to_representation()[0]

    def to_canonical(self) -> str:
        return f"*={self.expression.to_canonical()}"


class CodeRelocationAstNode(AstNode):
    def __init__(self, expression: ExpressionAstNode, file_info: Token):
        super().__init__("at_eq", file_info)
        self.expression = expression

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.expression.to_representation()[0]

    def to_canonical(self) -> str:
        return f"@={self.expression.to_canonical()}"


class MapArgs(TypedDict, total=False):
    identifier: str | int
    writable: bool
    bank_range: tuple[int, int]
    addr_range: tuple[int, int]
    mask: int
    mirror_bank_range: tuple[int, int]


class MapAstNode(AstNode):
    args: MapArgs

    def __init__(self, args: MapArgs, file_info: Token):
        super().__init__("map", file_info)
        self.args = args

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.args

    def to_canonical(self) -> str:
        return f".map {self.args}"


class IfAstNode(AstNode):
    expression: ExpressionAstNode
    block: CompoundAstNode
    else_block: CompoundAstNode | None

    def __init__(
        self,
        expression: ExpressionAstNode,
        block: CompoundAstNode,
        else_bock: CompoundAstNode | None,
        file_info: Token,
    ):
        super().__init__("if", file_info)
        self.expression = expression
        self.block = block
        self.else_block = else_bock

    def to_representation(self) -> tuple[Any, ...]:
        return (
            self.kind,
            self.expression.to_representation()[0],
            self.block.to_representation(),
            self.else_block.to_representation() if self.else_block else None,
        )

    def to_canonical(self) -> str:
        condition = self.expression.to_canonical()
        block_content = self.block.to_canonical()
        if self.else_block:
            else_content = self.else_block.to_canonical()
            return f".if {condition}\n{block_content}.else\n{else_content}.endif"
        else:
            return f".if {condition}\n{block_content}.endif"


class MacroAstNode(AstNode):
    name: str
    args: list[str]
    block: BlockAstNode

    def __init__(self, name: str, args: list[str], block: BlockAstNode, file_info: Token, docstring: str | None = None):
        super().__init__("macro", file_info, docstring)
        self.name = name
        self.args = args
        self.block = block

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.name, ("args", self.args), self.block.to_representation()

    def to_canonical(self) -> str:
        args_str = ", ".join(self.args) if self.args else ""
        block_content = self.block.to_canonical()
        return f".macro {self.name}({args_str}) {{{block_content}}}"


class MacroApplyAstNode(AstNode):
    name: str
    args: list[ExpressionAstNode | BlockAstNode]

    def __init__(
        self,
        name: str,
        args: list[ExpressionAstNode | BlockAstNode],
        file_info: Token,
    ):
        super().__init__("macro_apply", file_info)
        self.name = name
        self.args = args

    def to_representation(self) -> tuple[Any, ...]:
        apply_args = []

        for arg in self.args:
            if isinstance(arg, ExpressionAstNode):
                apply_args.append(arg.to_representation()[0])
            else:
                apply_args.append(arg.to_representation())

        return (
            self.kind,
            self.name,
            ("apply_args", apply_args),
        )

    def to_canonical(self) -> str:
        if self.args:
            args_list = []
            for arg in self.args:
                if isinstance(arg, ExpressionAstNode):
                    args_list.append(arg.to_canonical())
                else:
                    args_list.append(arg.to_canonical())
            args_str = ", ".join(args_list)
            return f"{self.name}({args_str})"
        else:
            return f"{self.name}()"


class DataNode(AstNode):
    data: list[ExpressionAstNode]

    def __init__(
        self,
        kind: str,
        data: list[ExpressionAstNode | BlockAstNode],
        file_info: Token,
    ):
        super().__init__(kind, file_info)

        self.data = []

        for d in data:
            assert isinstance(d, ExpressionAstNode)
            self.data.append(d)

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, [d.to_representation()[0] for d in self.data]

    def to_canonical(self) -> str:
        values = [d.to_canonical() for d in self.data]
        return f".{self.kind} {', '.join(values)}"


class TableAstNode(AstNode):
    file_path: str

    def __init__(self, file_path: str, file_info: Token):
        super().__init__("table", file_info)
        self.file_path = file_path

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.file_path

    def to_canonical(self) -> str:
        return f'.table "{self.file_path}"'


class IncludeAstNode(AstNode):
    file_path: str
    included_nodes: list[AstNode]

    def __init__(self, file_path: str, included_nodes: list[AstNode], file_info: Token):
        """Represents a source-level .include directive while preserving the nested AST."""
        super().__init__("include", file_info)
        self.file_path = file_path
        self.included_nodes = included_nodes

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.file_path, [node.to_representation() for node in self.included_nodes]

    def to_canonical(self) -> str:
        return f'.include "{self.file_path}"'


class IncludeIpsAstNode(AstNode):
    file_path: str

    def __init__(self, file_path: str, expression: ExpressionAstNode, file_info: Token):
        super().__init__("include_ips", file_info)
        self.file_path = file_path
        self.expression = expression

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.file_path, self.expression.to_representation()[0]

    def to_canonical(self) -> str:
        expr = self.expression.to_canonical()
        return f'.include_ips "{self.file_path}" {expr}'


class IncludeBinaryAstNode(AstNode):
    file_path: str

    def __init__(self, file_path: str, file_info: Token):
        super().__init__("incbin", file_info)
        self.file_path = file_path

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.file_path

    def to_canonical(self) -> str:
        return f'.incbin "{self.file_path}"'


class SymbolAffectationAstNode(AstNode):
    def __init__(self, symbol: str, value: ExpressionAstNode, file_info: Token):
        super().__init__("symbol", file_info)
        self.symbol = symbol
        self.value = value

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.symbol, self.value.to_representation()[0]

    def to_canonical(self) -> str:
        value_str = self.value.to_canonical()
        return f"{self.symbol} = {value_str}"


class ExternAstNode(AstNode):
    def __init__(self, symbol: str, file_info: Token):
        super().__init__("extern", file_info)
        self.symbol = symbol

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.symbol

    def to_canonical(self) -> str:
        return f".extern {self.symbol}"


class ImportAstNode(AstNode):
    """AST node for .import "module" directive.

    Imports all public symbols from a module (object file or source file).
    This is similar to Turbo Pascal's 'uses' clause.
    """

    module_name: str

    def __init__(self, module_name: str, file_info: Token) -> None:
        super().__init__("import", file_info)
        self.module_name = module_name

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.module_name

    def to_canonical(self) -> str:
        return f'.import "{self.module_name}"'


class DebugAstNode(AstNode):
    def __init__(self, message: str, file_info: Token) -> None:
        super().__init__("debug", file_info)
        self.message = message

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.message

    def to_canonical(self) -> str:
        return f".debug '{self.message}'"


class RegisterSizeAstNode(AstNode):
    """AST node for register size directives (.a8, .a16, .i8, .i16)"""

    def __init__(self, register: str, size: int, file_info: Token) -> None:
        super().__init__("register_size", file_info)
        self.register = register  # "a" for accumulator, "i" for index
        self.size = size  # 8 or 16

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.register, self.size

    def to_canonical(self) -> str:
        return f".{self.register}{self.size}"


class AssignAstNode(AstNode):
    def __init__(self, symbol: str, value: ExpressionAstNode, file_info: Token):
        super().__init__("assign", file_info)
        self.symbol = symbol
        self.value = value

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.symbol, self.value.to_representation()[0]

    def to_canonical(self) -> str:
        return f"{self.symbol} := {self.value.to_canonical()}"


class CodeLookupAstNode(AstNode):
    def __init__(self, symbol: str, file_info: Token):
        super().__init__("code_lookup", file_info)
        self.symbol = symbol

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.symbol

    def to_canonical(self) -> str:
        return f"{{{{{self.symbol}}}}}"


class StructAstNode(AstNode):
    def __init__(self, name: str, fields: dict[str, str], file_info: Token) -> None:
        super().__init__("struct", file_info)
        self.name = name
        self.fields = fields

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.name, self.fields

    def to_canonical(self) -> str:
        fields_str = ", ".join(f"{name}: {type_}" for name, type_ in self.fields.items())
        return f".struct {self.name} {{ {fields_str} }}"


class ForAstNode(AstNode):
    def __init__(
        self,
        symbol: str,
        min_value: ExpressionAstNode,
        max_value: ExpressionAstNode,
        body: CompoundAstNode,
        file_info: Token,
    ) -> None:
        super().__init__("for", file_info)
        self.symbol = symbol
        self.min_value = min_value
        self.max_value = max_value
        self.body = body

    def to_representation(self) -> tuple[Any, ...]:
        return (
            self.kind,
            self.symbol,
            self.min_value.to_representation(),
            self.max_value.to_representation(),
            self.body.to_representation(),
        )

    def to_canonical(self) -> str:
        min_val = self.min_value.to_canonical()
        max_val = self.max_value.to_canonical()
        body_content = self.body.to_canonical()
        return f".for {self.symbol} {min_val} {max_val}\n{body_content}.endfor"


KeywordAstNode = (
    ScopeAstNode
    | MapAstNode
    | MacroAstNode
    | IfAstNode
    | ForAstNode
    | DataNode
    | TextAstNode
    | AsciiAstNode
    | IncludeAstNode
    | IncludeIpsAstNode
    | IncludeBinaryAstNode
    | BlockAstNode
    | TableAstNode
    | StructAstNode
    | ExternAstNode
    | ImportAstNode
    | DebugAstNode
    | RegisterSizeAstNode
)
FileInfoAstNode = tuple[Literal["file_info"], Token]


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
    ):
        super().__init__("opcode", file_info)
        self.addressing_mode = addressing_mode
        self.opcode = opcode
        self.value_size = value_size
        self.operand = operand
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


DeclAstNode = (
    CodeLookupAstNode
    | LabelAstNode
    | CompoundAstNode
    | CodePositionAstNode
    | CodeRelocationAstNode
    | OpcodeAstNode
    | KeywordAstNode
    | MacroApplyAstNode
    | SymbolAffectationAstNode
    | IfAstNode
    | CommentAstNode,
)

index_map = {
    AddressingMode.indirect: AddressingMode.indirect_indexed,
    AddressingMode.indirect_long: AddressingMode.indirect_indexed_long,
    AddressingMode.direct: AddressingMode.direct_indexed,
    AddressingMode.dp_or_sr_indirect_indexed: AddressingMode.stack_indexed_indirect_indexed,
}
