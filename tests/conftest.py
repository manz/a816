"""Suite-wide guard: codegen must never mutate a cached AST.

Imported sources (`ParsedImport`) and `.include` bodies are parsed once and
shared by every module and site that uses them. That is only sound while
codegen reads those nodes and builds fresh ones. Every test fingerprints each
cached tree when it is first handed out and checks it is unchanged at
teardown, so a mutation anywhere in codegen fails the test that triggers it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from enum import Enum
from pathlib import Path
from typing import Any

import pytest

from a816.parse.codegen import modules
from a816.parse.parser_states import directives
from a816.parse.tokens import File

_ATOMS = (str, int, float, bool, bytes, type(None), Enum, Path)


def fingerprint(root: object) -> str:
    """Digest of everything reachable from `root`: values, container order,
    attributes. `File` objects (the source text behind tokens) are skipped."""
    digest = hashlib.sha256()
    seen: set[int] = set()
    stack: list[Any] = [root]
    while stack:
        obj = stack.pop()
        if isinstance(obj, _ATOMS):
            digest.update(f"{type(obj).__name__}:{obj!r};".encode())
            continue
        if isinstance(obj, File) or id(obj) in seen:
            digest.update(b"@;")
            continue
        seen.add(id(obj))
        digest.update(f"<{type(obj).__name__}>".encode())
        stack.extend(reversed(_children(obj)))
    return digest.hexdigest()


def _children(obj: object) -> list[Any]:
    if isinstance(obj, list | tuple):
        return [len(obj), *obj]
    if isinstance(obj, dict):
        return [len(obj), *(part for item in obj.items() for part in item)]
    if isinstance(obj, set | frozenset):
        return sorted(repr(item) for item in obj)
    slots = [name for cls in type(obj).__mro__ for name in getattr(cls, "__slots__", ())]
    fields = {name: getattr(obj, name) for name in slots if hasattr(obj, name)}
    fields.update(getattr(obj, "__dict__", {}))
    return [part for name in sorted(fields) for part in (name, fields[name])]


@pytest.fixture(autouse=True)
def cached_asts_stay_untouched(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    recorded: dict[int, tuple[str, object, str]] = {}

    def remember(label: str, tree: object) -> None:
        if id(tree) not in recorded:
            recorded[id(tree)] = (label, tree, fingerprint(tree))

    parse_import = modules._parse_import
    included_ast = directives._included_ast

    def watched_parse_import(src_path: Path, resolver: Any) -> modules.ParsedImport | None:
        parsed = parse_import(src_path, resolver)
        if parsed is not None:
            remember(f"import {src_path}", parsed.result.nodes)
        return parsed

    def watched_included_ast(resolved_path: str, include_paths: list[Path]) -> tuple[Any, ...]:
        body = included_ast(resolved_path, include_paths)
        remember(f"include {resolved_path}", body)
        return body

    monkeypatch.setattr(modules, "_parse_import", watched_parse_import)
    monkeypatch.setattr(directives, "_included_ast", watched_included_ast)
    yield
    changed = [label for label, tree, before in recorded.values() if fingerprint(tree) != before]
    assert not changed, f"codegen mutated a shared AST: {', '.join(changed)}"
