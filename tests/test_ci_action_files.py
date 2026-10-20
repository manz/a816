"""The `actions/*/action.yml` files call `a816 ci` steps and flags that exist.

The actions are thin wrappers; this catches a renamed step or flag before a
game repo's CI does.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

from a816.ci.cli import _parser

ACTIONS = Path(__file__).resolve().parent.parent / "actions"
_CALL = re.compile(r"uv run --no-sync a816 ci ([a-z][a-z-]*)(.*)")
_ARGS = re.compile(r"args=\((.*)\)")


def _calls(text: str) -> list[list[str]]:
    """Each `a816 ci STEP ...` command, folded (`>-`) lines joined back, `${args[@]}` expanded."""
    joined = re.sub(r"\n\s+--", " --", text)
    array = _ARGS.search(joined)
    expanded = joined.replace('"${args[@]}"', array.group(1) if array else "")
    return [[step, *shlex.split(rest)] for step, rest in _CALL.findall(expanded)]


def _action_calls() -> list[tuple[str, list[str]]]:
    return [
        (path.parent.name, call) for path in sorted(ACTIONS.glob("*/action.yml")) for call in _calls(path.read_text())
    ]


def test_every_action_has_a_file() -> None:
    assert sorted(p.parent.name for p in ACTIONS.glob("*/action.yml")) == [
        "build",
        "decrypt-rom",
        "lint",
        "nightly",
        "pr-artifact",
        "release",
        "seal",
        "setup",
    ]


@pytest.mark.parametrize(("action", "call"), _action_calls(), ids=lambda value: str(value))
def test_the_call_parses(action: str, call: list[str]) -> None:
    args = [re.sub(r"\$\{?\w+\}?", "x", arg) for arg in call]
    assert _parser().parse_args(args).step == args[0], action
