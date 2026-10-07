import logging
from collections.abc import ItemsView
from typing import TYPE_CHECKING, Any

from a816.context import AssemblyContext
from a816.cpu.mapping import Address, Bus
from a816.cpu.types import RomType
from a816.exceptions import ExternalSymbolReference, SymbolNotDefined
from a816.mappers import build_mapper_bus
from a816.parse.ast.nodes import BlockAstNode
from a816.parse.tokens import Token
from a816.pool import Pool

if TYPE_CHECKING:
    from a816.object_file import LinkAssert
from script import Table


def _is_exportable(name: str) -> bool:
    """Names with a single leading underscore are private to their scope.

    Mirrors the object-mode export rule: `_helper` stays local, `helper`
    promotes, and dunder names like `__size` (struct size symbol) keep
    promoting because they're system-injected, not user-private.
    The `__sc<idx>__` prefix is the codegen's internal scope-isolation
    mangling - purely scaffolding and must stay private even though it
    starts with double underscore.
    """
    if name.startswith("__sc"):
        return False
    return not name.startswith("_") or name.startswith("__")


def _bubble_anon_exportables(scope: "Scope", parent: "Scope") -> None:
    """Promote exportable labels/symbols from an anonymous scope to its parent.

    Used when an `.alloc` body or a macro / `{ }` block closes:
    underscore labels (`_skip`, `_end`) stay private, non-underscore
    names surface so sibling allocs, `.extern` declarations and a
    NamedScope parent's dotted export can reach them.

    Runs on every resolver pass. A name this scope already promoted is
    refreshed with its latest value - pass 1 measures `.alloc` bodies
    before the allocator places them, so the first promotion carries a
    provisional address. Names the parent got from elsewhere are left alone.
    """
    _bubble_names(scope.labels, parent.labels, scope.bubbled_labels)
    _bubble_names(scope.symbols, parent.symbols, scope.bubbled_symbols)


def _bubble_names[V: (int, int | str)](source: dict[str, V], target: dict[str, V], owned: dict[str, V]) -> None:
    for name, value in source.items():
        if not _is_exportable(name):
            continue
        if name not in target or (name in owned and target[name] == owned[name]):
            target[name] = value
            owned[name] = value


def _publish_named_dotted(scope: "NamedScope", parent: "Scope") -> None:
    """Promote `Name.label`, `Name.symbol` and `Name.alias` into the parent scope.

    Both labels and symbols carry the dotted prefix so the object writer
    can distinguish CODE labels (need link-time rebasing) from DATA
    constants without re-walking scopes. Link-time aliases (`fd = label`
    in object mode) are re-registered under the dotted name and exported
    so importers resolve `Name.alias` through the owner's `.o`.
    """
    parent.symbols |= {f"{scope.name}.{k}": v for k, v in scope.symbols.items() if _is_exportable(k)}
    parent.labels |= {f"{scope.name}.{k}": v for k, v in scope.labels.items() if _is_exportable(k)}
    writer = scope.resolver.context.object_writer
    for name, expression in scope.external_aliases.items():
        if not _is_exportable(name):
            continue
        dotted = f"{scope.name}.{name}"
        parent.add_external_alias(dotted, expression)
        if writer is not None:
            writer.add_alias(dotted, expression)


logger = logging.getLogger("a816")


