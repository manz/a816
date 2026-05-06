"""Coverage for the fluff lints (DOC001, DOC002, E501)."""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.fluff import fluff_main
from a816.fluff_lint import lint_file


def test_doc001_flags_missing_module_docstring(tmp_path: Path) -> None:
    src = tmp_path / "main.s"
    src.write_text(
        """; just a comment header
"""
        '"""Documented."""\n'
        "main:\n"
        "    rts\n",
        encoding="utf-8",
    )
    diags = lint_file(src)
    codes = [d.code for d in diags]
    assert "DOC001" not in codes  # leading docstring is present (after the comment)


def test_doc001_flags_when_no_leading_docstring(tmp_path: Path) -> None:
    src = tmp_path / "main.s"
    src.write_text("main:\n    rts\n", encoding="utf-8")
    diags = lint_file(src)
    codes = [d.code for d in diags]
    assert "DOC001" in codes


def test_doc002_flags_undocumented_macro(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\n.macro public_macro() {\n    rts\n}\n',
        encoding="utf-8",
    )
    diags = lint_file(src)
    codes = [d.code for d in diags]
    assert "DOC002" in codes
    assert any("public_macro" in d.message for d in diags)


def test_doc002_skips_underscore_prefixed_names(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\n.macro _private_macro() {\n    rts\n}\n',
        encoding="utf-8",
    )
    diags = lint_file(src)
    assert all(d.code != "DOC002" for d in diags)


def test_doc002_accepts_attached_docstring(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\n"""Documented."""\n.macro documented_macro() {\n    rts\n}\n',
        encoding="utf-8",
    )
    diags = lint_file(src)
    assert all(d.code != "DOC002" for d in diags)


def test_check_command_reports_and_exits_nonzero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "main.s"
    src.write_text("main:\n    rts\n", encoding="utf-8")
    rc = fluff_main(["check", str(src)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "DOC001" in captured.out


def test_e501_flags_lines_over_limit(tmp_path: Path) -> None:
    src = tmp_path / "long.s"
    long_line = "; " + ("x" * 130)
    src.write_text(
        '"""Module."""\n' + long_line + "\n",
        encoding="utf-8",
    )
    diags = lint_file(src)
    e501 = [d for d in diags if d.code == "E501"]
    assert len(e501) == 1
    assert e501[0].line == 2
    assert "132 > 120" in e501[0].message


def test_e501_ignores_lines_at_or_below_limit(tmp_path: Path) -> None:
    src = tmp_path / "short.s"
    src.write_text(
        '"""Module."""\n; ' + ("x" * 118) + "\n",
        encoding="utf-8",
    )
    diags = lint_file(src)
    assert all(d.code != "E501" for d in diags)


def test_e501_reported_even_when_parse_fails(tmp_path: Path) -> None:
    src = tmp_path / "broken.s"
    long_line = "; " + ("x" * 130)
    src.write_text(long_line + "\n@@@ broken syntax @@@\n", encoding="utf-8")
    diags = lint_file(src)
    assert any(d.code == "E501" for d in diags)


def test_n801_flags_camelcase_label(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\nMyLabel:\n    rts\n',
        encoding="utf-8",
    )
    diags = lint_file(src)
    n801 = [d for d in diags if d.code == "N801"]
    assert len(n801) == 1
    assert "MyLabel" in n801[0].message


def test_n801_accepts_snake_case_label(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\nmy_label:\n    rts\n_private_label:\n    rts\n',
        encoding="utf-8",
    )
    diags = lint_file(src)
    assert all(d.code != "N801" for d in diags)


def test_n801_flags_screaming_snake_label(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\nMY_LABEL:\n    rts\n',
        encoding="utf-8",
    )
    diags = lint_file(src)
    assert any(d.code == "N801" for d in diags)


def test_n802_accepts_snake_and_screaming_constants(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\nfoo_bar = 0x10\nMAX_HP = 0xFF\n',
        encoding="utf-8",
    )
    diags = lint_file(src)
    assert all(d.code != "N802" for d in diags)


def test_n802_flags_mixed_case_constant(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\nMixedThing = 0x10\n',
        encoding="utf-8",
    )
    diags = lint_file(src)
    n802 = [d for d in diags if d.code == "N802"]
    assert len(n802) == 1
    assert "MixedThing" in n802[0].message


def test_doc003_flags_docstring_above_macro(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\n"""Macro doc."""\n.macro public_macro() {\n    rts\n}\n',
        encoding="utf-8",
    )
    diags = lint_file(src)
    doc003 = [d for d in diags if d.code == "DOC003"]
    assert len(doc003) == 1
    assert "public_macro" in doc003[0].message


def test_doc003_quiet_when_docstring_inside_body(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\n.macro public_macro() {\n    """Macro doc."""\n    rts\n}\n',
        encoding="utf-8",
    )
    diags = lint_file(src)
    assert all(d.code != "DOC003" for d in diags)


def test_doc003_skips_private_target(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\n"""Private doc."""\n.macro _private() {\n    rts\n}\n',
        encoding="utf-8",
    )
    diags = lint_file(src)
    assert all(d.code != "DOC003" for d in diags)


def test_doc004_flags_orphan_docstring(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\nmain:\n    """orphan note used as comment"""\n    rts\n',
        encoding="utf-8",
    )
    diags = lint_file(src)
    assert any(d.code == "DOC004" for d in diags)


def test_doc004_quiet_for_docstring_above_label(tmp_path: Path) -> None:
    """Labels have no inside-body slot; a docstring directly above them
    is the canonical attach point and must not trigger DOC004."""
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\n'
        '"""Get pointer for bank 1-1."""\n'
        "get_bank1_1_pointer:\n"
        "    rtl\n"
        '"""Get pointer for bank 1-2."""\n'
        "get_bank1_2_pointer:\n"
        "    rtl\n",
        encoding="utf-8",
    )
    diags = lint_file(src)
    assert all(d.code != "DOC004" for d in diags)
    assert all(d.code != "DOC005" for d in diags)


def test_doc004_quiet_when_docstring_attaches_to_target(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\n.macro foo() {\n    """attached"""\n    rts\n}\n',
        encoding="utf-8",
    )
    diags = lint_file(src)
    assert all(d.code != "DOC004" for d in diags)


def test_doc005_flags_comment_block_above_macro(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\n; first banner line\n; second banner line\n.macro foo() {\n    rts\n}\n',
        encoding="utf-8",
    )
    diags = lint_file(src)
    doc005 = [d for d in diags if d.code == "DOC005"]
    assert len(doc005) == 1
    assert "foo" in doc005[0].message


def test_doc005_quiet_for_single_line_comment(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\n; tag\n.macro foo() {\n    """doc"""\n    rts\n}\n',
        encoding="utf-8",
    )
    diags = lint_file(src)
    assert all(d.code != "DOC005" for d in diags)


def test_doc006_quiet_when_intervening_node_separates_comment_and_docstring(tmp_path: Path) -> None:
    """A target with a leading comment must not 'leak' onto a later target's docstring."""
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\n'
        "; banner line one\n"
        "; banner line two\n"
        ".macro intervening() {\n"
        '    """doc"""\n'
        "    rts\n"
        "}\n"
        ".macro foo() {\n"
        '    """doc"""\n'
        "    rts\n"
        "}\n",
        encoding="utf-8",
    )
    diags = lint_file(src)
    doc006 = [d for d in diags if d.code == "DOC006"]
    assert len(doc006) == 1
    assert "intervening" in doc006[0].message


