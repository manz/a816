"""EmitMixin: inline byte emission to Writer (IPS / SFC).

Walks resolved nodes and emits machine code one block at a time, flushing
pending bytes at `*=` boundaries and routing `.import` LinkedModuleNode
blocks + `.alloc` body sections through their own placement paths.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from a816.parse.nodes import (
    AllocNode,
    CodePositionNode,
    IncludeIpsNode,
    LinkedModuleNode,
)
from a816.program.state import EmitState
from a816.protocols import NodeProtocol
from a816.writers import Writer

if TYPE_CHECKING:
    from a816.symbols import Resolver

logger = logging.getLogger("a816")


class EmitMixin:
    """Inline emission handler set. Mixed into `Program`."""

    if TYPE_CHECKING:
        resolver: Resolver

        def _to_physical(self, logical_address: int) -> int: ...
        def _trace_block(self, snes: int, phys: int, size: int, src: str = ...) -> None: ...
        def _record_debug_line(self, node: NodeProtocol, address: int) -> None: ...

    def emit(self, program: list[NodeProtocol], writer: Writer) -> None:
        """Emit machine code from resolved nodes to a writer.

        Iterates through program nodes, generating machine code bytes and
        writing them to the output writer. Handles code position changes
        and IPS block includes.

        Args:
            program: List of resolved executable nodes.
            writer: Output writer (IPSWriter, SFCWriter, etc.).
        """
        state = EmitState(
            current_block=b"",
            current_block_addr=self.resolver.pc,
            current_block_logical=self.resolver.reloc_address.logical_value,
        )
        for node in program:
            self._emit_one(node, writer, state)
        self._flush_pending(writer, state)

    def _emit_one(self, node: NodeProtocol, writer: Writer, state: EmitState) -> None:
        """Dispatch a single node to the right emission path."""
        if isinstance(node, LinkedModuleNode):
            self._emit_linked_module(node, writer, state)
            return
        if isinstance(node, AllocNode):
            self._emit_alloc(node, writer, state)
            return
        self._emit_default(node, writer, state)
        if isinstance(node, CodePositionNode):
            self._handle_code_position(writer, state)
        if isinstance(node, IncludeIpsNode):
            self._emit_ips_blocks(node, writer)

    def _emit_linked_module(self, node: LinkedModuleNode, writer: Writer, state: EmitState) -> None:
        """Emit a `.import`'d module's sections and refresh emission state.

        Loser duplicates are pure no-ops (pc_after also bailed out so
        the resolver PC is untouched — leaving current_block alone keeps
        surrounding inline source addressed correctly).
        """
        if node.is_loser:
            return
        self._flush_pending(writer, state)
        blocks = node.emit_blocks(self.resolver.reloc_address)
        for base, block in blocks:
            if block:
                writer.write_block(block, self._to_physical(base))
        # Only relocatable modules consume linear PC at the import site;
        # pinned sections land at their declared `*=` and the importer's
        # PC stays where it was.
        if blocks and node.relocatable:
            advance = len(blocks[0][1])
            self.resolver.pc += advance
            self.resolver.reloc_address += advance
        state.current_block_addr = self.resolver.pc
        state.current_block_logical = self.resolver.reloc_address.logical_value

    def _emit_alloc(self, node: AllocNode, writer: Writer, state: EmitState) -> None:
        """Write an `.alloc` block at its allocator-chosen address.

        Walks the attributed body so per-sub-node debug-line entries
        (`_record_debug_line`) and emit-trace records (`_trace_block`)
        match the granularity the direct-mode path produces for `*=`
        bodies. Without per-sub-node attribution, debug consumers see
        a single line entry for the whole alloc and lose source ↔
        address mapping for opcodes inside it.
        """
        self._flush_pending(writer, state)
        attributed = node.emit_attributed_blocks(self.resolver.reloc_address)
        # Per-sub-node debug-line entries so consumers can resolve a
        # source position for each opcode in the body.
        consolidated = b""
        first_addr: int | None = None
        for sub_node, snes_addr, chunk in attributed:
            if not chunk:
                continue
            if first_addr is None:
                first_addr = snes_addr
            self._record_debug_line(sub_node, snes_addr)
            consolidated += chunk
        # One consolidated trace + one writer call per alloc so trace
        # records + writer block records stay alloc-granular (matches
        # what the direct-mode `*=` path produced before the desugar).
        if consolidated and first_addr is not None:
            phys = self._to_physical(first_addr)
            writer.write_block(consolidated, phys)
            self._trace_block(first_addr, phys, len(consolidated))
        state.current_block_addr = self.resolver.pc
        state.current_block_logical = self.resolver.reloc_address.logical_value

    def _emit_default(self, node: NodeProtocol, writer: Writer, state: EmitState) -> None:
        """Emit a non-LinkedModule node and accumulate its bytes."""
        del writer  # accumulation only — flush happens at boundaries
        pre_emit_addr = self.resolver.reloc_address.logical_value
        node_bytes = node.emit(self.resolver.reloc_address)
        if not node_bytes:
            return
        self._record_debug_line(node, pre_emit_addr)
        state.current_block += node_bytes
        self.resolver.pc += len(node_bytes)
        self.resolver.reloc_address += len(node_bytes)

    def _handle_code_position(self, writer: Writer, state: EmitState) -> None:
        """Flush at a `*=` boundary and re-anchor for subsequent bytes."""
        self._flush_pending(writer, state)
        state.current_block_addr = self.resolver.pc
        state.current_block_logical = self.resolver.reloc_address.logical_value

    @staticmethod
    def _emit_ips_blocks(node: IncludeIpsNode, writer: Writer) -> None:
        """Pass an `.includeips`-loaded patch's blocks straight through."""
        for block_addr, block in node.blocks:
            writer.write_block(block, block_addr)

    def _flush_pending(self, writer: Writer, state: EmitState) -> None:
        """Write the accumulated current_block at its anchor and reset."""
        if state.current_block:
            writer.write_block(state.current_block, state.current_block_addr)
            self._trace_block(
                state.current_block_logical,
                state.current_block_addr,
                len(state.current_block),
            )
            state.current_block = b""
