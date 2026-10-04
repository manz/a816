"""ObjectEmitMixin: emit into per-section object-file buckets for the linker."""

from __future__ import annotations

from typing import TYPE_CHECKING

from a816.error_codes import E_CODEGEN_UNPLACED_CODE
from a816.exceptions import UnmappedBankError
from a816.parse.nodes import AllocNode, CodePositionNode, IncludeIpsNode, NodeError
from a816.parse.nodes.errors import node_file_info, unmapped_bank_error
from a816.parse.tokens import Token
from a816.program.state import ObjectEmitState
from a816.protocols import NodeProtocol
from a816.writers import ObjectWriter

if TYPE_CHECKING:
    from a816.symbols import Resolver


class ObjectEmitMixin:
    """Object-file emission handler set. Mixed into `Program`."""

    if TYPE_CHECKING:
        resolver: Resolver

        def _record_object_line(self, node: NodeProtocol, offset: int, object_writer: ObjectWriter) -> None: ...

    def emit_with_relocations(self, program: list[NodeProtocol], object_writer: ObjectWriter) -> None:
        """Emit code into per-section object-file buckets.

        A new section opens on every CodePositionNode. Relocation/line offsets
        recorded by emitting nodes are section-relative byte offsets, decoupled
        from `resolver.pc` (which CodePositionNode rewrites to a physical
        address).
        """
        original_pc = self.resolver.pc
        original_reloc = self.resolver.reloc_address

        # Seed the initial (implicit) section at the resolver's reloc_address.
        # If the source begins with `*=`, that emit immediately closes this
        # placeholder section and opens a new explicit one.
        object_writer.start_section(self.resolver.reloc_address.logical_value, explicit=False)
        self.resolver.reset_register_sizes()
        # Without `require_placement` the implicit section is a legitimate
        # home: the `.o` stays relocatable and the linker places it.
        state = ObjectEmitState(current_block=b"", placed=not self.resolver.context.require_placement)
        try:
            for node in program:
                self._object_emit_one(node, object_writer, state)
            self._flush_object_block(object_writer, state)
        finally:
            self.resolver.pc = original_pc
            self.resolver.reloc_address = original_reloc

    def _object_emit_one(self, node: NodeProtocol, object_writer: ObjectWriter, state: ObjectEmitState) -> None:
        """Emit one node, locating a bus-level unmapped-bank failure on it."""
        try:
            self._object_emit_dispatch(node, object_writer, state)
        except UnmappedBankError as exc:
            raise unmapped_bank_error(exc, node_file_info(node)) from exc

    def _object_emit_dispatch(self, node: NodeProtocol, object_writer: ObjectWriter, state: ObjectEmitState) -> None:
        """Emit one node into the current object-writer section.

        Splits the dispatch the way `emit()` does so each branch — the
        common byte accumulator, the `*=` boundary, and the `.includeips`
        passthrough — owns a single concern.
        """
        if isinstance(node, AllocNode):
            self._object_emit_alloc(node, object_writer, state)
            return
        self._accumulate_object_bytes(node, object_writer, state)
        if isinstance(node, CodePositionNode):
            state.placed = True
            self._object_open_section(object_writer, state, explicit=True)
        if isinstance(node, IncludeIpsNode):
            self._object_emit_ips_blocks(node, object_writer, state)

    def _object_emit_alloc(self, node: AllocNode, object_writer: ObjectWriter, state: ObjectEmitState) -> None:
        """Emit `.alloc` body into a deferred section for link-time placement.

        The body section opens at the sandbox PC (pool's first range start)
        so the body's own labels — already bound there by AllocNode
        pass-1 — emit correctly relative to that base. The linker re-runs
        the allocator across all input modules' pool decls and PoolAlloc
        requests, then rebases this section; the existing CODE-symbol delta
        path carries every label inside the body to its final address.
        """
        from a816.object_file import PoolAlloc

        alloc = node._alloc
        if alloc is None:
            return
        # Use the per-alloc sandbox base (`pool.start + cursor`), not
        # the pool's first range start. Two allocs in the same pool
        # must land in distinct sections; without the cursor offset
        # every alloc's section base collapses to the same address
        # and the linker's `_pool_delta_for_symbol` can't tell them
        # apart.
        if node._sandbox_base is None:
            raise RuntimeError(f"alloc {node.name!r} reached object-emit without a sandbox base")
        sandbox_logical = node._sandbox_base
        self._flush_object_block(object_writer, state)
        saved_pc = self.resolver.pc
        saved_reloc = self.resolver.reloc_address
        pool = self.resolver.pools.get(node.pool_name)
        is_bss = bool(pool and pool.bss)
        # Each alloc body is its own routine: drop asserted A/X sizes.
        self.resolver.forget_register_sizes()
        node.enter_body_sizes()
        try:
            self.resolver.set_position(sandbox_logical)
            # Always force-create the body section (`bss=True` here means
            # "byte-less-capable": survives the writer's drop-empty pass and
            # gets a stable index for the PoolAlloc). A label-only `.alloc at`
            # (entry-point marker) and a `.reserve` both legitimately emit no
            # bytes; both must survive. Sections that DO emit bytes get their
            # bss flag cleared below so the final SFC/IPS emit still writes them.
            object_writer.start_section(sandbox_logical, explicit=True, bss=True)
            outer_placed, state.placed = state.placed, True
            for child in node.body:
                self._object_emit_one(child, object_writer, state)
            self._flush_object_block(object_writer, state)
            state.placed = outer_placed
            section = object_writer.sections[-1] if object_writer.sections else None
            if section is not None and section.code:
                if is_bss:
                    from a816.parse.nodes import NodeError

                    raise NodeError(
                        f".alloc in bss pool {node.pool_name!r} cannot emit bytes; reserve space with `.res` instead",
                        node.file_info,
                    )
                # Real bytes: not a byte-less section, so don't skip it at emit.
                section.bss = False
        finally:
            self.resolver.pc = saved_pc
            self.resolver.reloc_address = saved_reloc
        object_writer.start_section(self.resolver.reloc_address.logical_value, explicit=False)
        # Section landed at index section_idx; if a current_section was lazily
        # created at start_section above, it's now the last section index.
        actual_idx = len(object_writer.sections) - 1
        object_writer.pool_allocs.append(
            PoolAlloc(
                pool_name=node.pool_name,
                symbol_name=node.name,
                section_idx=actual_idx,
                size=node._size,
                pinned_addr=node.pinned_addr if node.pinned_addr is not None else -1,
            )
        )

    def _accumulate_object_bytes(self, node: NodeProtocol, object_writer: ObjectWriter, state: ObjectEmitState) -> None:
        node_bytes = node.emit(self.resolver.reloc_address)
        if not node_bytes:
            return
        if not state.placed:
            raise _unplaced_code_error(node)
        self._record_object_line(node, object_writer.relocation_offset(), object_writer)
        state.current_block += node_bytes
        object_writer.mark_emitted(len(node_bytes))
        self.resolver.pc += len(node_bytes)
        self.resolver.reloc_address += len(node_bytes)

    def _object_open_section(self, object_writer: ObjectWriter, state: ObjectEmitState, *, explicit: bool) -> None:
        """Flush any pending block then open a fresh section at the new PC."""
        self._flush_object_block(object_writer, state)
        object_writer.start_section(self.resolver.reloc_address.logical_value, explicit=explicit)

    def _object_emit_ips_blocks(
        self, node: IncludeIpsNode, object_writer: ObjectWriter, state: ObjectEmitState
    ) -> None:
        """Pass an `.includeips`-loaded patch through as one section per block."""
        self._flush_object_block(object_writer, state)
        for block_addr, block in node.blocks:
            object_writer.start_section(block_addr, explicit=True)
            object_writer.write_block(block, block_addr)

    @staticmethod
    def _flush_object_block(object_writer: ObjectWriter, state: ObjectEmitState) -> None:
        if state.current_block:
            object_writer.write_block(state.current_block, 0)
            state.current_block = b""


def _statement_token(node: NodeProtocol) -> Token | None:
    """Source token of the statement that produced `node`, if it carries one.

    Opcodes / text carry `file_info` directly; `.db` / `.dw` / `.dl`
    nodes carry it on their value expression.
    """
    token = getattr(node, "file_info", None)
    if token is None:
        token = getattr(getattr(node, "value_node", None), "file_info", None)
    return token if isinstance(token, Token) else None


def _unplaced_code_error(node: NodeProtocol) -> NodeError:
    return NodeError(
        "code emitted outside any placement",
        _statement_token(node),
        code=str(E_CODEGEN_UNPLACED_CODE),
        hint="give these bytes a home: wrap them in `.alloc NAME in POOL { ... }` / "
        "`.alloc at ADDR { ... }`, or set the position with `*= ADDR` first",
    )
