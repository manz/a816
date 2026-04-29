"""AST traversal helper.

Centralizes the recursion logic that was duplicated across module_builder
and codegen. Walks every node in pre-order, descending into the standard
container attributes (body, block, else_block, included_nodes).
"""

from collections.abc import Iterator

from a816.parse.ast.nodes import AstNode, BlockAstNode, CompoundAstNode


def walk(nodes: list[AstNode]) -> Iterator[AstNode]:
    """Yield every node in the AST in pre-order."""
    for node in nodes:
        yield node
        yield from _walk_children(node)


def _walk_children(node: AstNode) -> Iterator[AstNode]:
    body = getattr(node, "body", None)
    if isinstance(body, BlockAstNode | CompoundAstNode):
        yield from walk(body.body)
    elif isinstance(body, list):
        yield from walk(body)

    block = getattr(node, "block", None)
    if isinstance(block, BlockAstNode | CompoundAstNode):
        yield from walk(block.body)

    else_block = getattr(node, "else_block", None)
    if isinstance(else_block, BlockAstNode | CompoundAstNode):
        yield from walk(else_block.body)

    included = getattr(node, "included_nodes", None)
    if isinstance(included, list):
        yield from walk(included)
