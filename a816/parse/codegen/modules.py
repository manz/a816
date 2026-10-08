"""`.import` resolution + public-symbol extraction from source files."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, overload

from a816.error_codes import E_CODEGEN_IMPORT_IN_PLACEMENT, E_SYMBOL_MODULE_NOT_FOUND
from a816.module_loader import resolve_module
from a816.object_file import BusMapping, ObjectFile, PoolDecl, SymbolType
from a816.parse.ast.nodes import (
    AssignAstNode,
    AstNode,
    BlockAstNode,
    CastValueExprNode,
    CommentAstNode,
    CompoundAstNode,
    DocstringAstNode,
    ExpressionAstNode,
    ExternAstNode,
    ForAstNode,
    IfAstNode,
    ImportAstNode,
    IncludeAstNode,
    IncludeBinaryAstNode,
    LabelAstNode,
    LabelDeclAstNode,
    MacroAstNode,
    MapAstNode,
    PoolAstNode,
    ReclaimAstNode,
    ScopeAstNode,
    StructAstNode,
    SymbolAffectationAstNode,
)
from a816.parse.codegen.base import GenNodes, MacroDefinitions, _code_gen, declared_name, generators, logger
from a816.parse.codegen.structs import declare_bus_mapping
from a816.parse.nodes import ExternNode, LinkedModuleNode, NodeError
from a816.parse.tokens import Token
from a816.symbols import Resolver, _is_exportable

if TYPE_CHECKING:
    from a816.incbin_names import PathName
    from a816.parse.mzparser import ParserResult

# Declarations whose effect must be visible to codegen of the importer
# (struct/macro/const defs, `.map` bus layout, nested imports, pool
# decls, reclaims, docstrings). Everything else is runtime-bound: the
# imported module's own `.o` emits it, and its names surface as
# `ExternNode`s so the linker wires them up at link time.
_INLINE_IMPORT_TYPES: tuple[type[AstNode], ...] = (
    StructAstNode,
    MacroAstNode,
    MapAstNode,
    SymbolAffectationAstNode,
    AssignAstNode,
    LabelDeclAstNode,
    ImportAstNode,
    PoolAstNode,
    ReclaimAstNode,
    DocstringAstNode,
    ExpressionAstNode,
    CommentAstNode,
)


def _import_search_paths(resolver: Resolver) -> list[Path]:
    """Where `.import` looks, in order.

    The configured module paths, and nothing else. The importing file's
    own directory used to come first, which let a neighbour shadow a
    project-wide module: `.import "items"` from `src/ingame/` resolved
    to `src/ingame/items.s` rather than `src/items.s`, compiled the
    wrong file under that name, and failed somewhere else entirely with
    the shared module's declarations missing.

    A module under a subdirectory is addressed by its path,
    `ingame/items`, so a bare name means the same module wherever it is
    imported from.
    """
    return list(resolver.context.module_paths)


@dataclass(frozen=True)
class _ImportView:
    """What an object-mode importer reads from an imported `.o`."""

    provided_names: tuple[str, ...]
    pool_decls: tuple[PoolDecl, ...]
    bus_mappings: tuple[BusMapping, ...]
    has_pool_allocs: bool


def _import_view(obj_path: Path) -> _ImportView:
    """The imported `.o`'s import view, decoded once per file version.

    Every module of a build imports the same core `.o` files; decoding each
    one again per importer made a cold build quadratic in modules. The stat
    in the cache key picks up a `.o` rebuilt earlier in the same build.
    Raises like `ObjectFile.from_file`.
    """
    stat = obj_path.stat()
    return _decode_import_view(str(obj_path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=1024)
def _decode_import_view(path: str, _mtime_ns: int, _size: int) -> _ImportView:
    obj_file = ObjectFile.from_file(path)
    return _ImportView(
        provided_names=tuple(_object_provided_names(obj_file)),
        pool_decls=tuple(obj_file.pool_decls),
        bus_mappings=tuple(obj_file.bus_mappings),
        has_pool_allocs=bool(obj_file.pool_allocs),
    )


def _object_has_pool_allocs(obj_path: Path) -> bool:
    """Cheap check: does the .o carry any `.alloc` requests? Used to
    gate the direct-mode `.o` shortcut."""
    try:
        return _import_view(obj_path).has_pool_allocs
    except (FileNotFoundError, ValueError):
        return False


def _import_from_object(
    module_name: str,
    obj_path: Path,
    resolver: Resolver,
    direct_mode: bool,
    file_info: Token,
) -> GenNodes | None:
    if direct_mode:
        return _import_linked_module(module_name, obj_path, resolver, file_info)
    try:
        view = _import_view(obj_path)
    except (FileNotFoundError, ValueError):
        return None

    for mapping in view.bus_mappings:
        declare_bus_mapping(resolver, mapping, file_info)
    # Object mode: importer is being compiled to its own `.o`. Surface
    # the imported `.o`'s pool decls in the importer's resolver so any
    # `.alloc NAME in <pool>` site at the importer's top level resolves
    # at codegen. Tagged as imported so the resolver knows the linker
    # will handle final placement.
    _register_imported_object_pools(view.pool_decls, resolver)

    # Each `.o` owns only what it defines (GLOBAL). EXTERNAL re-export
    # cascades quadratically across diamond imports — a module that
    # `.import`s a facade ends up extern-stubbing every transitive
    # symbol reachable through the facade, and the symbol table blows
    # past the `<H>` 65535 limit within 3-4 hops. Importers must
    # `.import` direct deps explicitly; the resolver's own dedup
    # (`imported_module_paths`) handles the diamond.
    return [ExternNode(name, resolver) for name in view.provided_names]


def _import_linked_module(module_name: str, obj_path: Path, resolver: Resolver, file_info: Token) -> GenNodes | None:
    """Direct mode collapses object compilation + link into a single
    resolver pass: the whole `.o` comes in, and its pool decls surface so
    top-level `.alloc` sites (and subsequent imports) can find the pools."""
    try:
        obj_file = ObjectFile.from_file(str(obj_path))
    except (FileNotFoundError, ValueError):
        return None
    for mapping in obj_file.bus_mappings:
        declare_bus_mapping(resolver, mapping, file_info)
    symbols_data = [
        (name, address, sym_type.value, section.value) for name, address, sym_type, section in obj_file.symbols
    ]
    node = LinkedModuleNode(module_name, obj_file.sections, symbols_data, resolver, obj_file.relocatable)
    node.imported_pool_decls = list(obj_file.pool_decls)
    return [node]


def _object_provided_names(obj_file: ObjectFile) -> list[str]:
    """GLOBAL symbols plus exported link-time aliases, in `.o` order.

    Aliases (`sc.fd = sc.here`) live in the `.o` alias table, not the
    symbol table; the importer still needs an extern stub to reference
    them, since a scope alias over a scope label can't be re-derived
    from the inlined declarations.
    """
    names = [name for name, _, sym_type, _ in obj_file.symbols if sym_type == SymbolType.GLOBAL]
    names += [name for name, _ in obj_file.aliases if _is_exportable(name)]
    return list(dict.fromkeys(names))


def _register_imported_object_pools(pool_decls: tuple[PoolDecl, ...], resolver: Resolver) -> None:
    """Mirror the imported `.o`'s pool decls into the importer's
    resolver.pools so `.alloc ... in POOL` sites resolve at codegen, and
    publish their `.capacity` / `.fragments` / `.largest_chunk` as a
    local `.pool` does. Idempotent: identical re-registrations are skipped."""
    from a816.parse.codegen.pool import _publish_pool_stats
    from a816.pool import Pool

    for decl in pool_decls:
        if decl.name in resolver.pools:
            continue
        pool = Pool.from_decl(decl)
        resolver.pools[decl.name] = pool
        _publish_pool_stats(decl.name, pool, resolver)


def _import_from_source(
    src_path: Path,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    direct_mode: bool,
    module_name: str,
) -> GenNodes | None:
    """Bring an `.import`ed source module into the importer.

    Direct mode = importer building the final ROM in one resolver
    pass. Every node from the imported module runs inline so its
    bytes land in the right place.

    Object mode = importer being compiled to its own `.o`. The
    imported module's `.o` owns its emitted bytes + runtime symbols;
    the importer only needs:
      * compile-time-only nodes (structs, macros, constants, typed
        binds, sub-imports) inlined so codegen of THIS module can
        resolve them, and
      * extern stubs for runtime-bound names (labels, alloc names,
        `.incbin` auto-symbols) so cross-module refs link.

    Per-node split lives in `_import_object_mode`.
    """
    parsed = _parse_import(src_path, resolver)
    if parsed is None:
        return None
    nodes = parsed.result.nodes
    if not nodes:
        return []
    resolver.imported_path_names.update(parsed.path_names())
    if direct_mode:
        return _code_gen(nodes, resolver, macro_definitions)
    _register_private_names(parsed.private_names(src_path), module_name, resolver)
    return _import_object_mode(parsed.object_mode_plan(), resolver, macro_definitions)


@dataclass
class ParsedImport:
    """One imported source as the build caches it: its AST, the lookups that
    missed while parsing it, and what each importer derives from that AST
    (object-mode plan, private declarations) once first needed. The AST is
    immutable, so neither can go stale."""

    result: ParserResult
    misses: set[str]
    plan: _ImportPlan | None = None
    privates: _PrivateNames | None = None
    incbin_names: dict[str, PathName] | None = None

    def path_names(self) -> dict[str, PathName]:
        """The path-derived `.incbin` names the module binds (W0001)."""
        if self.incbin_names is None:
            from a816.incbin_names import imported_path_names

            self.incbin_names = imported_path_names(self.result.nodes)
        return self.incbin_names

    def object_mode_plan(self) -> _ImportPlan:
        if self.plan is None:
            self.plan = _plan_object_mode(self.result.nodes)
        return self.plan

    def private_names(self, src_path: Path) -> _PrivateNames:
        if self.privates is None:
            self.privates = _private_names(self.result.nodes, src_path)
        return self.privates


def _parse_import(src_path: Path, resolver: Resolver) -> ParsedImport | None:
    """The imported source's AST, from the build's shared cache when there is
    one. A cache hit replays the lookups that missed while parsing, so the
    importer's recorded inputs match a fresh parse. None when unreadable."""
    from a816.build_inputs import recording_misses, replay_misses
    from a816.parse.mzparser import A816Parser

    cache = resolver.context.import_asts
    key = str(src_path)
    if cache is not None and key in cache:
        parsed = cache[key]
        replay_misses(parsed.misses)
        return parsed
    try:
        content = src_path.read_text(encoding="utf-8")
    except OSError:
        return None
    context = resolver.context
    include_paths = context.include_paths if context.import_include_paths is None else context.import_include_paths
    with recording_misses() as misses:
        parsed = ParsedImport(A816Parser.parse_as_ast(content, key, include_paths=list(include_paths)), misses)
    replay_misses(misses)
    if cache is not None:
        cache[key] = parsed
    return parsed


