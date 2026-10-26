"""Link-time expressions stored as RPN (`a816.link_expr`)."""

from __future__ import annotations

import pytest

from a816.exceptions import ExpressionEvaluationError, UnresolvedSymbolError
from a816.link_expr import Binding, ExprRelocation, LinkExpr, RelocKind, UnresolvedLinkName
from a816.linker import Linker
from a816.object_file import ObjectFile, SymbolSection, SymbolType
from a816.parse.ast.expression import ExprEvalError, eval_constant_expression

OPERATIONS = [
    "a + b",
    "a - b",
    "a * b",
    "a / b",
    "a % b",
    "a & b",
    "a | b",
    "a ^ b",
    "a >> 2",
    "a << 2",
    "a >= b",
    "a <= b",
    "a < b",
    "a > b",
    "a == b",
    "a != b",
    "-a",
    "~a",
    "<a",
    ">a",
    "a - -b",
    "<a < >b",
    "(a + b) * -(a - b) / 3",
    "~(a | 0x100)",
]
VALUES = {"a": 0x1234, "b": 7}


def _lookup(name: str, _binding: Binding) -> int:
    return VALUES[name]


@pytest.mark.parametrize("text", OPERATIONS)
def test_the_rendered_text_parses_back(text: str) -> None:
    expression = LinkExpr.parse(text)

    assert LinkExpr.parse(str(expression)) == expression


@pytest.mark.parametrize("text", OPERATIONS)
def test_link_evaluation_matches_the_assembler(text: str) -> None:
    substituted = text.replace("a", str(VALUES["a"])).replace("b", str(VALUES["b"]))

    assert LinkExpr.parse(text).evaluate(_lookup) == int(eval_constant_expression(substituted))


def test_not_masks_by_the_linked_value() -> None:
    """Compile time reads an extern as 0; the mask must come from the final value."""
    assert LinkExpr.parse("~ext").evaluate(lambda _n, _b: 0x123) == 0xFEDC


def test_a_number_tail_is_not_a_name() -> None:
    assert LinkExpr.parse("x1FFF + 0x1FFF").names() == {"x1FFF"}


def test_a_string_compares() -> None:
    assert LinkExpr.parse('name == "abc"').evaluate(lambda _n, _b: "abc") == 1


def test_a_missing_name_says_which() -> None:
    with pytest.raises(UnresolvedLinkName) as caught:
        LinkExpr.parse("a + nowhere").evaluate(lambda name, _b: VALUES[name] if name in VALUES else _missing(name))

    assert caught.value.name == "nowhere"


def _missing(name: str) -> int:
    raise UnresolvedLinkName(name)


def test_a_string_where_a_number_goes_is_refused() -> None:
    with pytest.raises(ExprEvalError):
        LinkExpr.parse("name + 1").evaluate(lambda _n, _b: "abc")


def test_a_string_result_is_refused() -> None:
    with pytest.raises(ExprEvalError):
        LinkExpr.parse("name").evaluate(lambda _n, _b: "abc")


def test_substitute_replaces_a_name_with_its_expression() -> None:
    expression = LinkExpr.parse("alias + 1").substitute({"alias": LinkExpr.parse("base * 2")})

    assert str(expression) == "(base * 2) + 1"


def test_values_left_over_are_refused() -> None:
    ops = LinkExpr.parse("a").ops + LinkExpr.parse("b").ops

    with pytest.raises(ValueError, match="leave 2 values"):
        LinkExpr(ops)


def test_an_operator_short_of_operands_is_refused() -> None:
    from a816.link_expr import ExprOp, ExprOpCode

    with pytest.raises(ValueError, match="`ADD` lacks an operand"):
        LinkExpr((ExprOp(ExprOpCode.ADD),))


def test_an_empty_expression_is_refused() -> None:
    with pytest.raises(ValueError, match="leave 0 values"):
        LinkExpr(())


