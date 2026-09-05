"""Behaviour pins for the memoised `.include` body.

A header pulled in from many sites used to be scanned and parsed once per
site. These tests pin that it is parsed once per revision instead, and
that a revision is noticed.
"""

from __future__ import annotations

import os
from pathlib import Path

from a816.parse.ast.nodes import IncludeAstNode
from a816.parse.mzparser import A816Parser
from a816.parse.parser_states.directives import clear_include_ast_cache


def _write(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.write_text(text, encoding="utf-8")
    return path


def _include_nodes(nodes: list[object]) -> list[IncludeAstNode]:
    return [node for node in nodes if isinstance(node, IncludeAstNode)]


def _parse(source_path: Path) -> list[object]:
    result = A816Parser.parse_as_ast(source_path.read_text(encoding="utf-8"), str(source_path))
    return list(result.nodes)


def test_two_sites_share_one_parsed_body(tmp_path: Path) -> None:
    """The pin: including the same header twice parses it once."""
    clear_include_ast_cache()
    _write(tmp_path, "header.i", "CONST = 0x1234\n")
    main = _write(tmp_path, "main.s", '.include "header.i"\n.include "header.i"\n')

    includes = _include_nodes(_parse(main))

    assert len(includes) == 2
    assert includes[0].included_nodes is includes[1].included_nodes


def test_separate_headers_do_not_collide(tmp_path: Path) -> None:
    clear_include_ast_cache()
    _write(tmp_path, "a.i", "A = 1\n")
    _write(tmp_path, "b.i", "B = 2\n")
    main = _write(tmp_path, "main.s", '.include "a.i"\n.include "b.i"\n')

    includes = _include_nodes(_parse(main))

    assert includes[0].included_nodes is not includes[1].included_nodes


def test_a_rewritten_header_is_reparsed(tmp_path: Path) -> None:
    """Stale bodies would be worse than no cache at all."""
    clear_include_ast_cache()
    header = _write(tmp_path, "header.i", "CONST = 1\n")
    main = _write(tmp_path, "main.s", '.include "header.i"\n')

    first = _include_nodes(_parse(main))[0].included_nodes

    header.write_text("CONST = 2\nOTHER = 3\n", encoding="utf-8")
    stamp = header.stat()
    os.utime(header, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000))

    second = _include_nodes(_parse(main))[0].included_nodes

    assert second is not first
    assert len(second) > len(first)


def test_clearing_the_cache_forces_a_reparse(tmp_path: Path) -> None:
    clear_include_ast_cache()
    _write(tmp_path, "header.i", "CONST = 1\n")
    main = _write(tmp_path, "main.s", '.include "header.i"\n')

    first = _include_nodes(_parse(main))[0].included_nodes
    clear_include_ast_cache()
    second = _include_nodes(_parse(main))[0].included_nodes

    assert second is not first
    assert len(second) == len(first)


def test_nested_includes_are_shared_too(tmp_path: Path) -> None:
    clear_include_ast_cache()
    _write(tmp_path, "leaf.i", "LEAF = 1\n")
    _write(tmp_path, "mid_a.i", '.include "leaf.i"\n')
    _write(tmp_path, "mid_b.i", '.include "leaf.i"\n')
    main = _write(tmp_path, "main.s", '.include "mid_a.i"\n.include "mid_b.i"\n')

    mids = _include_nodes(_parse(main))
    leaf_a = _include_nodes(list(mids[0].included_nodes))[0]
    leaf_b = _include_nodes(list(mids[1].included_nodes))[0]

    assert leaf_a.included_nodes is leaf_b.included_nodes
