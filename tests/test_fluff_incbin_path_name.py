"""W0001 in `a816 check` and its `a816 fix` rewrite."""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.fluff import fluff_main, lint_text
from a816.fluff.runner import apply_fixes

ALONE = '"""M."""\n.alloc font at 0x008000 {\n    .incbin "f.bin"\n}\n'
SHARED = '"""M."""\n.alloc at 0x008000 {\n    .incbin "f.bin"\n    .db 0\n}\n'


def _hits(source: str, path: Path) -> list[str]:
    return [f"{d.line}:{d.column}" for d in lint_text(source, path) if d.code == "W0001"]


def _fixed(source: str, path: Path) -> str:
    return apply_fixes(source, lint_text(source, path))[0]


def test_check_reports_a_path_name_reference(tmp_path: Path) -> None:
    assert _hits(ALONE + ".dl f_bin\n", tmp_path / "m.s") == ["5:5"]


def test_fix_rewrites_the_start_to_the_alloc_name(tmp_path: Path) -> None:
    assert _fixed(ALONE + ".dl f_bin\n", tmp_path / "m.s").endswith(".dl font\n")


def test_fix_rewrites_the_size_to_sizeof(tmp_path: Path) -> None:
    assert _fixed(ALONE + ".dw f_bin__size\n", tmp_path / "m.s").endswith(".dw sizeof(font)\n")


def test_a_blob_sharing_its_block_is_flagged_not_fixed(tmp_path: Path) -> None:
    source = SHARED + ".dl f_bin\n"

    assert _fixed(source, tmp_path / "m.s") == source


def test_a_blob_sharing_its_block_is_still_reported(tmp_path: Path) -> None:
    assert _hits(SHARED + ".dl f_bin\n", tmp_path / "m.s") == ["6:5"]


def test_check_sees_an_imported_blob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a816.toml").write_text('module-paths = ["."]\n', encoding="utf-8")
    (tmp_path / "assets.s").write_text(ALONE, encoding="utf-8")
    (tmp_path / "main.s").write_text('"""Main."""\n.import "assets"\n.dl f_bin\n', encoding="utf-8")

    fluff_main(["check", "main.s"])

    assert " W0001 " in capsys.readouterr().out


def test_fix_rewrites_a_reference_to_an_imported_blob(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a816.toml").write_text('module-paths = ["."]\n', encoding="utf-8")
    (tmp_path / "assets.s").write_text(ALONE, encoding="utf-8")
    (tmp_path / "main.s").write_text('"""Main."""\n.import "assets"\n.dl f_bin\n', encoding="utf-8")

    fluff_main(["fix", "main.s"])

    assert (tmp_path / "main.s").read_text(encoding="utf-8").endswith(".dl font\n")


def test_noqa_silences_it(tmp_path: Path) -> None:
    assert _hits(ALONE + ".dl f_bin ; noqa: W0001\n", tmp_path / "m.s") == []


def test_a_warning_alone_does_not_fail_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Like the build, which still succeeds: ff4 and BL keep a green `make check`."""
    (tmp_path / "m.s").write_text(ALONE + ".dl f_bin\n", encoding="utf-8")

    assert fluff_main(["check", str(tmp_path / "m.s")]) == 0


def test_the_warning_is_still_printed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "m.s").write_text(ALONE + ".dl f_bin\n", encoding="utf-8")

    fluff_main(["check", str(tmp_path / "m.s")])

    assert " W0001 " in capsys.readouterr().out
