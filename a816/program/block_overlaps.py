"""Find placed blocks that would write the same ROM bytes, named the way the source names them."""

from __future__ import annotations

from collections.abc import Callable

from a816.exceptions import EmittedBlock
from a816.object_file import ObjectFile
from a816.section import ANONYMOUS_ALLOC_PREFIX, ANONYMOUS_PREFIX, PINNED_POOL_PREFIX, Section


def emitted_blocks(linked_obj: ObjectFile, to_physical: Callable[[int], int]) -> list[EmittedBlock]:
    """Every section that emits bytes, with its file span and a source-facing name."""
    blocks = []
    for section in linked_obj.sections:
        if not section.code:
            continue
        start = to_physical(section.placed_base)
        blocks.append(
            EmittedBlock(
                name=_block_name(section),
                logical=section.placed_base,
                start=start,
                end=start + len(section.code),
                source=_source(section, linked_obj.files),
                pooled=not _pinned(section),
            )
        )
    return blocks


def overlapping_blocks(blocks: list[EmittedBlock]) -> list[tuple[EmittedBlock, EmittedBlock]]:
    """Pairs of blocks sharing file bytes, the earlier one first."""
    ordered = sorted(blocks, key=lambda block: (block.start, block.end))
    clashes = []
    for index, block in enumerate(ordered):
        for other in ordered[index + 1 :]:
            if other.start >= block.end:
                break
            clashes.append((block, other))
    return clashes


def _pinned(section: Section) -> bool:
    return section.pool_name is None or section.pool_name.startswith(PINNED_POOL_PREFIX)


def _block_name(section: Section) -> str:
    anonymous = section.name.startswith((ANONYMOUS_PREFIX, ANONYMOUS_ALLOC_PREFIX))
    if not _pinned(section):
        name = "a block" if anonymous else f"`{section.name}`"
        return f"{name} in pool `{section.pool_name}`"
    if anonymous:
        address = section.placed_base
        return f"block at ${address >> 16:02X}:{address & 0xFFFF:04X}"
    return f"`{section.name}`"


def _source(section: Section, files: list[str]) -> str:
    if section.source:
        return section.source
    if not section.lines:
        return ""
    _offset, file_idx, line, _column, _flags = min(section.lines)
    return f"{files[file_idx]}:{line + 1}" if file_idx < len(files) else ""
