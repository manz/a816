"""The suite's shared-AST guard (`conftest.fingerprint`) sees what codegen could change."""

from __future__ import annotations

import pytest

from a816.parse.mzparser import A816Parser
from a816.parse.tokens import File
from tests.conftest import fingerprint

SOURCE = ".scope s {\n    X = 1\n}\nY = 2\n"


def _nodes() -> list[object]:
    return list(A816Parser.parse_as_ast(SOURCE, "guard.s").nodes)


def test_the_same_tree_has_one_fingerprint() -> None:
    first, second = _nodes(), _nodes()

    assert fingerprint(first) == fingerprint(second)


def test_a_reassigned_attribute_changes_it() -> None:
    nodes = _nodes()
    before = fingerprint(nodes)

    nodes[1].docstring = "changed"  # type: ignore[attr-defined]

    assert fingerprint(nodes) != before


def test_a_nested_change_changes_it() -> None:
    nodes = _nodes()
    before = fingerprint(nodes)

    nodes[0].body.body[0].docstring = "changed"  # type: ignore[attr-defined]

    assert fingerprint(nodes) != before


def test_a_parsed_body_cannot_grow() -> None:
    scope = _nodes()[0]

    with pytest.raises(AttributeError):
        scope.body.body.append(scope)  # type: ignore[attr-defined]


def test_source_text_behind_tokens_is_not_walked() -> None:
    file = File("guard.s")
    file.lines = ["one"]
    before = fingerprint([file])

    file.lines.append("two")

    assert fingerprint([file]) == before