def test_doc006_flags_comment_block_plus_docstring(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\n; banner line one\n; banner line two\n.macro foo() {\n    """doc"""\n    rts\n}\n',
        encoding="utf-8",
    )
    diags = lint_file(src)
    doc006 = [d for d in diags if d.code == "DOC006"]
    assert len(doc006) == 1
    assert "foo" in doc006[0].message


def test_doc007_flags_over_indented_docstring(tmp_path: Path) -> None:
    """Opening `\"\"\"` at column 0 but content at column 4 → over-indented."""
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\n"""\n    summary line\n"""\n.macro foo() {\n    rts\n}\n',
        encoding="utf-8",
    )
    diags = lint_file(src)
    doc007 = [d for d in diags if d.code == "DOC007"]
    assert len(doc007) >= 1
    assert "over-indented" in doc007[0].message


def test_doc007_quiet_when_content_aligns(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\n"""\nsummary line\n"""\n.macro foo() {\n    rts\n}\n',
        encoding="utf-8",
    )
    diags = lint_file(src)
    assert all(d.code != "DOC007" for d in diags)


def test_doc007_quiet_for_single_line(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    src.write_text('"""Module."""\n"""one liner"""\nmain:\n    rts\n', encoding="utf-8")
    diags = lint_file(src)
    assert all(d.code != "DOC007" for d in diags)


def test_noqa_blanket_silences_all_codes_on_line(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    long_data = ", ".join(["0x1234"] * 30)
    src.write_text(
        f'"""Module."""\nBadName: ; noqa\n    .dw {long_data} ; noqa\n    rts\n',
        encoding="utf-8",
    )
    diags = lint_file(src)
    assert all(d.code not in {"N801", "E501"} for d in diags)


def test_noqa_with_codes_silences_only_listed(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    long_data = ", ".join(["0x1234"] * 30)
    src.write_text(
        f'"""Module."""\n    .dw {long_data} ; noqa: E501\nBadName: ; noqa: E501\n',
        encoding="utf-8",
    )
    diags = lint_file(src)
    codes = [d.code for d in diags]
    assert "E501" not in codes
    assert "N801" in codes  # not silenced


def test_noqa_codes_case_insensitive_and_multi(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\nBadName: ; noqa: n801, e501\n',
        encoding="utf-8",
    )
    diags = lint_file(src)
    assert all(d.code != "N801" for d in diags)


def test_check_command_clean_exits_zero(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\n.macro foo() {\n    """Documented."""\n    rts\n}\n',
        encoding="utf-8",
    )
    assert fluff_main(["check", str(src)]) == 0


def test_lint_file_picks_up_include_paths_from_a816_toml(tmp_path: Path) -> None:
    """Source `.include`s a file that lives under a config-declared path.
    Without `a816.toml` discovery the parser would fail; with it, fluff
    parses cleanly and emits its usual lint hits."""
    project = tmp_path / "proj"
    project.mkdir()
    inc_dir = project / "src" / "include"
    inc_dir.mkdir(parents=True)
    (inc_dir / "constants.s").write_text('"""Shared constants."""\nMY_CONST = 0x42\n', encoding="utf-8")
    (project / "a816.toml").write_text(
        'entrypoint = "src/main.s"\ninclude-paths = ["src/include"]\n',
        encoding="utf-8",
    )
    src_dir = project / "src"
    src_dir.mkdir(exist_ok=True)
    main = src_dir / "main.s"
    main.write_text('"""Main."""\n.include "constants.s"\nmain:\n    rts\n', encoding="utf-8")
    diags = lint_file(main)
    # No parse-error fallout from the unresolved include.
    assert all(d.code != "DOC001" for d in diags)
