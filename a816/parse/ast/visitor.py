"""AST traversal helper.

Centralizes the recursion logic that was duplicated across module_builder
and codegen. Walks every node in pre-order, descending into the standard
container attributes (body, block, else_block, included_nodes).
"""

from collections.abc import Iterator

from a816.parse.ast.nodes import AstNode, BlockAstNode, CompoundAstNode


def walk(nodes: list[AstNode], *, into_includes: bool = True) -> Iterator[AstNode]:
    """Yield every node in the AST in pre-order.

    `into_includes=False` stops at `.include` nodes: per-file consumers
    (lint rules) see the directive but not the spliced file's nodes,
    whose positions belong to another file.
    """
    for node in nodes:
        yield node
        yield from _walk_children(node, into_includes)


def _walk_children(node: AstNode, into_includes: bool) -> Iterator[AstNode]:
    body = getattr(node, "body", None)
    if isinstance(body, BlockAstNode | CompoundAstNode):
        yield from walk(body.body, into_includes=into_includes)
    elif isinstance(body, list):
        yield from walk(body, into_includes=into_includes)

    block = getattr(node, "block", None)
    if isinstance(block, BlockAstNode | CompoundAstNode):
        yield from walk(block.body, into_includes=into_includes)

    else_block = getattr(node, "else_block", None)
    if isinstance(else_block, BlockAstNode | CompoundAstNode):
        yield from walk(else_block.body, into_includes=into_includes)

    included = getattr(node, "included_nodes", None)
    if into_includes and isinstance(included, list):
        yield from walk(included, into_includes=into_includes)
