"""Codegen base: types, generators registry, entry point.

Each submodule registers its `generate_*` functions into `generators` at
import time; `_code_gen` dispatches per-node by `node.kind`. `code_gen`
is the public entry: it threads a fresh `macro_definitions` dict and
delegates to `_code_gen`.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from a816.parse.ast.nodes import AstNode
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


def _code_gen(ast_nodes: list[AstNode], resolver: Resolver, macro_definitions: MacroDefinitions) -> list[NodeProtocol]:
    code = []
    for node in ast_nodes:
        file_info = _get_file_info(node)
        generator = generators.get(node.kind)
        if generator:
            code += generator(node, resolver, macro_definitions, file_info)
        else:
            raise RuntimeError("Left over node", node)
    return code


def code_gen(ast_nodes: list[AstNode], resolver: Resolver) -> GenNodes:
    macro_definitions: MacroDefinitions = {}
    return _code_gen(ast_nodes, resolver, macro_definitions)
