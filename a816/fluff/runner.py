"""Lint engine entry points + RULES registry."""

from __future__ import annotations

from pathlib import Path

from a816.config import discover_a816_config
from a816.error_codes import E_PARSER_UNEXPECTED_TOKEN
from a816.fluff.core import (
    Applicability,
    Diagnostic,
    LintContext,
    Rule,
    TextEdit,
    build_noqa_map,
    is_suppressed,
)
from a816.fluff.rules_doc import (
    CommentInsteadOfDocstring,
    DocstringAlignment,
    MisplacedDocstring,
    MissingModuleDocstring,
    MissingTargetDocstring,
    OrphanDocstring,
    RedundantCommentAndDocstring,
)
from a816.fluff.rules_flow import UndefinedIfName
from a816.fluff.rules_naming import ConstantNaming, LabelNaming
from a816.fluff.rules_opcode import RedundantOpcodeSizeSuffix
from a816.fluff.rules_structural import ImportOutsidePrelude, NestedPlacementInAlloc
from a816.fluff.rules_style import (
    LineTooLong,
    RedundantTypedCast,
    RepeatedInlineCast,
    UnknownStructTypeCast,
)
from a816.fluff.rules_upgrade import IncbinPathName, StarEqualToAllocAt
from a816.parse.mzparser import A816Parser, ParserResult

RULES: list[Rule] = [
    MissingModuleDocstring(),
    MissingTargetDocstring(),
    MisplacedDocstring(),
    OrphanDocstring(),
    CommentInsteadOfDocstring(),
    RedundantCommentAndDocstring(),
    DocstringAlignment(),
    LineTooLong(),
    LabelNaming(),
    ConstantNaming(),
    UnknownStructTypeCast(),
    RedundantTypedCast(),
    RepeatedInlineCast(),
    StarEqualToAllocAt(),
    RedundantOpcodeSizeSuffix(),
    NestedPlacementInAlloc(),
    ImportOutsidePrelude(),
    IncbinPathName(),
    UndefinedIfName(),
]
Rule.registry = {rule.code: rule for rule in RULES}


def all_rule_codes() -> list[str]:
    return sorted(Rule.registry)


def lint_text(
    text: str,
    path: Path,
    *,
    include_paths: list[Path] | None = None,
    module_paths: list[Path] | None = None,
    report_parse_errors: bool = False,
) -> list[Diagnostic]:
    """Run every registered rule against in-memory source text.

    `report_parse_errors` adds each parse error as a diagnostic (`check`);
    off by default since the LSP publishes them on its own.

    `include_paths` is forwarded to the parser so `.include` directives
    resolve the same way they do under the assembler. `module_paths`
    lets struct-type rules (S001) follow `.import` chains so cross-
    module struct names resolve. The fluff CLI fills both from the
    project's `a816.toml`; callers without a config can pass them
    explicitly.
    """
    result = A816Parser.parse_as_ast(text, str(path), include_paths=include_paths)
    parse_failed = bool(result.error)
    nodes = None if parse_failed else list(result.nodes)
    ctx = LintContext(
        path=path,
        text=text,
        nodes=nodes,
        parse_failed=parse_failed,
        module_paths=module_paths,
        include_paths_for_lookup=include_paths,
    )

    # AST rules skip a failed parse; without these the file passed `check`.
    # The LSP reports parse errors itself, so it leaves them out.
    diagnostics: list[Diagnostic] = _parse_diagnostics(result, path) if report_parse_errors else []
    for rule in RULES:
        if not rule.applies_to(ctx):
            continue
        diagnostics.extend(rule.run(ctx))

    noqa_map = build_noqa_map(text)
    return [d for d in diagnostics if not is_suppressed(d.line, d.code, noqa_map)]


def _parse_diagnostics(result: ParserResult, path: Path) -> list[Diagnostic]:
    errors = result.parse_errors or ([result.parse_error] if result.parse_error else [])
    return [
        Diagnostic(path, error.line + 1, error.column + 1, error.code or str(E_PARSER_UNEXPECTED_TOKEN), error.message)
        for error in errors
    ]


def apply_fixes(
    text: str,
    diagnostics: list[Diagnostic],
    *,
    allow_unsafe: bool = False,
    select: set[str] | None = None,
) -> tuple[str, list[Diagnostic]]:
    """Return `(new_text, applied_diagnostics)` after applying fixes.

    Filters:
      * `allow_unsafe=False` (default) skips fixes marked UNSAFE.
      * `select` limits to a specific rule-code subset; `None` means all.

    Edits are applied in reverse-offset order so an earlier edit's
    replacement length cannot invalidate a later edit's offsets.
    Overlapping edits are dropped silently — the lint runs again after
    fixes converge so the next pass can produce non-overlapping edits.
    """
    candidates = _collect_fix_candidates(diagnostics, allow_unsafe=allow_unsafe, select=select)
    # Sort by descending start; for equal starts, descending end so the
    # widest replacement at a given anchor wins the overlap rejection.
    candidates.sort(key=lambda pair: (pair[1].start, pair[1].end), reverse=True)
    return _apply_candidate_edits(text, candidates)


def _collect_fix_candidates(
    diagnostics: list[Diagnostic],
    *,
    allow_unsafe: bool,
    select: set[str] | None,
) -> list[tuple[Diagnostic, TextEdit]]:
    candidates: list[tuple[Diagnostic, TextEdit]] = []
    for diag in diagnostics:
        if not _candidate_passes_filters(diag, allow_unsafe=allow_unsafe, select=select):
            continue
        assert diag.fix is not None  # guarded by `_candidate_passes_filters`
        for edit in diag.fix.edits:
            candidates.append((diag, edit))
    return candidates


def _candidate_passes_filters(diag: Diagnostic, *, allow_unsafe: bool, select: set[str] | None) -> bool:
    if diag.fix is None:
        return False
    if select is not None and diag.code not in select:
        return False
    return allow_unsafe or diag.fix.applicability is not Applicability.UNSAFE


def _apply_candidate_edits(text: str, candidates: list[tuple[Diagnostic, TextEdit]]) -> tuple[str, list[Diagnostic]]:
    new_text = text
    applied: list[Diagnostic] = []
    last_kept_start: int | None = None
    seen_diags: set[int] = set()
    for diag, edit in candidates:
        if last_kept_start is not None and edit.end > last_kept_start:
            continue  # overlaps a previously kept edit
        new_text = new_text[: edit.start] + edit.replacement + new_text[edit.end :]
        last_kept_start = edit.start
        if id(diag) not in seen_diags:
            applied.append(diag)
            seen_diags.add(id(diag))
    return new_text, applied


def lint_file(path: Path) -> list[Diagnostic]:
    """Run every registered rule against a single source file.

    Discovers the project's `a816.toml` by walking up from `path` and
    forwards its `include-paths` + `module-paths` to the parser and
    lint context respectively.
    """
    config = discover_a816_config(path)
    include_paths = config.include_paths if config is not None else None
    module_paths = config.module_paths if config is not None else None
    return lint_text(
        path.read_text(encoding="utf-8"),
        path,
        include_paths=include_paths,
        module_paths=module_paths,
        report_parse_errors=True,
    )
