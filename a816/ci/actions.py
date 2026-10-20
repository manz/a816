"""Step outputs and job environment, the way GitHub Actions takes them.

Outside Actions (no GITHUB_OUTPUT / GITHUB_ENV), they are printed instead,
so a step can be tried locally.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from pathlib import Path


class Actions:
    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self.environ = os.environ if environ is None else environ

    def get(self, name: str, default: str = "") -> str:
        return self.environ.get(name, "") or default

    def output(self, name: str, value: str) -> None:
        """A step output (`steps.<id>.outputs.<name>`)."""
        self._append("GITHUB_OUTPUT", name, value)

    def export(self, name: str, value: str) -> None:
        """An environment variable for the job's later steps."""
        self._append("GITHUB_ENV", name, value)

    def _append(self, file_var: str, name: str, value: str) -> None:
        if "\n" in value:
            delimiter = f"A816_EOF_{secrets.token_hex(8)}"
            entry = f"{name}<<{delimiter}\n{value}\n{delimiter}\n"
        else:
            entry = f"{name}={value}\n"
        path = self.environ.get(file_var)
        if not path:
            print(entry, end="")
            return
        with Path(path).open("a", encoding="utf-8") as file:
            file.write(entry)
