"""Codegen base: types, generators registry, entry point.

Each submodule registers its `generate_*` functions into `generators` at
import time; `_code_gen` dispatches per-node by `node.kind`. `code_gen`
is the public entry: it threads a fresh `macro_definitions` dict and
delegates to `_code_gen`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Protocol

from a816.exceptions import SymbolNotDefined
from a816.parse.ast.nodes import (
    AssignAstNode,
    AstNode,
    LabelAstNode,
    LabelDeclAstNode,
    MacroAstNode,
    StructAstNode,
    SymbolAffectationAstNode,
)
from a816.parse.nodes import NodeError
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


def _code_gen(
    ast_nodes: Sequence[AstNode], resolver: Resolver, macro_definitions: MacroDefinitions
) -> list[NodeProtocol]:
    code = []
    for node in ast_nodes:
        file_info = _get_file_info(node)
        if resolver.private_owners:
            name = declared_name(node)
            if name is not None:
                resolver.claim_private(name, file_info)
        generator = generators.get(node.kind)
        if generator is None:
            raise RuntimeError("Left over node", node)
        try:
            code += generator(node, resolver, macro_definitions, file_info)
        except SymbolNotDefined as missing:
            raise _located_missing_symbol(missing, file_info, resolver) from missing
    return code


def _located_missing_symbol(missing: SymbolNotDefined, file_info: Token, resolver: Resolver) -> NodeError:
    """A name a generator needed while expanding, as a located, coded error.

    Generators evaluate `.for` bounds, `.if` conditions and the like
    directly; a miss used to escape unconverted and print as a bare
    `Build failed: NAME`. An alloc's size (`sizeof(blob)` reads
    `blob.__size`) exists only after layout, so asking for it here gets
    its own message.
    """
    from a816.error_codes import E_CODEGEN_SIZE_OPERAND
    from a816.parse.nodes.errors import undefined_symbol_error

    name = str(missing)
    if name.endswith(".__size"):
        alloc = name.removesuffix(".__size")
        return NodeError(
            f"`sizeof({alloc})` is only known after layout, and this needs it while expanding",
            missing.token or file_info,
            code=str(E_CODEGEN_SIZE_OPERAND),
            hint="`.for` bounds, `.if` conditions and constants are evaluated before any alloc is placed; "
            "use a constant, or size the data some other way",
        )
    return undefined_symbol_error(missing, file_info, resolver.current_scope)


def _code_gen_placement_body(
    ast_nodes: Sequence[AstNode], resolver: Resolver, macro_definitions: MacroDefinitions
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
