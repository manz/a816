"""Running the external tools the CI steps need (gpg, gh, git)."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence


class CiError(Exception):
    """A CI step that cannot go on; `a816 ci` prints it and exits 1."""


Run = Callable[[Sequence[str], bytes | None], bytes]
"""Runs a command with optional stdin and returns its stdout; raises CiError on failure."""


def run_command(argv: Sequence[str], stdin: bytes | None = None) -> bytes:
    try:
        completed = subprocess.run(list(argv), input=stdin, stdout=subprocess.PIPE, check=False)
    except FileNotFoundError as error:
        raise CiError(f"{argv[0]} is not installed") from error
    if completed.returncode != 0:
        raise CiError(f"{' '.join(argv[:2])} failed with exit code {completed.returncode}")
    return completed.stdout
