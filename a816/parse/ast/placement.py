"""Classify which AST nodes act as placement boundaries.

A placement boundary opens (or implies the open of) a new section.
Body grouping for a leading `*= ADDR` runs from the directive
through every subsequent sibling that is NOT a boundary — i.e.
opcodes, data, labels, includes, declarations, etc. — and stops
at the next boundary (or end of the enclosing block).

Both the parse-time `desugar_star_eq` pass and the fluff UP001
autofix consume this classifier so they agree on where one `*=`
body ends and the next placement begins.

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
    RelocateAstNode,
)

_BOUNDARY_TYPES: tuple[type[AstNode], ...] = (
    CodePositionAstNode,
    AllocAstNode,
    RelocateAstNode,
)


def is_placement_boundary(node: AstNode) -> bool:
    return isinstance(node, _BOUNDARY_TYPES)
