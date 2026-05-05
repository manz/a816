"""Docstring-coverage lint rules for `a816 fluff check`.

Rules:
- DOC001 — every source file should open with a leading docstring describing
  what the module is for.
- DOC002 — every public top-level macro, scope, or label should be documented.
  Names starting with a single underscore are considered private and skipped.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from a816.parse.ast.nodes import (
    AstNode,
    CommentAstNode,
    DocstringAstNode,
    LabelAstNode,
    MacroAstNode,
    ScopeAstNode,
)
from a816.parse.mzparser import MZParser


@dataclass(frozen=True)
class Diagnostic:
    """One lint hit. `path:line:col code message`, ruff-style."""

    path: Path
    line: int  # 1-based for human output
    column: int  # 1-based
    code: str
    message: str

    def format(self) -> str:
        return f"{self.path}:{self.line}:{self.column} {self.code} {self.message}"


def _is_public(name: str) -> bool:
    return not name.startswith("_")


def _node_position(node: AstNode) -> tuple[int, int]:
    pos = getattr(node.file_info, "position", None)
    if pos is None:
        return 1, 1
    return pos.line + 1, pos.column + 1


def _check_module_docstring(path: Path, nodes: list[AstNode]) -> Diagnostic | None:
    """DOC001: first non-comment top-level node must be a docstring."""
    for node in nodes:
        if isinstance(node, CommentAstNode):
            continue
        if isinstance(node, DocstringAstNode):
            return None
        return Diagnostic(
            path=path,
            line=1,
            column=1,
            code="DOC001",
            message="module is missing a leading docstring",
        )
    # Empty file: skip — nothing to document.
    return None


def _kind_label(node: AstNode) -> str:
    if isinstance(node, MacroAstNode):
        return "macro"
    if isinstance(node, ScopeAstNode):
        return "scope"
    if isinstance(node, LabelAstNode):
        return "label"
    return "symbol"


def _check_public_docstrings(path: Path, nodes: list[AstNode]) -> list[Diagnostic]:
    """DOC002: each public macro/scope/label needs an attached docstring.

    The leading file docstring (DOC001's target) is consumed as the module
    description and does not count as a macro/scope/label's docstring.
    """
    hits: list[Diagnostic] = []
    pending_doc = False
    module_doc_consumed = False
    for node in nodes:
        if isinstance(node, CommentAstNode):
            continue
        if isinstance(node, DocstringAstNode):
            if not module_doc_consumed:
                module_doc_consumed = True
                continue
            pending_doc = True
            continue
        # Any non-comment node implies the module-doc slot is closed.
        module_doc_consumed = True
        if isinstance(node, MacroAstNode | ScopeAstNode | LabelAstNode):
            name = getattr(node, "name", None) or getattr(node, "label", "") or ""
            if _is_public(str(name)):
                has_doc = pending_doc or bool(getattr(node, "docstring", None))
                if not has_doc:
                    line, col = _node_position(node)
                    hits.append(
                        Diagnostic(
                            path=path,
                            line=line,
                            column=col,
                            code="DOC002",
                            message=f"public {_kind_label(node)} '{name}' is missing a docstring",
                        )
                    )
        pending_doc = False
    return hits


def lint_file(path: Path) -> list[Diagnostic]:
    """Run all DOC* rules against a single source file."""
    text = path.read_text(encoding="utf-8")
    result = MZParser.parse_as_ast(text, str(path))
    if result.error:
        return []  # leave parse errors for the format pass to surface
    nodes = list(result.nodes)
    diagnostics: list[Diagnostic] = []
    module_hit = _check_module_docstring(path, nodes)
    if module_hit is not None:
        diagnostics.append(module_hit)
    diagnostics.extend(_check_public_docstrings(path, nodes))
    return diagnostics
