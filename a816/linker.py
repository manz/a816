import re
import struct
from re import Match
from typing import cast

from a816.exceptions import (
    DuplicateSymbolError,
    ExpressionEvaluationError,
    RelocationError,
    UnresolvedSymbolError,
)
from a816.object_file import ObjectFile, PoolDecl, Region, RelocationType, SymbolSection, SymbolType
from a816.pool import Pool

SYMBOL_TOKEN_RE = re.compile(r"([A-Za-z_\.][A-Za-z0-9_\.]*)")


class Linker:
    """Links a list of ObjectFiles into one ObjectFile.

    Each input object's regions are placed at their declared (absolute)
    base_address — relocatable modules are shifted by the linker's
    base_address against region 0. Region offsets are then translated to
    final logical addresses, and relocations are patched into per-region
    bytearrays. The linked output preserves the per-region structure so
    downstream IPS/SFC writers emit one block per region rather than one
    flat span.
    """

    def __init__(self, object_files: list[ObjectFile], base_address: int = 0) -> None:
        self.object_files = object_files
        self.base_address = base_address
        # Linked regions, keyed by their final logical base_address.
        self.linked_regions: list[Region] = []
        self.linked_symbols: list[tuple[str, int, SymbolType, SymbolSection]] = []
        # (final_address, region_idx, symbol_name, RelocationType)
        self._linked_relocations: list[tuple[int, int, str, RelocationType]] = []
        # (final_address, region_idx, expression, size_bytes)
        self._linked_expression_relocations: list[tuple[int, int, str, int]] = []
        self.linked_aliases: list[tuple[str, str]] = []
        self.linked_files: list[str] = []
        self._file_index: dict[str, int] = {}
        self.symbol_map: dict[str, int] = {}
        self._region_buffers: dict[int, bytearray] = {}

    @property
    def linked_code(self) -> bytes:
        """Concatenated bytes of all linked regions, kept for legacy callers."""
        return b"".join(region.code for region in self.linked_regions)

    def link(self, base_address: int | None = None) -> ObjectFile:
        if base_address is not None:
            self.base_address = base_address
        # Pool allocation must happen before symbol ingestion so the
        # region.base_address values we ingest reflect allocator choices.
        self._allocate_pools_across_modules()
        self._resolve_symbols()
        self._resolve_aliases()
        self._check_unresolved()
        self._apply_relocations()
        self._apply_expression_relocations()
        return ObjectFile(
            self.linked_regions,
            self.linked_symbols,
            aliases=[],
            files=self.linked_files,
            relocatable=False,
            pool_decls=self._merged_pool_decls,
        )

    def _allocate_pools_across_modules(self) -> None:
        """Union pool decls, run allocator over merged view, patch regions.

        Each .o carries `pool_decls` (declarations) + `pool_allocs`
        (deferred placement requests). The linker unions same-named
        pools (fill/strategy must agree), requests every alloc in
        declaration-then-request order, runs `Pool.allocate()`, and
        rewrites each requesting region's `base_address` to the
        allocator-chosen address. Body labels inside the region keep
        their offsets; the existing CODE-symbol delta path carries them
        to their final positions when `_resolve_symbols` ingests.
        """
        self._merge_pool_decls()
        merged: dict[str, Pool] = {p.name: self._pool_from_decl(p) for p in self._merged_pool_decls}
        # (obj_idx, region_idx) -> Allocation, to look up alloc.addr later.
        self._region_pool_alloc: dict[tuple[int, int], object] = {}
        # Allocation request order: stable across rebuilds = (obj input order,
        # then declaration order within each .o).
        for obj_idx, obj_file in enumerate(self.object_files):
            for req in obj_file.pool_allocs:
                pool = merged.get(req.pool_name)
                if pool is None:
                    raise ValueError(
                        f"pool alloc {req.symbol_name!r} references undeclared pool {req.pool_name!r}"
                    )
                alloc_obj = pool.request(req.symbol_name, req.size)
                self._region_pool_alloc[(obj_idx, req.region_idx)] = alloc_obj
        for pool in merged.values():
            pool.allocate()
        self._merged_pools_after_alloc = merged

    def _pool_delta_for_symbol(self, obj_file: ObjectFile, obj_idx: int, address: int) -> int | None:
        """Return the pool region delta if `address` falls inside a pool region.

        Pool regions are placed by the link-time allocator independent of
        module delta; symbols inside them must shift by the region's
        own delta, not by the module's relocation.
        """
        for local_idx, region in enumerate(obj_file.regions):
            if (obj_idx, local_idx) not in self._pool_region_deltas:
                continue
            if region.base_address <= address < region.base_address + len(region.code):
                return self._pool_region_deltas[(obj_idx, local_idx)]
        return None

    @staticmethod
    def _pool_from_decl(decl: "PoolDecl") -> "Pool":
        from a816.pool import Pool, PoolRange, Strategy

        return Pool(
            name=decl.name,
            ranges=[PoolRange(start=s, end=e) for s, e in decl.ranges],
            fill=decl.fill,
            strategy=Strategy(decl.strategy),
        )

    def _merge_pool_decls(self) -> None:
        """Union same-named `.pool` declarations across input modules.

        Two modules declaring the same pool name must agree on `fill` and
        `strategy` and contribute non-overlapping ranges. The merged pool
        carries the union of ranges; the linker exposes it on the output
        ObjectFile.pool_decls for tooling (e.g. xobj) and as the source
        of truth for future link-time allocation passes.
        """
        from a816.object_file import PoolDecl
        from a816.pool import Pool, PoolRange, Strategy

        merged: dict[str, Pool] = {}
        for obj_file in self.object_files:
            for decl in obj_file.pool_decls:
                if decl.name in merged:
                    existing = merged[decl.name]
                    if existing.fill != decl.fill:
                        raise ValueError(
                            f"pool {decl.name!r} declared with conflicting fill bytes: "
                            f"0x{existing.fill:02x} vs 0x{decl.fill:02x}"
                        )
                    if existing.strategy.value != decl.strategy:
                        raise ValueError(
                            f"pool {decl.name!r} declared with conflicting strategies: "
                            f"{existing.strategy.value!r} vs {decl.strategy!r}"
                        )
                    for start, end in decl.ranges:
                        existing.reclaim(PoolRange(start=start, end=end))
                else:
                    merged[decl.name] = Pool(
                        name=decl.name,
                        ranges=[PoolRange(start=s, end=e) for s, e in decl.ranges],
                        fill=decl.fill,
                        strategy=Strategy(decl.strategy),
                    )
        self._merged_pool_decls = [
            PoolDecl(
                name=p.name,
                ranges=[(r.start, r.end) for r in p.ranges],
                fill=p.fill,
                strategy=p.strategy.value,
            )
            for p in merged.values()
        ]

    def _delta_for(self, obj_file: ObjectFile, running_offset: int) -> int:
        """How much to shift this module's logical addresses by.

        Relocatable modules anchor region 0 at the linker's base_address
        plus the running byte offset of prior relocatable modules. Pinned
        modules keep their declared *= addresses unchanged.
        """
        if obj_file.relocatable and obj_file.regions:
            return self.base_address + running_offset - obj_file.regions[0].base_address
        return 0

    def _ingest_object(self, obj_file: ObjectFile, running_offset: int) -> None:
        delta = self._delta_for(obj_file, running_offset)
        local_to_linked_file = self._merge_file_table(obj_file)
        obj_idx = self.object_files.index(obj_file)
        pool_allocs_by_region: dict[int, object] = {
            r_idx: alloc
            for (oi, r_idx), alloc in getattr(self, "_region_pool_alloc", {}).items()
            if oi == obj_idx
        }

        for local_region_idx, region in enumerate(obj_file.regions):
            if local_region_idx in pool_allocs_by_region:
                # Pool-allocated region: linker chose this region's
                # base_address; ignore the .o's placeholder.
                alloc = pool_allocs_by_region[local_region_idx]
                final_base = alloc.addr  # type: ignore[attr-defined]
                # Per-region symbol delta = (linker base) - (compile base).
                region_delta = final_base - region.base_address
                self._pool_region_deltas[(obj_idx, local_region_idx)] = region_delta
            else:
                final_base = region.base_address + delta
            region_idx = len(self.linked_regions)
            new_region = Region(
                base_address=final_base,
                code=bytes(region.code),
                relocations=list(region.relocations),
                expression_relocations=list(region.expression_relocations),
                lines=[
                    (offset, local_to_linked_file.get(file_idx, 0), line, column, flags)
                    for offset, file_idx, line, column, flags in region.lines
                ],
            )
            self.linked_regions.append(new_region)

            for offset, name, reloc_type in region.relocations:
                self._linked_relocations.append((final_base + offset, region_idx, name, reloc_type))
            for offset, expression, size_bytes in region.expression_relocations:
                self._linked_expression_relocations.append((final_base + offset, region_idx, expression, size_bytes))

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
        # CODE symbols ride the module's delta; DATA/BSS/ABS_LABEL are absolute.
        # ABS_LABEL is a `.label`-declared address binding — the user picked
        # the value, so it must NOT shift with the module placement.
        # Pool-allocated regions get their own per-region delta (link-time
        # allocator chose the address, not module relocation).
        if section == SymbolSection.CODE:
            pool_delta = self._pool_delta_for_symbol(obj_file, obj_idx, address)
            final_address = address + (pool_delta if pool_delta is not None else delta)
        else:
            final_address = address
        if symbol_type == SymbolType.GLOBAL:
            if name in self.symbol_map:
                raise DuplicateSymbolError(name)
            self.symbol_map[name] = final_address
            self.linked_symbols.append((name, final_address, symbol_type, section))
            return
        if symbol_type == SymbolType.LOCAL:
            self.linked_symbols.append((name, final_address, symbol_type, section))
            return
        raise ValueError(f"Unknown symbol type: {symbol_type}")

    def _merge_file_table(self, obj_file: ObjectFile) -> dict[int, int]:
        local_to_linked: dict[int, int] = {}
        for local_idx, path in enumerate(obj_file.files):
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
        self._pool_region_deltas: dict[tuple[int, int], int] = {}
        running_offset = 0
        for obj_file in self.object_files:
            self._ingest_object(obj_file, running_offset)
            if obj_file.relocatable:
                running_offset += sum(len(r.code) for r in obj_file.regions)

    def _check_unresolved(self) -> None:
        unresolved_symbols = self._external_symbols_needed - set(self.symbol_map.keys())
        if unresolved_symbols:
            raise UnresolvedSymbolError(unresolved_symbols)

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
                progress = True
            remaining = still_pending
        if remaining:
            raise UnresolvedSymbolError({name for name, _ in remaining})

    def _region_view(self, region_idx: int) -> tuple[Region, bytearray]:
        region = self.linked_regions[region_idx]
        buf = self._region_buffers.get(region_idx)
        if buf is None:
            buf = bytearray(region.code)
            self._region_buffers[region_idx] = buf
        return region, buf

    def _flush_region_buffers(self) -> None:
        for region_idx, buf in self._region_buffers.items():
            self.linked_regions[region_idx].code = bytes(buf)
        self._region_buffers.clear()

    def _apply_relocations(self) -> None:
        self._region_buffers = {}
        for final_address, region_idx, symbol_name, relocation_type in self._linked_relocations:
            if symbol_name not in self.symbol_map:
                raise UnresolvedSymbolError({symbol_name})
            symbol_address = self.symbol_map[symbol_name]
            region, code = self._region_view(region_idx)
            offset = final_address - region.base_address

            match relocation_type:
                case RelocationType.ABSOLUTE_16:
                    if not 0 <= symbol_address <= 0xFFFF:
                        raise RelocationError(
                            symbol_name, "16-bit absolute", symbol_address, "is out of range (must be 0x0000-0xFFFF)"
                        )
                    struct.pack_into("<H", code, offset, symbol_address)
                case RelocationType.ABSOLUTE_24:
                    if not 0 <= symbol_address <= 0xFFFFFF:
                        raise RelocationError(
                            symbol_name,
                            "24-bit absolute",
                            symbol_address,
                            "is out of range (must be 0x000000-0xFFFFFF)",
                        )
                    self._write_le24(code, offset, symbol_address)
                case RelocationType.RELATIVE_16:
                    target_address = symbol_address - (final_address + 2)
                    if not -0x8000 <= target_address <= 0x7FFF:
                        raise RelocationError(
                            symbol_name,
                            "16-bit relative",
                            target_address,
                            "is out of range (must be -0x8000 to 0x7FFF)",
                        )
                    struct.pack_into("<h", code, offset, target_address)
                case RelocationType.RELATIVE_24:
                    target_address = symbol_address - (final_address + 3)
                    if not -0x800000 <= target_address <= 0x7FFFFF:
                        raise RelocationError(
                            symbol_name,
                            "24-bit relative",
                            target_address,
                            "is out of range (must be -0x800000 to 0x7FFFFF)",
                        )
                    self._write_le24(code, offset, target_address & 0xFFFFFF)
                case _:
                    raise ValueError(f"Unknown relocation type: {relocation_type}")

        self._flush_region_buffers()

    def _apply_expression_relocations(self) -> None:
        self._region_buffers = {}
        for final_address, region_idx, expression, size_bytes in self._linked_expression_relocations:
            evaluated_value = self._evaluate_expression(expression)
            region, code = self._region_view(region_idx)
            offset = final_address - region.base_address
            if size_bytes == 1:
                struct.pack_into("<B", code, offset, evaluated_value & 0xFF)
            elif size_bytes == 2:
                struct.pack_into("<H", code, offset, evaluated_value & 0xFFFF)
            elif size_bytes == 3:
                if not -0x800000 <= evaluated_value <= 0xFFFFFF:
                    raise ExpressionEvaluationError(expression, f"result {evaluated_value:#x} is out of 24-bit range")
                self._write_le24(code, offset, evaluated_value & 0xFFFFFF)
            else:
                raise ExpressionEvaluationError(expression, f"unsupported operand size: {size_bytes} bytes")

        self._flush_region_buffers()

    def _evaluate_expression(self, expression: str) -> int:
        expr_to_eval = self._substitute_symbols(expression)
        try:
            return cast(int, eval(expr_to_eval, {"__builtins__": {}}, {}))
        except (SyntaxError, NameError, TypeError, ValueError) as e:
            raise ExpressionEvaluationError(expression, str(e)) from e

    def _substitute_symbols(self, expression: str) -> str:
        def replace(match: Match[str]) -> str:
            token = match.group(0)
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
