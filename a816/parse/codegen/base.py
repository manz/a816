"""Codegen base: types, generators registry, entry point.

Each submodule registers its `generate_*` functions into `generators` at
import time; `_code_gen` dispatches per-node by `node.kind`. `code_gen`
is the public entry: it threads a fresh `macro_definitions` dict and
delegates to `_code_gen`.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from a816.parse.ast.nodes import (
    AssignAstNode,
    AstNode,
    LabelAstNode,
    LabelDeclAstNode,
    MacroAstNode,
    StructAstNode,
    SymbolAffectationAstNode,
)
from a816.parse.tokens import Token
from a816.protocols import NodeProtocol
from a816.symbols import Resolver

logger = logging.getLogger("a816.codegen")

MacroDefinitions = dict[str, Any]
GenNodes = list[NodeProtocol]


class CodeGenFuncProtocol(Protocol):
    def __call__(
        self,
        node: AstNode,
        resolver: Resolver,
        macro_definitions: MacroDefinitions,
        file_info: Token,
    ) -> GenNodes:
        """Protocol for codegen functions."""


generators: dict[str, Any] = {}


def _get_file_info(node: AstNode) -> Token:
    return node.file_info


def declared_name(node: AstNode) -> str | None:
    """The name a declaration node binds (constant, label, macro, struct), else None."""
    if isinstance(node, MacroAstNode | StructAstNode):
        return node.name
    if isinstance(node, SymbolAffectationAstNode | AssignAstNode | LabelDeclAstNode):
        return node.symbol
    if isinstance(node, LabelAstNode):
        return node.label
    return None


def _code_gen(ast_nodes: list[AstNode], resolver: Resolver, macro_definitions: MacroDefinitions) -> list[NodeProtocol]:
    code = []
    for node in ast_nodes:
        file_info = _get_file_info(node)
        if resolver.private_owners:
            name = declared_name(node)
            if name is not None:
                resolver.claim_private(name, file_info)
        generator = generators.get(node.kind)
        if generator:
            code += generator(node, resolver, macro_definitions, file_info)
        else:
            raise RuntimeError("Left over node", node)
    return code


def _code_gen_placement_body(
    ast_nodes: list[AstNode], resolver: Resolver, macro_definitions: MacroDefinitions
) -> list[NodeProtocol]:
    """Generate an `.alloc` / `.relocate` body, tracking the nesting
    depth so `.import` can refuse to run inside a placement body."""
    resolver.placement_body_depth += 1
    try:
        return _code_gen(ast_nodes, resolver, macro_definitions)
    finally:
        resolver.placement_body_depth -= 1


def code_gen(ast_nodes: list[AstNode], resolver: Resolver) -> GenNodes:
    # One call per source unit: a `*=` cursor left by a previous unit
    # assembled on the same resolver must not reject this unit's imports.
    resolver.star_eq_cursor_active = False
    macro_definitions: MacroDefinitions = {}
    return _code_gen(ast_nodes, resolver, macro_definitions)
