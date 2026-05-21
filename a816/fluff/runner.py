"""Lint engine entry points + RULES registry."""

from __future__ import annotations

from pathlib import Path

from a816.config import discover_a816_config
from a816.fluff.core import (
    Diagnostic,
    LintContext,
    Rule,
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
from a816.fluff.rules_naming import ConstantNaming, LabelNaming
from a816.fluff.rules_style import (
    LineTooLong,
    RedundantTypedCast,
    RepeatedInlineCast,
    UnknownStructTypeCast,
)
from a816.parse.mzparser import A816Parser

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
) -> list[Diagnostic]:
    """Run every registered rule against in-memory source text.

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

    diagnostics: list[Diagnostic] = []
    for rule in RULES:
        if not rule.applies_to(ctx):
            continue
        diagnostics.extend(rule.run(ctx))

    noqa_map = build_noqa_map(text)
    return [d for d in diagnostics if not is_suppressed(d.line, d.code, noqa_map)]


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
    )
