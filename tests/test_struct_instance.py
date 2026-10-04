"""`.istruct T { field = value, ... }`: emit a struct instance as data."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from a816.linker import Linker
from a816.object_file import ObjectFile
from a816.parse.mzparser import A816Parser
from a816.parse.nodes import NodeError
from a816.program import Program
from tests import StubWriter

_SCALARS = """
.struct S {
    byte a
    word b
    long c
    dword d
}
"""

_ARRAYS = """
.struct Pt {
    byte x
    byte y
}
.struct A {
    byte[4] name
    word[3] words
    Pt pos
    Pt[2] pts
}
"""

_BITS = """
.struct F {
    u4 lo
    u4 hi
    byte tail
}
"""


@pytest.fixture(autouse=True)
def _disable_colors(monkeypatch: pytest.MonkeyPatch) -> None:
    import a816.errors as err_mod

    monkeypatch.setattr(err_mod, "_USE_COLORS", False)


def _emit(src: str) -> bytes:
    writer = StubWriter()
    Program().assemble_string_with_emitter("*=0x8000\n" + src, "inst.s", writer)
    return b"".join(writer.data)


def _codegen_error(src: str) -> NodeError:
    program = Program()
    writer = StubWriter()
    with pytest.raises(NodeError) as exc_info:
        program.assemble_string_with_emitter("*=0x8000\n" + src, "inst.s", writer)
    return exc_info.value


def _error_token(src: str) -> str:
    token = _codegen_error(src).file_info
    return token.value if token is not None else ""


def _parse_error(src: str) -> str:
    return A816Parser.parse_as_ast(src, "inst.s").error or ""


def test_scalar_fields_emit_in_layout_order_with_zero_fill() -> None:
    data = _emit(_SCALARS + ".istruct S { b = 0x1234, d = 0xDEADBEEF }\n")
    assert data == bytes.fromhex("00 3412 000000 EFBEADDE")


def test_empty_initializer_zero_fills_whole_struct() -> None:
    assert _emit(_SCALARS + ".istruct S {}\n") == bytes(10)


def test_fields_split_by_newlines_and_comments() -> None:
    src = _SCALARS + ".istruct S {\n    a = 1 ; first\n    ; standalone\n    c = 0x030201\n}\n"
    assert _emit(src) == bytes.fromhex("01 0000 010203 00000000")


def test_wide_value_is_masked_like_dw() -> None:
    assert _emit(_SCALARS + ".istruct S { b = 0x12345 }\n")[1:3] == bytes.fromhex("4523")


def test_negative_value_wraps_like_db() -> None:
    assert _emit(_SCALARS + ".istruct S { a = -1 }\n")[0] == 0xFF


def test_field_expression_uses_symbols() -> None:
    src = _SCALARS + "BASE = 0x10\n.istruct S { a = BASE + 2, b = later }\nlater:\n"
    assert _emit(src)[:3] == bytes.fromhex("12 0A80")


def test_byte_array_string_pads_with_zero() -> None:
    assert _emit(_ARRAYS + '.istruct A { name = "AB" }\n')[:4] == b"AB\x00\x00"


def test_byte_array_string_of_exact_length() -> None:
    assert _emit(_ARRAYS + ".istruct A { name = 'ABCD' }\n")[:4] == b"ABCD"


def test_byte_array_accepts_expression_list() -> None:
    assert _emit(_ARRAYS + ".istruct A { name = [1, 2 + 1] }\n")[:4] == bytes.fromhex("01030000")


def test_word_array_list_is_little_endian_and_padded() -> None:
    assert _emit(_ARRAYS + ".istruct A { words = [0x1234] }\n")[4:10] == bytes.fromhex("341200000000")


def test_nested_struct_field() -> None:
    assert _emit(_ARRAYS + ".istruct A { pos = { y = 7 } }\n")[10:12] == bytes.fromhex("0007")


def test_struct_array_of_initializers() -> None:
    data = _emit(_ARRAYS + ".istruct A { pts = [{ x = 1, y = 2 }, { x = 3 }] }\n")
    assert data[12:16] == bytes.fromhex("01020300")


def test_whole_array_struct_size() -> None:
    assert len(_emit(_ARRAYS + ".istruct A {}\n")) == 16


def test_bit_fields_pack_into_their_byte() -> None:
    assert _emit(_BITS + ".istruct F { lo = 1, hi = 0xF, tail = 9 }\n") == bytes.fromhex("F109")


def test_unset_bit_field_run_is_zero() -> None:
    assert _emit(_BITS + ".istruct F { tail = 9 }\n") == bytes.fromhex("0009")


def test_bit_field_value_is_masked_to_its_width() -> None:
    assert _emit(_BITS + ".istruct F { lo = 0x1F }\n")[0] == 0x0F


def test_label_before_instance_binds_its_address() -> None:
    src = _SCALARS + "hdr: .istruct S { a = 1 }\nafter:\nlda.w #after - hdr\n"
    assert _emit(src)[-3:] == bytes.fromhex("A90A00")


def test_unknown_struct_reports_e0330() -> None:
    assert _codegen_error(".istruct Nope {}\n").code == "E0330"


def test_unknown_struct_points_at_type() -> None:
    assert _error_token(".istruct Nope {}\n") == "Nope"


def test_unknown_field_reports_e0331() -> None:
    assert _codegen_error(_SCALARS + ".istruct S { zz = 1 }\n").code == "E0331"


def test_unknown_field_points_at_field_name() -> None:
    assert _error_token(_SCALARS + ".istruct S { zz = 1 }\n") == "zz"


def test_unknown_nested_field_reports_e0331() -> None:
    assert _codegen_error(_ARRAYS + ".istruct A { pos = { z = 1 } }\n").code == "E0331"


@pytest.mark.parametrize(
    "init",
    [
        'b = "AB"',
        "b = [1]",
        "b = { x = 1 }",
    ],
)
def test_scalar_field_rejects_aggregate_values(init: str) -> None:
    assert _codegen_error(_SCALARS + f".istruct S {{ {init} }}\n").code == "E0332"


@pytest.mark.parametrize(
    "init",
    [
        "name = 1",
        'words = "AB"',
        "pos = 1",
        "pos = [1]",
        "pts = [1]",
        "pts = { x = 1 }",
        "name = [{ x = 1 }]",
    ],
)
def test_aggregate_field_rejects_mismatched_values(init: str) -> None:
    assert _codegen_error(_ARRAYS + f".istruct A {{ {init} }}\n").code == "E0332"


def test_bit_field_rejects_aggregate_value() -> None:
    assert _codegen_error(_BITS + ".istruct F { lo = [1] }\n").code == "E0332"


@pytest.mark.parametrize("init", ['name = "ABCDE"', "name = [1, 2, 3, 4, 5]", "pts = [{}, {}, {}]"])
def test_too_long_initializer_reports_e0333(init: str) -> None:
    assert _codegen_error(_ARRAYS + f".istruct A {{ {init} }}\n").code == "E0333"


def test_non_ascii_string_reports_e0334() -> None:
    assert _codegen_error(_ARRAYS + '.istruct A { name = "é" }\n').code == "E0334"


def test_bit_run_wider_than_32_bits_reports_e0335() -> None:
    src = ".struct W {\n    u20 a\n    u20 b\n}\n.istruct W { a = 1 }\n"
    assert _codegen_error(src).code == "E0335"


def test_wide_bit_run_left_unset_is_zero_filled() -> None:
    src = ".struct W {\n    u20 a\n    u20 b\n}\n.istruct W {}\n"
    assert _emit(src) == bytes(5)


def test_duplicate_field_reports_e0122() -> None:
    assert "[E0122]" in _parse_error(_SCALARS + ".istruct S { a = 1, a = 2 }\n")


def test_duplicate_nested_field_reports_e0122() -> None:
    assert "[E0122]" in _parse_error(_ARRAYS + ".istruct A { pos = { x = 1, x = 2 } }\n")


def test_string_inside_list_reports_e0123() -> None:
    assert "[E0123]" in _parse_error(_ARRAYS + '.istruct A { name = ["A"] }\n')


def test_missing_equal_is_rejected() -> None:
    assert "[E0101]" in _parse_error(_SCALARS + ".istruct S { a 1 }\n")


def test_unclosed_list_is_rejected() -> None:
    assert "[E0101]" in _parse_error(_ARRAYS + ".istruct A { name = [1 2] }\n")


def test_missing_type_is_rejected() -> None:
    assert "[E0101]" in _parse_error(".istruct { a = 1 }\n")


def _link(modules: dict[str, str]) -> ObjectFile:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        objects = []
        for name, src in modules.items():
            (tmp / f"{name}.s").write_text(src)
            assert Program().assemble_as_object(str(tmp / f"{name}.s"), tmp / f"{name}.o") == 0
            objects.append(ObjectFile.from_file(str(tmp / f"{name}.o")))
    return Linker(objects).link()


_PTR = """
.struct Ptr {
    word addr
    byte bank
    long full
    dword wide
}
"""


def test_extern_fields_relocate_at_link_time() -> None:
    linked = _link(
        {
            "target": "*=0x018000\nnop\ntarget:\n    rts\n",
            "table": _PTR
            + ".extern target\n*=0x009000\n"
            + ".istruct Ptr { addr = target & 0xFFFF, bank = target >> 16, full = target, wide = target }\n",
        }
    )
    assert bytes(linked.sections[1].code) == bytes.fromhex("0180 01 018001 01800100")


def test_instance_inside_pooled_alloc_with_label() -> None:
    src = (
        _PTR
        + ".pool code { range 0x008000 0x00ffff }\n"
        + ".alloc table in code {\n    nop\n    entry: .istruct Ptr { addr = entry }\n}\n"
    )
    linked = _link({"m": src})
    assert bytes(linked.sections[0].code)[:3] == bytes.fromhex("EA0180")
