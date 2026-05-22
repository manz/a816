"""`.import` resolution + public-symbol extraction from source files."""

from __future__ import annotations

from pathlib import Path

from a816.module_loader import resolve_module
from a816.parse.ast.nodes import (
    AssignAstNode,
    AstNode,
    ImportAstNode,
    IncludeBinaryAstNode,
    LabelAstNode,
    LabelDeclAstNode,
    SymbolAffectationAstNode,
)
from a816.parse.codegen.base import GenNodes, MacroDefinitions, _code_gen, generators, logger
from a816.parse.nodes import ExternNode, LinkedModuleNode, NodeError
from a816.parse.tokens import Token
from a816.symbols import Resolver


def _import_search_paths(resolver: Resolver, file_info: Token) -> list[Path]:
    paths: list[Path] = []
    if file_info.position and file_info.position.file:
        from a816.util import uri_to_path

        paths.append(uri_to_path(file_info.position.file.filename).parent)
    paths.extend(resolver.context.module_paths)
    return paths


def _object_has_pool_allocs(obj_path: Path) -> bool:
    """Cheap check: parse the .o header to see if it carries any
    `.alloc` requests. Used to gate the direct-mode `.o` shortcut."""
    from a816.object_file import ObjectFile

    try:
        return bool(ObjectFile.from_file(str(obj_path)).pool_allocs)
    except (FileNotFoundError, ValueError):
        return False


def _import_from_object(
    module_name: str,
    obj_path: Path,
    resolver: Resolver,
    direct_mode: bool,
) -> GenNodes | None:
    from a816.object_file import ObjectFile, SymbolType

    try:
        obj_file = ObjectFile.from_file(str(obj_path))
    except (FileNotFoundError, ValueError):
        return None

    if direct_mode:
        symbols_data = [
            (name, address, sym_type.value, section.value) for name, address, sym_type, section in obj_file.symbols
        ]
        node = LinkedModuleNode(module_name, obj_file.regions, symbols_data, resolver, obj_file.relocatable)
        # Direct mode collapses object compilation + link into a single
        # resolver pass: surface the .o's pool decls so top-level
        # `.alloc` sites (and subsequent imports) can find the pools.
        node.imported_pool_decls = list(obj_file.pool_decls)
        return [node]

    return [ExternNode(name, resolver) for name, _, sym_type, _ in obj_file.symbols if sym_type == SymbolType.GLOBAL]


def _import_from_source(
    src_path: Path,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    direct_mode: bool,
) -> GenNodes | None:
    try:
        # Inline-parse when the importer is in direct mode OR when the
        # module is a stdlib source (always under `@std/...`, never
        # pre-compiled to a `.o`). Stdlib modules publish struct +
        # macro definitions that the importer needs at codegen time —
        # ExternNodes alone would lose those.
        is_stdlib = "stdlib" in src_path.parts
        if direct_mode or is_stdlib:
            from a816.parse.mzparser import A816Parser

            content = src_path.read_text(encoding="utf-8")
            result = A816Parser.parse_as_ast(content, str(src_path))
            return _code_gen(result.nodes, resolver, macro_definitions) if result.nodes else []

        return [ExternNode(symbol_name, resolver) for symbol_name in _extract_public_symbols_from_source(src_path)]
    except OSError:
        return None


