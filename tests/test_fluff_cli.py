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


def test_fluff_missing_path_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Missing path reports on stderr and exits 2 without aborting other inputs."""
    exit_code = fluff_main(["format", str(tmp_path / "missing.s")])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "path not found" in captured.err
    assert str(tmp_path / "missing.s") in captured.err


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


def test_fluff_stdin_format(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import io

    raw = "start:\nlda #0\nrts\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(raw))
    exit_code = fluff_main(["format", "-"])
    captured = capsys.readouterr()
    assert exit_code == 0
    # Formatter indents instructions and keeps labels flush-left.
    assert "start:" in captured.out
    assert "    lda #0" in captured.out


def test_fluff_stdin_check_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    already_formatted = "start:\n    lda #0\n    rts\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(already_formatted))
    assert fluff_main(["format", "--check", "-"]) == 0


def test_fluff_stdin_check_dirty(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("start:\nlda #0\n"))
    exit_code = fluff_main(["format", "--check", "-"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Would reformat <stdin>" in captured.err


def test_fluff_multi_file_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`format --check a.s b.s` reports both, exits 1 if either is dirty."""
    a = tmp_path / "a.s"
    b = tmp_path / "b.s"
    a.write_text("start:\n    lda #0\n    rts\n", encoding="utf-8")  # already formatted
    b.write_text("start:\nlda #0 ; bad\n", encoding="utf-8")  # dirty

    exit_code = fluff_main(["format", "--check", str(a), str(b)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Would reformat" in captured.out
    assert str(b) in captured.out


def test_fluff_file_and_dir_mix(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`format src/ extra.s` formats both the directory and the extra file."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "in_dir.s").write_text("start:\nlda #0\n", encoding="utf-8")
    extra = tmp_path / "extra.s"
    extra.write_text("foo:\n    rts\n", encoding="utf-8")

    exit_code = fluff_main(["format", str(src), str(extra)])
    captured = capsys.readouterr()
    assert exit_code == 0
    # Write happened — check `in_dir.s` got rewritten with formatted output.
    assert "start:" in (src / "in_dir.s").read_text(encoding="utf-8")
    assert "Formatted" in captured.out


def test_fluff_dedupe_overlapping_paths(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Passing a file inside a directory alongside the directory dedupes it."""
    src = tmp_path / "src"
    src.mkdir()
    f = src / "kerning.s"
    f.write_text("foo:\nlda #0\n", encoding="utf-8")

    exit_code = fluff_main(["format", "--check", str(src), str(f)])
    captured = capsys.readouterr()
    assert exit_code == 1
    # File should appear once in the report, not twice.
    assert captured.out.count(str(f)) == 1
    assert "Would reformat 1 file(s)." in captured.out


def test_fluff_stdin_mixed_with_path_errors(tmp_path: Path) -> None:
    f = tmp_path / "f.s"
    f.write_text("foo:\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        fluff_main(["format", "-", str(f)])


def test_fluff_continues_after_missing_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A missing path reports + sets exit 2 but doesn't skip the next input."""
    real = tmp_path / "real.s"
    real.write_text("foo:\n    rts\n", encoding="utf-8")
    missing = tmp_path / "missing.s"

    exit_code = fluff_main(["format", "--check", str(missing), str(real)])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "path not found" in captured.err
    # Real file was scanned (already formatted, so no warning) — combined exit
    # remains 2 from the missing input.
    assert "All files are formatted correctly." in captured.out
