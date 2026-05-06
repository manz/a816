"""Lint rules for `a816 fluff check`.

Rules:
- DOC001 — every source file should open with a leading docstring describing
  what the module is for.
- DOC002 — every public top-level macro, scope, or label should be documented.
  Names starting with a single underscore are considered private and skipped.
- E501 — source line exceeds the maximum allowed length (120 characters).
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

MAX_LINE_LENGTH = 120


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


def _public_target_name(node: AstNode) -> str | None:
    """Return the public name of a documentable node, or None when private/N/A."""
    if not isinstance(node, MacroAstNode | ScopeAstNode | LabelAstNode):
        return None
    raw = getattr(node, "name", None) or getattr(node, "label", "") or ""
    name = str(raw)
    return name if _is_public(name) else None


def _missing_doc_diagnostic(path: Path, node: AstNode, name: str, pending_doc: bool) -> Diagnostic | None:
    if pending_doc or bool(getattr(node, "docstring", None)):
        return None
    line, col = _node_position(node)
    return Diagnostic(
        path=path,
        line=line,
        column=col,
        code="DOC002",
        message=f"public {_kind_label(node)} '{name}' is missing a docstring",
    )


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
            if module_doc_consumed:
                pending_doc = True
            else:
                module_doc_consumed = True
            continue
        module_doc_consumed = True
        name = _public_target_name(node)
        if name is not None:
            hit = _missing_doc_diagnostic(path, node, name, pending_doc)
            if hit is not None:
                hits.append(hit)
        pending_doc = False
    return hits


def _check_line_length(path: Path, text: str) -> list[Diagnostic]:
    """E501: flag every source line longer than `MAX_LINE_LENGTH`."""
    hits: list[Diagnostic] = []
    for index, line in enumerate(text.splitlines(), start=1):
        length = len(line)
        if length > MAX_LINE_LENGTH:
            hits.append(
                Diagnostic(
                    path=path,
                    line=index,
                    column=MAX_LINE_LENGTH + 1,
                    code="E501",
                    message=f"line too long ({length} > {MAX_LINE_LENGTH} characters)",
                )
            )
    return hits


def lint_file(path: Path) -> list[Diagnostic]:
    """Run all lint rules against a single source file."""
    text = path.read_text(encoding="utf-8")
    diagnostics: list[Diagnostic] = _check_line_length(path, text)
    result = MZParser.parse_as_ast(text, str(path))
    if result.error:
        return diagnostics  # leave parse errors for the format pass to surface
    nodes = list(result.nodes)
    module_hit = _check_module_docstring(path, nodes)
    if module_hit is not None:
        diagnostics.append(module_hit)
    diagnostics.extend(_check_public_docstrings(path, nodes))
    return diagnostics
