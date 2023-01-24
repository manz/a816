from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple, TypedDict, Union

from a816.cpu.cpu_65c816 import AddressingMode
from a816.parse.tokens import Token


class AstNode(ABC):
    kind: str

    def __init__(self, kind: str, file_info: Token) -> None:

        self.kind = kind
        self.file_info = file_info

    @abstractmethod
    def to_representation(self) -> Tuple[Any, ...]:
        """Returns the tuple representation of the node."""


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
    tokens: List[ExprNode]

    def __init__(self, tokens: List[ExprNode]) -> None:
        super().__init__("expression", tokens[0].token)
        self.tokens = tokens

    def to_representation(self) -> Tuple[Any, ...]:
        return (" ".join([expr_node.token.value for expr_node in self.tokens]),)


class BlockAstNode(AstNode):
    body: List[AstNode]

    def __init__(self, body: List[AstNode], file_info: Token) -> None:
        super().__init__("block", file_info)
        self.body = body

    def to_representation(self) -> Tuple[Any, ...]:
        return self.kind, list(node.to_representation() for node in self.body)


class CompoundAstNode(AstNode):
    body: List[AstNode]

    def __init__(self, body: List[AstNode], file_info: Token):
        super().__init__("compound", file_info)
        self.body = body

    def to_representation(self) -> Tuple[Any, ...]:
        return self.kind, list(node.to_representation() for node in self.body)


class LabelAstNode(AstNode):
    label: str

    def __init__(self, label: str, file_info: Token) -> None:
        super().__init__("label", file_info)
        self.label = label

    def to_representation(self) -> Tuple[Any, ...]:
        return self.kind, self.label


class TextAstNode(AstNode):
    text: str

    def __init__(self, text: str, file_info: Token) -> None:
        super().__init__("text", file_info)
        self.text = text

    def to_representation(self) -> Tuple[Any, ...]:
        return self.kind, self.text


class AsciiAstNode(AstNode):
    text: str

    def __init__(self, text: str, file_info: Token) -> None:
        super().__init__("ascii", file_info)
        self.text = text

    def to_representation(self) -> Tuple[Any, ...]:
        return self.kind, self.text


class ScopeAstNode(AstNode):
    name: str
    body: BlockAstNode

    def __init__(self, name: str, body: Any, file_info: Token) -> None:
        super().__init__("scope", file_info)
        self.name = name
        self.body = body

    def to_representation(self) -> Tuple[Any, ...]:
        return self.kind, self.name, self.body.to_representation()


class CodePositionAstNode(AstNode):
    def __init__(self, expression: ExpressionAstNode, file_info: Token):
        super().__init__("star_eq", file_info)
        self.expression = expression

    def to_representation(self) -> Tuple[Any, ...]:
        return self.kind, self.expression.to_representation()[0]


class CodeRelocationAstNode(AstNode):
    def __init__(self, expression: ExpressionAstNode, file_info: Token):
        super().__init__("at_eq", file_info)
        self.expression = expression

    def to_representation(self) -> Tuple[Any, ...]:
        return self.kind, self.expression.to_representation()[0]


MapArgs = TypedDict(
    "MapArgs",
    {
        "identifier": Union[str, int],
        "writable": bool,
        "bank_range": Tuple[int, int],
        "addr_range": Tuple[int, int],
        "mask": int,
        "mirror_bank_range": Tuple[int, int],
    },
    total=False,
)


class MapAstNode(AstNode):
    args: MapArgs

    def __init__(self, args: MapArgs, file_info: Token):
        super().__init__("map", file_info)
        self.args = args

    def to_representation(self) -> Tuple[Any, ...]:
        return self.kind, self.args


class IfAstNode(AstNode):
    expression: ExpressionAstNode
    block: CompoundAstNode
    else_block: Optional[CompoundAstNode]

    def __init__(
        self,
        expression: ExpressionAstNode,
        block: CompoundAstNode,
        else_bock: Optional[CompoundAstNode],
        file_info: Token,
    ):
        super().__init__("if", file_info)
        self.expression = expression
        self.block = block
        self.else_block = else_bock

    def to_representation(self) -> Tuple[Any, ...]:
        return (
            self.kind,
            self.expression.to_representation()[0],
            self.block.to_representation(),
            self.else_block.to_representation() if self.else_block else None,
        )


class MacroAstNode(AstNode):
    name: str
    args: List[str]
    block: BlockAstNode

    def __init__(self, name: str, args: List[str], block: BlockAstNode, file_info: Token):
        super().__init__("macro", file_info)
        self.name = name
        self.args = args
        self.block = block

    def to_representation(self) -> Tuple[Any, ...]:
        return self.kind, self.name, ("args", self.args), self.block.to_representation()


class MacroApplyAstNode(AstNode):
    name: str
    args: List[Union[ExpressionAstNode, BlockAstNode]]

    def __init__(self, name: str, args: List[Union[ExpressionAstNode, BlockAstNode]], file_info: Token):
        super().__init__("macro_apply", file_info)
        self.name = name
        self.args = args

    def to_representation(self) -> Tuple[Any, ...]:
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