class Scope:
    """A symbol scope for managing labels, symbols, and tables.

    Scopes form a hierarchy for symbol resolution, allowing nested namespaces
    (e.g., inside macros or named scopes). Each scope can contain:
    - Labels (code addresses)
    - Symbols (numeric or string constants)
    - Code symbols (macro-like block definitions)
    - External symbol declarations

    Symbol lookup traverses up the parent chain until found or root is reached.
    """

    def __init__(self, resolver: "Resolver", parent: "Scope | None" = None) -> None:
        """Initialize a new scope.

        Args:
            resolver: The parent Resolver managing this scope.
            parent: Optional parent scope for hierarchical lookup.
        """
        self.symbols: dict[str, int | str] = {}
        self.code_symbols: dict[str, BlockAstNode] = {}
        self.external_symbols: set[str] = set()
        # Aliased externals: name -> expression string (e.g. "extern_sym + 1").
        # The alias name behaves like an external symbol locally; the linker
        # resolves it once the underlying externs are known.
        self.external_aliases: dict[str, str] = {}
        self.parent = parent
        self.resolver: Resolver = resolver
        self.table: Table | None = None
        self.labels: dict[str, int] = {}
        # Names declared via `.label NAME = ADDR` - kept separate from
        # `labels` because the value is an absolute address the user picked
        # (not the current PC). The linker must NOT add the relocation
        # delta to these; treat them as DATA at object-file level but as
        # LABEL kind in `.adbg`.
        self.absolute_labels: dict[str, int] = {}
        # Names this scope promoted into its parent, with the value last
        # promoted, so later passes can refresh them (`_bubble_anon_exportables`).
        self.bubbled_labels: dict[str, int] = {}
        self.bubbled_symbols: dict[str, int | str] = {}
        # Macro parameters bound to an argument that did not resolve at the
        # call: name -> (argument expression, "passed to `m` as `p`"). A
        # lookup that misses on the parameter reports the argument instead.
        self.macro_arguments: dict[str, tuple[Any, str]] = {}

    def macro_argument(self, name: str) -> tuple[Any, str] | None:
        """The pending macro argument bound to `name`, searching outwards."""
        scope: Scope | None = self
        while scope is not None:
            if name in scope.macro_arguments:
                return scope.macro_arguments[name]
            scope = scope.parent
        return None

    def add_label(self, label: str, value: Address) -> None:
        self.labels[label] = value.logical_value
        self.add_symbol(label, value.logical_value)

    def get_labels(self) -> ItemsView[str, int]:
        return self.labels.items()

    def add_symbol(self, symbol: str, value: int | BlockAstNode | str) -> None:
        """Bind `symbol` to `value`. Idempotent upsert.

        Multi-pass resolution re-runs `add_symbol` on every pass - first
        with a guessed address, later with the refined one - so the
        value legitimately changes between calls. Distinguishing
        "expected refinement" from "real duplicate declaration" needs
        per-call source attribution we don't currently track; the parser
        + codegen layers catch the user-visible duplicates (struct
        redefinition, etc.), so this is a silent upsert.
        """
        if isinstance(value, BlockAstNode):
            self.code_symbols[symbol] = value
        else:
            self.symbols[symbol] = value

    def add_external_symbol(self, symbol: str) -> None:
        """Mark a symbol as external (defined in another object file)"""
        self.external_symbols.add(symbol)

    def add_external_alias(self, symbol: str, expression_str: str) -> None:
        """Register an alias whose value is a deferred expression over externs.

        The alias is treated as an external symbol locally so references
        emit deferred relocations. The linker resolves the alias by
        evaluating ``expression_str`` against the final symbol map.
        """
        self.external_symbols.add(symbol)
        self.external_aliases[symbol] = expression_str

    def lookup_alias(self, symbol: str) -> str | None:
        """Walk the scope chain looking for a registered external alias."""
        scope: Scope | None = self
        while scope is not None:
            if symbol in scope.external_aliases:
                return scope.external_aliases[symbol]
            scope = scope.parent
        return None

    def find_label_scope(self, name: str) -> "Scope | None":
        """Walk the scope chain looking for the scope that owns ``name`` as a label."""
        scope: Scope | None = self
        while scope is not None:
            if name in scope.labels:
                return scope
            scope = scope.parent
        return None

    def is_external_symbol(self, symbol: str) -> bool:
        """Check if a symbol is marked as external.

        `.extern Foo` captures the whole `Foo` namespace: any reference
        to `Foo.bar`, `Foo.baz.qux`, etc. resolves as external too.
        The full dotted name then flows through the relocation / alias
        pipeline and the linker matches it against the provider's
        `Foo.bar` GLOBAL export (NamedScope members already export with
        the dotted prefix per `Resolver._export_name`). Avoids forcing
        consumers to `.extern Foo.x` per member.
        """
        if symbol in self.external_symbols:
            return True
        if "." in symbol:
            head = symbol.split(".", 1)[0]
            if head in self.external_symbols:
                return True
        if self.parent:
            return self.parent.is_external_symbol(symbol)
        return False

    def __getitem__(self, item: str) -> int | str | BlockAstNode:
        try:
            return self.code_symbols[item]
        except KeyError:
            pass

        try:
            return self.symbols[item]
        except KeyError:
            # Check if this is an external symbol
            if self.is_external_symbol(item):
                raise ExternalSymbolReference(item) from None
            else:
                raise SymbolNotDefined(item) from None

    def get_table(self) -> Table | None:
        if self.table is None:
            if self.parent:
                return self.parent.get_table()
            else:
                return None
        else:
            return self.table

    def value_for(self, symbol: str) -> int | str | BlockAstNode | None:
        if self.parent:
            if symbol in self.symbols or symbol in self.code_symbols:
                return self[symbol]
            # External alias declared at this level (e.g. macro arg bound to
            # an extern expression) needs to be visible here so eval can defer.
            if symbol in self.external_aliases:
                return self[symbol]
            return self.parent.value_for(symbol)
        try:
            return self[symbol]
        except SymbolNotDefined:
            value = self.resolver.unimported_constant(symbol)
            if value is None:
                raise
            return value


