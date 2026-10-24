"""F001 / F002: a module-private name nothing in its translation unit references.

No other module can see a `_` name, so one its own module never uses is dead.
F001 covers constants, macros, structs, pools, `.label` and `.reserve`; F002
covers code labels and scopes, opt-in since a bare `_label:` is often a marker
for the debugger or xdds.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.fluff import lint_text
from a816.fluff.runner import apply_fixes
from a816.fluff.selection import RuleSelection

HEAD = '"""M."""\n'


def _hits(source: str, path: Path = Path("main.s")) -> list[tuple[str, int, int, str]]:
    return [(d.code, d.line, d.column, d.message.split("`")[1]) for d in lint_text(source, path) if d.code[0] == "F"]


def _names(source: str, path: Path = Path("main.s")) -> list[str]:
    return [name for _, _, _, name in _hits(source, path)]


def test_an_unused_private_constant_is_flagged_at_its_name() -> None:
    assert _hits(HEAD + "_K = 3\n") == [("F001", 2, 1, "_K")]


def test_a_constant_an_opcode_uses_is_quiet() -> None:
    assert _names(HEAD + "_K = 3\nlda #_K\n") == []


def test_a_typed_bind_is_a_constant() -> None:
    assert _names(HEAD + "_p := 0x7E2000\n") == ["_p"]


def test_an_unused_private_macro_is_flagged() -> None:
    assert _names(HEAD + ".macro _m(x) {\n    lda #x\n}\n") == ["_m"]


def test_a_macro_invocation_uses_the_macro() -> None:
    assert _names(HEAD + ".macro _m(x) {\n    lda #x\n}\n_m(1)\n") == []


def test_an_unused_private_struct_is_flagged() -> None:
    assert _names(HEAD + ".struct _T {\n    word a\n}\n") == ["_T"]


def test_sizeof_a_field_uses_the_struct() -> None:
    assert _names(HEAD + ".struct _T {\n    word a\n}\nlda #sizeof(_T.a)\n") == []


def test_an_unused_private_pool_is_flagged() -> None:
    assert _names(HEAD + ".pool _p {\n    range 0x7E2000 0x7E2100\n    bss\n}\n") == ["_p"]


def test_an_alloc_in_the_pool_uses_it() -> None:
    source = HEAD + ".pool _p {\n    range 0x7E2000 0x7E2100\n    bss\n}\n.alloc buf in _p {\n    .res 4\n}\n"

    assert _names(source) == []


def test_an_unused_label_directive_is_flagged() -> None:
    assert _names(HEAD + ".label _hook = 0x008000\n") == ["_hook"]


def test_an_unused_reservation_is_flagged() -> None:
    source = HEAD + ".struct T {\n    word a\n}\n.reserve _r as T in wram\n"

    assert _names(source) == ["_r"]


def test_a_field_access_uses_the_reservation() -> None:
    source = HEAD + ".struct T {\n    word a\n}\n.reserve _r as T in wram\nlda.w _r.a\n"

    assert _names(source) == []


def test_a_private_alloc_is_a_placement_handle_not_a_name() -> None:
    assert _names(HEAD + ".alloc _a in code {\n    rts\n}\n") == []


def test_public_names_are_never_flagged() -> None:
    assert _names(HEAD + "K = 3\n.macro m() {\n    rts\n}\n") == []


def test_an_unused_code_label_is_f002() -> None:
    assert _hits(HEAD + "_loop:\n    rts\n") == [("F002", 2, 1, "_loop")]


def test_a_branch_uses_the_label() -> None:
    assert _names(HEAD + "_loop:\n    bne _loop\n") == []


def test_a_qualified_reference_uses_the_scoped_label() -> None:
    assert _names(HEAD + ".scope s {\n_x:\n    rts\n}\njsr s._x\n") == []


def test_a_text_interpolation_uses_the_name() -> None:
    assert _names(HEAD + '_V = 1\n.text "${_V}"\n') == []


def test_a_debug_interpolation_uses_the_name() -> None:
    assert _names(HEAD + '_W = 1\n.debug "{_W + 1}"\n') == []


def test_a_for_variable_is_not_a_module_name() -> None:
    assert _names(HEAD + ".for _i := 0, 3 {\n    .db 0\n}\n") == []


def test_a_name_defined_in_both_branches_is_reported_once() -> None:
    source = HEAD + ".if X {\n    _K = 1\n} .else {\n    _K = 2\n}\n"

    assert _names(source) == ["_K"]


def test_noqa_silences_the_hit() -> None:
    assert _names(HEAD + "_K = 3 ; noqa: F001\n") == []


@pytest.fixture
def unit(tmp_path: Path) -> Path:
    (tmp_path / "a816.toml").write_text('entrypoint = "main.s"\n', encoding="utf-8")
    return tmp_path


def _lint(path: Path) -> list[str]:
    return _names(path.read_text(encoding="utf-8"), path)


def test_a_use_in_an_included_file_counts(unit: Path) -> None:
    (unit / "inc.i").write_text("lda #_K\n", encoding="utf-8")
    main = unit / "main.s"
    main.write_text(HEAD + '_K = 3\n.include "inc.i"\n', encoding="utf-8")

    assert _lint(main) == []


def test_a_name_an_includer_uses_is_quiet_in_the_include(unit: Path) -> None:
    inc = unit / "inc.i"
    inc.write_text('"""Shared."""\n_K = 3\n', encoding="utf-8")
    (unit / "main.s").write_text(HEAD + '.include "inc.i"\nlda #_K\n', encoding="utf-8")

    assert _lint(inc) == []


def test_a_name_no_includer_uses_is_reported_in_the_include_only(unit: Path) -> None:
    inc = unit / "inc.i"
    inc.write_text('"""Shared."""\n_K = 3\n', encoding="utf-8")
    main = unit / "main.s"
    main.write_text(HEAD + '.include "inc.i"\n', encoding="utf-8")

    assert (_lint(inc), _lint(main)) == (["_K"], [])


def test_a_sibling_unit_of_the_include_counts(unit: Path) -> None:
    """`main.s` includes both: a use in `b.i` keeps `a.i`'s name alive."""
    a = unit / "a.i"
    a.write_text('"""A."""\n_K = 3\n', encoding="utf-8")
    (unit / "b.i").write_text('"""B."""\nlda #_K\n', encoding="utf-8")
    (unit / "main.s").write_text(HEAD + '.include "a.i"\n.include "b.i"\n', encoding="utf-8")

    assert _lint(a) == []


