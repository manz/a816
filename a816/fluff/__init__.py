"""`a816 fluff`: lint + format toolchain. Public API re-exports."""

from __future__ import annotations

from a816.fluff.cli import fluff_legacy_main, fluff_main
from a816.fluff.core import MAX_LINE_LENGTH, Diagnostic, LintContext, Rule
from a816.fluff.runner import RULES, all_rule_codes, lint_file, lint_text

__all__ = [
    "MAX_LINE_LENGTH",
    "RULES",
    "Diagnostic",
    "LintContext",
    "Rule",
    "all_rule_codes",
    "fluff_legacy_main",
    "fluff_main",
    "lint_file",
    "lint_text",
]
