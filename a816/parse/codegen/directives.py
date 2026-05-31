"""Small per-directive emitters: include / incbin / data / text / ascii /
table / at_eq / star_eq / register_size / debug / comment / docstring /
label / label_decl."""

from __future__ import annotations

from a816.parse.ast.nodes import (
    AsciiAstNode,
    CodePositionAstNode,
    CodeRelocationAstNode,
    CommentAstNode,
    DataNode,
    DebugAstNode,
    DocstringAstNode,
    ExpressionAstNode,
    FileInfoAstNode,
    IncludeAstNode,
    IncludeBinaryAstNode,
    IncludeIpsAstNode,
    LabelAstNode,
    LabelDeclAstNode,
    RegisterSizeAstNode,
    TableAstNode,
    TextAstNode,
)
from a816.parse.codegen.base import GenNodes, MacroDefinitions, _code_gen, generators
from a816.parse.nodes import (
    AsciiNode,
    BinaryNode,
    ByteNode,
    CodePositionNode,
    DebugNode,
    ExpressionNode,
    IncludeIpsNode,
    LabelDeclNode,
    LabelNode,
    LongNode,
    RegisterSizeNode,
    RelocationAddressNode,
    TableNode,
    TextNode,
    WordNode,
)
from a816.parse.tokens import Token
from a816.protocols import NodeProtocol
from a816.symbols import Resolver


def generate_include_ips(
    node: IncludeIpsAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return [IncludeIpsNode(node.file_path, resolver, node.expression)]


def generate_include(
    node: IncludeAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    """Inline the AST captured from an .include directive while honouring original scoping."""
    if node.resolved_path:
        import os

        # Track the include as a build dependency even when it only defines
        # constants (no emitted line reaches the object's file table).
        resolver.dependency_files.add(os.path.abspath(node.resolved_path))
    code: GenNodes = []
    if node.included_nodes:
        code.extend(_code_gen(node.included_nodes, resolver, macro_definitions))
    return code


def generate_docstring(
    node: DocstringAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    """Docstrings are metadata-only and do not emit code."""
    return []


def generate_incbin(
    node: IncludeBinaryAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return [BinaryNode(node.file_path, resolver)]


def _generate_data(
    node: DataNode,
    node_type: type[ByteNode] | type[WordNode] | type[LongNode],
    resolver: Resolver,
    file_info: Token,
) -> GenNodes:
    """Generate data nodes for .db, .dw, or .dl directives."""
    code: GenNodes = []
    for expr in node.data:
        assert isinstance(expr, ExpressionAstNode)
        code.append(node_type(ExpressionNode(expr, resolver, file_info)))
    return code


def generate_dl(
    node: DataNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return _generate_data(node, LongNode, resolver, file_info)


def generate_dw(
    node: DataNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return _generate_data(node, WordNode, resolver, file_info)


def generate_db(
    node: DataNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return _generate_data(node, ByteNode, resolver, file_info)


def generate_register_size(
    node: RegisterSizeAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return [RegisterSizeNode(node.register, node.size, resolver)]


def generate_label(
    node: LabelAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return [LabelNode(node.label, resolver)]


def generate_label_decl(
    node: LabelDeclAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return [LabelDeclNode(node.symbol, node.value, resolver, file_info)]


def generate_text(
    node: TextAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return [TextNode(node.text, resolver, file_info)]


def generate_ascii(
    node: AsciiAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return [AsciiNode(node.text, resolver)]


def generate_table(
    node: TableAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return [TableNode(node.file_path, resolver)]


def generate_at_eq(
    node: CodeRelocationAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return [RelocationAddressNode(ExpressionNode(node.expression, resolver, file_info), resolver)]


def generate_star_eq(
    node: CodePositionAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return [CodePositionNode(ExpressionNode(node.expression, resolver, file_info), resolver)]


def generate_comment(
    node: CommentAstNode, resolver: Resolver, macro_definitions: MacroDefinitions, file_info: FileInfoAstNode
) -> list[NodeProtocol]:
    # Comments don't generate executable code, so return empty list
    return []


def generate_debug(
    node: DebugAstNode, resolver: Resolver, macro_definitions: MacroDefinitions, file_info: FileInfoAstNode
) -> list[NodeProtocol]:
    return [DebugNode(node.message, resolver)]


generators["include_ips"] = generate_include_ips
generators["include"] = generate_include
generators["docstring"] = generate_docstring
generators["incbin"] = generate_incbin
generators["db"] = generate_db
generators["dw"] = generate_dw
generators["dl"] = generate_dl
generators["pointer"] = generate_dl
generators["register_size"] = generate_register_size
generators["label"] = generate_label
generators["label_decl"] = generate_label_decl
generators["text"] = generate_text
generators["ascii"] = generate_ascii
generators["table"] = generate_table
generators["at_eq"] = generate_at_eq
generators["star_eq"] = generate_star_eq
generators["comment"] = generate_comment
generators["debug"] = generate_debug