class InternalScope(Scope):
    pass


class AllocBodyScope(Scope):
    """Scope spanning an `.alloc` body. Isolates underscore-private
    labels from sibling allocs in the same module without forcing the
    `__sc<idx>__` export mangle that ordinary anonymous scopes get.

    Branch / jump resolution walks the scope chain and finds local
    `_skip` / `_end` first - sibling allocs no longer collide. Public
    body labels still bubble to the parent on restore so cross-alloc
    calls keep working; underscore labels stay in this scope and are
    invisible from sibling alloc bodies.
    """


class NamedScope(Scope):
    def __init__(self, name: str, resolver: "Resolver", parent: Scope | None = None):
        super().__init__(resolver, parent)
        self.name = name


low_rom_bus = build_mapper_bus("low_rom_default_mapping", "lorom")
high_rom_bus = build_mapper_bus("high_rom_default_mapping", "hirom")

BUS_MAPPING = {RomType.low_rom: low_rom_bus, RomType.high_rom: high_rom_bus}


class Resolver:
    """Symbol resolver managing scopes, addresses, and CPU state during assembly.

    The Resolver is the central state manager during assembly, tracking:
    - Current program counter (PC) and relocation address
    - Symbol scopes (hierarchical namespaces)
    - CPU register sizes (A and X/Y for 65c816)
    - ROM memory mapping type
    - Address bus configuration

    It provides symbol lookup, address calculation, and state management
    across multiple assembly passes.
    """

    def __init__(self, pc: int = 0x000000):
        """Initialize the resolver with default state.

        Args:
            pc: Initial program counter value (default: 0x000000).
        """
        self.reloc = False
        self.a_size: int = 8  # Accumulator size: 8 or 16 bits
        self.i_size: int = 8  # Index register size: 8 or 16 bits
        # Opt-in rep/sep -> a_size/i_size inference. Off by default
        # because pre-existing sources (ff4 master, similar) were
        # written assuming value-driven width inference only - a
        # `lda #$01` after some earlier `rep #$20` was always meant
        # as 2 bytes (value forces .b). Enabling globally breaks
        # those. Enabled via `--experimental track_register_size` or
        # the `[experimental]` table in `a816.toml`.
        self.track_register_size: bool = False
        # Whether `a_size` / `i_size` reflect a size the source actually
        # asserted (`.a8`/`.a16`/`.i8`/`.i16`, or a tracked `rep`/`sep`)
        # during emission, as opposed to the 8-bit default. Only known
        # sizes drive the immediate-width mismatch warning.
        self.a_size_known: bool = False
        self.i_size_known: bool = False
        # Whether `a_size` / `i_size` came from a tracked `rep`/`sep` rather
        # than a `.a*`/`.i*` declaration. A flag-set size is runtime state of
        # the routine that set it, so it ends where control flow leaves
        # (`end_of_flow`); a declared size holds until redeclared.
        self.a_size_from_flags: bool = False
        self.i_size_from_flags: bool = False
        # Per-pool sandbox cursor for object-mode `.alloc` body labels.
        # Each `.alloc NAME in POOL` advances this so successive allocs
        # bind their bodies at distinct addresses inside the pool's
        # first range. Without this, every alloc would record body
        # labels at `pool.ranges[0].start` and the linker's
        # `_pool_delta_for_symbol` (which looks up the owning section
        # by address) would route every label through the first
        # section's delta.
        self.alloc_sandbox_cursors: dict[str, int] = {}
        self.rom_type = RomType.low_rom
        self.current_scope_index = 0
        self.last_used_scope = 0
        self.current_scope: Scope = Scope(self)
        self.scopes = [self.current_scope]
        self.bus = Bus()
        self.pc = 0
        self.reloc_address: Address
        self.context = AssemblyContext()
        self.pools: dict[str, Pool] = {}
        # Source file of each pool's first `.pool` declaration: a second one
        # from another file contributes ranges, one from the same file is a
        # mistake (`.reclaim` adds ranges there).
        self.pool_sources: dict[str, str] = {}
        # (pool, alloc name) -> source token of the `.alloc` that requested
        # the slot, so an allocator overflow can point back at it.
        self.alloc_sites: dict[tuple[str, str], Token] = {}
        # Names registered by `_publish_pool_stats` - kept out of object-mode
        # symbol export so two `.o` files declaring the same pool don't
        # collide on `<pool>.capacity` etc. at link time.
        self.pool_stat_symbol_names: set[str] = set()
        # Struct layout registry: type name → flat (field_path, offset) pairs.
        # Populated by `generate_struct`; consumed by typed-bind eager
        # expansion and by `(expr as T).field` field-access codegen so we
        # don't have to walk scopes to enumerate a struct's fields.
        # Each entry: (dotted_field_path, byte_offset, byte_width). The
        # width column is what enables auto-sized opcode emission on typed
        # field accesses; nested struct sub-fields inherit their declared
        # primitive width.
        self.struct_layouts: dict[str, list[tuple[str, int, int]]] = {}
        # Total size per registered struct type, exposed as `Type.__size`.
        self.struct_sizes: dict[str, int] = {}
        # Bit-field metadata: struct_name → {field_name: (mask, shift)}.
        # Used by the idempotent-redef check; the mask / shift constants
        # themselves are also published as flat scope symbols for assembly
        # use (`Type.field.mask`, `Type.field.shift`).
        self.struct_bitfields: dict[str, dict[str, tuple[int, int]]] = {}
        # Array field byte sizes: struct_name → {field_path: total_bytes}.
        # Nested arrays carry their dotted path so an enclosing struct can
        # re-publish them as `Outer.inner.items.__size`.
        self.struct_array_sizes: dict[str, dict[str, int]] = {}
        # Reservation sizes for `sizeof(NAME)`: the size when it is known at
        # codegen (a constant flat size, a typed reserve), None when only the
        # alloc's `NAME.__size` symbol carries it (measured later, or imported).
        self.reservation_sizes: dict[str, int | None] = {}
        # Named allocs seen at codegen: `sizeof(NAME)` resolves through their
        # `NAME.__size` even before the body is measured (macro args, `.assert`).
        self.alloc_names: set[str] = set()
        # Declared `(name, type)` fields per struct, in source order. `.istruct`
        # walks these to lay an instance out as bytes.
        self.struct_fields: dict[str, list[tuple[str, str]]] = {}
        # Typed-bind registry: instance name → struct type name. Lets the
        # linter spot redundant casts and field access on non-typed bindings.
        self.typed_instances: dict[str, str] = {}
        # Address width per typed instance - "b" (DP), "w" (abs 16-bit),
        # or "l" (long 24-bit). Derived from the base value's bank at
        # bind time so opcode emission can pick `lda` / `lda.w` / `lda.l`
        # without re-parsing the operand string.
        self.typed_instance_addr_width: dict[str, str] = {}
        # Canonical paths of modules already loaded by `.import`. Used to
        # short-circuit transitive re-imports (file A and file B both import
        # "inc"; without dedup the third party that imports both re-parses
        # "inc" twice and trips struct-redef and similar idempotency checks).
        self.imported_module_paths: set[str] = set()
        # Names contributed to the root scope by inlined `.import`
        # passes (object mode). `_export_object_symbols` skips these
        # so the importing module's `.o` doesn't re-export symbols
        # owned by its dependencies - the owner's `.o` is the single
        # source of truth, downstream `.o`s carry externs.
        self.imported_symbol_names: set[str] = set()
        # Private (`_`) declarations inlined from imported modules: name ->
        # (module, the files of that module). The owner's own declarations
        # (a public macro or constant built on a private one) still use them;
        # a reference written in any other file is an error.
        self.private_owners: dict[str, tuple[str, frozenset[str]]] = {}
        # Constants of already-built modules this one does not import
        # (name -> (value, owning module)). Still resolved during 1.1.0 so
        # projects relying on compile order keep building, with a warning
        # naming the `.import` to add; the ones used land in
        # `used_unimported` (name -> owning module) so the build cache tracks them.
        self.unimported_constants: dict[str, tuple[int, str]] = {}
        self.used_unimported: dict[str, str] = {}
        # `.assert`s of a direct build, checked once labels are final
        # (object mode hands them to the linker instead).
        self.direct_asserts: list[LinkAssert] = []
        # Placement context seen by `.import` at codegen. A `*=` cursor
        # stays active until the end of the source unit that opened it
        # (`.import` restores the importer's flag); the depth counts the
        # `.alloc` / `.relocate` bodies being generated. Either one makes
        # an `.import` a hard error: modules own their placement.
        self.star_eq_cursor_active: bool = False
        self.placement_body_depth: int = 0
        # Absolute paths of non-source assets pulled in during assembly
        # (`.incbin` blobs, `.table` files). The module builder records
        # these alongside the `.o`'s source-file table so an incremental
        # rebuild re-stats them: editing an asset must invalidate the
        # cached object the same way editing the `.s` does.
        self.dependency_files: set[str] = set()
        self.set_position(pc)

    def allocate_pools(self) -> None:
        """Run the allocator on every declared pool.

        Called between resolver passes by Program.resolve_labels so that
        `.alloc` and `.relocate` blocks see their final addresses on the
        pass that binds labels. Pool.allocate is idempotent - safe to call
        multiple times.

        Skipped in object mode: the linker collects pool decls + alloc
        requests across all input modules, unions same-named pools, and
        runs the allocator over the merged view. Local pre-allocation
        would assign each module its own copy of the pool, defeating
        cross-TU sharing.
        """
        if self.context.is_object_mode:
            return
        contiguous = self.bus.contiguous if self.bus.has_mappings() else None
        for pool in self.pools.values():
            pool.contiguous = contiguous
            pool.allocate()

    def reset_register_sizes(self) -> None:
        """Back to the power-on 8-bit A/X sizes, before each label pass and emission.

        Labels bind from one walk and bytes come from another; both must
        start from the same state or a trailing `rep` / `.a16` from the
        previous walk resizes opcodes ahead of it.
        """
        self.a_size = 8
        self.i_size = 8
        self.a_size_from_flags = False
        self.i_size_from_flags = False
        self.forget_register_sizes()

    def end_of_flow(self) -> None:
        """Leaving the current flow (`rts`, `jmp`, `bra`, `plp`, ...): code
        after it is reached from elsewhere, so a size a tracked `rep`/`sep`
        set goes back to the 8-bit default. Declared sizes stay."""
        if self.a_size_from_flags:
            self.a_size = 8
            self.a_size_from_flags = False
        if self.i_size_from_flags:
            self.i_size = 8
            self.i_size_from_flags = False

    def forget_register_sizes(self) -> None:
        """Mark A/X sizes unknown (new placement block, `plp`, ...)."""
        self.a_size_known = False
        self.i_size_known = False

    def get_bus(self) -> Bus:
        if self.bus.has_mappings():
            bus = self.bus
        else:
            bus = BUS_MAPPING[self.rom_type]
        return bus

    def set_position(self, pc: int) -> None:
        addr = self.get_bus().get_address(pc)
        physical = addr.physical

        if physical is not None:
            self.pc = physical

        # Normalize through the bus mapping so reloc_address.logical_value is
        # always the mapped logical address (e.g. 0x8000 for LoROM bank 0).
        self.reloc_address = addr + 0
        self.reloc = False

    def append_named_scope(self, name: str) -> None:
        scope = NamedScope(name, self, self.current_scope)
        self.scopes.append(scope)

    def append_scope(self) -> None:
        scope = Scope(self, self.current_scope)
        self.scopes.append(scope)

    def append_alloc_body_scope(self) -> None:
        scope = AllocBodyScope(self, self.current_scope)
        self.scopes.append(scope)

    def append_internal_scope(self) -> None:
        scope = InternalScope(self, self.current_scope)
        self.scopes.append(scope)

    def use_next_scope(self) -> None:
        self.last_used_scope += 1
        self.current_scope = self.scopes[self.last_used_scope]

    def restore_scope(self, exports: bool = False) -> None:
        scope = self.current_scope
        parent = scope.parent
        if parent is None:
            raise RuntimeError("Current scope has no parent...")

        if not isinstance(scope, NamedScope) and (exports or isinstance(parent, NamedScope)):
            _bubble_anon_exportables(scope, parent)
        if exports and isinstance(scope, NamedScope):
            _publish_named_dotted(scope, parent)

        self.current_scope = parent

    def _dump_symbols(self, symbols: dict[str, Any]) -> None:
        keys = sorted(symbols.keys())
        for key in keys:
            value = symbols[key]
            if isinstance(value, dict):
                print("namedscope", key)
                self._dump_symbols(value)
            else:
                if isinstance(value, tuple):
                    print(f"{key.ljust(32)} {value}")
                elif isinstance(value, int):
                    print(f"{key.ljust(32)} 0x{value:02x}")
                else:
                    print(f"{key.ljust(32)} {value}")

    def dump_symbol_map(self) -> None:
        for scope in self.scopes:
            if not isinstance(scope, InternalScope):
                print("Scope\n")
                self._dump_symbols(scope.symbols)

    def get_all_labels(self, mangle_nested: bool = False) -> list[tuple[str, int]]:
        """Return labels across all non-internal scopes.

        When ``mangle_nested`` is true, labels in nested anonymous scopes are
        prefixed with ``__sc<idx>__`` to mirror the export naming used by
        :meth:`get_all_symbols`. Labels in NAMED scopes are prefixed with
        the scope's name (`shops.gils`) so two `.scope` blocks with the
        same internal label don't collide as bare names in the linker's
        global symbol table.
        """
        labels: list[tuple[str, int]] = []
        for idx, scope in enumerate(self.scopes):
            if isinstance(scope, InternalScope):
                continue
            for name, value in scope.get_labels():
                exported = self._export_name(name, scope, idx, mangle_nested)
                labels.append((exported, value))
        return labels

    def get_all_absolute_labels(self, mangle_nested: bool = False) -> list[tuple[str, int]]:
        """Return `.label`-declared names across all non-internal scopes."""
        labels: list[tuple[str, int]] = []
        for idx, scope in enumerate(self.scopes):
            if isinstance(scope, InternalScope):
                continue
            for name, value in scope.absolute_labels.items():
                exported = self._export_name(name, scope, idx, mangle_nested)
                labels.append((exported, value))
        return labels

    @staticmethod
    def _export_name(name: str, scope: "Scope", idx: int, mangle_nested: bool) -> str:
        """Compute the exported name for a label/symbol in `scope`.

        Names already carrying the scope's prefix (e.g. `shops.gils`
        re-published by `_publish_named_dotted`) pass through unchanged so
        we don't double-prefix. Every other name inside a NamedScope gets
        the scope's name as a prefix, relative dotted ones included (a
        struct's bit-field `lo.mask` exports as `T.lo.mask`, never bare),
        to avoid bare-name collisions across modules.
        Anonymous nested scopes opt into the `__sc<idx>__` mangle when
        `mangle_nested` is set.
        """
        if isinstance(scope, NamedScope) and not name.startswith(f"{scope.name}."):
            return f"{scope.name}.{name}"
        # AllocBodyScope keeps PUBLIC (non-underscore) labels bare so
        # cross-alloc refs + `.extern` resolve them by their source name.
        # But underscore-PRIVATE labels are local to THIS alloc body and must
        # be unique per alloc: two sibling allocs both declaring `_loop`/`_done`
        # otherwise collide in the flat symbol table, and a `jmp.w _loop`
        # relocation binds to whichever duplicate the linker placed last (a
        # wild, layout-dependent branch). Mangle them with the body's scope
        # index so each alloc's private label exports (and relocates)
        # under a distinct name.
        if isinstance(scope, AllocBodyScope):
            # Private (underscore) labels are local to THIS alloc body; mangle them
            # to a per-scope-index name so two allocs' `_loop`/`_done` don't collide
            # as bare globals in the flat link table (a wild, layout-dependent
            # branch). The index-0 alloc is NOT exempt: an alloc can legitimately be
            # scope index 0 in its module, and skipping it leaked bare privates that
            # collided across modules (boot crash). Include idx 0.
            if name.startswith("_") and mangle_nested:
                return f"__sc{idx}__{name}"
            return name
        if mangle_nested and idx > 0 and not isinstance(scope, NamedScope):
            return f"__sc{idx}__{name}"
        return name

    def register_external_alias(self, name: str, expression: str) -> None:
        """Bind `name` in the current scope to a link-time `expression`.

        The object writer gets the alias under its exported name (see
        :meth:`_export_name`) so a NamedScope member surfaces as `Scope.name`
        and an anonymous-scope alias stays private behind the `__sc<idx>__`
        mangle instead of leaking as a bare global.
        """
        scope = self.current_scope
        scope.add_external_alias(name, expression)
        writer = self.context.object_writer
        if writer is not None:
            writer.add_alias(self._export_name(name, scope, self.scopes.index(scope), mangle_nested=True), expression)

    def exported_label_name(self, name: str) -> str:
        """Exported name for a label *reference* as seen from the current scope.

        Mirrors :meth:`_export_name` so a relocation or alias expression names
        a label exactly as :meth:`get_all_symbols` exports it: bare for root and
        ``AllocBodyScope`` labels, ``Scope.label`` for ``NamedScope`` members,
        ``__sc<idx>__label`` for anonymous nested scopes. Unknown or root-owned
        names pass through unchanged so externs and root labels are untouched.
        """
        owner = self.current_scope.find_label_scope(name)
        # Root-owned (scopes[0]) and unknown/extern names pass through bare - EXCEPT
        # when scopes[0] is itself an AllocBodyScope: its private labels must still
        # mangle (mirrors _export_name including idx 0), else a reference binds bare
        # while the definition exports `__sc0__name`.
        if owner is None or (owner is self.scopes[0] and not isinstance(owner, AllocBodyScope)):
            return name
        return self._export_name(name, owner, self.scopes.index(owner), mangle_nested=True)

    @staticmethod
    def _mangle(name: str, idx: int, mangle: bool) -> str:
        # Every nested anonymous scope's labels get a unique prefix so
        # repeated macro invocations don't collide on the same name -
        # underscore-prefixed names included (they are private to the
        # *invocation*, which still needs distinct addresses across calls).
        return f"__sc{idx}__{name}" if mangle else name

    def _scope_int_symbols(self, scope: "Scope") -> list[tuple[str, int]]:
        return [(name, value) for name, value in scope.symbols.items() if isinstance(value, int)]

    def get_all_symbols(self) -> list[tuple[str, int]]:
        """All labels + int-valued assignments. NamedScope members get
        the scope-name prefix (`items_description.source`); anon nested
        scopes get the `__sc<idx>__` mangle. Bare names from a nested
        scope never leak into the export so two `.scope` blocks with
        the same inner label (or two macro invocations whose args
        bubble up into different scopes) don't collide as bare globals
        in the linker."""
        symbols: list[tuple[str, int]] = []
        seen: set[str] = set()
        for idx, scope in enumerate(self.scopes):
            if isinstance(scope, InternalScope):
                continue
            for source in (scope.get_labels(), self._scope_int_symbols(scope)):
                for name, value in source:
                    exported = self._export_name(name, scope, idx, mangle_nested=True)
                    if exported not in seen:
                        symbols.append((exported, value))
                        seen.add(exported)
        return symbols

    def foreign_private_owner(self, name: str, token: Token | None) -> str | None:
        """The module owning `name` when `name` is another module's private
        (`_`) declaration referenced from outside it, else None."""
        if not name.startswith("_") or not self.private_owners:
            return None
        owner = self.private_owners.get(name.split(".", 1)[0])
        if owner is None:
            return None
        module, files = owner
        position = token.position if token is not None else None
        if position is None or position.file is None:
            return None
        return None if _canonical_file(position.file.filename) in files else module

    def claim_private(self, name: str, token: Token | None) -> None:
        """A file outside the owning module declares a private name of its
        own: from here on the name is that file's, not the imported one."""
        if self.foreign_private_owner(name, token) is not None:
            del self.private_owners[name.split(".", 1)[0]]

    def unimported_constant(self, name: str) -> int | None:
        """A constant of a module this one does not `.import`, or None.

        Recorded in `used_unimported`: visibility that depends on compile
        order breaks as soon as the order changes; the module builder warns.
        """
        found = self.unimported_constants.get(name)
        if found is None:
            return None
        value, module = found
        self.used_unimported[name] = module
        return value

    def is_root_scope_symbol(self, name: str) -> bool:
        """Check whether ``name`` is defined directly in the root scope.

        Used by the object-file emitter to decide whether a symbol should be
        exported as GLOBAL (root scope or named scope) or LOCAL (nested
        anonymous block).
        """
        if not self.scopes:
            return False
        root = self.scopes[0]
        if name in root.symbols or name in root.code_symbols or name in root.labels:
            return True
        # NamedScope contributes its dotted exports back into the root via
        # restore_scope(exports=True), so "name.foo" entries also live at the
        # root once the scope is closed.
        for scope in self.scopes:
            if isinstance(scope, NamedScope):
                qualified = f"{scope.name}.{name}"
                if qualified in root.symbols or qualified in root.labels:
                    return True
        return False


def _canonical_file(filename: str) -> str:
    """A source path as `private_owners` stores it (absolute, normalised)."""
    import os

    if filename.startswith("file://"):
        from urllib.parse import unquote, urlparse

        filename = unquote(urlparse(filename).path)
    return os.path.realpath(filename)
