"""End-to-end `a816 fix` CLI tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.fluff.cli import _parse_select, fluff_main


def test_parse_select_flattens_repeats_and_comma_lists() -> None:
    assert _parse_select(["DOC003,DOC004", "UP001"]) == {"DOC003", "DOC004", "UP001"}


def test_parse_select_returns_none_when_unset() -> None:
    assert _parse_select(None) is None


def test_parse_select_uppercases_and_drops_blanks() -> None:
    assert _parse_select(["doc003, ,up001"]) == {"DOC003", "UP001"}


class TestFixCli:
    # Private label `_main` keeps DOC002 (missing docstring) quiet so
    # the only remaining hit is DOC004, which the autofix handles.
    SRC = '"""m"""\n_main:\n    rts\n    """orphan"""\n    nop\n'

    def _write(self, tmp_path: Path) -> Path:
        target = tmp_path / "patch.s"
        target.write_text(self.SRC, encoding="utf-8")
        return target

    def test_fix_writes_safe_fix_in_place(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        target = self._write(tmp_path)
        rc = fluff_main(["fix", str(target)])
        assert rc == 0
        new = target.read_text(encoding="utf-8")
        assert '"""orphan"""' not in new
        assert "; orphan" in new
        out = capsys.readouterr().out
        assert "fixed DOC004" in out

    def test_fix_check_exits_nonzero_when_changes_pending(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        target = self._write(tmp_path)
        rc = fluff_main(["fix", "--check", str(target)])
        assert rc == 1
        assert target.read_text(encoding="utf-8") == self.SRC  # unchanged
        out = capsys.readouterr().out
        assert "Would fix" in out

    def test_fix_diff_prints_unified_diff_without_writing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        target = self._write(tmp_path)
        rc = fluff_main(["fix", "--diff", str(target)])
        # diff mode reports remaining (unfixed) hits as exit non-zero when any survive.
        assert rc in (0, 1)
        assert target.read_text(encoding="utf-8") == self.SRC
        out = capsys.readouterr().out
        assert "@@" in out
        assert '-    """orphan"""' in out

    def test_fix_unsafe_fixes_flag_applies_unsafe(self, tmp_path: Path) -> None:
        src = '"""m"""\n*=0x008000\n.db 0xEA\n'
        target = tmp_path / "u.s"
        target.write_text(src, encoding="utf-8")
        rc = fluff_main(["fix", "--unsafe-fixes", "--select", "UP001", str(target)])
        assert rc == 0
        new = target.read_text(encoding="utf-8")
        assert "*=" not in new
        assert ".alloc at 0x008000" in new

    def test_fix_select_skips_other_rules(self, tmp_path: Path) -> None:
        # File has both DOC004 (safe) and UP001 (unsafe). --select DOC004 should only
        # apply DOC004; UP001 is skipped entirely.
        src = '"""m"""\n*=0x008000\nmain:\n    rts\n    """orphan"""\n    nop\n'
        target = tmp_path / "s.s"
        target.write_text(src, encoding="utf-8")
        rc = fluff_main(["fix", "--select", "DOC004", str(target)])
        assert rc != 0  # UP001 still reported (unfixed)
        new = target.read_text(encoding="utf-8")
        # DOC004 applied, UP001 not.
        assert '"""orphan"""' not in new
        assert "*=0x008000" in new

    def test_fix_missing_path_reports_and_returns_2(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        rc = fluff_main(["fix", str(tmp_path / "does-not-exist.s")])
        assert rc == 2
        assert "path not found" in capsys.readouterr().err

    def test_fix_clean_file_reports_no_changes(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        clean = tmp_path / "clean.s"
        clean.write_text('"""ok"""\n', encoding="utf-8")
        rc = fluff_main(["fix", str(clean)])
        # No diagnostics means no fixes; exit 0.
        assert rc == 0
        out = capsys.readouterr().out
        assert "Fixed 0 file(s)" in out

    def test_fix_check_on_clean_file_reports_clean(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        clean = tmp_path / "clean.s"
        clean.write_text('"""ok"""\n', encoding="utf-8")
        rc = fluff_main(["fix", "--check", str(clean)])
        assert rc == 0
        assert "No fixes needed" in capsys.readouterr().out
