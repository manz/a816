"""`ST002`: `.import` outside the file prelude.

The prelude is the leading run of top-level docstrings, comments,
`.import`, `.include`, `.extern`, constant declarations (`NAME = x`,
`NAME := x`, `.label NAME = x`) and file-level configuration (`.table`,
`.map`). The first other statement ends it;
any `.import` after that point, or nested in a block, is flagged.
"""

from __future__ import annotations

from pathlib import Path

from a816.fluff.runner import lint_text


def _st002_lines(src: str) -> list[int]:
    return [d.line for d in lint_text(src, Path("x.s")) if d.code == "ST002"]


class TestImportInPrelude:
    def test_imports_after_docstring_and_comments_pass(self) -> None:
        src = '"""m."""\n; deps\n.import "a"\n.import "b"\n*=0x008000\n    nop\n'
        assert _st002_lines(src) == []

    def test_imports_interleaved_with_includes_constants_and_externs_pass(self, tmp_path: Path) -> None:
        (tmp_path / "config.i").write_text("CONFIG = 1\n")
        src = (
            f'"""m."""\n.include "{tmp_path / "config.i"}"\nFOO = 1\nbar := FOO\n.label baz = 0x7e0000\n'
            '.extern ext\n.import "a"\n.alloc at 0x008000 {\n    nop\n}\n'
        )
        assert _st002_lines(src) == []

    def test_imports_after_table_and_map_pass(self) -> None:
        src = (
            '"""m."""\n.map identifier=0 bank_range=0x00, 0x7d addr_range=0x0000, 0xffff mask=0x10000\n'
            '.table "text/menus.tbl"\n.import "a"\n*=0x008000\n    nop\n'
        )
        assert _st002_lines(src) == []

    def test_file_without_imports_passes(self) -> None:
        assert _st002_lines('"""m."""\n*=0x008000\n    nop\n') == []


class TestImportOutsidePrelude:
    def test_import_after_alloc_is_flagged(self) -> None:
        src = '"""m."""\n.alloc at 0x008000 {\n    nop\n}\n.import "a"\n'
        assert _st002_lines(src) == [5]

    def test_import_after_star_eq_is_flagged(self) -> None:
        assert _st002_lines('"""m."""\n*=0x008000\n.import "a"\n') == [3]

    def test_import_after_label_is_flagged(self) -> None:
        assert _st002_lines('"""m."""\nmain:\n.import "a"\n') == [3]

    def test_import_after_struct_is_flagged(self) -> None:
        assert _st002_lines('"""m."""\n.struct P { byte x }\n.import "a"\n') == [3]

    def test_import_inside_alloc_body_is_flagged(self) -> None:
        src = '"""m."""\n.import "a"\n.alloc at 0x008000 {\n    .import "b"\n}\n'
        assert _st002_lines(src) == [4]

    def test_import_inside_conditional_is_flagged(self) -> None:
        src = '"""m."""\nDEBUG = 1\n.if DEBUG {\n    .import "dbg"\n}\n'
        assert _st002_lines(src) == [4]

    def test_every_late_import_is_flagged(self) -> None:
        src = '"""m."""\n.import "a"\n*=0x008000\n.import "b"\n    nop\n.import "c"\n'
        assert _st002_lines(src) == [4, 6]

    def test_message_names_where_the_prelude_ended(self) -> None:
        src = '"""m."""\n*=0x008000\n.import "a"\n'
        [diag] = [d for d in lint_text(src, Path("x.s")) if d.code == "ST002"]
        assert "line 2" in diag.message


def test_imports_inside_an_included_file_are_left_to_its_own_lint(tmp_path: Path) -> None:
    (tmp_path / "part.i").write_text('.import "a"\n')
    src = f'"""m."""\n*=0x008000\n.include "{tmp_path / "part.i"}"\n'
    assert _st002_lines(src) == []