def _canonical(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def generate_import(
    node: ImportAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    """Resolve an .import to ExternNode (object/parse mode) or LinkedModuleNode/inline source (direct).

    Source-inlined imports (direct mode, no `.o` available) are deduped by
    canonical path so transitive cascades don't re-execute the same module
    and trip idempotency checks (struct redef, etc). Object-mode imports
    keep the existing winner/loser mechanism intact — `LinkedModuleNode`
    already handles duplicates by marking earlier placements as losers.
    """
    module_name = node.module_name
    direct_mode = resolver.context.is_direct_mode and not resolver.context.is_object_mode
    search_paths = _import_search_paths(resolver, file_info)

    obj_path = resolve_module(module_name, ".o", search_paths)
    if obj_path:
        # Direct mode + .o-with-pool-allocs would emit each alloc twice
        # (once at the module's sandbox PC inside the .o, once at the
        # main allocator's pick) → write-overlap auditor noise + the
        # module bytes get clobbered. Fall through to source re-parse
        # so the main resolver owns alloc placement.
        if direct_mode and _object_has_pool_allocs(obj_path):
            pass
        else:
            nodes = _import_from_object(module_name, obj_path, resolver, direct_mode)
            if nodes is not None:
                return nodes

    src_path = resolve_module(module_name, ".s", search_paths)
    if src_path:
        if direct_mode:
            key = _canonical(src_path)
            if key in resolver.imported_module_paths:
                logger.info("`.import %r` deduped — already loaded from %s", module_name, key)
                return []
            resolver.imported_module_paths.add(key)
        nodes = _import_from_source(src_path, resolver, macro_definitions, direct_mode)
        if nodes is not None:
            return nodes

    raise NodeError(f'Module not found: "{module_name}"', file_info)


def _extract_public_symbols_from_source(source_path: Path) -> list[str]:
    """Extract public symbols from a source file using the AST parser.

    Public symbols are:
    - Labels that don't start with a dot (.)
    - Symbol assignments that don't start with a dot

    Uses the full parser to correctly handle comments, strings, conditionals, etc.
    """
    from a816.parse.mzparser import A816Parser

    symbols: list[str] = []
    content = source_path.read_text(encoding="utf-8")

    # Parse using the actual parser
    result = A816Parser.parse_as_ast(content, str(source_path))

    # Extract symbols from AST nodes
    _collect_public_symbols(result.nodes, symbols)

    return symbols


def _record_public_symbol(symbols: list[str], prefix: str, name: str) -> None:
    """Append `prefix.name` (or just `name`) to `symbols` unless either
    segment is private (underscore-prefixed) or the name is empty / a
    duplicate of something already recorded.
    """
    if not name:
        return
    if name.startswith("_") or (prefix and prefix.startswith("_")):
        return
    full = f"{prefix}.{name}" if prefix else name
    if full not in symbols:
        symbols.append(full)


def _emit_symbols_for_node(node: AstNode, prefix: str, symbols: list[str]) -> None:
    """Record any public names introduced by a single AST node. Doesn't
    descend into children — `_visit_for_public_symbols` handles recursion.
    """
    if isinstance(node, LabelAstNode):
        _record_public_symbol(symbols, prefix, node.label)
        return
    if isinstance(node, LabelDeclAstNode):
        _record_public_symbol(symbols, prefix, node.symbol)
        return
    if isinstance(node, SymbolAffectationAstNode | AssignAstNode):
        _record_public_symbol(symbols, prefix, node.symbol)
        return
    if isinstance(node, IncludeBinaryAstNode):
        base = node.file_path.replace("/", "_").replace(".", "_")
        _record_public_symbol(symbols, prefix, base)
        _record_public_symbol(symbols, prefix, f"{base}__size")


def _visit_for_public_symbols(nodes: list[AstNode], prefix: str, symbols: list[str]) -> None:
    """Walk `nodes`, emitting symbols and recursing into child containers.

    `.scope name { ... }` opens a dotted prefix for its members; every
    other node carries the current prefix down into `body` / `block` /
    `else_block` / `included_nodes`.
    """
    from a816.parse.ast.nodes import BlockAstNode, CompoundAstNode, ScopeAstNode

    for node in nodes:
        if isinstance(node, ScopeAstNode):
            inner = f"{prefix}.{node.name}" if prefix else node.name
            body = node.body
            if isinstance(body, BlockAstNode | CompoundAstNode):
                _visit_for_public_symbols(body.body, inner, symbols)
            continue

        _emit_symbols_for_node(node, prefix, symbols)
        _descend_into_children(node, prefix, symbols)


def _descend_into_children(node: AstNode, prefix: str, symbols: list[str]) -> None:
    """Recurse into the conventional container attributes carried by
    block-like AST nodes, keeping the prefix intact.
    """
    from a816.parse.ast.nodes import BlockAstNode, CompoundAstNode

    for attr in ("body", "block", "else_block"):
        child = getattr(node, attr, None)
        if isinstance(child, BlockAstNode | CompoundAstNode):
            _visit_for_public_symbols(child.body, prefix, symbols)
        elif isinstance(child, list):
            _visit_for_public_symbols(child, prefix, symbols)
    included = getattr(node, "included_nodes", None)
    if isinstance(included, list):
        _visit_for_public_symbols(included, prefix, symbols)


def _collect_public_symbols(nodes: list[AstNode], symbols: list[str]) -> None:
    """Recursively collect public symbols from AST nodes.

    Public symbols don't start with underscore (_); underscored symbols are
    treated as module-private.

    Labels and equates declared inside a `.scope name { ... }` block are
    surfaced with their dotted form (`name.label`) — the same way the
    object emitter exports them — so `.import` consumers can resolve the
    qualified names without re-declaring each as `.extern`.

    `.incbin "path"` registers the same auto-symbols `BinaryNode.pc_after`
    creates at codegen time (`<sanitized_path>` and `<sanitized_path>__size`)
    so `.import` consumers can resolve those bare names without an extra
    `.extern` declaration.
    """
    _visit_for_public_symbols(nodes, "", symbols)


generators["import"] = generate_import
