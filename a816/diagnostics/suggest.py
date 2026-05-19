"""Did-you-mean suggestions for undefined symbols.

The closest-match search runs over the user-visible names in the active
scope chain (symbols + labels), filters by an edit-distance threshold,
and returns the top candidates. Designed to be a one-shot call from an
error-handling site — no caching, no global state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid circular import at runtime; Scope only needed for typing
    from a816.symbols import Scope


_MAX_SUGGESTIONS = 3


def _levenshtein(a: str, b: str) -> int:
    """Standard iterative Levenshtein distance — O(len(a) * len(b))."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            current[j] = min(
                current[j - 1] + 1,        # insertion
                previous[j] + 1,           # deletion
                previous[j - 1] + cost,    # substitution
            )
        previous = current
    return previous[-1]


def _collect_visible_names(scope: Scope) -> set[str]:
    """Walk parent chain and gather symbol + label names a user might mean."""
    names: set[str] = set()
    current: Scope | None = scope
    while current is not None:
        names.update(current.symbols.keys())
        names.update(current.labels.keys())
        current = current.parent
    return names


def closest_matches(target: str, scope: Scope) -> list[str]:
    """Return up to 3 visible names within an adaptive edit-distance budget.

    Budget scales with `target` length so a typo in a 4-char name doesn't
    pull in unrelated 8-char names. Empty result means "no good guess —
    don't add a hint" so callers can omit the did-you-mean line entirely.
    """
    if not target:
        return []
    budget = max(1, min(len(target) // 2, 3))
    candidates: list[tuple[int, str]] = []
    for name in _collect_visible_names(scope):
        if name == target:
            continue
        distance = _levenshtein(target, name)
        if distance <= budget:
            candidates.append((distance, name))
    candidates.sort()
    return [name for _, name in candidates[:_MAX_SUGGESTIONS]]


def did_you_mean_hint(target: str, scope: Scope) -> str | None:
    """Format the suggestions as a `hint:` payload, or None when nothing fits."""
    matches = closest_matches(target, scope)
    if not matches:
        return None
    if len(matches) == 1:
        return f"did you mean `{matches[0]}`?"
    formatted = ", ".join(f"`{m}`" for m in matches)
    return f"did you mean one of: {formatted}?"