_PRIVATE_DECLARATION_TYPES = (SymbolAffectationAstNode, AssignAstNode, LabelDeclAstNode, MacroAstNode, StructAstNode)


# A module's private (`_`) top-level names, and the files that may use them.
_PrivateNames = tuple[tuple[str, ...], frozenset[str]]


def _private_names(nodes: Sequence[AstNode], src_path: Path) -> _PrivateNames:
    from a816.symbols import _canonical_file

    files = frozenset({_canonical_file(str(src_path)), *(_canonical_file(f) for f in _included_files(nodes))})
    names = (declared_name(node) for node in _top_level_declarations(nodes))
    return tuple(name for name in names if name is not None and name.startswith("_")), files


def _register_private_names(privates: _PrivateNames, module_name: str, resolver: Resolver) -> None:
    """Record the module owning each private (`_`) declaration it hands to
    the importer: the importer may not name them (`Resolver.foreign_private_owner`)."""
    names, files = privates
    for name in names:
        resolver.private_owners.setdefault(name, (module_name, files))


def _included_files(nodes: Sequence[AstNode]) -> list[str]:
    out: list[str] = []
    for node in nodes:
        if isinstance(node, IncludeAstNode):
            if node.resolved_path:
                out.append(node.resolved_path)
            out.extend(_included_files(node.included_nodes))
    return out


