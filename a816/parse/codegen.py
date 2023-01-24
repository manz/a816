from typing import Any, Dict, List, Protocol, Union

from a816.cpu.cpu_65c816 import AddressingMode
from a816.exceptions import SymbolNotDefined
from a816.parse.ast.expression import eval_expression
from a816.parse.ast.nodes import (
    AsciiAstNode,
    AssignAstNode,
    AstNode,
    BlockAstNode,
    CodeLookupAstNode,
    CodePositionAstNode,
    CodeRelocationAstNode,
    CompoundAstNode,
    DataNode,
    ExpressionAstNode,
    ForAstNode,
    IfAstNode,
    IncludeBinaryAstNode,
    IncludeIpsAstNode,
    LabelAstNode,
    MacroApplyAstNode,
    MacroAstNode,
    MapAstNode,
    OpcodeAstNode,
    ScopeAstNode,
    SymbolAffectationAstNode,
    TableAstNode,
    Term,
    TextAstNode,
)
from a816.parse.nodes import (
    AsciiNode,
    BinaryNode,
    ByteNode,
    CodePositionNode,
    ExpressionNode,
    IncludeIpsNode,
    LabelNode,
    LongNode,
    NodeError,
    NodeProtocol,
    OpcodeNode,
    PopScopeNode,
    RelocationAddressNode,
    ScopeNode,
    SymbolNode,
    TableNode,
    TextNode,
    WordNode,
)
from a816.parse.tokens import Token, TokenType
from a816.symbols import Resolver

MacroDefinitions = Dict[str, Any]
GenNodes = List[NodeProtocol]


class CodeGenFuncProtocol(Protocol):
    def __call__(
        self, node: AstNode, resolver: Resolver, macro_definitions: MacroDefinitions, file_info: Token
    ) -> GenNodes:
        """Protocol for codegen functions."""


def code_gen(ast_nodes: List[AstNode], resolver: Resolver) -> GenNodes:
    macro_definitions: MacroDefinitions = {}
    return _code_gen(ast_nodes, resolver, macro_definitions)


def _get_file_info(node: AstNode) -> Token:
    return node.file_info


