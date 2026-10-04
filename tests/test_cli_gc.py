"""The build path collects gen 0 less often, and only while it runs."""

from __future__ import annotations

import argparse
import gc

import pytest

from a816 import cli


def test_build_raises_gen0_threshold_and_restores_it(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[int, int, int]] = []

    def record(args: argparse.Namespace) -> int:
        seen.append(gc.get_threshold())
        return 0

    monkeypatch.setattr(cli, "_assemble", record)
    before = gc.get_threshold()

    assert cli._run_assemble(argparse.Namespace()) == 0

    assert seen == [(cli._BUILD_GC_THRESHOLD, *before[1:])]
    assert gc.get_threshold() == before


def test_threshold_is_restored_when_the_build_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(args: argparse.Namespace) -> int:
        raise RuntimeError("build failed")

    monkeypatch.setattr(cli, "_assemble", boom)
    before = gc.get_threshold()

    with pytest.raises(RuntimeError):
        cli._run_assemble(argparse.Namespace())

    assert gc.get_threshold() == before
