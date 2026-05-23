"""Autofix driver + per-rule fix tests."""

from __future__ import annotations

from pathlib import Path

from a816.fluff.core import Applicability, Diagnostic, Fix, TextEdit
from a816.fluff.runner import apply_fixes, lint_text


def _diag(start: int, end: int, replacement: str, app: Applicability = Applicability.SAFE) -> Diagnostic:
    return Diagnostic(
        path=Path("<mem>"),
        line=1,
        column=1,
        code="TEST001",
        message="test",
        fix=Fix(
            edits=(TextEdit(start=start, end=end, replacement=replacement),),
            applicability=app,
            description="t",
        ),
    )


class TestApplyFixes:
    def test_single_edit_applied(self) -> None:
        text = "hello world"
        new, applied = apply_fixes(text, [_diag(6, 11, "ruff")])
        assert new == "hello ruff"
        assert len(applied) == 1

    def test_multiple_non_overlapping_edits_applied_in_reverse(self) -> None:
        text = "AAA BBB CCC"
        diags = [_diag(0, 3, "xxx"), _diag(8, 11, "zzz")]
        new, applied = apply_fixes(text, diags)
        assert new == "xxx BBB zzz"
        assert len(applied) == 2

    def test_overlapping_edit_dropped(self) -> None:
        text = "hello world"
        # Two edits that touch the same span — the later (higher start)
        # wins; the earlier-start one is dropped because it overlaps.
        diags = [_diag(0, 5, "hi"), _diag(3, 8, "QQQQQ")]
        new, applied = apply_fixes(text, diags)
        # Highest-start edit wins. The other diag's edit overlaps and is skipped.
        assert "QQQQQ" in new
        assert len(applied) == 1

    def test_unsafe_skipped_without_flag(self) -> None:
        text = "hello"
        diags = [_diag(0, 5, "world", app=Applicability.UNSAFE)]
        new, applied = apply_fixes(text, diags)
        assert new == "hello"
        assert applied == []

    def test_unsafe_applied_with_flag(self) -> None:
        text = "hello"
        diags = [_diag(0, 5, "world", app=Applicability.UNSAFE)]
        new, applied = apply_fixes(text, diags, allow_unsafe=True)
        assert new == "world"
        assert len(applied) == 1

    def test_select_limits_to_codes(self) -> None:
        text = "hello"
        diag = _diag(0, 5, "world")
        # Same diag, different code via select filter.
        new, applied = apply_fixes(text, [diag], select={"OTHER"})
        assert new == "hello"
        assert applied == []
        new, applied = apply_fixes(text, [diag], select={"TEST001"})
        assert new == "world"
        assert len(applied) == 1


class TestOrphanDocstringFix:
    def test_single_line_docstring_becomes_comment(self) -> None:
        src = '"""m"""\nmain:\n    rts\n    """note"""\n    nop\n'
        diagnostics = lint_text(src, Path("<mem>"))
        d4 = [d for d in diagnostics if d.code == "DOC004"]
        assert len(d4) == 1 and d4[0].fix is not None
        new, _ = apply_fixes(src, diagnostics)
        assert '"""note"""' not in new
        assert "    ; note\n" in new

    def test_multiline_docstring_becomes_block_of_comments(self) -> None:
        src = '"""m"""\nmain:\n    rts\n    """line a\n    line b"""\n    nop\n'
        diagnostics = lint_text(src, Path("<mem>"))
        d4 = [d for d in diagnostics if d.code == "DOC004"]
        assert len(d4) == 1 and d4[0].fix is not None
        new, _ = apply_fixes(src, diagnostics)
        # Only the module-level docstring remains in `"""...`""" form.
        assert new.count('"""') == 2
        assert "    ; line a\n" in new
        assert "    ;     line b\n" in new


class TestRedundantTypedCastFix:
    def test_strips_cast_and_keeps_field_access(self) -> None:
        src = '"""m"""\n.struct Pt { word x }\np := (0x100 as Pt)\nlda.w (p as Pt).x\n'
        diagnostics = lint_text(src, Path("<mem>"))
        s003 = [d for d in diagnostics if d.code == "S003"]
        assert len(s003) == 1
        assert s003[0].fix is not None
        new, applied = apply_fixes(src, diagnostics)
        assert "lda.w p.x\n" in new
        assert "(p as Pt)" not in new
        assert len(applied) == 1
