"""Single source of truth for `.import` module file resolution.

Search order (highest precedence first):
1. Bundled `@std/...` stdlib mapping
2. Each `search_paths` directory in declared order

Used by codegen (resolves `.import` at AST→Node time), module_builder
(resolves dependencies during the build pipeline), the LSP server
(go-to-definition for `.import` targets), and fluff lint S001 (follows
`.import` chains to discover cross-module struct names).
"""

from __future__ import annotations

from pathlib import Path

from a816.stdlib import resolve_stdlib_module


def resolve_module(module_name: str, extension: str, search_paths: list[Path]) -> Path | None:
    """Locate a module's source / object file.

    `module_name` may contain `/` (e.g. "battle/sram"). Tries the stdlib
    `@std/...` mapping first; on miss, joins `module_name + extension`
    against each search path and returns the first existing candidate.

    Returns the raw join (no `.resolve()`); callers that need a canonical
    path should call `.resolve()` themselves.
    """
    stdlib_path = resolve_stdlib_module(module_name, extension)
    if stdlib_path is not None:
        return stdlib_path

    module_file = module_name + extension
    for search_path in search_paths:
        candidate = search_path / module_file
        if candidate.exists():
            return candidate
    return None