class DataNode(AstNode):
    data: List[ExpressionAstNode]

    def __init__(self, kind: str, data: List[Union[ExpressionAstNode, BlockAstNode]], file_info: Token):
        super().__init__(kind, file_info)

        self.data = []

        for d in data:
            assert isinstance(d, ExpressionAstNode)
            self.data.append(d)

    def to_representation(self) -> Tuple[Any, ...]:
        return self.kind, [d.to_representation()[0] for d in self.data]


class TableAstNode(AstNode):
    file_path: str

    def __init__(self, file_path: str, file_info: Token):
        super().__init__("table", file_info)
        self.file_path = file_path

    def to_representation(self) -> Tuple[Any, ...]:
        return self.kind, self.file_path


class IncludeAstNode(AstNode):
    file_path: str

    def __init__(self, file_path: str, file_info: Token):
        super().__init__("include", file_info)
        self.file_path = file_path

    def to_representation(self) -> Tuple[Any, ...]:
        return self.kind, self.file_path


class IncludeIpsAstNode(AstNode):
    file_path: str

    def __init__(self, file_path: str, expression: ExpressionAstNode, file_info: Token):
        super().__init__("include_ips", file_info)
        self.file_path = file_path
        self.expression = expression

    def to_representation(self) -> Tuple[Any, ...]:
        return self.kind, self.file_path, self.expression.to_representation()[0]


class IncludeBinaryAstNode(AstNode):
    file_path: str

    def __init__(self, file_path: str, file_info: Token):
        super().__init__("incbin", file_info)
        self.file_path = file_path

    def to_representation(self) -> Tuple[Any, ...]:
        return self.kind, self.file_path


class SymbolAffectationAstNode(AstNode):
    def __init__(self, symbol: str, value: ExpressionAstNode, file_info: Token):
        super().__init__("symbol", file_info)
        self.symbol = symbol
        self.value = value

    def to_representation(self) -> Tuple[Any, ...]:
        return self.kind, self.symbol, self.value.to_representation()[0]


class AssignAstNode(AstNode):
    def __init__(self, symbol: str, value: ExpressionAstNode, file_info: Token):
        super().__init__("assign", file_info)
        self.symbol = symbol
        self.value = value

    def to_representation(self) -> Tuple[Any, ...]:
        return self.kind, self.symbol, self.value.to_representation()[0]


class CodeLookupAstNode(AstNode):
    def __init__(self, symbol: str, file_info: Token):
        super().__init__("code_lookup", file_info)
        self.symbol = symbol

    def to_representation(self) -> Tuple[Any, ...]:
        return self.kind, self.symbol


class StructAstNode(AstNode):
    def __init__(self, name: str, fields: Dict[str, str], file_info: Token) -> None:
        super().__init__("struct", file_info)
        self.name = name
        self.fields = fields

    def to_representation(self) -> Tuple[Any, ...]:
        return self.kind, self.name, self.fields


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

    def to_representation(self) -> Tuple[Any, ...]:
        return (
            self.kind,
            self.symbol,
            self.min_value.to_representation(),
            self.max_value.to_representation(),
            self.body.to_representation(),
        )


KeywordAstNode = Union[
    ScopeAstNode,
    MapAstNode,
    MacroAstNode,
    IfAstNode,
    ForAstNode,
    DataNode,
    TextAstNode,
    AsciiAstNode,
    IncludeAstNode,
    IncludeIpsAstNode,
    IncludeBinaryAstNode,
    BlockAstNode,
    TableAstNode,
    StructAstNode,
]
FileInfoAstNode = Tuple[Literal["file_info"], Token]


class OpcodeAstNode(AstNode):
    def __init__(
        self,
        *,
        addressing_mode: AddressingMode,
        opcode_value: Union[Tuple[str, str], str],
        operand: Optional[ExpressionAstNode],
        index: Optional[str],
        file_info: Token,
    ):
        super().__init__("opcode", file_info)
        self.addressing_mode = addressing_mode
        self.opcode_value = opcode_value
        self.operand = operand
        self.index = index

    def to_representation(self) -> Tuple[Any, ...]:
        return (
            self.kind,
            self.addressing_mode,
            self.opcode_value,
            self.operand.to_representation()[0] if self.operand else None,
            self.index,
        )


DeclAstNode = Union[
    CodeLookupAstNode,
    LabelAstNode,
    CompoundAstNode,
    CodePositionAstNode,
    CodeRelocationAstNode,
    OpcodeAstNode,
    KeywordAstNode,
    MacroApplyAstNode,
    SymbolAffectationAstNode,
    IfAstNode,
    # TableAstNode
]
index_map = {
    AddressingMode.indirect: AddressingMode.indirect_indexed,
    AddressingMode.indirect_long: AddressingMode.indirect_indexed_long,
    AddressingMode.direct: AddressingMode.direct_indexed,
    AddressingMode.dp_or_sr_indirect_indexed: AddressingMode.stack_indexed_indirect_indexed,
}