def test_text_the_assembler_rejects_does_not_parse() -> None:
    with pytest.raises(ValueError, match="malformed"):
        LinkExpr.parse("< <x")


def test_a_constant_past_64_bits_is_refused() -> None:
    from a816.link_expr import ExprOp, ExprOpCode

    with pytest.raises(ValueError, match="does not fit"):
        LinkExpr((ExprOp(ExprOpCode.CONST, 1 << 64),))


def test_an_object_keeps_a_local_binding_its_producer_set() -> None:
    """A name a producer bound LOCAL stays LOCAL though the object's symbol
    table does not list it."""
    from a816.link_expr import symbol

    obj = ObjectFile(
        b"\xa9\x00\x00",
        [],
        expression_relocations=[ExprRelocation(1, 2, RelocKind.VALUE, symbol("_mine", Binding.LOCAL))],
        relocatable=False,
    )

    assert obj.expression_relocations[0].expr == symbol("_mine", Binding.LOCAL)


def test_a_negative_constant_renders_as_negation() -> None:
    from a816.link_expr import ExprOp, ExprOpCode

    assert str(LinkExpr((ExprOp(ExprOpCode.CONST, -0x20),))) == "-0x20"


def _object(code: bytes, local: int, expression: str) -> ObjectFile:
    return ObjectFile(
        code,
        [("_here", local, SymbolType.LOCAL, SymbolSection.DATA)],
        expression_relocations=[ExprRelocation(1, 2, RelocKind.VALUE, LinkExpr.parse(expression))],
        relocatable=False,
    )


def test_a_local_name_reads_its_own_object() -> None:
    """Two objects with the same LOCAL `_here`: each expression binds its own."""
    linker = Linker([_object(b"\xa9\x00\x00", 0x1111, "_here"), _object(b"\xa9\x00\x00", 0x2222, "_here")])
    linker.link()

    assert [section.code[1:3] for section in linker.linked_sections] == [b"\x11\x11", b"\x22\x22"]


def test_an_own_local_is_bound_local() -> None:
    (relocation,) = _object(b"\xa9\x00\x00", 1, "_here + other").expression_relocations

    assert [op.value for op in relocation.expr.ops if op.name] == [Binding.LOCAL.value, Binding.GLOBAL.value]


def test_an_operator_mistake_at_link_names_the_expression() -> None:
    obj = _object(b"\xa9\x00\x00", 1, "_here / 0")

    with pytest.raises(ExpressionEvaluationError) as caught:
        Linker([obj]).link()

    assert (caught.value.expression, caught.value.reason) == ("_here / 0", "division by zero")


def test_negating_a_string_is_refused() -> None:
    with pytest.raises(ExprEvalError):
        LinkExpr.parse("-name").evaluate_value(lambda _name, _binding: "text")


def _aliased(aliases: list[tuple[str, str]]) -> ObjectFile:
    return ObjectFile(
        b"\xea",
        [],
        aliases=[(name, LinkExpr.parse(expression)) for name, expression in aliases],
        relocatable=False,
    )


def test_an_alias_waits_for_an_alias_listed_after_it() -> None:
    linker = Linker([_aliased([("first", "second + 1"), ("second", "0x10")])])
    linker.link()

    assert linker.symbol_map["first"] == 0x11


def test_an_alias_of_a_missing_name_is_unresolved() -> None:
    linker = Linker([_aliased([("lonely", "nowhere + 1")])])

    with pytest.raises(UnresolvedSymbolError) as caught:
        linker.link()

    assert "lonely" in str(caught.value)


@pytest.mark.parametrize("text", ["sizeof(x)", "(x as Y).f"])
def test_a_compile_time_form_does_not_parse(text: str) -> None:
    with pytest.raises(ValueError, match="cannot appear in a link-time expression"):
        LinkExpr.parse(text)


def test_a_parenthesis_inside_a_string_keeps_the_outer_ones() -> None:
    expression = LinkExpr.parse('"a)b" == s')

    assert LinkExpr.parse(str(expression)) == expression
