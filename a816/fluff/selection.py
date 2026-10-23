"""Which lint rules run: `[lint]` in a816.toml, overridden by `check --select/--ignore`.

Codes match by prefix, as in ruff: `N` is every naming rule, `DOC00` the
first docstring rules. An opt-in rule (`F002`) runs only when `select` names
its exact code. Only lint rule codes are filtered; a parse error
always shows.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from a816.config import A816Config
from a816.error_codes import E_CONFIG_UNKNOWN_LINT_RULE
from a816.exceptions import A816ConfigError
from a816.fluff.core import Diagnostic, Rule
from a816.fluff.runner import all_rule_codes


def _matches(code: str, prefixes: Iterable[str]) -> bool:
    return any(code.startswith(prefix) for prefix in prefixes)


def unknown_codes(prefixes: Iterable[str]) -> list[str]:
    """The entries that name no rule, not even as a prefix."""
    return [prefix for prefix in prefixes if not _matches_any_rule(prefix)]


def _matches_any_rule(prefix: str) -> bool:
    return any(code.startswith(prefix) for code in all_rule_codes())


def parse_codes(raw: Sequence[str] | None) -> tuple[str, ...] | None:
    """Flatten `--select A,B --select C` into `(A, B, C)`; None when the flag is absent."""
    if raw is None:
        return None
    return tuple(code.strip().upper() for chunk in raw for code in chunk.split(",") if code.strip())


@dataclass(frozen=True)
class RuleSelection:
    """`select` empty means every rule; `ignore` then drops some."""

    select: tuple[str, ...] = ()
    ignore: tuple[str, ...] = ()

    @classmethod
    def of(
        cls, config: A816Config | None, select: tuple[str, ...] | None = None, ignore: tuple[str, ...] | None = None
    ) -> RuleSelection:
        """The project's `[lint]`, each list replaced by its flag when given."""
        if config is not None:
            bad = unknown_codes((*config.lint_select, *config.lint_ignore))
            if bad:
                raise A816ConfigError(
                    E_CONFIG_UNKNOWN_LINT_RULE, f"[lint] names no rule: {', '.join(bad)}", config.config_path
                )
        base_select = config.lint_select if config is not None else ()
        base_ignore = config.lint_ignore if config is not None else ()
        return cls(
            select=base_select if select is None else select,
            ignore=base_ignore if ignore is None else ignore,
        )

    def allows(self, code: str) -> bool:
        rule = Rule.registry.get(code)
        if rule is None:
            return True
        if rule.opt_in and code not in self.select:
            return False
        if self.select and not _matches(code, self.select):
            return False
        return not _matches(code, self.ignore)

    def filter(self, diagnostics: Iterable[Diagnostic]) -> list[Diagnostic]:
        return [diag for diag in diagnostics if self.allows(diag.code)]