def _top_level_declarations(nodes: Sequence[AstNode]) -> list[AstNode]:
    out: list[AstNode] = []
    for node in nodes:
        if isinstance(node, _PRIVATE_DECLARATION_TYPES):
            out.append(node)
        elif isinstance(node, IncludeAstNode):
            out.extend(_top_level_declarations(node.included_nodes))
    return out


@dataclass(frozen=True)
class _Externs:
    """Runtime names the imported `.o` owns: extern stubs in the importer."""

    names: tuple[str, ...]


@dataclass(frozen=True)
class _TypedBind:
    """`view := (base as T)` over an imported runtime name: the view and its
    fields become externs, named from the importer's struct layout."""

    node: AssignAstNode


@dataclass(frozen=True)
class _Inline:
    """Compile-time nodes the importer's resolver evaluates itself."""

    nodes: list[AstNode]


@dataclass(frozen=True)
class _ImportPlan:
    """What an object-mode `.import` does with one source, worked out once
    from its AST and replayed against every importer's resolver.

    `reservations` pairs each public reservation with its struct type (None
    for a flat one); `steps` keeps the source order of externs and inlines.
    """

    reservations: tuple[tuple[str, str | None], ...]
    steps: tuple[_Externs | _TypedBind | _Inline, ...]


def _plan_object_mode(nodes: list[AstNode]) -> _ImportPlan:
    """Per-node classifier for object-mode `.import`s.

    Inlines compile-time-only nodes (struct, macro, constant, typed
    bind, `.label`, nested `.import`); `.if` / `.scope` / `.for` /
    `.include` are inlined with their bodies cut down to declarations
    (`_declarations_only`). Runtime-bound names become externs so
    cross-module references resolve at link time.
    """
    runtime = _runtime_names_in(nodes) | {node.symbol for node in nodes if isinstance(node, ExternAstNode)}
    steps: list[_Externs | _TypedBind | _Inline] = []
    for node in nodes:
        if isinstance(node, AssignAstNode) and _mentions(node.value, runtime):
            steps.append(_TypedBind(node) if _is_typed_bind(node) else _Externs((node.symbol,)))
        elif isinstance(node, _INLINE_IMPORT_TYPES):
            steps.append(_Inline([node]))
        elif isinstance(node, IfAstNode | ScopeAstNode | ForAstNode | IncludeAstNode):
            steps.append(_Inline(_declarations_only([node], bare_names=True)))
        elif names := _runtime_extern_names(node):
            steps.append(_Externs(tuple(names)))
    return _ImportPlan(_reservations_in(nodes), tuple(steps))


