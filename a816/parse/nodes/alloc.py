"""AllocNode + RelocateNode: pool-allocated bodies."""

from __future__ import annotations

from a816.cpu.mapping import Address
from a816.parse.nodes.errors import NodeError
from a816.parse.nodes.symbols import SymbolNode
from a816.parse.tokens import Token
from a816.pool import Allocation
from a816.protocols import NodeProtocol
from a816.symbols import Resolver


class AllocNode(NodeProtocol):
    """Emits `body` at an address picked by the named pool's allocator.

    `pc_after` runs once per resolver pass; the pool allocator is invoked
    by `Resolver.allocate_pools()` between passes. The body is walked
    first to measure its size, the slot is requested, and once placed the
    `name` label binds at the allocated address.
    """

    def __init__(
        self,
        name: str,
        pool_name: str,
        body: list[NodeProtocol],
        resolver: Resolver,
        file_info: Token,
    ) -> None:
        self.name = name
        self.pool_name = pool_name
        self.body = body
        self.resolver = resolver
        self.file_info = file_info
        self._alloc: Allocation | None = None
        self._size: int = 0
        # Only set in object mode (`_request_slot`). Stays `None` in
        # direct mode where the alloc body emits at the allocator's
        # chosen address directly. Object-emit reads it; reading in
        # any other path is a bug, so keep the optional shape loud.
        self._sandbox_base: int | None = None
        # Snapshot of the A/X size that the body should assume on
        # entry — captured once in `_measure_body` from the running
        # `alloc_carry_*` channel, then reused by `_bind_body_labels_at`
        # so label placement matches the measured-size pass.
        self._entry_a_size: int = 8
        self._entry_i_size: int = 8

    def _sandbox_pc(self) -> Address:
        pool = self.resolver.pools[self.pool_name]
        base = pool.ranges[0].start if pool.ranges else 0
        cursor = self.resolver.alloc_sandbox_cursors.get(self.pool_name, 0)
        return self.resolver.get_bus().get_address(base + cursor)

    @staticmethod
    def _skip_in_pass1(node: NodeProtocol) -> bool:
        """Mirror `Program.resolve_labels` pass-1 skip set.

        Nodes whose `pc_after` eagerly evaluates expressions (e.g.
        `SymbolNode`) must wait for pass-2 or forward references raise.
        AllocNode walks its body across the same passes, so applies the
        same skip rule when measuring + when binding labels.
        """
        return isinstance(node, SymbolNode)

    def _measure_body(self) -> int:
        # Mirror `RegisterSizeNode.emit`'s mutation across the body walk
        # so `OpcodeNode.pc_after` sees the A/X size that emission will
        # see. Inherit the size state from whichever alloc was measured
        # immediately before this one (stashed on the resolver under
        # `_alloc_carry_a_size`/`_alloc_carry_i_size`) — matches runtime,
        # where the CPU's M/X flags carry across `jsr` calls. Restore
        # the live resolver state on exit so top-level passes (which
        # historically treat `.a8`/`.a16` as no-ops) stay unaffected.
        from a816.parse.nodes.data import RegisterSizeNode

        start = self._sandbox_pc()
        pc = start
        saved_a = self.resolver.a_size
        saved_i = self.resolver.i_size
        self._entry_a_size = self.resolver.alloc_carry_a_size
        self._entry_i_size = self.resolver.alloc_carry_i_size
        self.resolver.a_size = self._entry_a_size
        self.resolver.i_size = self._entry_i_size
        try:
            for node in self.body:
                if self._skip_in_pass1(node):
                    continue
                if isinstance(node, RegisterSizeNode):
                    if node.register == "a":
                        self.resolver.a_size = node.size
                    else:
                        self.resolver.i_size = node.size
                pc = node.pc_after(pc)
            self.resolver.alloc_carry_a_size = self.resolver.a_size
            self.resolver.alloc_carry_i_size = self.resolver.i_size
        finally:
            self.resolver.a_size = saved_a
            self.resolver.i_size = saved_i
        # Use physical-address diff so bank-edge allocs measure
        # correctly. `pc.logical_value - start.logical_value` jumps
        # `0x8020` for a 32-byte alloc that ends at `$00:FFFF` because
        # the next logical address `$01:0000` is bus-mapped past the
        # WRAM-mirror low half of bank 1. Physical (file-offset) diff
        # is the true byte count for any in-ROM pool range.
        if start.physical is not None and pc.physical is not None:
            return pc.physical - start.physical
        return pc.logical_value - start.logical_value

    def _request_slot(self) -> None:
        pool = self.resolver.pools[self.pool_name]
        self._size = max(1, self._measure_body())
        self._alloc = pool.request(self.name, self._size)
        # Object mode defers allocator to link time. Bind the alloc's
        # symbol + body labels at the sandbox PC (pool start + cursor)
        # so they record sensible offsets; the linker rebases the body
        # section at link time and the existing CODE-symbol delta path
        # carries every label to its final address. Advance the
        # per-pool cursor by this alloc's size so the next alloc in
        # the same pool gets a distinct sandbox base — without that,
        # `_pool_delta_for_symbol` collapses all sections onto the
        # first one's delta and every symbol lands at the same place.
        if self.resolver.context.is_object_mode:
            sandbox = self._sandbox_pc()
            self._sandbox_base = sandbox.logical_value
            self._bind_body_labels_at(sandbox)
            self.resolver.alloc_sandbox_cursors[self.pool_name] = (
                self.resolver.alloc_sandbox_cursors.get(self.pool_name, 0) + self._size
            )

    def _bind_body_labels_at(self, target: Address) -> None:
        # Mirror `_measure_body`: walk with the inherited A/X carry so
        # `OpcodeNode.pc_after` sizes opcodes the same way emission will,
        # and body labels (e.g. `_draw_string_loop:`) bind at the right
        # offsets. Restore live resolver state on exit so top-level
        # passes stay clean.
        from a816.parse.nodes.data import RegisterSizeNode

        self.resolver.current_scope.add_label(self.name, target)
        pc = target
        saved_a = self.resolver.a_size
        saved_i = self.resolver.i_size
        self.resolver.a_size = self._entry_a_size
        self.resolver.i_size = self._entry_i_size
        try:
            for node in self.body:
                if isinstance(node, RegisterSizeNode):
                    if node.register == "a":
                        self.resolver.a_size = node.size
                    else:
                        self.resolver.i_size = node.size
                pc = node.pc_after(pc)
        finally:
            self.resolver.a_size = saved_a
            self.resolver.i_size = saved_i

    def _bind_body_labels(self) -> None:
        alloc = self._alloc
        assert alloc is not None
        target = self.resolver.get_bus().get_address(alloc.addr)
        self._bind_body_labels_at(target)

    def pc_after(self, current_pc: Address) -> Address:  # NOSONAR S3516
        # Returning current_pc unchanged is by design: an .alloc block emits
        # at the allocator-chosen address (via emit_blocks), not inline,
        # so the surrounding PC must not advance past this node.
        pool = self.resolver.pools.get(self.pool_name)
        if pool is None:
            raise NodeError(f".alloc into unknown pool {self.pool_name!r}", self.file_info)
        if self._alloc is None:
            self._request_slot()
        elif self._alloc.placed:
            self._bind_body_labels()
        return current_pc

    def emit(self, current_addr: Address) -> bytes:
        del current_addr
        return b""

    def emit_blocks(self, current_addr: Address) -> list[tuple[int, bytes]]:
        del current_addr
        alloc = self._alloc
        if alloc is None or not alloc.placed:
            return []
        # Lazy import to avoid the import cycle through codegen.
        from a816.parse.nodes.module import LinkedModuleNode

        saved_pc = self.resolver.pc
        saved_reloc = self.resolver.reloc_address
        try:
            self.resolver.set_position(alloc.addr)
            out = b""
            cur = self.resolver.reloc_address
            for node in self.body:
                # Skip loser `.import` duplicates inside the body — their
                # pc_after returned unchanged so subsequent siblings are
                # already laid out at the right offsets, and emitting the
                # loser bytes would both double-emit and overlap the
                # winner's placement. Mirrors the top-level
                # `_emit_linked_module` is_loser guard.
                if isinstance(node, LinkedModuleNode) and node.is_loser:
                    continue
                emitted = node.emit(cur)
                if emitted:
                    out += emitted
                    cur = cur + len(emitted)
                    self.resolver.pc += len(emitted)
                    self.resolver.reloc_address = cur
            return [(alloc.addr, out)]
        finally:
            self.resolver.pc = saved_pc
            self.resolver.reloc_address = saved_reloc

    def __str__(self) -> str:
        return f"AllocNode({self.name} in {self.pool_name})"


