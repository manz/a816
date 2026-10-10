"""Codegen base: types, generators registry, entry point.

Each submodule registers its `generate_*` functions into `generators` at
import time; `_code_gen` dispatches per-node by `node.kind`. `code_gen`
is the public entry: it threads a fresh `macro_definitions` dict and
delegates to `_code_gen`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

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

if TYPE_CHECKING:
    from a816.incbin_names import PathName

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
    if name.endswith("__size"):
        # `blob.__size` is what `sizeof(blob)` reads; `path_bin__size` is an
        # `.incbin` path-name size. Both are bound when the bytes are laid out.
        spelled = f"sizeof({name.removesuffix('.__size')})" if name.endswith(".__size") else name
        return NodeError(
            f"`{spelled}` is only known after layout, and this needs it while expanding",
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
    code = _code_gen(ast_nodes, resolver, macro_definitions)
    _warn_path_names(ast_nodes, resolver)
    return code


def _record_path_names(resolver: Resolver, own: dict[str, PathName], checked: dict[str, PathName]) -> None:
    """Carry the unit's path names to the linker, which warns on a reference
    that binds to one from a module whose compile never saw it."""
    from a816.object_file import PathNameRecord

    writer = resolver.context.object_writer
    if writer is None:
        return
    writer.path_names = [
        PathNameRecord(name, found.file_path, found.seen_from_importer().hint())
        for name, found in own.items()
        if not found.ambiguous
    ]
    writer.checked_path_names = sorted(checked)


def _warn_path_names(ast_nodes: Sequence[AstNode], resolver: Resolver) -> None:
    """W0001 on each reference to a path-derived `.incbin` name, this unit's
    or an import's; run after codegen, once every import has been read."""
    from a816.error_codes import E_CODEGEN_AMBIGUOUS_PATH_NAME, W_INCBIN_PATH_NAME
    from a816.incbin_names import path_names, references
    from a816.parse.nodes.errors import format_node_warning

    own = path_names(ast_nodes)
    names = {**resolver.imported_path_names, **own}
    _record_path_names(resolver, own, names)
    for token, name in references(ast_nodes, names):
        if name.ambiguous:
            raise NodeError(
                f"`{name.name}` is bound by more than one `.incbin`, so it names whichever came first",
                token,
                code=str(E_CODEGEN_AMBIGUOUS_PATH_NAME),
                hint="label each blob and use the labels",
            )
        message = f"`{name.name}` is named after the asset path {name.file_path!r}"
        logger.warning(format_node_warning(message, token, code=str(W_INCBIN_PATH_NAME), hint=name.hint()))
