"""Single source of truth for `.import` module file resolution.

Search order (highest precedence first):
1. Bundled `@std/...` stdlib mapping
2. Each `search_paths` directory in declared order

Used by codegen (resolves `.import` at AST→Node time), module_builder
(resolves dependencies during the build pipeline), the LSP server
(go-to-definition for `.import` targets), and fluff lint S001 (follows
`.import` chains to discover cross-module struct names).

SPC700 code (`current_arch`) finds `.import "dsp"` as `dsp.spc.s` first.
"""

from __future__ import annotations

from pathlib import Path

from a816.cpu.arch import is_spc700
from a816.stdlib import resolve_stdlib_module


def resolve_module(module_name: str, extension: str, search_paths: list[Path]) -> Path | None:
    """Locate a module's source / object file.

    `module_name` may contain `/` (e.g. "battle/sram"). Tries the stdlib
    `@std/...` mapping first; on miss, joins `module_name + extension`
    against each search path and returns the first existing candidate.
    SPC700 sources try `module_name.spc.s` before `module_name.s`.

    Returns the raw join (no `.resolve()`); callers that need a canonical
    path should call `.resolve()` themselves.
    """
    if extension == ".s" and is_spc700() and not module_name.endswith(".spc"):
        found = _resolve_module(f"{module_name}.spc", extension, search_paths)
        if found is not None:
            return found
    return _resolve_module(module_name, extension, search_paths)


def _resolve_module(module_name: str, extension: str, search_paths: list[Path]) -> Path | None:
    stdlib_path = resolve_stdlib_module(module_name, extension)
    if stdlib_path is not None:
        return stdlib_path

    # Two name shapes per module: the raw `a/b/c` form (used for source
    # files under nested dirs) and the sanitised `a_b_c` form (used by
    # `ModuleBuilder._get_obj_path` so every `.o` lives flat under
    # `build/obj/`). Try raw first, then sanitised — keeps source-file
    # discovery untouched and lets `.o` lookups find sanitised artefacts
    # without a second loader for the object case.
    candidates = [module_name + extension]
    sanitised = module_name.replace("/", "_") + extension
    if sanitised != candidates[0]:
        candidates.append(sanitised)
    for search_path in search_paths:
        for candidate_name in candidates:
            candidate = search_path / candidate_name
            if candidate.exists():
                return candidate
    return None
