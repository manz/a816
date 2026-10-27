"""Codegen builds link expressions from the parsed operand, not from its text."""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.exceptions import A816Error, ExternalSymbolReference
from a816.link_expr import ExprOpCode, LinkExpr, symbol
from a816.object_file import ObjectFile
from a816.parse.ast.expression import (
    ExprEvalError,
    deferred_link_expression,
    expr_to_ast,
    link_expression,
    link_name,
)
from a816.parse.tokens import Token, TokenType
from a816.program import Program
from a816.symbols import Resolver
from a816.writers import ObjectWriter


def _object(tmp_path: Path, source: str) -> ObjectFile:
    source_file = tmp_path / "m.s"
    source_file.write_text(source)
    object_file = tmp_path / "m.o"
    assert Program().assemble_as_object(str(source_file), object_file) == 0
    return ObjectFile.from_file(str(object_file))


def _relocations(obj: ObjectFile) -> list[LinkExpr]:
    return [relocation.expr for section in obj.sections for relocation in section.expression_relocations]


def test_a_number_is_not_read_as_a_label(tmp_path: Path) -> None:
    """`0x1FFF` holds the letters of a label `x1FFF`; it stays a number."""
    obj = _object(tmp_path, ".extern ext\n*=0x8000\n{\nx1FFF:\n    lda.l ext + 0x1FFF\n}\n")

    assert [str(e) for e in _relocations(obj)] == ["ext + 0x1fff"]


def test_a_scoped_name_is_not_renamed_with_a_local_label(tmp_path: Path) -> None:
    """A local `loop` takes its exported name; `Foo.loop` keeps its own."""
    source = ".scope Foo {\nloop:\n    nop\n}\n*=0x8000\n{\nloop:\n    lda.w Foo.loop - loop\n}\n"
    obj = _object(tmp_path, source)

    (expression,) = _relocations(obj)
    assert "Foo.loop" in expression.names()


def test_a_string_in_an_assert_is_not_renamed(tmp_path: Path) -> None:
    source = '.extern ext\n*=0x8000\n{\nloop:\n    nop\n    .assert ext == "loop", "probe"\n}\n'
    obj = _object(tmp_path, source)

    assert [op.name for op in obj.asserts[0].expression.ops if op.op is ExprOpCode.STRING] == ["loop"]


def test_a_macro_argument_reads_the_alias_it_shadows(tmp_path: Path) -> None:
    """`m(count + 1)` binds the macro's `count` to the caller's `count`
    plus one, not to itself."""
    source = ".extern ext\ncount = ext + 1\n.macro m(count) {\n    lda.w count\n}\n*=0x8000\n    m(count + 1)\n"
    obj = _object(tmp_path, source)

    assert [str(e) for e in _relocations(obj)] == ["(ext + 1) + 1"]


def _cyclic_resolver() -> Resolver:
    resolver = Resolver()
    resolver.current_scope.add_external_alias("a", LinkExpr.parse("b + 1"))
    resolver.current_scope.add_external_alias("b", LinkExpr.parse("a + 1"))
    return resolver


def test_an_alias_defined_through_itself_is_refused() -> None:
    with pytest.raises(ExprEvalError, match=r"alias `a` is defined through itself: a -> b -> a"):
        link_name("a", _cyclic_resolver())


def test_a_cyclic_alias_in_an_operand_points_at_it() -> None:
    expression = expr_to_ast("a + 2", "m.s")
    resolver = _cyclic_resolver()

    with pytest.raises(A816Error, match="defined through itself"):
        link_expression(expression, resolver)


def test_a_macro_argument_naming_a_local_label_relocates(tmp_path: Path) -> None:
    source = ".macro m(target) {\n    lda.w target\n}\n*=0x8000\n{\nhere:\n    nop\n    m(here + 1)\n}\n"
    obj = _object(tmp_path, source)

    assert [str(e) for e in _relocations(obj)] == ["__sc2__here + 1"]


def test_a_label_address_names_the_extern_it_met(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    source_file = tmp_path / "m.s"
    source_file.write_text(".extern ext\n.label spot = ext + 1\n")

    Program().assemble_as_object(str(source_file), tmp_path / "m.o")

    assert "got external reference 'ext + 1'" in caplog.text


def test_a_bare_extern_defers_to_its_name() -> None:
    assert deferred_link_expression(ExternalSymbolReference("ext"), Resolver()) == symbol("ext")


def test_an_operand_meeting_a_bare_extern_relocates_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from a816.context import AssemblyMode
    from a816.parse.nodes import expr as expr_module

    def meets_extern(_expression: object, _resolver: object) -> int:
        raise ExternalSymbolReference("ext")

    monkeypatch.setattr(expr_module, "eval_expression", meets_extern)
    resolver = Resolver()
    resolver.context.mode = AssemblyMode.OBJECT
    resolver.context.object_writer = ObjectWriter("unused.o")
    node = expr_module.ExpressionNode(expr_to_ast("ext"), resolver, Token(TokenType.IDENTIFIER, "ext"))

    assert (node.get_value(), node._deferred_expression) == (0, symbol("ext"))


def test_a_macro_argument_naming_a_known_label_is_an_alias(tmp_path: Path) -> None:
    """A label already placed when the macro expands binds the argument as
    a link expression, so the operand follows the module's placement."""
    source_file = tmp_path / "m.s"
    source_file.write_text(".macro m(target) {\n    lda.w target\n}\n*=0x8000\n    m(here + 1)\nhere:\n    nop\n")
    program = Program()
    program.resolver.current_scope.labels["here"] = 0
    program.assemble_as_object(str(source_file), tmp_path / "m.o")

    assert [str(e) for e in _relocations(ObjectFile.from_file(str(tmp_path / "m.o")))] == ["here + 1"]