def generate_block(
    node: Union[CompoundAstNode, BlockAstNode],
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return _code_gen(node.body, resolver, macro_definitions)


def generate_scope(
    node: ScopeAstNode, resolver: Resolver, macro_definitions: MacroDefinitions, file_info: Token
) -> GenNodes:
    name = node.name
    resolver.append_named_scope(name)
    resolver.use_next_scope()
    code: List[NodeProtocol] = [ScopeNode(resolver)]

    code += _code_gen(node.body.body, resolver, macro_definitions)
    code.append(PopScopeNode(resolver))
    resolver.restore_scope(exports=False)
    return code


def generate_map(
    node: MapAstNode, resolver: Resolver, macro_definitions: MacroDefinitions, file_info: Token
) -> GenNodes:
    attributes = node.args

    resolver.bus.map(
        str(attributes["identifier"]),
        attributes["bank_range"],
        attributes["addr_range"],
        attributes["mask"],
        writeable=attributes.get("writable", False),
        mirror_bank_range=attributes.get("mirror_bank_range"),
    )
    return []


def generate_opcode(
    node: OpcodeAstNode, resolver: Resolver, macro_definitions: MacroDefinitions, file_info: Token
) -> GenNodes:
    code: List[NodeProtocol] = []
    opcode = node.opcode_value
    size = None

    if isinstance(node.operand, BlockAstNode):
        raise NodeError("Opcode operand must not be code", file_info)

    if isinstance(opcode, list) or isinstance(opcode, tuple):
        size = opcode[1]
        opcode = opcode[0]
    mode = node.addressing_mode
    if mode == AddressingMode.none:
        code.append(OpcodeNode(opcode, addressing_mode=mode, file_info=file_info, resolver=resolver))
    else:
        operand = node.operand
        assert operand is not None

        if mode in (
            AddressingMode.direct_indexed,
            AddressingMode.indirect_indexed,
            AddressingMode.indirect_indexed_long,
            AddressingMode.dp_or_sr_indirect_indexed,
            AddressingMode.stack_indexed_indirect_indexed,
        ):
            code.append(
                OpcodeNode(
                    opcode,
                    addressing_mode=mode,
                    size=size,
                    value_node=ExpressionNode(operand, resolver, file_info),
                    index=node.index,
                    file_info=file_info,
                    resolver=resolver,
                )
            )
        else:
            code.append(
                OpcodeNode(
                    opcode,
                    addressing_mode=mode,
                    size=size,
                    value_node=ExpressionNode(operand, resolver, file_info),
                    file_info=file_info,
                    resolver=resolver,
                )
            )

    return code


def generate_include_ips(
    node: IncludeIpsAstNode, resolver: Resolver, macro_definitions: MacroDefinitions, file_info: Token
) -> GenNodes:
    return [IncludeIpsNode(node.file_path, resolver, node.expression)]


def generate_incbin(
    node: IncludeBinaryAstNode, resolver: Resolver, macro_definitions: MacroDefinitions, file_info: Token
) -> GenNodes:
    return [BinaryNode(node.file_path, resolver)]


def generate_dl(node: DataNode, resolver: Resolver, macro_definitions: MacroDefinitions, file_info: Token) -> GenNodes:
    code: List[NodeProtocol] = []
    for expr in node.data:
        assert isinstance(expr, ExpressionAstNode)
        code.append(LongNode(ExpressionNode(expr, resolver, file_info)))
    return code


def generate_dw(node: DataNode, resolver: Resolver, macro_definitions: MacroDefinitions, file_info: Token) -> GenNodes:
    code: GenNodes = []
    for expr in node.data:
        assert isinstance(expr, ExpressionAstNode)
        code.append(WordNode(ExpressionNode(expr, resolver, file_info)))
    return code


def generate_db(node: DataNode, resolver: Resolver, macro_definitions: MacroDefinitions, file_info: Token) -> GenNodes:
    code: GenNodes = []
    for expr in node.data:
        assert isinstance(expr, ExpressionAstNode)
        code.append(ByteNode(ExpressionNode(expr, resolver, file_info)))
    return code


def generate_symbol(
    node: Union[SymbolAffectationAstNode, AssignAstNode],
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return [SymbolNode(node.symbol, node.value, resolver)]


def generate_label(
    node: LabelAstNode, resolver: Resolver, macro_definitions: MacroDefinitions, file_info: Token
) -> GenNodes:
    return [LabelNode(node.label, resolver)]


def generate_text(
    node: TextAstNode, resolver: Resolver, macro_definitions: MacroDefinitions, file_info: Token
) -> GenNodes:
    return [TextNode(node.text, resolver, file_info)]


def generate_ascii(
    node: AsciiAstNode, resolver: Resolver, macro_definitions: MacroDefinitions, file_info: Token
) -> GenNodes:
    return [AsciiNode(node.text, resolver)]


def generate_table(
    node: TableAstNode, resolver: Resolver, macro_definitions: MacroDefinitions, file_info: Token
) -> GenNodes:
    return [TableNode(node.file_path, resolver)]


def generate_at_eq(
    node: CodeRelocationAstNode, resolver: Resolver, macro_definitions: MacroDefinitions, file_info: Token
) -> GenNodes:
    return [RelocationAddressNode(ExpressionNode(node.expression, resolver, file_info), resolver)]


def generate_star_eq(
    node: CodePositionAstNode, resolver: Resolver, macro_definitions: MacroDefinitions, file_info: Token
) -> GenNodes:
    return [CodePositionNode(ExpressionNode(node.expression, resolver, file_info), resolver)]


def generate_for(
    node: ForAstNode, resolver: Resolver, macro_definitions: MacroDefinitions, file_info: Token
) -> GenNodes:
    code: GenNodes = []
    from_val = eval_expression(node.min_value, resolver)
    to_val = eval_expression(node.max_value, resolver)
    for k in range(from_val, to_val):
        resolver.append_internal_scope()
        resolver.use_next_scope()
        code.append(ScopeNode(resolver))
        code.append(SymbolNode(node.symbol, ExpressionAstNode([Term(Token(TokenType.NUMBER, str(k)))]), resolver))
        code += _code_gen(node.body.body, resolver, macro_definitions)
        code.append(PopScopeNode(resolver))
        resolver.restore_scope()
    return code


def generate_if(node: IfAstNode, resolver: Resolver, macro_definitions: MacroDefinitions, file_info: Token) -> GenNodes:
    code = []
    if_branch_true = node.block
    if_branch_false = node.else_block

    try:
        condition = eval_expression(node.expression, resolver)
    except (KeyError, SymbolNotDefined):
        condition = False
    if condition:
        code += _code_gen(if_branch_true.body, resolver, macro_definitions)
    elif if_branch_false:
        code += _code_gen(if_branch_false.body, resolver, macro_definitions)
    return code


def generate_code_lookup(
    node: CodeLookupAstNode, resolver: Resolver, macro_definitions: MacroDefinitions, file_info: Token
) -> GenNodes:
    value = resolver.current_scope.value_for(node.symbol)

    if isinstance(value, BlockAstNode):
        return _code_gen(value.body, resolver, macro_definitions)
    else:
        raise NodeError(f"{node.symbol} is not a code block ({value})", file_info)


def generate_macro_application(
    node: MacroApplyAstNode, resolver: Resolver, macro_definitions: MacroDefinitions, file_info: Token
) -> GenNodes:
    code: GenNodes = []
    macro_def: MacroAstNode = macro_definitions[node.name]
    macro_code = macro_def.block
    macro_args = macro_def.args
    macro_args_values = node.args
    resolver.append_scope()
    resolver.use_next_scope()
    code.append(ScopeNode(resolver))
    for index, arg in enumerate(macro_args):
        value = macro_args_values[index]
        try:
            if isinstance(value, BlockAstNode):
                resolver.current_scope.add_symbol(arg, value)
            else:
                resolver.current_scope.add_symbol(arg, eval_expression(value, resolver))
        except SymbolNotDefined:
            # defer the resolve to the emit part.
            code.append(SymbolNode(arg, value, resolver))
    code += _code_gen(macro_code.body, resolver, macro_definitions)
    code.append(PopScopeNode(resolver))
    resolver.restore_scope()
    return code


def generate_macro(
    node: MacroAstNode, resolver: Resolver, macro_definitions: MacroDefinitions, file_info: Token
) -> GenNodes:
    macro_definitions[node.name] = node
    return []


def generate_compound(
    node: CompoundAstNode, resolver: Resolver, macro_definitions: MacroDefinitions, file_info: Token
) -> GenNodes:
    code: GenNodes = []
    resolver.append_scope()
    resolver.use_next_scope()
    code.append(ScopeNode(resolver))
    code += generate_block(node, resolver, macro_definitions, file_info)
    code.append(PopScopeNode(resolver))
    resolver.restore_scope()
    return code


generators = {
    "block": generate_block,
    "scope": generate_scope,
    "map": generate_map,
    "compound": generate_compound,
    "macro": generate_macro,
    "macro_apply": generate_macro_application,
    "code_lookup": generate_code_lookup,
    "if": generate_if,
    "for": generate_for,
    "at_eq": generate_at_eq,
    "star_eq": generate_star_eq,
    "table": generate_table,
    "text": generate_text,
    "ascii": generate_ascii,
    "db": generate_db,
    "dw": generate_dw,
    "dl": generate_dl,
    "pointer": generate_dl,
    "symbol": generate_symbol,
    "assign": generate_symbol,
    "label": generate_label,
    "opcode": generate_opcode,
    "incbin": generate_incbin,
    "include_ips": generate_include_ips,
}


def _code_gen(ast_nodes: List[AstNode], resolver: Resolver, macro_definitions: MacroDefinitions) -> List[NodeProtocol]:
    code = []
    for node in ast_nodes:
        file_info = _get_file_info(node)
        generator = generators.get(node.kind)
        if generator:
            code += generator(node, resolver, macro_definitions, file_info)  # type:ignore
        else:
            raise RuntimeError("Left over node", node)

    return code
