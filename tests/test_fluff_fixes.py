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


class TestDropCommentBlockFix:
    def test_doc006_drops_leading_comment_block(self) -> None:
        src = '"""m"""\n; banner one\n; banner two\n.macro setup() {\n    """real docstring"""\n    ldx.w #0\n}\n'
        diagnostics = lint_text(src, Path("<mem>"))
        d6 = [d for d in diagnostics if d.code == "DOC006"]
        assert len(d6) == 1 and d6[0].fix is not None
        new, _ = apply_fixes(src, diagnostics)
        assert "; banner one" not in new
        assert "; banner two" not in new
        assert '"""real docstring"""' in new
        # Macro still in place.
        assert ".macro setup()" in new


class TestCommentToDocstringFix:
    def test_doc005_unsafe_rewraps_comment_block_into_body(self) -> None:
        src = '"""m"""\n; banner one\n; banner two\n.macro setup() {\n    ldx.w #0\n}\n'
        diagnostics = lint_text(src, Path("<mem>"))
        d5 = [d for d in diagnostics if d.code == "DOC005"]
        assert len(d5) == 1 and d5[0].fix is not None
        # Safe-only run leaves the source untouched.
        same, _ = apply_fixes(src, diagnostics)
        assert same == src
        # With unsafe allowed, the comment block migrates into the body.
        new, applied = apply_fixes(src, diagnostics, allow_unsafe=True)
        assert "; banner one" not in new
        assert "; banner two" not in new
        assert '"""\n    banner one\n    banner two\n    """' in new
        assert ".macro setup()" in new


class TestMisplacedDocstringFix:
    def test_doc003_moves_docstring_inside_macro_body(self) -> None:
        src = '"""m"""\n"""Setup the counter."""\n.macro setup() {\n    ldx.w #0\n}\n'
        diagnostics = lint_text(src, Path("<mem>"))
        d3 = [d for d in diagnostics if d.code == "DOC003"]
        assert len(d3) == 1 and d3[0].fix is not None
        new, _ = apply_fixes(src, diagnostics)
        # Original above-target docstring is gone.
        assert new.count('"""Setup the counter."""') == 1
        # It now sits as the body's first statement, indented.
        assert '.macro setup() {\n    """Setup the counter."""' in new


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