def _import_object_mode(plan: _ImportPlan, resolver: Resolver, macro_definitions: MacroDefinitions) -> GenNodes:
    """Replay an import plan against the importer's resolver.

    Names contributed by the inline pass land in
    `Resolver.imported_symbol_names`; `_export_object_symbols` skips
    them so the importer's `.o` doesn't re-export symbols owned by
    its dependency. The owning module's `.o` is the single source.
    """
    out: GenNodes = []
    root = resolver.scopes[0]
    before_labels = set(root.labels.keys())
    before_symbols = set(root.symbols.keys())
    _record_imported_reservations(plan.reservations, resolver)

    for step in plan.steps:
        if isinstance(step, _Inline):
            out.extend(_code_gen(step.nodes, resolver, macro_definitions) or [])
        elif isinstance(step, _TypedBind):
            out.extend(ExternNode(name, resolver) for name in _typed_bind_names(step.node, resolver))
        else:
            out.extend(ExternNode(name, resolver) for name in step.names)

    resolver.imported_symbol_names.update(set(root.labels.keys()) - before_labels)
    resolver.imported_symbol_names.update(set(root.symbols.keys()) - before_symbols)
    return out


def _reservations_in(nodes: list[AstNode]) -> tuple[tuple[str, str | None], ...]:
    """An imported module's public reservations, each with its struct type
    (None for a flat one)."""
    from a816.parse.ast.nodes import AllocAstNode, ReserveTypedAstNode
    from a816.parse.ast.visitor import walk

    found: list[tuple[str, str | None]] = []
    for node in walk(nodes):
        if isinstance(node, ReserveTypedAstNode) and not node.name.startswith("_"):
            found.append((node.name, node.type_name))
        elif isinstance(node, AllocAstNode) and node.reserve and node.name and not node.name.startswith("_"):
            found.append((node.name, None))
    return tuple(found)


