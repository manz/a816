"""ObjectEmitMixin: emit into per-region object-file buckets for the linker."""

from __future__ import annotations

from typing import TYPE_CHECKING

from a816.parse.nodes import AllocNode, CodePositionNode, IncludeIpsNode
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
        """Emit code into per-region object-file buckets.

        A new region opens on every CodePositionNode. Relocation/line offsets
        recorded by emitting nodes are region-relative byte offsets, decoupled
        from `resolver.pc` (which CodePositionNode rewrites to a physical
        address).
        """
        original_pc = self.resolver.pc
        original_reloc = self.resolver.reloc_address

        # Seed the initial (implicit) region at the resolver's reloc_address.
        # If the source begins with `*=`, that emit immediately closes this
        # placeholder region and opens a new explicit one.
        object_writer.start_region(self.resolver.reloc_address.logical_value, explicit=False)
        state = ObjectEmitState(current_block=b"")
        try:
            for node in program:
                self._object_emit_one(node, object_writer, state)
            self._flush_object_block(object_writer, state)
        finally:
            self.resolver.pc = original_pc
            self.resolver.reloc_address = original_reloc

    def _object_emit_one(self, node: NodeProtocol, object_writer: ObjectWriter, state: ObjectEmitState) -> None:
        """Emit one node into the current object-writer region.

        Splits the dispatch the way `emit()` does so each branch — the
        common byte accumulator, the `*=` boundary, and the `.includeips`
        passthrough — owns a single concern.
        """
        if isinstance(node, AllocNode):
            self._object_emit_alloc(node, object_writer, state)
            return
        self._accumulate_object_bytes(node, object_writer, state)
        if isinstance(node, CodePositionNode):
            self._object_open_region(object_writer, state, explicit=True)
        if isinstance(node, IncludeIpsNode):
            self._object_emit_ips_blocks(node, object_writer, state)

    def _object_emit_alloc(self, node: AllocNode, object_writer: ObjectWriter, state: ObjectEmitState) -> None:
        """Emit `.alloc` body into a deferred region for link-time placement.

        The body region opens at the sandbox PC (pool's first range start)
        so the body's own labels — already bound there by AllocNode
        pass-1 — emit correctly relative to that base. The linker re-runs
        the allocator across all input modules' pool decls and PoolAlloc
        requests, then rebases this region; the existing CODE-symbol delta
        path carries every label inside the body to its final address.
        """
        from a816.object_file import PoolAlloc

        alloc = node._alloc
        if alloc is None:
            return
        sandbox_logical = (
            self.resolver.get_bus().get_address(self.resolver.pools[node.pool_name].ranges[0].start).logical_value
        )
        self._flush_object_block(object_writer, state)
        saved_pc = self.resolver.pc
        saved_reloc = self.resolver.reloc_address
        try:
            self.resolver.set_position(sandbox_logical)
            object_writer.start_region(sandbox_logical, explicit=True)
            for child in node.body:
                self._object_emit_one(child, object_writer, state)
            self._flush_object_block(object_writer, state)
        finally:
            self.resolver.pc = saved_pc
            self.resolver.reloc_address = saved_reloc
        object_writer.start_region(self.resolver.reloc_address.logical_value, explicit=False)
        # Region was at index region_idx; if a current_region was lazily
        # created at start_region above, it's now the last region index.
        actual_idx = len(object_writer.regions) - 1
        object_writer.pool_allocs.append(
            PoolAlloc(
                pool_name=node.pool_name,
                symbol_name=node.name,
                region_idx=actual_idx,
                size=node._size,
            )
        )

    def _accumulate_object_bytes(
        self, node: NodeProtocol, object_writer: ObjectWriter, state: ObjectEmitState
    ) -> None:
        node_bytes = node.emit(self.resolver.reloc_address)
        if not node_bytes:
            return
        self._record_object_line(node, object_writer.relocation_offset(), object_writer)
        state.current_block += node_bytes
        object_writer.mark_emitted(len(node_bytes))
        self.resolver.pc += len(node_bytes)
        self.resolver.reloc_address += len(node_bytes)

    def _object_open_region(self, object_writer: ObjectWriter, state: ObjectEmitState, *, explicit: bool) -> None:
        """Flush any pending block then open a fresh region at the new PC."""
        self._flush_object_block(object_writer, state)
        object_writer.start_region(self.resolver.reloc_address.logical_value, explicit=explicit)

    def _object_emit_ips_blocks(
        self, node: IncludeIpsNode, object_writer: ObjectWriter, state: ObjectEmitState
    ) -> None:
        """Pass an `.includeips`-loaded patch through as one region per block."""
        self._flush_object_block(object_writer, state)
        for block_addr, block in node.blocks:
            object_writer.start_region(block_addr, explicit=True)
            object_writer.write_block(block, block_addr)

    @staticmethod
    def _flush_object_block(object_writer: ObjectWriter, state: ObjectEmitState) -> None:
        if state.current_block:
            object_writer.write_block(state.current_block, 0)
            state.current_block = b""
