"""End-to-end `a816 fix` CLI tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.fluff.cli import fluff_main


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
        assert f"Fixed 1 hit(s) in {target} (DOC004)" in out

    def test_fix_check_exits_nonzero_when_changes_pending(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        target = self._write(tmp_path)
        rc = fluff_main(["fix", "--check", str(target)])
        assert rc == 1
        assert target.read_text(encoding="utf-8") == self.SRC  # unchanged
        out = capsys.readouterr().out
        assert f"Would fix 1 hit(s) in {target} (DOC004)" in out

    def test_fix_diff_prints_unified_diff_without_writing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        target = self._write(tmp_path)
        rc = fluff_main(["fix", "--diff", str(target)])
        # rs3: a pending fix fails the run, as under `format --diff`.
        assert rc == 1
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

    _MIXED = '"""m"""\n*=0x008000\nmain:\n    rts\n    """orphan"""\n    nop\n'

    def test_fix_select_skips_other_rules(self, tmp_path: Path) -> None:
        # File has both DOC004 (safe) and UP001 (unsafe). --select DOC004 should only
        # apply DOC004; UP001 is skipped entirely.
        target = tmp_path / "s.s"
        target.write_text(self._MIXED, encoding="utf-8")
        rc = fluff_main(["fix", "--select", "DOC004", str(target)])
        assert rc == 0  # UP001 is outside the selection: not a remaining hit
        new = target.read_text(encoding="utf-8")
        # DOC004 applied, UP001 not.
        assert '"""orphan"""' not in new
        assert "*=0x008000" in new

    def test_fix_select_narrows_the_report(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        target = tmp_path / "s.s"
        target.write_text(self._MIXED, encoding="utf-8")
        fluff_main(["fix", "--diff", "--select", "DOC", str(target)])
        assert "UP001" not in capsys.readouterr().out

    def test_fix_select_takes_a_prefix(self, tmp_path: Path) -> None:
        target = tmp_path / "s.s"
        target.write_text(self._MIXED, encoding="utf-8")
        fluff_main(["fix", "--select", "doc", str(target)])
        assert '"""orphan"""' not in target.read_text(encoding="utf-8")

    def test_fix_select_unknown_code_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        target = self._write(tmp_path)
        rc = fluff_main(["fix", "--select", "Z9", str(target)])
        assert (rc, "E0516" in capsys.readouterr().err) == (2, True)

    def test_fix_diff_summary_says_would(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        target = self._write(tmp_path)
        fluff_main(["fix", "--diff", str(target)])
        captured = capsys.readouterr()
        # dq6: "0 hit(s) remain" read as if they were already gone.
        assert ("Fixed" in captured.out, "1 file(s) would be fixed, 0 hit(s) would remain" in captured.err) == (
            False,
            True,
        )

    def test_fix_diff_on_a_clean_file_exits_0(self, tmp_path: Path) -> None:
        clean = tmp_path / "clean.s"
        clean.write_text('"""ok"""\n', encoding="utf-8")
        assert fluff_main(["fix", "--diff", str(clean)]) == 0

    def test_fix_diff_is_plain_when_piped(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        target = self._write(tmp_path)
        fluff_main(["fix", "--diff", str(target)])
        assert "\033[" not in capsys.readouterr().out

    def test_fix_diff_is_coloured_on_request(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FORCE_COLOR", "1")
        monkeypatch.delenv("NO_COLOR", raising=False)
        target = self._write(tmp_path)
        fluff_main(["fix", "--diff", str(target)])
        assert "\033[" in capsys.readouterr().out

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
