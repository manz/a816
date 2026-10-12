"""`.patch TYPE at ADDR { field = value }` writes only the fields given.

ff4's header (maker, version, checksum and vectors must stay vanilla) and
dq6's box descriptors could not use `.istruct`, which zero-fills every
field it is not given: they patched field by field with binds and
`BASE + Type.field` arithmetic.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from a816.module_builder import build_with_imports
from a816.parse.mzparser import A816Parser
from tests.ips_helpers import parse_ips_records

HEADER = ".struct Header {\n    byte[4] title\n    byte maker\n    word checksum\n}\n"


def _records(tmp_path: Path, source: str) -> list[tuple[int, bytes]]:
    (tmp_path / "main.s").write_text(source, encoding="utf-8")
    result = build_with_imports(tmp_path / "main.s", tmp_path / "out.ips", output_dir=tmp_path / "obj")
    assert result.exit_code == 0, result.diagnostics
    return parse_ips_records((tmp_path / "out.ips").read_bytes())


def _error(tmp_path: Path, source: str, caplog: pytest.LogCaptureFixture) -> str:
    (tmp_path / "main.s").write_text(source, encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        result = build_with_imports(tmp_path / "main.s", tmp_path / "out.ips", output_dir=tmp_path / "obj")
    assert result.exit_code != 0
    return caplog.text


def test_only_the_given_fields_are_written(tmp_path: Path) -> None:
    source = HEADER + '.patch Header at 0x008000 {\n    title = "AB"\n    checksum = 0x1234\n}\n'

    assert _records(tmp_path, source) == [(0x0000, b"AB"), (0x0005, b"\x34\x12")]


def test_adjacent_fields_land_in_one_block(tmp_path: Path) -> None:
    source = HEADER + ".patch Header at 0x008000 { maker = 0x33, checksum = 0x1234 }\n"

    assert _records(tmp_path, source) == [(0x0004, b"\x33\x34\x12")]


def test_the_address_takes_a_constant_expression(tmp_path: Path) -> None:
    source = HEADER + "HDR = 0x008010\n.patch Header at HDR + 2 { maker = 1 }\n"

    assert _records(tmp_path, source) == [(0x0016, b"\x01")]


def test_a_nested_struct_field_writes_only_its_given_fields(tmp_path: Path) -> None:
    source = (
        ".struct Point {\n    byte x\n    byte y\n}\n"
        ".struct Box {\n    byte id\n    Point corner\n}\n"
        ".patch Box at 0x008000 { corner = { y = 7 } }\n"
    )

    assert _records(tmp_path, source) == [(0x0002, b"\x07")]


def test_an_array_writes_only_its_given_elements(tmp_path: Path) -> None:
    source = ".struct Row {\n    byte tag\n    word[4] cells\n}\n.patch Row at 0x008000 { cells = [1, 2] }\n"

    assert _records(tmp_path, source) == [(0x0001, b"\x01\x00\x02\x00")]


def test_a_whole_bit_field_byte_is_written(tmp_path: Path) -> None:
    source = (
        ".struct Flags {\n    u4 low\n    u4 high\n    byte other\n}\n.patch Flags at 0x008000 { low = 1, high = 2 }\n"
    )

    assert _records(tmp_path, source) == [(0x0000, b"\x21")]


def test_a_partial_bit_field_byte_is_rejected(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    source = ".struct Flags {\n    u4 low\n    u4 high\n}\n.patch Flags at 0x008000 { low = 1 }\n"

    text = _error(tmp_path, source, caplog)

    assert ("E0348" in text, "`high`" in text) == (True, True)


def test_a_patch_over_another_pin_is_an_overlap(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    source = HEADER + '.alloc at 0x008000 {\n    .db 1, 2\n}\n.patch Header at 0x008000 { title = "AB" }\n'

    assert "E0408" in _error(tmp_path, source, caplog)


def test_an_unknown_type_is_rejected(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    assert "E0330" in _error(tmp_path, ".patch Nope at 0x008000 { a = 1 }\n", caplog)


def test_an_unknown_field_is_rejected(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    assert "E0331" in _error(tmp_path, HEADER + ".patch Header at 0x008000 { nope = 1 }\n", caplog)


def test_the_formatter_keeps_a_patch() -> None:
    source = '.patch Header at 0x008000 {\n    title = "AB"\n    checksum = 0x1234\n}'

    assert A816Parser.parse_as_ast(source, "x.s").nodes[0].to_canonical() == source


def test_format_keeps_a_patch_nested_in_a_scope() -> None:
    from a816.formatter import A816Formatter

    source = '"""M."""\n.scope s {\n    .patch Header at 0x008000 {\n        title = "AB"\n    }\n}\n'

    assert A816Formatter().format_text(source) == source


def test_check_flags_a_patch_of_an_unknown_type() -> None:
    from a816.fluff import lint_text

    hits = lint_text('"""M."""\n.patch Nope at 0x008000 { a = 1 }\n', Path("x.s"))

    assert [d.message for d in hits if d.code == "S001"] == ["`.patch` of unknown struct type 'Nope'"]


def test_a_rom_hack_patches_the_std_header_title_only(tmp_path: Path) -> None:
    """ff4's case: the title changes, maker / version / checksum stay vanilla."""
    source = '.import "@std/snes/header"\n.patch SnesHeader at SNES_HEADER_BASE { title = "FF4 FR" }\n'

    assert _records(tmp_path, source) == [(0x7FC0, b"FF4 FR")]


def test_a_patch_inside_an_alloc_is_nested_placement(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    source = HEADER + ".alloc at 0x009000 {\n    .patch Header at 0x008000 { maker = 1 }\n}\n"

    assert "E0341" in _error(tmp_path, source, caplog)


def test_an_empty_patch_renders_on_one_line() -> None:
    assert A816Parser.parse_as_ast(".patch Header at 0x008000 {}", "x.s").nodes[0].to_canonical() == (
        ".patch Header at 0x008000 {}"
    )


def test_a_patch_representation_names_type_address_and_fields() -> None:
    node = A816Parser.parse_as_ast(".patch Header at 0x008000 { maker = 1 }", "x.s").nodes[0]

    assert node.to_representation()[:2] == ("patch", "Header")
