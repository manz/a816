"""Parse-time AST rewrites that lower legacy syntax onto the new
section model.

Today: `*= ADDR / body...` runs collapse into anonymous
`.alloc at ADDR { body... }` nodes. The legacy `*=` parses to
`CodePositionAstNode`, which the codegen still handles via
`CodePositionNode` (pc-mutation). After this pass, no
`CodePositionAstNode` survives at any nesting level a codegen
walker visits, so the standard module pipeline handles every
source uniformly.

`@=` (`CodeRelocationAstNode`) is intentionally left alone: it has
different semantics (emit at A, bind labels as if at B) and there
is no equivalent `.alloc` shape yet.

Why post-parse rather than at scan or parse: keeps `*=` visible to
fluff (UP001 suggests the rewrite to source) and to the formatter
(canonical output preserves user-written shape). The desugar fires
only on the path that feeds codegen.
"""

from __future__ import annotations

from a816.parse.ast.nodes import (
    AllocAstNode,
    AstNode,
    BlockAstNode,
    CodePositionAstNode,
    CodeRelocationAstNode,
    CompoundAstNode,
    ForAstNode,
    IfAstNode,
    ImportAstNode,
    IncludeAstNode,
    MacroAstNode,
    ScopeAstNode,
)
from a816.parse.ast.placement import is_placement_boundary


def desugar_star_eq(nodes: list[AstNode]) -> list[AstNode]:
    """Collapse `*= ADDR / body...` runs into anonymous `.alloc at`.

    Operates recursively on container bodies (`.scope`, `.macro`,
    `.if` / `.else`, `.for`). Returns a fresh list; inputs are not
    mutated, but child container bodies get rewritten in place
    (the AST is mutable enough that rebuilding every block would
    just copy references for no benefit).
    """
    return _desugar_sequence(nodes)


def _desugar_sequence(nodes: list[AstNode]) -> list[AstNode]:
    """Walk a flat node list; group each `*=` plus its body run.

    Body run = nodes from the `*=` (exclusive) up to the next
    `CodePositionAstNode` or `AllocAstNode` at the same level
    (exclusive), or end of list. Each run becomes one anonymous
    `AllocAstNode(at_address=expr, body=BlockAstNode([...]))`.
    """
    out: list[AstNode] = []
    i = 0
    n = len(nodes)
    while i < n:
        node = nodes[i]
        if isinstance(node, CodePositionAstNode):
            j = i + 1
            while j < n and not is_placement_boundary(nodes[j]):
                j += 1
            candidate_body = nodes[i + 1 : j]
            # Conservative bail-out for body content the alloc-body
            # measure pass mis-handles:
            #
            # - `@=` (`CodeRelocationAstNode`) splits emit address from
            #   label-binding address through `resolver.reloc`. The
            #   measure pass walks `pc_after`, which for
            #   `RelocationAddressNode` jumps PC to the relocation
            #   target — measurement diverges from emit by megabytes.
            # - `.import` (`ImportAstNode`) materialises to a
            #   `LinkedModuleNode` whose `pc_after` semantics depend on
            #   the imported module's relocatable/pinned state and on
            #   delta computations that only stabilise across multiple
            #   resolve passes. Re-entering that machinery from inside
            #   a synthetic alloc body produces multi-megabyte body
            #   sizes during measure.
            #
            # In both cases, leave the `*=` raw so the direct-mode
            # codegen path keeps working. Pure-data and pure-opcode
            # `*=` bodies still lift cleanly.
            if any(isinstance(child, (CodeRelocationAstNode, ImportAstNode)) for child in candidate_body):
                out.append(node)
                i += 1
                continue
            body_nodes = [_desugar_in_place(child) for child in candidate_body]
            block = BlockAstNode(body=body_nodes, file_info=node.file_info)
            alloc = AllocAstNode(
                name=None,
                pool_name=None,
                body=block,
                file_info=node.file_info,
                at_address=node.expression,
                at_size=None,
            )
            out.append(alloc)
            i = j
            continue
        out.append(_desugar_in_place(node))
        i += 1
    return out


def _desugar_in_place(node: AstNode) -> AstNode:
    """Recurse into container bodies so nested `*=` collapses too."""
    if isinstance(node, ScopeAstNode):
        node.body.body = _desugar_sequence(list(node.body.body))
    elif isinstance(node, MacroAstNode):
        node.block.body = _desugar_sequence(list(node.block.body))
    elif isinstance(node, IfAstNode):
        node.block.body = _desugar_sequence(list(node.block.body))
        if node.else_block is not None:
            node.else_block.body = _desugar_sequence(list(node.else_block.body))
    elif isinstance(node, ForAstNode):
        if isinstance(node.body, (BlockAstNode, CompoundAstNode)):
            node.body.body = _desugar_sequence(list(node.body.body))
    elif isinstance(node, CompoundAstNode):
        # Bare `{ ... }` at top level parses to `CompoundAstNode`.
        node.body = _desugar_sequence(list(node.body))
    elif isinstance(node, AllocAstNode):
        node.body.body = _desugar_sequence(list(node.body.body))
    elif isinstance(node, IncludeAstNode):
        # `.include` splices a sub-AST into the importer at codegen
        # time. Lift any `*=` inside that sub-AST too so the detector
        # (which walks `included_nodes`) sees a clean tree.
        node.included_nodes = _desugar_sequence(list(node.included_nodes))
    return node