def _record_imported_reservations(reservations: tuple[tuple[str, str | None], ...], resolver: Resolver) -> None:
    """Make an imported module's reservations known to `sizeof`.

    A typed one's size is its (inlined) struct's; a flat one's is the
    owner's `NAME.__size`, which reaches the importer as an extern.
    """
    for name, type_name in reservations:
        resolver.reservation_sizes[name] = None if type_name is None else resolver.struct_sizes.get(type_name)


def _is_typed_bind(node: AssignAstNode) -> bool:
    """`view := (base as T)`: binds the view and one name per field."""
    return len(node.value.tokens) == 1 and isinstance(node.value.tokens[0], CastValueExprNode)


def _typed_bind_names(node: AssignAstNode, resolver: Resolver) -> list[str]:
    cast = node.value.tokens[0]
    assert isinstance(cast, CastValueExprNode)
    fields = resolver.struct_layouts.get(cast.type_name, [])
    return [node.symbol, *(f"{node.symbol}.{field_path}" for field_path, _offset, _width in fields)]


def _declarations_only(nodes: Sequence[AstNode], bare_names: bool) -> list[AstNode]:
    """Copy of `nodes` with every byte-emitting statement dropped.

    `.if` / `.scope` / `.for` / `.include` keep their shape so the
    importer evaluates conditions and scope prefixes exactly as the
    owning module does, but their bodies hold declarations only. The
    owning module's `.o` carries the bytes; inlining them here would
    re-emit the code into the importer's `.o`.

    Runtime names (labels, `.alloc` names, `.incbin` symbols) become
    `.extern`s where they would publish bare (module level, possibly
    under `.if`). Inside a named scope they publish dotted
    (`scope.label`) and the owning `.o` already supplies those externs;
    `.for` iterations live in internal scopes and publish nothing.
    """
    out: list[AstNode] = []
    for node in nodes:
        out.extend(_declarations_of(node, bare_names))
    return out


def _declarations_of(node: AstNode, bare_names: bool) -> list[AstNode]:
    if isinstance(node, _INLINE_IMPORT_TYPES):
        return [node]
    if isinstance(node, IfAstNode):
        return _pruned_if(node, bare_names)
    if isinstance(node, ScopeAstNode):
        body = _drop_runtime_bound(_declarations_only(node.body.body, bare_names=False), node.body.body)
        return [ScopeAstNode(node.name, _block(body, node.body), node.file_info, node.docstring)] if body else []
    if isinstance(node, ForAstNode):
        body = _declarations_only(node.body.body, bare_names=False)
        return (
            [ForAstNode(node.symbol, node.min_value, node.max_value, _block(body, node.body), node.file_info)]
            if body
            else []
        )
    if isinstance(node, IncludeAstNode):
        included = _declarations_only(node.included_nodes, bare_names)
        return [IncludeAstNode(node.file_path, included, node.file_info, node.resolved_path)]
    if not bare_names:
        return []
    return [ExternAstNode(name, node.file_info) for name in _runtime_extern_names(node)]


_IDENT_HEAD_RE = re.compile(r"(?<![\w.])([A-Za-z_]\w*)", re.ASCII)


def _drop_runtime_bound(declarations: list[AstNode], scope_body: Sequence[AstNode]) -> list[AstNode]:
    """Drop `=` / `:=` declarations that depend on the scope's own runtime names.

    The importer never sees the scope's labels (they're dropped with the
    bytes), so `fd = here` can't be evaluated there. The owning `.o`
    exports the result as the alias `scope.fd`, which reaches the importer
    as an extern stub. Dependents (`gd = fd + 1`) are runtime-bound too.
    """
    runtime = _runtime_names_in(scope_body)
    kept: list[AstNode] = []
    for node in declarations:
        if isinstance(node, SymbolAffectationAstNode | AssignAstNode) and _mentions(node.value, runtime):
            runtime.add(node.symbol)
            continue
        kept.append(node)
    return kept


