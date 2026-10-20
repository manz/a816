"""A recording stand-in for the commands `a816 ci` runs (gh, gpg, git)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from a816.ci.actions import Actions
from a816.ci.process import CiError


class FakeRun:
    """Answers commands by their leading words; anything unanswered fails like a missing release."""

    def __init__(self, answers: dict[tuple[str, ...], bytes] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[tuple[list[str], bytes | None]] = []

    def __call__(self, argv: Sequence[str], stdin: bytes | None) -> bytes:
        self.calls.append((list(argv), stdin))
        for prefix, answer in self.answers.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return answer
        raise CiError(f"{argv[0]} failed")

    def commands(self) -> list[list[str]]:
        return [argv for argv, _ in self.calls]


def actions_in(tmp_path: Path, **environ: str) -> Actions:
    """Actions whose GITHUB_OUTPUT and GITHUB_ENV are files under `tmp_path`."""
    return Actions(
        {"GITHUB_OUTPUT": str(tmp_path / "output"), "GITHUB_ENV": str(tmp_path / "env"), **environ},
    )


def written(tmp_path: Path, name: str) -> str:
    path = tmp_path / name
    return path.read_text(encoding="utf-8") if path.exists() else ""
