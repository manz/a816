"""LinkedModuleNode: emit pre-compiled module sections + bind their symbols."""

from __future__ import annotations

import logging

from a816.cpu.mapping import Address
from a816.exceptions import SymbolNotDefined
from a816.object_file import Section
from a816.parse.nodes.errors import NodeError
from a816.protocols import NodeProtocol
from a816.symbols import Resolver, Scope

logger = logging.getLogger("a816.nodes")


class LinkedModuleNode(NodeProtocol):
    """Emits a compiled module's sections and binds its symbols.

    Each section has a compile-time `base_address` (from `*=`) and a code
    blob. If the module is `relocatable` (no `*=` was present), section 0
    gets shifted by `delta = import_pc - sections[0].base_address` and
    every CODE symbol moves with it. Otherwise sections land at their
    declared absolute addresses, ignoring the import site.
    """

    def __init__(
        self,
        module_name: str,
        sections: list[Section],
        symbols: list[tuple[str, int, int, int]],  # (name, address, type, section)
        resolver: Resolver,
        relocatable: bool = True,
    ) -> None:
        self.module_name = module_name
        self.sections = sections
        self.symbols = symbols
        self.resolver = resolver
        self.relocatable = relocatable
        # Set by Program._mark_import_winners: True for every duplicate
        # `.import` of the same module except the last occurrence in
        # program order. Losers bind symbols (so the loser's source can
        # still reference them) but do NOT advance the PC and do NOT
        # emit bytes — the winner is the canonical placement.
        self.is_loser: bool = False
        self._delta = 0
        # Cache placed sections for emit_blocks; refreshed on every pc_after.
        self._placed: list[tuple[int, bytes]] = []
        # Populated by `_import_from_object` in direct mode so the .o's
        # pool declarations land in the main resolver on first pc_after.
        # Empty in object mode (the linker handles cross-module merging).
        from a816.object_file import PoolDecl

        self.imported_pool_decls: list[PoolDecl] = []
        self._pools_registered = False

    def emit(self, current_addr: Address) -> bytes:
        # Single-section modules still flow through the legacy single-bytes
        # path used by writers that don't know about emit_blocks.
        del current_addr
        placed = self._compute_placement()
        if not placed:
            return b""
        if len(placed) == 1:
            return placed[0][1]
        # Multi-section modules must go through emit_blocks; returning the
        # concatenation here would silently corrupt the output.
        return b""

    def emit_blocks(self, current_addr: Address) -> list[tuple[int, bytes]]:
        del current_addr
        return self._compute_placement()

    def pc_after(self, current_pc: Address) -> Address:
        self._local_map: dict[str, int] = {}
        self._register_imported_pools()
        self._compute_delta_and_base(current_pc)
        self._bind_module_symbols()
        return self._advance_pc(current_pc)

    def _register_imported_pools(self) -> None:
        """Surface the imported module's pool decls into the main
        resolver's pool registry. Runs once. Silently skips pools
        already declared by the main file (idempotent re-imports +
        diamond imports collapse cleanly)."""
        if self._pools_registered:
            return
        self._pools_registered = True
        if not self.imported_pool_decls:
            return
        from a816.pool import Pool, PoolRange, Strategy

        for decl in self.imported_pool_decls:
            if decl.name in self.resolver.pools:
                continue
            self.resolver.pools[decl.name] = Pool(
                name=decl.name,
                ranges=[PoolRange(start=lo, end=hi) for lo, hi in decl.ranges],
                fill=decl.fill,
                strategy=Strategy(decl.strategy),
            )

    def _compute_delta_and_base(self, current_pc: Address) -> None:
        if self.sections:
            self._delta = current_pc.logical_value - self.sections[0].placed_base if self.relocatable else 0
            # Shifted base of section 0 — used for the .sym/.adbg producer
            # to report where the module actually landed.
            self.base_address = self.sections[0].placed_base + self._delta
        else:
            # Symbol-only module (e.g. a stubs file with only `.label`
            # declarations and no emitted code). No sections means no delta
            # and no PC advance, but symbols still need binding.
            self._delta = 0
            self.base_address = current_pc.logical_value

    def _bind_module_symbols(self) -> None:
        from a816.object_file import SymbolType

        scope = self.resolver.current_scope
        for name, address, sym_type, section in self.symbols:
            final = self._resolve_symbol_address(address, section)
            if sym_type == SymbolType.GLOBAL.value:
                self._bind_global(scope, name, final, section)
            elif sym_type == SymbolType.LOCAL.value:
                self._local_map[name] = final

    @staticmethod
    def _bind_global(scope: Scope, name: str, final: int, section: int) -> None:
        from a816.object_file import SymbolSection

        scope.symbols[name] = final
        if section == SymbolSection.CODE.value:
            scope.labels[name] = final
        elif section == SymbolSection.ABS_LABEL.value:
            # `.label`-declared in the imported module — surface it as an
            # absolute label in the importer's scope so it lands in the
            # merged `.adbg` as SymbolKind.LABEL.
            scope.absolute_labels[name] = final

    def _advance_pc(self, current_pc: Address) -> Address:
        # Loser duplicates publish symbols (winner overwrites later via
        # last-pass) but must not consume PC space — otherwise inline
        # source surrounding the loser .import shifts forward by the
        # module's size and lands on top of unrelated ROM.
        # Symbol-only modules (no sections) don't advance PC either.
        # Pinned modules (any explicit `*=`) land at their declared
        # absolute base addresses; the importer's PC stays where it was
        # because the module does not occupy linear space at the import
        # site. Only relocatable single-section modules advance the
        # importer's PC by their first-section size.
        if self.is_loser or not self.sections or not self.relocatable:
            return current_pc
        first = self.sections[0]
        first_end = first.placed_base + self._delta + len(first.code)
        return self.resolver.get_bus().get_address(first_end)

    def _resolve_symbol_address(self, address: int, section: int) -> int:
        from a816.object_file import SymbolSection

        if section != SymbolSection.CODE.value:
            return address
        return address + self._delta

    def _compute_placement(self) -> list[tuple[int, bytes]]:
        # Re-evaluate every call: cross-module symbols may have been bound
        # by other LinkedModuleNodes after this one's pc_after ran. Doing
        # the eval lazily at emit time avoids spurious "Failed to
        # evaluate" warnings during the first resolve_labels pass.
        placed: list[tuple[int, bytes]] = []
        for section in self.sections:
            base = section.placed_base + self._delta
            patched = self._apply_region_relocations(section)
            placed.append((base, patched))
        self._placed = placed
        return placed

    def _apply_region_relocations(self, section: Section) -> bytes:
        if not section.expression_relocations:
            return section.code
        code_array = bytearray(section.code)
        root_scope = self.resolver.scopes[0]
        saved = self._inject_locals(root_scope)
        try:
            for offset, expr, size in section.expression_relocations:
                self._eval_one_relocation(expr, offset, size, code_array, section)
        finally:
            self._restore_locals(root_scope, saved)
        return bytes(code_array)

    def _reloc_context(self, offset: int, section: Section) -> str:
        return (
            f"module '{self.module_name}' section@0x{section.placed_base:x} offset 0x{offset:x}/0x{len(section.code):x}"
        )

    def _write_reloc(
        self, code_array: bytearray, offset: int, value: int, size: int, expr: str, section: Section
    ) -> None:
        ctx = self._reloc_context(offset, section)
        if size not in (1, 2, 3):
            logger.warning(f"Unsupported relocation size {size} for expression '{expr}' [{ctx}]")
            return
        if offset + size > len(code_array):
            logger.warning(
                f"Relocation runs past section code: offset 0x{offset:x} + size {size} "
                f"> 0x{len(code_array):x} for expression '{expr}' [{ctx}]"
            )
            return
        for i in range(size):
            code_array[offset + i] = (value >> (8 * i)) & 0xFF

    def _eval_one_relocation(self, expr: str, offset: int, size: int, code_array: bytearray, section: Section) -> None:
        from a816.parse.ast.expression import eval_expression_str

        ctx = self._reloc_context(offset, section)
        try:
            value = eval_expression_str(expr, self.resolver)
        except (SymbolNotDefined, NodeError, ValueError) as e:
            logger.warning(f"Failed to evaluate expression '{expr}': {e} [{ctx}]")
            return
        if not isinstance(value, int):
            logger.warning(f"Expression '{expr}' did not evaluate to int: {value} [{ctx}]")
            return
        self._write_reloc(code_array, offset, value, size, expr, section)

    def _inject_locals(self, root_scope: Scope) -> dict[str, int | str]:
        local_map = getattr(self, "_local_map", {})
        saved: dict[str, int | str] = {}
        for name, value in local_map.items():
            if name in root_scope.symbols:
                saved[name] = root_scope.symbols[name]
            root_scope.symbols[name] = value
        return saved

    def _restore_locals(self, root_scope: Scope, saved: dict[str, int | str]) -> None:
        local_map = getattr(self, "_local_map", {})
        for name in local_map:
            if name in saved:
                root_scope.symbols[name] = saved[name]
            else:
                root_scope.symbols.pop(name, None)

    def __str__(self) -> str:
        total = sum(len(r.code) for r in self.sections)
        return (
            f"LinkedModuleNode({self.module_name}, {len(self.sections)} sections, "
            f"{total} bytes, {len(self.symbols)} symbols)"
        )
