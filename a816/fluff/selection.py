"""Which lint rules run: `[lint]` in a816.toml, overridden by `check --select/--ignore`.

Codes match by prefix, as in ruff: `N` is every naming rule, `DOC00` the
first docstring rules. An opt-in rule (`F002`) runs only when `select` names
its exact code. `[lint.per-file-ignores]` drops codes in the files its globs
match. Only lint rule codes are filtered; a parse error always shows.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from a816.config import A816Config
from a816.error_codes import E_CONFIG_UNKNOWN_LINT_RULE
from a816.exceptions import A816ConfigError
from a816.fluff.core import Diagnostic, Rule
from a816.fluff.project import matches_any
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
    """`select` empty means every rule; `ignore` then drops some, and
    `per_file` drops codes in the files under `root` its globs match."""

    select: tuple[str, ...] = ()
    ignore: tuple[str, ...] = ()
    per_file: tuple[tuple[str, tuple[str, ...]], ...] = ()
    root: Path | None = None

    @classmethod
    def of(
        cls, config: A816Config | None, select: tuple[str, ...] | None = None, ignore: tuple[str, ...] | None = None
    ) -> RuleSelection:
        """The project's `[lint]`, each list replaced by its flag when given."""
        if config is not None:
            per_file_codes = [code for _, codes in config.lint_per_file_ignores for code in codes]
            bad = unknown_codes((*config.lint_select, *config.lint_ignore, *per_file_codes))
            if bad:
                raise A816ConfigError(
                    E_CONFIG_UNKNOWN_LINT_RULE, f"[lint] names no rule: {', '.join(bad)}", config.config_path
                )
        base_select = config.lint_select if config is not None else ()
        base_ignore = config.lint_ignore if config is not None else ()
        return cls(
            select=base_select if select is None else select,
            ignore=base_ignore if ignore is None else ignore,
            per_file=config.lint_per_file_ignores if config is not None else (),
            root=config.root.resolve() if config is not None else None,
        )

    def allows(self, code: str, path: Path | None = None) -> bool:
        """`code` runs, in `path` when given (per-file ignores apply)."""
        rule = Rule.registry.get(code)
        if rule is None:
            return True
        if rule.opt_in and code not in self.select:
            return False
        if self.select and not _matches(code, self.select):
            return False
        if _matches(code, self.ignore):
            return False
        return path is None or not _matches(code, self._ignored_in(path))

    def _ignored_in(self, path: Path) -> list[str]:
        """The per-file codes whose glob matches `path`."""
        if self.root is None:
            return []
        source = path.resolve()
        return [code for pattern, codes in self.per_file if matches_any(source, self.root, [pattern]) for code in codes]

    def filter(self, diagnostics: Iterable[Diagnostic]) -> list[Diagnostic]:
        return [diag for diag in diagnostics if self.allows(diag.code, diag.path)]