def test_default_selection_leaves_f002_out() -> None:
    assert not RuleSelection().allows("F002")


def test_a_prefix_does_not_select_f002() -> None:
    assert not RuleSelection(select=("F",)).allows("F002")


def test_the_exact_code_selects_f002() -> None:
    assert RuleSelection(select=("F002",)).allows("F002")


def test_f001_runs_by_default() -> None:
    assert RuleSelection().allows("F001")


def _fixed(source: str) -> str:
    text, _ = apply_fixes(source, lint_text(source, Path("main.s")), allow_unsafe=True, select={"F001", "F002"})
    return text


def test_the_fix_deletes_a_constant_line() -> None:
    assert _fixed(HEAD + "_K = 3\nlda #1\n") == HEAD + "lda #1\n"


def test_the_fix_deletes_a_whole_macro() -> None:
    assert _fixed(HEAD + ".macro _m(x) {\n    lda #x\n}\nrts\n") == HEAD + "rts\n"


def test_the_fix_deletes_a_label_line() -> None:
    assert _fixed(HEAD + "    lda #1\n_loop:\n    rts\n") == HEAD + "    lda #1\n    rts\n"


def test_the_fix_is_unsafe() -> None:
    (diag,) = [d for d in lint_text(HEAD + "_K = 3\n", Path("main.s")) if d.code == "F001"]

    assert diag.fix is not None
    assert diag.fix.applicability.value == "unsafe"


def test_fix_select_reaches_the_opt_in_rule(tmp_path: Path) -> None:
    from a816.fluff.cli import fluff_main

    target = tmp_path / "patch.s"
    target.write_text(HEAD + "    nop\n_dead:\n    rts\n", encoding="utf-8")
    fluff_main(["fix", "--select", "F002", "--unsafe-fixes", str(target)])

    assert target.read_text(encoding="utf-8") == HEAD + "    nop\n    rts\n"


