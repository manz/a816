"""Mutable state dataclasses threaded through Program.emit / emit_with_relocations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EmitState:
    """Mutable state threaded through Program.emit's per-node helpers."""

    current_block: bytes
    current_block_addr: int
    current_block_logical: int = 0


@dataclass
class ObjectEmitState:
    """Mutable state threaded through Program.emit_with_relocations."""

    current_block: bytes
    placed: bool = False
    """True once the emit cursor has a home (`*=` at root, inside an
    `.alloc` body, or from the start when the context does not require
    placement). Bytes emitted while False raise E0310."""