def _mentions(value: ExpressionAstNode, names: set[str]) -> bool:
    return any(match in names for match in _IDENT_HEAD_RE.findall(value.to_canonical()))


def _runtime_names_in(nodes: Sequence[AstNode]) -> set[str]:
    """Labels and other runtime names a scope body binds in its own scope,
    including those declared in `.if` / `.else` bodies (no scope of their own)."""
    names: set[str] = set()
    for node in nodes:
        if isinstance(node, LabelAstNode):
            names.add(node.label)
        names.update(_runtime_extern_names(node))
        if isinstance(node, IfAstNode):
            names |= _runtime_names_in(_if_branches(node))
    return names


def _if_branches(node: IfAstNode) -> list[AstNode]:
    return [*node.block.body, *(node.else_block.body if node.else_block else ())]


def _pruned_if(node: IfAstNode, bare_names: bool) -> list[AstNode]:
    then_body = _declarations_only(node.block.body, bare_names)
    else_body = _declarations_only(node.else_block.body, bare_names) if node.else_block else []
    if not then_body and not else_body:
        return []
    else_block = _block(else_body, node.else_block) if node.else_block else None
    return [IfAstNode(node.expression, _block(then_body, node.block), else_block, node.file_info)]


@overload
def _block(body: list[AstNode], original: BlockAstNode) -> BlockAstNode: ...
@overload
def _block(body: list[AstNode], original: CompoundAstNode) -> CompoundAstNode: ...
def _block(body: list[AstNode], original: BlockAstNode | CompoundAstNode) -> BlockAstNode | CompoundAstNode:
    if isinstance(original, CompoundAstNode):
        return CompoundAstNode(body, original.file_info)
    return BlockAstNode(body, original.file_info)


def _runtime_extern_names(node: object) -> list[str]:
    """Names a runtime-bound node would publish in its owning `.o`,
    surfaced to the importer as externs.

    Underscore-prefixed names are LOCAL by convention (matches
    `_classify_object_symbol`) and never get exported, so we don't
    extern them either — referencing a `_local` symbol from another
    module is a use error, not something the linker should pretend
    to support.

    Alloc + relocate bodies get walked for nested public names —
    `.incbin` auto-symbols + labels declared inside the body are
    visible to downstream importers, so they need extern stubs too.
    """
    from a816.parse.ast.nodes import (
        AllocAstNode,
        IncludeBinaryAstNode,
        LabelAstNode,
        RelocateAstNode,
    )

    def _public(names: list[str]) -> list[str]:
        return [n for n in names if not n.startswith("_")]

    if isinstance(node, LabelAstNode):
        return _public([node.label])
    if isinstance(node, AllocAstNode):
        return _public([node.name, f"{node.name}.__size"]) if node.name else []
    if isinstance(node, RelocateAstNode):
        return list(_public([node.symbol]))
    if isinstance(node, IncludeBinaryAstNode):
        # Auto-symbols from `.incbin` aren't user-named — leading
        # underscores in the sanitised path (e.g. `___assets_...` from
        # `../assets/...`) are encoding artefacts, not privacy markers.
        # Always expose.
        base = node.file_path.replace("/", "_").replace(".", "_")
        return [base, f"{base}__size"]
    return []


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
    _reject_import_in_placement(resolver, file_info)
    importer_cursor = resolver.star_eq_cursor_active
    resolver.star_eq_cursor_active = False
    try:
        return _resolve_import(node.module_name, resolver, macro_definitions, file_info)
    finally:
        resolver.star_eq_cursor_active = importer_cursor