def _fix_of(source: str) -> object:
    (diag,) = [d for d in lint_text(source, Path("main.s")) if d.code[0] == "F"]
    return diag.fix


def test_no_fix_for_a_label_with_code_on_its_line() -> None:
    assert _fix_of(HEAD + "_dead: rts\n") is None


def test_no_fix_for_a_constant_continuing_below() -> None:
    assert _fix_of(HEAD + "_K = (1 +\n    2)\n") is None


def test_the_fix_unwraps_a_scope_keeping_its_body() -> None:
    source = HEAD + ".scope _s {\n    lda #1\n_x:\n    bra _x\n}\n"

    assert _fixed(source) == HEAD + "{\n    lda #1\n_x:\n    bra _x\n}\n"


def _assembled(source: str) -> bytes:
    import io

    from a816.program import Program
    from a816.writers import SFCWriter

    buffer = io.BytesIO()
    Program().assemble_string_with_emitter(source, "main.s", SFCWriter(buffer))
    return buffer.getvalue()


def test_an_unwrapped_scope_assembles_to_the_same_bytes() -> None:
    source = HEAD + "*= 0x008000\n.scope _s {\n    lda #1\n_x:\n    bra _x\n}\n"

    assert _assembled(_fixed(source)) == _assembled(source)


def test_no_fix_for_a_reservation() -> None:
    source = HEAD + ".pool p { bss\n    range 0x7E2000 0x7E2FFF\n}\n.reserve _r 4 in p\n"

    assert _fix_of(source) is None


def test_the_fix_takes_the_comment_attached_above() -> None:
    source = HEAD + "lda #1\n\n; Step size.\n; In bytes.\n_K = 3\n\nrts\n"

    assert _fixed(source) == HEAD + "lda #1\n\nrts\n"


def test_a_comment_after_a_blank_line_stays() -> None:
    source = HEAD + "; Section.\n\n_K = 3\nrts\n"

    assert _fixed(source) == HEAD + "; Section.\n\nrts\n"


def test_the_fix_leaves_no_blank_line_run() -> None:
    source = HEAD + "lda #1\n\n\n_K = 3\n\n\nrts\n"

    assert _fixed(source) == HEAD + "lda #1\n\n\nrts\n"


def test_the_fix_leaves_no_trailing_blank_lines() -> None:
    source = HEAD + "lda #1\n\n_K = 3\n"

    assert _fixed(source) == HEAD + "lda #1\n"


def test_a_fixed_file_passes_the_formatter() -> None:
    from a816.formatter.core import A816Formatter

    source = HEAD + "lda #1\n\n\n; K.\n_K = 3\n\n\n.macro _m() {\n    nop\n}\n\n\nrts\n"
    fixed = _fixed(source)

    assert A816Formatter().format_text(fixed) == fixed


def test_an_edited_file_replaces_its_cache_entries(unit: Path) -> None:
    import os

    from a816.fluff import rules_unused

    shared = unit / "shared.i"
    shared.write_text('"""Shared."""\nlda #_K\n', encoding="utf-8")
    main = unit / "main.s"
    main.write_text(HEAD + '_K = 3\n.include "shared.i"\n', encoding="utf-8")
    lint_text(main.read_text(encoding="utf-8"), main)
    for step in (1, 2):
        shared.write_text(shared.read_text(encoding="utf-8") + f"; edit {step}\n", encoding="utf-8")
        os.utime(shared, ns=(step * 10**9, step * 10**9))
        lint_text(main.read_text(encoding="utf-8"), main)

    assert [key for key in rules_unused._TOKENS if key == str(shared.resolve())] == [str(shared.resolve())]
    assert len([key for key in rules_unused._EDGES if key[0] == str(shared.resolve())]) == 1


def test_no_fix_for_a_label_with_a_docstring() -> None:
    assert _fix_of(HEAD + '    nop\n_dead:\n"""\n    Doc.\n"""\n    rts\n') is None