class RelocateNode(AllocNode):
    """`AllocNode` that also reclaims the symbol's original (addr, end) range.

    Pass 1 reclaims `[old_start, old_end]` back into the pool *before*
    requesting a slot for the body, so the reclaimed bytes may be reused
    by the same `.relocate` if the pool's free space is otherwise full.
    """

    def __init__(
        self,
        name: str,
        old_start: int,
        old_end: int,
        pool_name: str,
        body: list[NodeProtocol],
        resolver: Resolver,
        file_info: Token,
    ) -> None:
        super().__init__(name, pool_name, body, resolver, file_info)
        self.old_start = old_start
        self.old_end = old_end
        self._reclaimed = False

    def _request_slot(self) -> None:
        if not self._reclaimed:
            from a816.pool import PoolRange

            pool = self.resolver.pools[self.pool_name]
            try:
                pool.reclaim(PoolRange(start=self.old_start, end=self.old_end))
            except Exception as exc:
                raise NodeError(
                    f".relocate {self.name!r} reclaim 0x{self.old_start:06x}..0x{self.old_end:06x}: {exc}",
                    self.file_info,
                ) from exc
            self._reclaimed = True
        super()._request_slot()

    def __str__(self) -> str:
        return f"RelocateNode({self.name} from 0x{self.old_start:06x}..0x{self.old_end:06x} -> {self.pool_name})"
