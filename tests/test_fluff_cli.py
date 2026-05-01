from pathlib import Path

import pytest

from a816.fluff import fluff_main


def run_fluff(args: list[str]) -> int:
    return fluff_main(args)


def test_fluff_formats_single_file(tmp_path: Path) -> None:
    source = tmp_path / "test.s"
    source.write_text("start:\n    lda #0 ; inline\n", encoding="utf-8")

    exit_code = run_fluff(["format", str(source)])
    assert exit_code == 0

    formatted = source.read_text(encoding="utf-8")
    assert "lda #0" in formatted
    assert "; inline" in formatted
    assert "    lda #0" in formatted


def test_fluff_recurses_directory(tmp_path: Path) -> None:
    dir_path = tmp_path / "src"
    dir_path.mkdir()
    (dir_path / "a.s").write_text("start:\n    lda #0 ; comment\n", encoding="utf-8")
    (dir_path / "b.i").write_text("macro:\n    lda #0 ; comment\n", encoding="utf-8")

    exit_code = run_fluff(["format", str(dir_path)])
    assert exit_code == 0

    for file in ["a.s", "b.i"]:
        content = (dir_path / file).read_text(encoding="utf-8")
        assert "; comment" in content


def test_fluff_ignores_non_matching_files(tmp_path: Path) -> None:
    file_path = tmp_path / "data.txt"
    file_path.write_text("raw content", encoding="utf-8")

    exit_code = run_fluff(["format", str(tmp_path)])
    assert exit_code == 0
    assert file_path.read_text(encoding="utf-8") == "raw content"


def test_fluff_missing_path_errors(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        fluff_main(["format", str(tmp_path / "missing.s")])


def test_fluff_check_mode(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    file_path = tmp_path / "file.s"
    file_path.write_text("start:\n    lda #0 ; comment\n", encoding="utf-8")

    exit_code = fluff_main(["format", "--check", str(file_path)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Would reformat" in captured.out

    formatted = fluff_main(["format", str(file_path)])
    captured = capsys.readouterr()
    assert formatted == 0
    assert "Formatted 1 file(s)." in captured.out

    exit_code = fluff_main(["format", "--check", str(file_path)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "All files are formatted correctly." in captured.out


def test_fluff_diff_mode(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    file_path = tmp_path / "file.s"
    original = "start:\n    lda #0 ; comment\n"
    file_path.write_text(original, encoding="utf-8")

    exit_code = fluff_main(["format", "--diff", str(file_path)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "\x1b[36m@@" in captured.out
    assert "\x1b[31m-" in captured.out
    assert "\x1b[32m+" in captured.out
    assert "Would reformat 1 file(s)." in captured.out
    assert file_path.read_text(encoding="utf-8") == original

    # After formatting, diff should pass without output
    fluff_main(["format", str(file_path)])
    capsys.readouterr()
    exit_code = fluff_main(["format", "--diff", str(file_path)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "All files are formatted correctly." in captured.out