def test_the_label_fix_keeps_the_comment_above() -> None:
    source = HEAD + "    lda #1\n    bne _right\n; Left side.\n_left:\n    nop\n_right:\n    rts\n"

    assert _fixed(source) == HEAD + "    lda #1\n    bne _right\n; Left side.\n    nop\n_right:\n    rts\n"


@pytest.mark.parametrize("boundary", ["rts", "rtl", "rti", "jmp.w 0x8000", "jml 0x808000", "bra _next", "brl _next"])
def test_no_fix_for_a_label_heading_code_after_a_flow_boundary(boundary: str) -> None:
    source = HEAD + f"_next:\n    bne _next\n    {boundary}\n; Unused: kept for reference.\n_dead:\n    sei\n    rts\n"

    assert _fix_of(source) is None


def test_no_fix_for_a_label_heading_code_at_the_start_of_a_file() -> None:
    assert _fix_of(HEAD + "_dead:\n    rts\n") is None


def test_no_fix_for_a_label_heading_code_at_the_start_of_a_block() -> None:
    assert _fix_of(HEAD + ".alloc main at 0x008000 {\n_dead:\n    rts\n}\n") is None


def test_a_macro_definition_between_does_not_hide_the_boundary() -> None:
    source = HEAD + "    rts\n.macro m() {\n    nop\n}\n_dead:\n    sei\n    m()\n"

    assert _fix_of(source) is None


def test_code_after_a_macro_definition_can_still_fall_through() -> None:
    source = HEAD + "    lda #1\n.macro m() {\n    nop\n}\n_mark:\n    m()\n"

    assert _fixed(source) == HEAD + "    lda #1\n.macro m() {\n    nop\n}\n    m()\n"


def test_no_fix_for_a_label_heading_code_after_an_alloc_body() -> None:
    source = HEAD + ".alloc a at 0x008000 {\n    lda #1\n}\n_dead:\n    rts\n"

    assert _fix_of(source) is None


def test_a_label_after_an_if_block_keeps_its_fix() -> None:
    source = HEAD + "    lda #1\n.if 1 {\n    nop\n}\n_mark:\n    rts\n"

    assert _fixed(source) == HEAD + "    lda #1\n.if 1 {\n    nop\n}\n    rts\n"


def test_no_fix_for_a_label_heading_an_anonymous_block_of_code() -> None:
    assert _fix_of(HEAD + "    rtl\n_dead:\n    {\n        inc 0x1811\n    }\n") is None


def test_no_fix_for_a_label_heading_a_macro_call() -> None:
    source = HEAD + ".macro m() {\n    nop\n}\n    rts\n_dead:\n    m()\n"

    assert _fix_of(source) is None


def test_no_fix_for_a_label_after_a_block_ending_its_routine() -> None:
    assert _fix_of(HEAD + "{\n    nop\n    rtl\n}\n_dead:\n    rts\n") is None


@pytest.mark.parametrize("data", [".db 1", ".dw 2", ".dl 3", '.text "a"', '.ascii "a"'])
def test_no_fix_for_a_label_heading_code_after_data(data: str) -> None:
    assert _fix_of(HEAD + f"    lda #1\n    {data}\n_dead:\n    rts\n") is None


def test_a_dead_routine_head_still_reports() -> None:
    assert _names(HEAD + "    rts\n_dead:\n    sei\n") == ["_dead"]


def test_a_label_ending_a_routine_keeps_its_fix() -> None:
    assert _fixed(HEAD + "    nop\n    rts\n_end:\n") == HEAD + "    nop\n    rts\n"


def test_neighbouring_fixes_leave_no_blank_line_run() -> None:
    from a816.formatter.core import A816Formatter

    source = HEAD + "K := 1\n_A := 2\n\n_B := 3\n\n\n_C := 4\n_D := 5\n\n\n; Next.\nE := K\n"
    fixed = _fixed(source)

    assert A816Formatter().format_text(fixed) == fixed


def test_the_message_holds_for_a_label_local_to_an_alloc_body() -> None:
    source = HEAD + ".alloc main at 0x008000 {\n_never:\n    rts\n}\n"
    (diag,) = [d for d in lint_text(source, Path("main.s")) if d.code == "F002"]

    assert diag.message == "private label `_never` is never used"
