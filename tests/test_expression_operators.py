"""One operator set, integer semantics, in every expression context.

Opcode operands, `X =`, `X :=`, `.db` / `.dw`, `.if` and object-mode
relocations evaluated by the linker must all accept the same operators
and agree on their results.

Division truncates toward zero and `%` takes the sign of the dividend
(C / ca65 semantics), so `a == (a / b) * b + a % b` always holds.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from a816.error_codes import E_CODEGEN_DIVISION_BY_ZERO
from a816.exceptions import ExpressionEvaluationError
from a816.linker import Linker
from a816.object_file import ObjectFile, SymbolSection, SymbolType
from a816.parse.ast.expression import eval_expression_str
from a816.parse.errors import ScannerException
from a816.parse.nodes import NodeError
from a816.program import Program
from a816.symbols import Resolver
from tests import StubWriter

OPERATOR_CASES = [
    ("7 + 2", 9),
    ("7 - 2", 5),
    ("7 * 2", 14),
    ("7 / 2", 3),
    ("7/2", 3),
    ("7 % 2", 1),
    ("7%2", 1),
    ("6 & 3", 2),
    ("6 | 3", 7),
    ("6|3", 7),
    ("6 ^ 3", 5),
    ("6^3", 5),
    ("1 << 4", 16),
    ("0x40 >> 2", 16),
    ("~0x0F", 0xF0),
    ("0o17", 15),
]


def _emit(src: str) -> bytes:
    program = Program()
    writer = StubWriter()
    program.assemble_string_with_emitter("*=0x008000\n" + src, "ops.s", writer)
    return b"".join(writer.data)


def _word(value: int) -> bytes:
    return value.to_bytes(2, "little")


def _division_error(src: str) -> NodeError:
    with pytest.raises(NodeError) as exc:
        _emit(src)
    return exc.value


class TestOperatorInEveryContext:
    @pytest.mark.parametrize(("expr", "expected"), OPERATOR_CASES)
    def test_opcode_operand(self, expr: str, expected: int) -> None:
        assert _emit(f"lda.w #{expr}\n") == b"\xa9" + _word(expected)

    @pytest.mark.parametrize(("expr", "expected"), OPERATOR_CASES)
    def test_symbol_affectation(self, expr: str, expected: int) -> None:
        assert _emit(f"X = {expr}\nlda.w #X\n") == b"\xa9" + _word(expected)

    @pytest.mark.parametrize(("expr", "expected"), OPERATOR_CASES)
    def test_assign(self, expr: str, expected: int) -> None:
        assert _emit(f"X := {expr}\nlda.w #X\n") == b"\xa9" + _word(expected)

    @pytest.mark.parametrize(("expr", "expected"), OPERATOR_CASES)
    def test_db(self, expr: str, expected: int) -> None:
        assert _emit(f".db {expr}\n") == bytes([expected])

    @pytest.mark.parametrize(("expr", "expected"), OPERATOR_CASES)
    def test_dw(self, expr: str, expected: int) -> None:
        assert _emit(f".dw {expr}\n") == _word(expected)

    @pytest.mark.parametrize(("expr", "expected"), OPERATOR_CASES)
    def test_if_condition(self, expr: str, expected: int) -> None:
        src = f".if ({expr}) == {expected} {{\n.db 1\n}} .else {{\n.db 0\n}}\n"
        assert _emit(src) == b"\x01"

    def test_bare_if_condition_with_bitwise_or(self) -> None:
        assert _emit(".if 1|0 {\n.db 1\n} .else {\n.db 0\n}\n") == b"\x01"


class TestIntegerSemantics:
    @pytest.mark.parametrize(
        ("expr", "expected"),
        [
            ("-7 / 2", -3),
            ("7 / -2", -3),
            ("-7 / -2", 3),
            ("-7 % 2", -1),
            ("7 % -2", 1),
            ("-7 % -2", -1),
        ],
    )
    def test_division_truncates_toward_zero(self, expr: str, expected: int) -> None:
        assert eval_expression_str(expr, Resolver()) == expected

    @pytest.mark.parametrize(("a", "b"), [(7, 2), (-7, 2), (7, -2), (-7, -2)])
    def test_division_and_modulo_agree(self, a: int, b: int) -> None:
        resolver = Resolver()
        quotient = eval_expression_str(f"{a} / {b}", resolver)
        remainder = eval_expression_str(f"{a} % {b}", resolver)
        assert isinstance(quotient, int)
        assert isinstance(remainder, int)
        assert quotient * b + remainder == a

    @pytest.mark.parametrize(
        ("expr", "expected"),
        [
            ("2 + 6 / 2", 5),
            ("7 % 4 * 2", 6),
            ("1 + 2 ^ 3", 0),
            ("6 & 3 ^ 1", 3),
            ("1 ^ 3 | 4", 6),
        ],
    )
    def test_precedence(self, expr: str, expected: int) -> None:
        assert eval_expression_str(expr, Resolver()) == expected

    def test_division_result_is_int(self) -> None:
        assert isinstance(eval_expression_str("10 / 4", Resolver()), int)


class TestDivisionByZero:
    @pytest.mark.parametrize(
        "src",
        ["X = 1 / 0\n", "lda.w #1 % 0\n", ".dw 4 / (2 - 2)\n", "X := 1 % 0\n"],
    )
    def test_is_a_located_node_error(self, src: str) -> None:
        assert _division_error(src).code == str(E_CODEGEN_DIVISION_BY_ZERO)

    def test_points_at_the_operator(self) -> None:
        error = _division_error("X = 10 / 0\n")
        assert error.file_info is not None
        assert error.file_info.value == "/"

    def test_rendered_location(self) -> None:
        rendered = _division_error("nop\nX = 10 % 0\n").format()
        assert "ops.s:3" in rendered


class TestLexerAmbiguities:
    def test_block_comment_after_operand(self) -> None:
        assert _emit("lda.w #4 /* four */\n") == b"\xa9" + _word(4)

    def test_block_comment_after_affectation(self) -> None:
        assert _emit("X = 8 /* eight */\nlda.w #X\n") == b"\xa9" + _word(8)

    def test_star_eq_still_sets_pc(self) -> None:
        assert _emit("*=0x008010\n.db 6 / 3\n") == b"\x02"

    def test_standalone_expression_rejects_unknown_character(self) -> None:
        resolver = Resolver()
        with pytest.raises(ScannerException):
            eval_expression_str("1 $ 2", resolver)


def _linked_word(expression: str, value: int) -> int:
    obj = ObjectFile(b"\xa9\x00\x00", [("VAL", value, SymbolType.GLOBAL, SymbolSection.DATA)], [])
    obj.expression_relocations = [(1, expression, 2)]
    linked = Linker([obj]).link()
    return int.from_bytes(bytes(linked.code[1:3]), "little")


def _link_error(expression: str) -> ExpressionEvaluationError:
    obj = ObjectFile(b"\xa9\x00", [("VAL", 1, SymbolType.GLOBAL, SymbolSection.DATA)], [])
    obj.expression_relocations = [(1, expression, 1)]
    linker = Linker([obj])
    with pytest.raises(ExpressionEvaluationError) as exc:
        linker.link()
    return exc.value


class TestLinkerExpressionSemantics:
    @pytest.mark.parametrize(
        ("expr", "expected"),
        [
            ("VAL / 3", 3),
            ("VAL % 3", 1),
            ("VAL ^ 0xFF", 0xF5),
            ("VAL | 0x100", 0x10A),
            ("~VAL", 0xF5),
            ("-VAL / 4", (-2) & 0xFFFF),
            ("VAL == 10", 1),
        ],
    )
    def test_integer_semantics(self, expr: str, expected: int) -> None:
        assert _linked_word(expr, 10) == expected

    def test_division_by_zero_is_a_clean_error(self) -> None:
        assert "division by zero" in _link_error("VAL / 0").reason

    def test_modulo_by_zero_is_a_clean_error(self) -> None:
        assert "division by zero" in _link_error("VAL % (VAL - 1)").reason

    def test_unresolved_symbol_is_a_clean_error(self) -> None:
        assert "missing" in _link_error("missing + 1").reason

    def test_invalid_character_is_a_clean_error(self) -> None:
        assert _link_error("VAL $ 2").expression == "VAL $ 2"


class TestExternRelocationOperators:
    @pytest.mark.parametrize(
        ("expr", "expected"),
        [
            ("ext_label / 2", 0x4000),
            ("ext_label % 0x300", 0x200),
            ("ext_label ^ 0xFFFF", 0x7FFF),
            ("ext_label | 3", 0x8003),
            ("~ext_label", 0x7FFF),
        ],
    )
    def test_operand_relocation(self, expr: str, expected: int) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / "p.s").write_text("*=0x008000\next_label:\n    nop\n")
            (tmp / "c.s").write_text(f".extern ext_label\n*=0x009000\n    lda.w #{expr}\n")
            Program().assemble_as_object(str(tmp / "p.s"), tmp / "p.o")
            Program().assemble_as_object(str(tmp / "c.s"), tmp / "c.o")
            objects = [ObjectFile.from_file(str(tmp / "p.o")), ObjectFile.from_file(str(tmp / "c.o"))]
        linked = Linker(objects).link()
        code = bytes(linked.sections[1].code)
        assert code == b"\xa9" + _word(expected)
