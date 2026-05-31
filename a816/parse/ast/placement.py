"""Classify which AST nodes act as placement boundaries.

A placement boundary opens (or implies the open of) a new section.
Body grouping for a leading `*= ADDR` runs from the directive
through every subsequent sibling that is NOT a boundary — i.e.
opcodes, data, labels, includes, declarations, etc. — and stops
at the next boundary (or end of the enclosing block).

The fluff UP001 autofix consumes this classifier to agree on
where one `*=` body ends and the next placement begins.

Boundary set:
- `CodePositionAstNode` (`*= ADDR`) — opens a pinned section.
- `AllocAstNode` (`.alloc … at ADDR` / `.alloc … in POOL`) —
  opens a pinned or pooled section explicitly.
- `RelocateAstNode` (`.relocate SYMBOL OLD into POOL { … }`) —
  opens a pooled section that supersedes a prior placement.

`@=` (`CodeRelocationAstNode`) is intentionally NOT a boundary:
it's a label-binding shift, not a new section. Bytes after `@=`
keep emitting at the current `*=` PC; only label resolution
moves. Grouping it as a sibling would orphan those bytes from
the alloc body the `*=` opened.

Everything else is non-boundary: metadata (`.pool` / `.reclaim` /
`.export` / `.struct` / `.macro` / typed binds), body content
(opcodes, data, `.incbin`, `.text`, labels), or splice directives
(`.import` / `.include`) whose own bodies recurse through the
desugar.
"""

from __future__ import annotations

from a816.parse.ast.nodes import (
    AllocAstNode,
    AstNode,
    CodePositionAstNode,
    CompoundAstNode,
    IfAstNode,
    RelocateAstNode,
    ScopeAstNode,
)

_BOUNDARY_TYPES: tuple[type[AstNode], ...] = (
    CodePositionAstNode,
    AllocAstNode,
    RelocateAstNode,
)


def _child_bodies(node: AstNode) -> list[list[AstNode]]:
    """All inner bodies a container exposes to placement-recursion.

    `.if` returns the then-block plus the else-block (when present),
    `.scope` and bare `{ }` return their single body, and anything
    else returns no bodies. Centralising the shape match here keeps
    the recursion in `_node_holds_placement` linear instead of
    branching per container kind.
    """
    match node:
        case CompoundAstNode():
            return [list(node.body)]
        case ScopeAstNode():
            return [list(node.body.body)]
        case IfAstNode():
            bodies = [list(node.block.body)]
            if node.else_block is not None:
                bodies.append(list(node.else_block.body))
            return bodies
        case _:
            return []


def _node_holds_placement(node: AstNode) -> bool:
    if isinstance(node, _BOUNDARY_TYPES):
        return True
    return any(_block_contains_placement(body) for body in _child_bodies(node))


def _block_contains_placement(body: list[AstNode]) -> bool:
    return any(_node_holds_placement(child) for child in body)


def is_placement_boundary(node: AstNode) -> bool:
    """Direct placement directives end an in-progress `*=` body run.

    Container nodes (bare `{ ... }`, `.if`, `.scope`, `.for`, `.macro`)
    that themselves contain a placement directive ALSO count as
    boundaries: the container opens its own address-flow context with
    `*= INNER_ADDR { ... }` siblings, so swallowing it into the outer
    `*=`'s body would nest the inner allocs inside the outer alloc —
    they then emit at the wrong physical address. The container must
    remain a sibling of the outer `*=` so its inner allocs route
    through their own placement.

    Containers with no inner placement (label scopes, data tables,
    conditional opcode blocks) keep grouping with the outer `*=` body.
    """
    return _node_holds_placement(node)
