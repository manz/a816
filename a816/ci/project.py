"""`a816 ci lint|build|seal`: the steps that work on the project itself."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from a816 import bps
from a816.bps_cli import UNSEALED_SUFFIX
from a816.ci.actions import Actions
from a816.ci.process import CiError
from a816.config import discover_a816_config

Main = Callable[[list[str]], int]


def run_lint(paths: Sequence[str], *, check_format: bool, fluff: Main) -> int:
    """`a816 format --diff` then `a816 check`; both run, either failing fails."""
    status = 0
    if check_format and fluff(["format", "--diff", *paths]) != 0:
        print("sources are not formatted: run a816 format")
        status = 1
    if fluff(["check", *paths]) != 0:
        status = 1
    return status


def entrypoint(entry: str, start: Path) -> str:
    if entry:
        return entry
    config = discover_a816_config(start)
    if config is None or config.entrypoint is None:
        raise CiError("no entry given and no entrypoint in a816.toml")
    return str(config.entrypoint)


def run_build(actions: Actions, build: Main, *, entry: str, outputs: Sequence[str], defines: Sequence[str]) -> int:
    """`a816 build` with one `-o` per output; `debug-info` lists the `.adbg` written."""
    if not outputs:
        raise CiError("no outputs: give at least one path")
    argv = [entrypoint(entry, Path.cwd())]
    for output in outputs:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        argv += ["-o", output]
    for define in defines:
        argv += ["-D", define]
    status = build(argv)
    if status != 0:
        return status
    debug_info = [f"{output}.adbg" for output in outputs if Path(f"{output}.adbg").is_file()]
    actions.output("debug-info", "\n".join(debug_info))
    return 0


def seal_patch(patch: Path, rom: bytes) -> Path:
    if patch.name.endswith(".bps" + UNSEALED_SUFFIX):
        sealed = patch.with_suffix("")
        # The path is the workflow author's own `patches` input, as with `a816 bps seal -o`.
        sealed.write_bytes(bps.seal(patch.read_bytes(), rom))  # NOSONAR
        return sealed
    raise CiError(f"{patch}: only .bps.unsealed patches can be sealed")


def run_seal(actions: Actions, *, rom: Path, patches: Sequence[str]) -> int:
    base = rom.read_bytes()
    sealed = [seal_patch(Path(patch), base) for patch in patches]
    for path in sealed:
        print(f"sealed {path}")
    actions.output("sealed", "\n".join(str(path) for path in sealed))
    return 0
