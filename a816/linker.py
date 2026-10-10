import logging
import re
import struct
from collections.abc import Callable
from re import Match

from a816.exceptions import (
    DuplicateSymbolError,
    ExpressionEvaluationError,
    LinkAssertError,
    PlacedSpan,
    PoolOverflowLinkError,
    PoolOverlapLinkError,
    RelocationError,
    UndeclaredPoolError,
    UnresolvedSymbolError,
)
from a816.object_file import (
    PC_RELATIVE_PREFIX,
    ObjectFile,
    PoolAlloc,
    PoolDecl,
    RelocationType,
    Section,
    SymbolSection,
    SymbolType,
)
from a816.parse.ast.expression import eval_constant_expression
from a816.parse.errors import ParserSyntaxError, ScannerException
from a816.parse.nodes.errors import NodeError
from a816.pool import Allocation, Pool, PoolOverflowError
from a816.section import PINNED_POOL_PREFIX, Placement

_SIZE_SYMBOL_RE = re.compile(r"(?<![\w.])([A-Za-z_][\w.]*)\.__size\b")

logger = logging.getLogger(__name__)

SYMBOL_TOKEN_RE = re.compile(r"([A-Za-z_\.][A-Za-z0-9_\.]*)")


class Linker:
    """Links a list of ObjectFiles into one ObjectFile.

    Each input object's sections are placed at their declared (absolute)
    base_address — relocatable modules are shifted by the linker's
    base_address against section 0. Section offsets are then translated to
    final logical addresses, and relocations are patched into per-section
    bytearrays. The linked output preserves the per-section structure so
    downstream IPS/SFC writers emit one block per section rather than one
    flat span.
    """

    def __init__(self, object_files: list[ObjectFile], base_address: int = 0) -> None:
        self.object_files = object_files
        self.base_address = base_address
        # Linked sections, keyed by their final logical base_address.
        self.linked_sections: list[Section] = []
        self.linked_symbols: list[tuple[str, int, SymbolType, SymbolSection]] = []
        # First GLOBAL address per name, mirroring `linked_symbols` order.
        self._global_addresses: dict[str, int] = {}
        self._global_owners: dict[str, ObjectFile] = {}  # first definer of each GLOBAL, for E0400
        # (final_address, section_idx, symbol_name, RelocationType)
        self._linked_relocations: list[tuple[int, int, str, RelocationType]] = []
        # (final_address, section_idx, expression, size_bytes)
        self._linked_expression_relocations: list[tuple[int, int, str, int]] = []
        self.linked_aliases: list[tuple[str, str]] = []
        self.linked_files: list[str] = []
        self._file_index: dict[str, int] = {}
        self.symbol_map: dict[str, int] = {}
        self._section_buffers: dict[int, bytearray] = {}
        # Per-object LOCAL symbols (underscore-private labels, alloc-body
        # locals). Bare LOCAL names collide across modules in the flat
        # `symbol_map`, so a relocation must resolve its LOCAL operands
        # against the object that emitted it, otherwise `jmp.w _loop` in
        # one module silently targets another module's `_loop`.
        self._local_by_obj: dict[int, dict[str, int]] = {}
        # Linked section index -> owning object index, so a relocation can
        # find its object's LOCAL overlay.
        self._section_obj: dict[int, int] = {}

    @property
    def linked_code(self) -> bytes:
        """Concatenated bytes of all linked sections, kept for legacy callers."""
        return b"".join(section.code for section in self.linked_sections)

    def link(self, base_address: int | None = None) -> ObjectFile:
        if base_address is not None:
            self.base_address = base_address
        # Pool allocation must happen before symbol ingestion so the
        # section.placed_base values we ingest reflect allocator choices.
        self._allocate_pools_across_modules()
        self._merge_bus_mappings()
        self._resolve_symbols()
        self._resolve_aliases()
        self._check_unresolved()
        self._check_asserts()
        self._warn_path_names()
        self._apply_relocations()
        self._apply_expression_relocations()
        return ObjectFile(
            self.linked_sections,
            self.linked_symbols,
            aliases=[],
            files=self.linked_files,
            relocatable=False,
            pool_decls=self._merged_pool_decls,
            bus_mappings=self._merged_bus_mappings,
        )

    def _allocate_pools_across_modules(self) -> None:
        """Union pool decls, run allocator over merged view, patch sections.

        Each .o carries `pool_decls` (declarations) + `pool_allocs`
        (deferred placement requests). The linker unions same-named
        pools (fill/strategy must agree), requests every alloc in
        declaration-then-request order, runs `Pool.allocate()`, and
        rewrites each requesting section's `base_address` to the
        allocator-chosen address. Body labels inside the section keep
        their offsets; the existing CODE-symbol delta path carries them
        to their final positions when `_resolve_symbols` ingests.
        """
        self._merge_pool_decls()
        merged: dict[str, Pool] = {p.name: self._pool_from_decl(p) for p in self._merged_pool_decls}
        contiguous = self._rom_contiguity()
        for pool in merged.values():
            pool.contiguous = contiguous
        # (obj_idx, section_idx) -> Allocation, to look up alloc.addr later.
        self._section_pool_alloc: dict[tuple[int, int], object] = {}
        request_sites = self._request_pool_allocs(merged)
        self._index_alloc_labels()
        # Pins first: every pool then keeps its floating blocks off the pins
        # it does not own, wherever they were declared.
        for pool in merged.values():
            if pool.name.startswith(PINNED_POOL_PREFIX):
                self._allocate(pool, request_sites)
        self._occupy_foreign_pins(merged)
        self._allocate_context_owners(merged, request_sites)
        for pool in merged.values():
            self._allocate(pool, request_sites)
        self._check_cross_pool_overlaps(merged, self._alloc_sources)
        self._merged_pools_after_alloc = merged

    def _allocate(self, pool: Pool, request_sites: dict[tuple[str, str], tuple[int, int]]) -> None:
        try:
            pool.allocate()
        except PoolOverflowError as exc:
            site = request_sites.get((exc.pool_name, exc.alloc_name))
            raise PoolOverflowLinkError(exc, self._section_location(site)) from exc

    def _allocate_context_owners(
        self, merged: dict[str, Pool], request_sites: dict[tuple[str, str], tuple[int, int]]
    ) -> None:
        """Place a pool's own reservations before its contexts, and keep each
        context off them.

        A reservation made directly in a pool is live in every context. Each
        context is its own allocator over the same ranges, so all of them
        used to start at the range start: the direct one and a context's
        landed on the same bytes, and the overlap check rejected the layout
        (E0406) that the allocator had just made.
        """
        owners = sorted({_context_owner(pool) for pool in merged.values() if pool.context is not None})
        for name in owners:
            owner = merged.get(name)
            if owner is None:
                continue
            self._allocate(owner, request_sites)
            contexts = [pool for pool in merged.values() if pool.context is not None and _context_owner(pool) == name]
            _occupy_reservations(owner, contexts)

    def _occupy_foreign_pins(self, merged: dict[str, Pool]) -> None:
        """Mark every pinned span inside a pool's ranges as taken in that pool.

        A pin written without `in POOL` (`.alloc at ADDR`, `*=`) or pinned in
        another pool used to be invisible to the pool, which then placed a
        floating block over it. Pools that may share memory (contexts of one
        pool) do not block each other.
        """
        pins = self._pinned_spans(merged)
        for pool in merged.values():
            if pool.name.startswith(PINNED_POOL_PREFIX):
                continue
            for start, end, owner in pins:
                if owner == pool.name or (owner in merged and _may_share(pool, merged[owner])):
                    continue
                if any(r.start <= end and start <= r.end for r in pool.ranges):
                    pool.occupy(start, end)

    def _pinned_spans(self, merged: dict[str, Pool]) -> list[tuple[int, int, str | None]]:
        """(start, end inclusive, owning pool) of every block whose address is fixed before allocation."""
        return [*_pool_pins(merged), *self._unpooled_pins()]

    def _unpooled_pins(self) -> list[tuple[int, int, str | None]]:
        """Sections of fixed-address objects that no pool places (`*=` blocks)."""
        spans: list[tuple[int, int, str | None]] = []
        for obj_idx, obj_file in enumerate(self.object_files):
            if obj_file.relocatable:
                continue
            spans.extend(
                (section.placed_base, section.placed_base + len(section.code) - 1, None)
                for section_idx, section in enumerate(obj_file.sections)
                if section.code and (obj_idx, section_idx) not in self._section_pool_alloc
            )
        return spans

    @staticmethod
    def _check_cross_pool_overlaps(merged: "dict[str, Pool]", sources: dict[tuple[str, str], str]) -> None:
        """Reject bss reservations from different pools that share bytes.

        Each pool overlap-checks its own allocations, and emitted bytes meet
        the writer's overlap check; bss reservations emit nothing, so two bss
        pools over the same memory linked silently. Pool *ranges* may still
        overlap: a pool nobody allocates from (a window over others, read for
        its `.capacity`) cannot collide. Only placed reservations count.
        """
        spans = sorted(
            (
                PlacedSpan(
                    pool.name, alloc.name, alloc.addr, alloc.addr + alloc.size, sources.get((pool.name, alloc.name), "")
                )
                for pool in merged.values()
                if pool.bss
                for alloc in pool.allocations
                if alloc.placed and alloc.size > 0
            ),
            key=lambda span: (span.start, span.end),
        )
        clashes: list[tuple[PlacedSpan, PlacedSpan]] = []
        for i, span in enumerate(spans):
            for other in spans[i + 1 :]:
                if other.start >= span.end:
                    break
                if other.pool != span.pool and not _may_share(merged[span.pool], merged[other.pool]):
                    clashes.append((span, other))
        if clashes:
            raise PoolOverlapLinkError(clashes)

    def _request_pool_allocs(self, merged: dict[str, Pool]) -> dict[tuple[str, str], tuple[int, int]]:
        """Request every alloc from its merged pool; return each first request's site.

        Dedupe by (pool_name, symbol_name): a `.import`ed module's alloc
        request can reach several consumers' `.o`. Without dedup the pool
        gets N copies of the same request and exhausts its ranges N-fold.
        Keep the FIRST placement; duplicates inherit the same Allocation so
        all importers see the same final address. The returned sites map
        (pool, symbol) -> (obj_idx, section_idx) so an overflow can name
        where the offending alloc body lives.
        """
        first_placed: dict[tuple[str, str], object] = {}
        request_sites: dict[tuple[str, str], tuple[int, int]] = {}
        self._alloc_sources: dict[tuple[str, str], str] = {}
        self._section_requests: dict[tuple[int, int], PoolAlloc] = {}
        for obj_idx, obj_file in enumerate(self.object_files):
            for req in obj_file.pool_allocs:
                pool = merged.get(req.pool_name)
                if pool is None:
                    raise UndeclaredPoolError(req.pool_name, req.symbol_name)
                key = (req.pool_name, req.symbol_name)
                alloc_obj = first_placed.get(key)
                if alloc_obj is not None:
                    _reject_a_second_alloc(req, self._alloc_sources[key])
                else:
                    alloc_obj = first_placed[key] = _request(pool, req)
                    request_sites[key] = (obj_idx, req.section_idx)
                    self._alloc_sources[key] = req.source
                self._section_pool_alloc[(obj_idx, req.section_idx)] = alloc_obj
                self._section_requests[(obj_idx, req.section_idx)] = req
        return request_sites

    def _rom_contiguity(self) -> Callable[[int, int], bool] | None:
        """`Bus.contiguous` over the modules' bus; None when no module
        declares a map: then nothing crosses."""
        from a816.cpu.mapping import Bus
        from a816.mappers import map_on_bus

        declared = {m.identifier: m for obj in self.object_files for m in obj.bus_mappings}
        if not declared:
            return None
        bus = Bus()
        for mapping in declared.values():
            map_on_bus(bus, mapping)
        # Both arguments are pool range bounds, mapped since compile (an
        # unmapped pool range already fails there).
        return bus.contiguous

    def _index_alloc_labels(self) -> None:
        """Map each symbol a pool alloc binds to that alloc's section:
        (obj_idx, symbol) -> (obj_idx, section_idx)."""
        self._label_sections: dict[tuple[int, str], tuple[int, int]] = {
            (obj_idx, label): (obj_idx, req.section_idx)
            for obj_idx, obj_file in enumerate(self.object_files)
            for req in obj_file.pool_allocs
            for label in req.labels
        }

    def _section_location(self, site: tuple[int, int] | None) -> str | None:
        """`file:line` of the first emitted line in a requesting section, if any."""
        if site is None:
            return None
        obj_file = self.object_files[site[0]]
        sections = obj_file.sections
        if site[1] >= len(sections) or not sections[site[1]].lines:
            return None
        _offset, file_idx, line, _column, _flags = sections[site[1]].lines[0]
        if file_idx >= len(obj_file.files):
            return None
        return f"{obj_file.files[file_idx]}:{line + 1}"

    def _pool_delta_for_symbol(self, obj_file: ObjectFile, obj_idx: int, name: str, address: int) -> int | None:
        """Return the pool section delta for a symbol bound in a pool section.

        Pool sections are placed by the link-time allocator independent of
        module delta; symbols inside them must shift by the section's
        own delta, not by the module's relocation. Each alloc lists the
        symbols bound in its section; the address lookup below only covers
        objects without that list, and is ambiguous once sections share
        sandbox addresses (contexts of one pool all start at its base).
        """
        owner = self._label_sections.get((obj_idx, name))
        if owner is not None and owner in self._pool_section_deltas:
            return self._pool_section_deltas[owner]
        for local_idx, section in enumerate(obj_file.sections):
            key = (obj_idx, local_idx)
            if key not in self._pool_section_deltas:
                continue
            # Byte-less (bss/reserve) sections carry no code, so `len(code)`
            # is 0 and an address-range check against the emitted bytes never
            # matches. Use the allocation's reserved size for the span so a
            # reserved symbol still picks up its section delta, required once
            # a pinned reserve shifts a pool's final addresses away from the
            # sequential sandbox bases.
            alloc = getattr(self, "_section_pool_alloc", {}).get(key)
            span = len(section.code)
            if alloc is not None:
                span = max(span, getattr(alloc, "size", 0))
            if section.placed_base <= address < section.placed_base + span:
                return self._pool_section_deltas[key]
        return None

    @staticmethod
    def _pool_from_decl(decl: "PoolDecl") -> "Pool":
        from a816.pool import Pool

        return Pool.from_decl(decl)

    def _merge_bus_mappings(self) -> None:
        """Collect `.map` declarations across input modules.

        Cartridge mapping is project-scoped (one ROM, one map), yet
        several modules may each declare it. Dedupe identical
        declarations on `identifier`; raise when two `.o`s ship the
        SAME identifier with a different shape (same rule codegen
        applies to `.map`s brought in through `.import`).
        """
        from a816.object_file import BusMapping

        merged: dict[str, BusMapping] = {}
        for obj_file in self.object_files:
            for mapping in obj_file.bus_mappings:
                existing = merged.get(mapping.identifier)
                if existing is None:
                    merged[mapping.identifier] = mapping
                    continue
                if existing != mapping:
                    raise ValueError(f"conflicting `.map {mapping.identifier!r}` declarations across modules")
        self._merged_bus_mappings = list(merged.values())

    def _merge_pool_decls(self) -> None:
        """Union same-named `.pool` declarations across input modules.

        Two modules declaring the same pool name must agree on `fill` and
        `strategy` and contribute non-overlapping ranges. The merged pool
        carries the union of ranges; the linker exposes it on the output
        ObjectFile.pool_decls for tooling (e.g. xobj) and as the source
        of truth for the link-time allocator.
        """
        from a816.object_file import PoolDecl

        merged: dict[str, Pool] = {}
        contexts_by_pool: dict[str, list[str]] = {}
        for obj_file in self.object_files:
            for decl in obj_file.pool_decls:
                self._merge_one_pool_decl(merged, decl)
            self._check_contexts_agree(obj_file.pool_decls, contexts_by_pool)
        self._merged_pool_decls = [
            PoolDecl(
                name=p.name,
                ranges=[(r.start, r.end) for r in p.ranges],
                fill=p.fill,
                strategy=p.strategy.value,
                bss=p.bss,
                context=p.context,
            )
            for p in merged.values()
        ]

    @staticmethod
    def _check_contexts_agree(decls: list[PoolDecl], seen: dict[str, list[str]]) -> None:
        """Every module declaring a pool must list the same `contexts`.

        Contexts reach the linker as sibling decls (`POOL.CTX`), which merge
        like any pool; without this check two modules listing different
        contexts would silently union them.
        """
        for decl in decls:
            if decl.context is not None:
                continue
            contexts = sorted(
                d.context for d in decls if d.context is not None and d.name == f"{decl.name}.{d.context}"
            )
            previous = seen.setdefault(decl.name, contexts)
            if previous != contexts:
                raise ValueError(f"pool {decl.name!r} declared with conflicting contexts: {previous} vs {contexts}")

    @staticmethod
    def _merge_one_pool_decl(merged: "dict[str, Pool]", decl: PoolDecl) -> None:
        from a816.pool import PoolRange

        if decl.name not in merged:
            merged[decl.name] = Linker._pool_from_decl(decl)
            return
        existing = merged[decl.name]
        if existing.fill != decl.fill:
            raise ValueError(
                f"pool {decl.name!r} declared with conflicting fill bytes: 0x{existing.fill:02x} vs 0x{decl.fill:02x}"
            )
        if existing.strategy.value != decl.strategy:
            raise ValueError(
                f"pool {decl.name!r} declared with conflicting strategies: "
                f"{existing.strategy.value!r} vs {decl.strategy!r}"
            )
        if existing.bss != decl.bss:
            raise ValueError(f"pool {decl.name!r} declared with conflicting bss flags: {existing.bss} vs {decl.bss}")
        if existing.context != decl.context:
            raise ValueError(
                f"pool {decl.name!r} declared with conflicting contexts: {existing.context!r} vs {decl.context!r}"
            )
        # Dedupe identical ranges: a prelude-declared pool replicates
        # across every module's .o, and reclaiming the same bytes twice
        # is an error. Skip ranges already covered; only contribute
        # genuinely new ones.
        existing_ranges = {(r.start, r.end) for r in existing.ranges}
        for start, end in decl.ranges:
            if (start, end) in existing_ranges:
                continue
            existing.reclaim(PoolRange(start=start, end=end))
            existing_ranges.add((start, end))

    def _name_linked_section(
        self, section: Section, obj_idx: int, local_idx: int, compile_base: int, labels_at: dict[int, str]
    ) -> None:
        """Give a linked section the name its source uses, for diagnostics.

        A pooled block is its alloc in its pool, located by the request's
        `file:line`. A pinned block takes the first label at its start, if any.
        """
        request = self._section_requests.get((obj_idx, local_idx))
        if request is not None:
            section.name = request.symbol_name
            section.placement = Placement.POOLED
            section.pool_name = request.pool_name
            section.source = request.source
            return
        label = labels_at.get(compile_base)
        if label is not None:
            section.name = label

    def _delta_for(self, obj_file: ObjectFile, running_offset: int) -> int:
        """How much to shift this module's logical addresses by.

        Relocatable modules anchor section 0 at the linker's base_address
        plus the running byte offset of prior relocatable modules. Pinned
        modules keep their declared *= addresses unchanged.
        """
        if obj_file.relocatable and obj_file.sections:
            return self.base_address + running_offset - obj_file.sections[0].placed_base
        return 0

    def _ingest_object(self, obj_file: ObjectFile, running_offset: int) -> None:
        delta = self._delta_for(obj_file, running_offset)
        local_to_linked_file = self._merge_file_table(obj_file)
        obj_idx = self.object_files.index(obj_file)
        pool_allocs_by_section: dict[int, object] = {
            r_idx: alloc for (oi, r_idx), alloc in getattr(self, "_section_pool_alloc", {}).items() if oi == obj_idx
        }

        labels_at = _first_label_at(obj_file)
        for local_section_idx, section in enumerate(obj_file.sections):
            if local_section_idx in pool_allocs_by_section:
                # Pool-allocated section: linker chose this section's
                # base_address; ignore the .o's placeholder.
                alloc = pool_allocs_by_section[local_section_idx]
                final_base = alloc.addr  # type: ignore[attr-defined]
                # Per-section symbol delta = (linker base) - (compile base).
                section_delta = final_base - section.placed_base
                self._pool_section_deltas[(obj_idx, local_section_idx)] = section_delta
            else:
                final_base = section.placed_base + delta
            section_idx = len(self.linked_sections)
            new_section = Section.anonymous_pinned(
                base_address=final_base,
                code=bytes(section.code),
                relocations=list(section.relocations),
                expression_relocations=list(section.expression_relocations),
                lines=[
                    (offset, local_to_linked_file.get(file_idx, 0), line, column, flags)
                    for offset, file_idx, line, column, flags in section.lines
                ],
            )
            new_section.bss = section.bss
            self._name_linked_section(new_section, obj_idx, local_section_idx, section.placed_base, labels_at)
            self.linked_sections.append(new_section)
            self._section_obj[section_idx] = obj_idx

            for offset, name, reloc_type in section.relocations:
                self._linked_relocations.append((final_base + offset, section_idx, name, reloc_type))
            for offset, expression, size_bytes in section.expression_relocations:
                self._linked_expression_relocations.append((final_base + offset, section_idx, expression, size_bytes))

        for sym in obj_file.symbols:
            self._ingest_symbol(sym, delta, obj_file, obj_idx)

        self.linked_aliases.extend(obj_file.aliases)

    def _ingest_symbol(
        self,
        sym: tuple[str, int, SymbolType, SymbolSection],
        delta: int,
        obj_file: ObjectFile,
        obj_idx: int,
    ) -> None:
        name, address, symbol_type, section = sym
        if symbol_type == SymbolType.EXTERNAL:
            self._external_symbols_needed.add(name)
            return
        final_address = self._final_address(name, section, address, delta, obj_file, obj_idx)
        if symbol_type == SymbolType.GLOBAL:
            self._register_global_symbol(name, final_address, section, obj_file)
            return
        if symbol_type == SymbolType.LOCAL:
            self._register_local_symbol(name, final_address, section)
            self._local_by_obj.setdefault(obj_idx, {})[name] = final_address
            return
        raise ValueError(f"Unknown symbol type: {symbol_type}")

    def _final_address(
        self,
        name: str,
        section: SymbolSection,
        address: int,
        delta: int,
        obj_file: ObjectFile,
        obj_idx: int,
    ) -> int:
        # CODE symbols ride the module's delta; DATA/BSS/ABS_LABEL are absolute.
        # ABS_LABEL is a `.label`-declared address binding — the user picked
        # the value, so it must NOT shift with the module placement.
        # Pool-allocated sections get their own per-section delta (link-time
        # allocator chose the address, not module relocation).
        if section != SymbolSection.CODE:
            return address
        pool_delta = self._pool_delta_for_symbol(obj_file, obj_idx, name, address)
        return address + (pool_delta if pool_delta is not None else delta)

    def _register_global_symbol(self, name: str, final_address: int, section: SymbolSection, owner: ObjectFile) -> None:
        # Only treat as duplicate when an existing GLOBAL claims the
        # same name AND resolves to a DIFFERENT address. Two .o's
        # exporting the same name at the same final address happens
        # legitimately when paired-import inlines an imported module's
        # source into every consumer's .o — each consumer re-publishes
        # the import's alloc-body auto-symbols (`.incbin` filenames,
        # label decls). The dedup in `_allocate_pools_across_modules`
        # makes them all resolve to the same address, so collapsing
        # them is safe. A name first seen as LOCAL (compatibility
        # bare-name shim for NamedScope members) gets UPGRADED to
        # GLOBAL.
        existing = self._existing_global_address(name)
        if existing is not None:
            if existing != final_address:
                first = self._global_owners[name].describe()
                raise DuplicateSymbolError(name, [(first, existing), (owner.describe(), final_address)])
            return
        self.symbol_map[name] = final_address
        self.linked_symbols.append((name, final_address, SymbolType.GLOBAL, section))
        self._global_addresses.setdefault(name, final_address)
        self._global_owners.setdefault(name, owner)

    def _existing_global_address(self, name: str) -> int | None:
        return self._global_addresses.get(name)

    def _register_local_symbol(self, name: str, final_address: int, section: SymbolSection) -> None:
        self.linked_symbols.append((name, final_address, SymbolType.LOCAL, section))
        # LOCAL names feed `_resolve_aliases` so an alias RHS like
        # `count = _endwinmap - _winmap` (macro arg bound to a
        # module-local label inside an alloc body) folds at link time.
        # `setdefault` keeps the FIRST module's binding when two
        # modules happen to share a LOCAL name — underscore privacy is
        # a source-side convention, not a hard linker guarantee.
        # Modules that need durable cross-module refs must drop the
        # underscore (export as GLOBAL).
        self.symbol_map.setdefault(name, final_address)

    def _merge_file_table(self, obj_file: ObjectFile) -> dict[int, int]:
        local_to_linked: dict[int, int] = {}
        for local_idx, raw_path in enumerate(obj_file.files):
            path = _linked_file_name(raw_path)
            if path in self._file_index:
                local_to_linked[local_idx] = self._file_index[path]
            else:
                new_idx = len(self.linked_files)
                self.linked_files.append(path)
                self._file_index[path] = new_idx
                local_to_linked[local_idx] = new_idx
        return local_to_linked

    def _resolve_symbols(self) -> None:
        self._external_symbols_needed: set[str] = set()
        self._pool_section_deltas: dict[tuple[int, int], int] = {}
        running_offset = 0
        for obj_file in self.object_files:
            self._ingest_object(obj_file, running_offset)
            if obj_file.relocatable:
                running_offset += sum(len(r.code) for r in obj_file.sections)

    def _check_unresolved(self) -> None:
        unresolved_symbols = self._external_symbols_needed - set(self.symbol_map.keys())
        # `.extern Foo` (scope capture) registers `Foo` as needed but the
        # provider only exports the dotted members (`Foo.bar`, `Foo.baz`).
        # Accept the scope extern as resolved when any symbol in the map
        # lives under that prefix — the dotted refs that prompted the
        # `.extern` already resolve via the relocation pipeline.
        if unresolved_symbols:
            satisfied_by_scope = {
                name for name in unresolved_symbols if any(key.startswith(f"{name}.") for key in self.symbol_map)
            }
            unresolved_symbols -= satisfied_by_scope
        if unresolved_symbols:
            raise UnresolvedSymbolError(unresolved_symbols)

    def _check_asserts(self) -> None:
        """Evaluate every module's `.assert` with final addresses, the
        module's own locals in scope; report all failures at once."""
        failures: list[tuple[str, str, str]] = []
        for obj_idx, obj_file in enumerate(self.object_files):
            local_overlay = self._local_by_obj.get(obj_idx)
            for check in obj_file.asserts:
                try:
                    holds = self._evaluate_expression(check.expression, local_overlay)
                except ExpressionEvaluationError as exc:
                    failures.append(
                        (f"{check.message} (cannot evaluate: {exc.reason})", check.expression, check.source)
                    )
                    continue
                if not holds:
                    shown = self._show_sizes(check.expression, local_overlay)
                    failures.append((check.message, shown, check.source))
        if failures:
            raise LinkAssertError(failures)

    def _show_sizes(self, expression: str, local_overlay: dict[str, int] | None) -> str:
        """An assert as a user reads it: the internal `NAME.__size` symbols
        (what `sizeof(NAME)` of an alloc becomes) show as their values."""

        def value(match: Match[str]) -> str:
            name = match.group(0)
            size = (local_overlay or {}).get(name, self.symbol_map.get(name))
            return f"{size:#x}" if isinstance(size, int) else f"sizeof({match.group(1)})"

        return _SIZE_SYMBOL_RE.sub(value, expression)

    def _resolve_aliases(self) -> None:
        if not self.linked_aliases:
            return
        remaining = list(self.linked_aliases)
        progress = True
        while remaining and progress:
            progress = False
            still_pending: list[tuple[str, str]] = []
            for name, expression in remaining:
                try:
                    value = self._evaluate_expression(expression)
                except ExpressionEvaluationError:
                    still_pending.append((name, expression))
                    continue
                self.symbol_map[name] = value
                self.linked_symbols.append((name, value, SymbolType.GLOBAL, SymbolSection.DATA))
                self._global_addresses.setdefault(name, value)
                progress = True
            remaining = still_pending
        if remaining:
            raise UnresolvedSymbolError({name for name, _ in remaining})

    def _warn_path_names(self) -> None:
        """W0001 on references that meet a path-derived `.incbin` name only here."""
        from a816.link_path_names import RelocationSite, path_name_warnings

        sites = [
            RelocationSite(
                self._section_obj.get(section_idx, -1),
                self.linked_sections[section_idx],
                address - self.linked_sections[section_idx].placed_base,
                operand,
            )
            for address, section_idx, operand, _kind in [
                *self._linked_relocations,
                *self._linked_expression_relocations,
            ]
            if section_idx in self._section_obj
        ]
        for message in path_name_warnings(self.object_files, sites, self.linked_files, self._local_by_obj):
            logger.warning(message)

    def _section_view(self, section_idx: int) -> tuple[Section, bytearray]:
        section = self.linked_sections[section_idx]
        buf = self._section_buffers.get(section_idx)
        if buf is None:
            buf = bytearray(section.code)
            self._section_buffers[section_idx] = buf
        return section, buf

    def _flush_section_buffers(self) -> None:
        for section_idx, buf in self._section_buffers.items():
            self.linked_sections[section_idx].code = bytes(buf)
        self._section_buffers.clear()

    def _relocation_symbol_address(self, section_idx: int, symbol_name: str) -> int:
        """Resolve a relocation's target, preferring the emitting object's LOCAL
        symbol over the global map so a bare LOCAL name can't bind to a
        same-named symbol elsewhere."""
        local_overlay = self._local_by_obj.get(self._section_obj.get(section_idx, -1)) or {}
        if symbol_name in local_overlay:
            return local_overlay[symbol_name]
        if symbol_name in self.symbol_map:
            return self.symbol_map[symbol_name]
        raise UnresolvedSymbolError({symbol_name})

    @staticmethod
    def _check_range(symbol_name: str, kind: str, value: int, low: int, high: int) -> None:
        if not low <= value <= high:
            raise RelocationError(symbol_name, kind, value, f"is out of range (must be {low:#x} to {high:#x})")

    def _patch_relocation(
        self,
        code: bytearray,
        offset: int,
        final_address: int,
        symbol_name: str,
        address: int,
        reloc_type: RelocationType,
    ) -> None:
        match reloc_type:
            case RelocationType.ABSOLUTE_16:
                self._check_range(symbol_name, "16-bit absolute", address, 0, 0xFFFF)
                struct.pack_into("<H", code, offset, address)
            case RelocationType.ABSOLUTE_24:
                self._check_range(symbol_name, "24-bit absolute", address, 0, 0xFFFFFF)
                self._write_le24(code, offset, address)
            case RelocationType.RELATIVE_16:
                target = address - (final_address + 2)
                self._check_range(symbol_name, "16-bit relative", target, -0x8000, 0x7FFF)
                struct.pack_into("<h", code, offset, target)
            case RelocationType.RELATIVE_24:
                target = address - (final_address + 3)
                self._check_range(symbol_name, "24-bit relative", target, -0x800000, 0x7FFFFF)
                self._write_le24(code, offset, target & 0xFFFFFF)
            case _:
                raise ValueError(f"Unknown relocation type: {reloc_type}")

    def _apply_relocations(self) -> None:
        self._section_buffers = {}
        for final_address, section_idx, symbol_name, relocation_type in self._linked_relocations:
            symbol_address = self._relocation_symbol_address(section_idx, symbol_name)
            section, code = self._section_view(section_idx)
            offset = final_address - section.placed_base
            self._patch_relocation(code, offset, final_address, symbol_name, symbol_address, relocation_type)
        self._flush_section_buffers()

    def _apply_expression_relocations(self) -> None:
        self._section_buffers = {}
        for final_address, section_idx, expression, size_bytes in self._linked_expression_relocations:
            local_overlay = self._local_by_obj.get(self._section_obj.get(section_idx, -1))
            if expression.startswith(PC_RELATIVE_PREFIX):
                evaluated_value = self._branch_offset(expression, final_address, size_bytes, local_overlay)
            else:
                evaluated_value = self._evaluate_expression(expression, local_overlay)
            section, code = self._section_view(section_idx)
            offset = final_address - section.placed_base
            if size_bytes == 1:
                struct.pack_into("<B", code, offset, evaluated_value & 0xFF)
            elif size_bytes == 2:
                struct.pack_into("<H", code, offset, evaluated_value & 0xFFFF)
            elif size_bytes == 3:
                if not -0x800000 <= evaluated_value <= 0xFFFFFF:
                    raise ExpressionEvaluationError(expression, f"result {evaluated_value:#x} is out of 24-bit range")
                self._write_le24(code, offset, evaluated_value & 0xFFFFFF)
            elif size_bytes == 4:
                struct.pack_into("<I", code, offset, evaluated_value & 0xFFFFFFFF)
            else:
                raise ExpressionEvaluationError(expression, f"unsupported operand size: {size_bytes} bytes")

        self._flush_section_buffers()

    def _branch_offset(
        self, expression: str, operand_address: int, size_bytes: int, local_overlay: dict[str, int] | None
    ) -> int:
        """A relative branch's offset from final addresses: the target minus
        the operand's end (the next instruction), within the operand's signed width."""
        target = expression.removeprefix(PC_RELATIVE_PREFIX)
        offset = self._evaluate_expression(target, local_overlay) - (operand_address + size_bytes)
        limit = 1 << (8 * size_bytes - 1)
        if not -limit <= offset < limit:
            hint = "use `brl` or `jmp`" if size_bytes == 1 else "use `jmp` / `jml`"
            raise ExpressionEvaluationError(
                target,
                f"branch offset {offset} from ${operand_address - 1:06X} exceeds signed {8 * size_bytes}-bit range; {hint}",
            )
        return offset

    def _evaluate_expression(self, expression: str, local_overlay: dict[str, int] | None = None) -> int:
        expr_to_eval = self._substitute_symbols(expression, local_overlay)
        try:
            return eval_constant_expression(expr_to_eval)
        except NodeError as e:
            raise ExpressionEvaluationError(expression, e.message) from e
        except (ScannerException, ParserSyntaxError, RuntimeError, ValueError) as e:
            raise ExpressionEvaluationError(expression, str(e)) from e

    def _substitute_symbols(self, expression: str, local_overlay: dict[str, int] | None = None) -> str:
        def replace(match: Match[str]) -> str:
            token = match.group(0)
            # Object-local symbols win over the global map: a relocation's
            # bare LOCAL operand must resolve to the emitting module's label,
            # not another module that happens to export the same name.
            if local_overlay is not None and token in local_overlay:
                return str(local_overlay[token])
            if token in self.symbol_map:
                return str(self.symbol_map[token])
            return token

        return SYMBOL_TOKEN_RE.sub(replace, expression)

    def _write_le24(self, code: bytearray, offset: int, value: int) -> None:
        code[offset : offset + 3] = bytes(
            (
                value & 0xFF,
                (value >> 8) & 0xFF,
                (value >> 16) & 0xFF,
            )
        )