def _reject_import_in_placement(resolver: Resolver, file_info: Token) -> None:
    """Refuse an `.import` that would ride a placement context.

    The `*= ADDR / .import "mod"` chain and `.import` inside an `.alloc`
    body used to be accepted and silently placed the module wherever the
    module itself said (or nowhere sensible). Modules own their placement
    through `.alloc at` / `.alloc in POOL`; imports belong in the prelude.
    """
    if resolver.placement_body_depth > 0:
        where = "inside an `.alloc` / `.relocate` body"
    elif resolver.star_eq_cursor_active:
        where = "after a `*=` placement"
    else:
        return
    raise NodeError(
        f"`.import` {where}: modules own their placement",
        file_info,
        code=str(E_CODEGEN_IMPORT_IN_PLACEMENT),
        hint="move the `.import` to the top of the file and place code inside the module with `.alloc at` / `.alloc in`",
    )


def _resolve_import(
    module_name: str,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    """Locate the module and bring it in as `.o` stubs, a linked module or inlined source."""
    direct_mode = resolver.context.is_direct_mode and not resolver.context.is_object_mode
    search_paths = _import_search_paths(resolver)

    obj_path = resolve_module(module_name, ".o", search_paths)
    src_path = resolve_module(module_name, ".s", search_paths)

    if obj_path and not direct_mode and src_path:
        return _paired_object_and_source_import(module_name, obj_path, src_path, resolver, macro_definitions, file_info)

    if obj_path and not (direct_mode and _object_has_pool_allocs(obj_path)):
        nodes = _import_from_object(module_name, obj_path, resolver, direct_mode, file_info)
        if nodes is not None:
            return nodes

    if src_path:
        return _source_import(module_name, src_path, resolver, macro_definitions, direct_mode, file_info)

    raise NodeError(f'Module not found: "{module_name}"', file_info, code=str(E_SYMBOL_MODULE_NOT_FOUND))


def _paired_object_and_source_import(
    module_name: str,
    obj_path: Path,
    src_path: Path,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    """Object-mode import: pair `.o` (runtime extern stubs) with source
    (compile-time inline). Neither half is complete on its own — `.o`
    can't carry struct defs / macros (compile-time only), source
    re-running would duplicate the runtime symbols the `.o` owns.
    `imported_symbol_names` (set by the inline classifier) prevents
    the importer's `.o` from re-exporting the inline-contributed
    runtime symbols that overlap with the extern stubs."""
    if _is_imported(src_path, resolver, module_name):
        return []
    _mark_imported(src_path, resolver)
    extern_nodes = _import_from_object(module_name, obj_path, resolver, direct_mode=False, file_info=file_info) or []
    inline_nodes = (
        _import_from_source(src_path, resolver, macro_definitions, direct_mode=False, module_name=module_name) or []
    )
    return extern_nodes + inline_nodes


def _source_import(
    module_name: str,
    src_path: Path,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    direct_mode: bool,
    file_info: Token,
) -> GenNodes:
    if direct_mode:
        if _is_imported(src_path, resolver, module_name):
            return []
        _mark_imported(src_path, resolver)
    nodes = _import_from_source(src_path, resolver, macro_definitions, direct_mode, module_name)
    if nodes is None:
        raise NodeError(f'Module not found: "{module_name}"', file_info, code=str(E_SYMBOL_MODULE_NOT_FOUND))
    return nodes


def _is_imported(src_path: Path, resolver: Resolver, module_name: str) -> bool:
    """Pure check: has `src_path` already been imported in this resolver?
    Logs a dedup notice on the hit path so the build log shows why a
    transitive `.import` resolved to nothing."""
    key = _canonical(src_path)
    if key in resolver.imported_module_paths:
        logger.debug("`.import %r` deduped — already loaded from %s", module_name, key)
        return True
    return False


def _mark_imported(src_path: Path, resolver: Resolver) -> None:
    """Record that `src_path` has been imported. Pairs with `_is_imported`
    so call sites can split the check from the commit."""
    resolver.imported_module_paths.add(_canonical(src_path))


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


def _visit_for_public_symbols(nodes: Sequence[AstNode], prefix: str, symbols: list[str]) -> None:
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
        elif isinstance(child, list | tuple):
            _visit_for_public_symbols(child, prefix, symbols)
    included = getattr(node, "included_nodes", None)
    if isinstance(included, list | tuple):
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
