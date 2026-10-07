"""A build collects cycles rarely, and gives the host its GC thresholds back.

The build allocates millions of short-lived nodes; the default thresholds
spent about 15% of a cold build scanning them. The LSP hosts builds in a
long-lived process, so the thresholds must be restored, failure included.
"""

from __future__ import annotations

import gc
from pathlib import Path

import pytest

from a816.module_builder import _BUILD_GC_THRESHOLD, ModuleBuilder
from a816.object_file import ObjectFile


def _builder(root: Path) -> ModuleBuilder:
    return ModuleBuilder(module_paths=[root], include_paths=[root], output_dir=root / "obj")


def test_the_build_runs_with_raised_thresholds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[int, ...]] = []

    def record(self: ModuleBuilder, *_args: object) -> ObjectFile:
        seen.append(gc.get_threshold())
        return ObjectFile([], [])

    monkeypatch.setattr(ModuleBuilder, "_build", record)

    _builder(tmp_path).build(tmp_path / "main.s")

    assert seen == [_BUILD_GC_THRESHOLD]


def test_the_thresholds_come_back_after_a_build(tmp_path: Path) -> None:
    before = gc.get_threshold()
    main = tmp_path / "main.s"
    main.write_text(".alloc code at 0x008000 {\nmain:\n    rts\n}\n", encoding="utf-8")

    _builder(tmp_path).build(main)

    assert gc.get_threshold() == before


def test_the_thresholds_come_back_after_a_failed_build(tmp_path: Path) -> None:
    before = gc.get_threshold()
    main = tmp_path / "main.s"
    main.write_text("    lda.w #undefined_symbol\n", encoding="utf-8")
    builder = _builder(tmp_path)

    with pytest.raises(RuntimeError):
        builder.build(main)

    assert gc.get_threshold() == before
