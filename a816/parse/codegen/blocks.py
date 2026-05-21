"""Scope / block / compound emitters."""

from __future__ import annotations

from a816.parse.ast.nodes import BlockAstNode, CompoundAstNode, ScopeAstNode
from a816.parse.codegen.base import GenNodes, MacroDefinitions, _code_gen, generators
from a816.parse.nodes import PopScopeNode, ScopeNode
from a816.parse.tokens import Token
from a816.protocols import NodeProtocol
from a816.symbols import Resolver


def generate_block(
    node: CompoundAstNode | BlockAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    # Anonymous `{}` blocks scope their labels — don't leak names like
    # `loop`/`exit` into the parent scope.
    resolver.append_scope()
    resolver.use_next_scope()
    code: list[NodeProtocol] = [ScopeNode(resolver)]
    code += _code_gen(node.body, resolver, macro_definitions)
    code.append(PopScopeNode(resolver))
    resolver.restore_scope(exports=False)
    return code


def generate_scope(
    node: ScopeAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    name = node.name
    resolver.append_named_scope(name)
    resolver.use_next_scope()
    code: list[NodeProtocol] = [ScopeNode(resolver)]

    code += _code_gen(node.body.body, resolver, macro_definitions)
    code.append(PopScopeNode(resolver))
    # Promote `name.label` and `name.symbol` to the parent so callers can
    # `jsr.l ns.init` after `.scope ns { init: ... }`. Underscore-prefixed
    # names stay private (see Resolver.restore_scope filter).
    resolver.restore_scope(exports=True)
    return code


def generate_compound(
    node: CompoundAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    code: GenNodes = []
    resolver.append_scope()
    resolver.use_next_scope()
    code.append(ScopeNode(resolver))
    code += generate_block(node, resolver, macro_definitions, file_info)
    code.append(PopScopeNode(resolver))
    resolver.restore_scope()
    return code


generators["block"] = generate_block
generators["scope"] = generate_scope
generators["compound"] = generate_compound