def _may_share(first: "Pool", second: "Pool") -> bool:
    """Two contexts of the same pool (`POOL.A`, `POOL.B`) never live at the
    same time, so their reservations may hold the same memory. Nothing else may."""
    if first.context is None or second.context is None or first.context == second.context:
        return False
    return _context_owner(first) == _context_owner(second)


def _context_owner(pool: "Pool") -> str:
    return pool.name.removesuffix(f".{pool.context}")


def _occupy_reservations(owner: "Pool", contexts: "list[Pool]") -> None:
    """Mark every placed reservation of `owner` as taken in each of its contexts."""
    spans = [(alloc.addr, alloc.addr + alloc.size - 1) for alloc in owner.allocations if alloc.size]
    for context in contexts:
        for start, end in spans:
            context.occupy(start, end)


def _first_label_at(obj_file: ObjectFile) -> dict[int, str]:
    """The first public code label at each address of one object, for naming its blocks."""
    labels: dict[int, str] = {}
    for name, value, _sym_type, sym_section in obj_file.symbols:
        if sym_section is SymbolSection.CODE and not name.startswith("_") and isinstance(value, int):
            labels.setdefault(value, name)
    return labels


def _pool_pins(merged: dict[str, Pool]) -> list[tuple[int, int, str | None]]:
    """Pins held by pools: `at ADDR in POOL` requests and the blocks of one-slot pin pools."""
    spans: list[tuple[int, int, str | None]] = []
    for pool in merged.values():
        for alloc in pool.allocations:
            start = _pin_start(pool, alloc)
            if start is not None:
                spans.append((start, start + alloc.size - 1, pool.name))
    return spans


def _pin_start(pool: Pool, alloc: Allocation) -> int | None:
    if alloc.size <= 0:
        return None
    if alloc.pinned:
        return alloc.pinned_addr
    if pool.name.startswith(PINNED_POOL_PREFIX) and alloc.placed:
        return alloc.addr
    return None


def _request(pool: Pool, request: PoolAlloc) -> Allocation:
    """Queue one alloc request in its merged pool."""
    pinned = request.pinned_addr if request.pinned_addr >= 0 else None
    return pool.request(request.symbol_name, request.size, pinned, align=request.align, cross_bank=request.cross_bank)


def _reject_a_second_alloc(request: PoolAlloc, first_source: str) -> None:
    """The same (pool, name) reached from another `file:line` is a second alloc
    reusing the name, not one module's request seen through two importers."""
    if request.source and first_source and request.source != first_source:
        raise DuplicateSymbolError(
            request.symbol_name,
            [(first_source, None), (request.source, None)],
            hint="alloc names are global: rename one of the allocs",
        )


def _linked_file_name(name: str) -> str:
    """One name per source file in the linked table: relative to the working
    directory when the file lies under it, else resolved absolute.

    Objects keep a file's name as their parse reached it, so ff4's `libmz.i`
    came out both as `src/libmz.i` and as an absolute path; absolute names
    also made the `.adbg` differ between checkouts. Placeholders such as
    `<linked>` are kept as they are.
    """
    if name.startswith("<"):
        return name
    import os

    resolved = os.path.realpath(name)
    cwd = os.path.realpath(os.getcwd())
    if resolved == cwd or resolved.startswith(cwd + os.sep):
        return os.path.relpath(resolved, cwd)
    return resolved
