import logging
import struct
from typing import BinaryIO, Literal, Protocol

from a816 import bps
from a816.cpu.arch import current_arch
from a816.object_file import (
    BusMapping,
    LinkAssert,
    ObjectFile,
    PathNameRecord,
    PoolAlloc,
    PoolDecl,
    RelocationType,
    Section,
    SymbolSection,
    SymbolType,
)

logger = logging.getLogger("a816.writers")


class Writer(Protocol):
    def begin(self) -> None:
        """Writes the header"""

    def write_block_header(self, block: bytes, block_address: int) -> None:
        """Writes the block header"""

    def write_block(self, block: bytes, block_address: int) -> None:
        """Writes the block"""

    def end(self) -> None:
        """Writes the footer."""


OverlapMode = Literal["error", "warn", "off"]


class WriteAuditor:
    """Wraps a `Writer` to detect overlapping byte writes.

    Every `write_block` records the `[addr, addr + len)` byte range it
    served. Subsequent writes that touch any already-recorded byte are
    reported per the configured `mode`:

      - `error`: raise `OverlapError`
      - `warn` (default): log a WARNING and continue
      - `off`: silent passthrough

    Non-overlapping adjacent blocks are silent. The implementation is
    naive O(n) per write — fine for typical ROM patches that emit a
    handful of sections; if that ever becomes hot, switch the inner
    structure to an interval tree.
    """

    def __init__(self, inner: Writer, mode: OverlapMode = "warn") -> None:
        self._inner = inner
        self._mode: OverlapMode = mode
        self._ranges: list[tuple[int, int]] = []  # sorted by start; non-overlapping when no conflicts

    def begin(self) -> None:
        self._inner.begin()

    def write_block_header(self, block: bytes, block_address: int) -> None:
        self._inner.write_block_header(block, block_address)

    def write_block(self, block: bytes, block_address: int) -> None:
        if self._mode != "off" and block:
            self._check_overlap(block_address, block_address + len(block))
        self._inner.write_block(block, block_address)

    def end(self) -> None:
        self._inner.end()

    def _check_overlap(self, start: int, end: int) -> None:
        for existing_start, existing_end in self._ranges:
            if start < existing_end and existing_start < end:
                overlap_start = max(start, existing_start)
                overlap_end = min(end, existing_end)
                message = (
                    f"write at ${start:06x}..${end - 1:06x} overlaps previous "
                    f"write at ${existing_start:06x}..${existing_end - 1:06x} "
                    f"(${overlap_start:06x}..${overlap_end - 1:06x} would be silently overwritten)"
                )
                if self._mode == "error":
                    raise OverlapError(message)
                logger.warning(message)
        self._ranges.append((start, end))


class OverlapError(Exception):
    """Raised by `WriteAuditor` in `error` mode when two writes share bytes."""


class IPSWriter(Writer):
    def __init__(self, file: BinaryIO, copier_header: bool = False) -> None:
        self.file = file
        self._sections: list[tuple[int, int]] = []
        self._copier_header = copier_header

    def begin(self) -> None:
        self.file.write(b"PATCH")

    def write_block_header(self, block: bytes, block_address: int) -> None:
        if self._copier_header:
            block_address += 0x200
        self.file.write(struct.pack(">BH", block_address >> 16, block_address & 0xFFFF))
        self.file.write(struct.pack(">H", len(block)))

    def write_block(self, block: bytes, block_address: int) -> None:
        k = 0
        while block[k:]:
            slice_size = min(0xFFFF, len(block) - k)
            block_slice = block[k : k + slice_size]

            self.write_block_header(block_slice, block_address)
            self.file.write(block_slice)
            block_address += slice_size

            k += slice_size

    def end(self) -> None:
        self.file.write(b"EOF")


class BPSWriter(Writer):
    """An unsealed linear BPS patch over the base ROM `[bps]` describes.

    BPS walks the target front to back, so the blocks are buffered and
    encoded at `end()`, in address order, a later one winning an overlap.
    The target is `rom_size` long when the project sets it, else as long
    as the source or the last block.
    """

    def __init__(
        self, file: BinaryIO, source_size: int, source_crc32: int, metadata: str = "", rom_size: int = 0
    ) -> None:
        self.file = file
        self.source_size = source_size
        self.source_crc32 = source_crc32
        self.metadata = metadata
        self.rom_size = rom_size
        self._blocks: list[tuple[int, bytes]] = []

    def begin(self) -> None:
        """Nothing to write before the blocks are known."""

    def write_block_header(self, block: bytes, block_address: int) -> None:
        """Blocks are encoded together at `end()`."""

    def write_block(self, block: bytes, block_address: int) -> None:
        self._blocks.append((block_address, bytes(block)))

    def end(self) -> None:
        target_size = max(self.source_size, self.rom_size, bps.blocks_end(self._blocks))
        self.file.write(
            bps.encode_linear(
                self._blocks,
                source_size=self.source_size,
                source_crc32=self.source_crc32,
                target_size=target_size,
                metadata=self.metadata,
            )
        )


class SFCWriter(Writer):
    def __init__(self, file: BinaryIO, copier_header: bool = False) -> None:
        self.file = file
        self.copier_header = copier_header

    def begin(self) -> None:
        """SFC is contiguous it only needs to implement write_block."""

    def write_block_header(self, block: bytes, block_address: int) -> None:
        """SFC is contiguous it only needs to implement write_block."""
        del block, block_address

    def write_block(self, block: bytes, block_address: int) -> None:
        self.file.seek(block_address)
        self.file.write(block)

    def end(self) -> None:
        """SFC is contiguous it only needs to implement write_block."""


class AramWriter(Writer):
    """An SPC700 program image: the bytes from its lowest written address to its highest.

    The S-CPU uploads it to ARAM in one transfer, so `base` is where it loads.
    """

    def __init__(self, file: BinaryIO) -> None:
        self.file = file
        self._aram = bytearray(0x10000)
        self.base: int | None = None
        self._end = 0

    def begin(self) -> None:
        """The image is written whole at `end`."""

    def write_block_header(self, block: bytes, block_address: int) -> None:
        """The image is contiguous: no block headers."""
        del block, block_address

    def write_block(self, block: bytes, block_address: int) -> None:
        if not block:
            return
        if block_address + len(block) > len(self._aram):
            raise ValueError(f"SPC700 code at ${block_address:04X} runs past the 64 KB of ARAM")
        self._aram[block_address : block_address + len(block)] = block
        self.base = block_address if self.base is None else min(self.base, block_address)
        self._end = max(self._end, block_address + len(block))

    def end(self) -> None:
        if self.base is not None:
            self.file.write(self._aram[self.base : self._end])


class ObjectWriter(Writer):
    """Collects sections, symbols, relocations into a v6 ObjectFile.

    Section lifecycle:
        start_section(base_address) opens a new section and resets the
        per-section byte counter. Subsequent write_block() appends to the
        current section; add_relocation/add_expression_relocation/add_line
        record their offsets relative to the current section's start.

    The first section opens lazily on first write_block() (or first add_*
    call) using `_pending_base_address`, which the emit driver seeds via
    `start_section(initial_base)` before emission begins.
    """

    def __init__(self, output_file: str) -> None:
        self.output_file = output_file
        self.sections: list[Section] = []
        self.symbols: list[tuple[str, int, SymbolType, SymbolSection]] = []
        self.aliases: list[tuple[str, str]] = []
        self.files: list[str] = []
        self._file_index: dict[str, int] = {}
        self.pool_decls: list[PoolDecl] = []  # populated by generate_pool
        self.pool_allocs: list[PoolAlloc] = []  # populated by AllocNode object-mode emit
        self.bus_mappings: list[BusMapping] = []  # populated by generate_map
        self.asserts: list[LinkAssert] = []  # populated by generate_assert
        self.path_names: list[PathNameRecord] = []  # populated by code_gen
        self._current_section: Section | None = None
        self._pending_base_address: int = 0
        self._section_bytes_emitted: int = 0
        # Bytes emitted from the active node but not yet flushed to the
        # section via write_block. The driver (emit_with_relocations) calls
        # mark_emitted(len(node_bytes)) after each node so relocation
        # offsets recorded mid-emit reflect the right intra-section position.
        self._pending_emit_bytes: int = 0
        self._has_explicit_position: bool = False
        self._pending_bss: bool = False

    def begin(self) -> None:
        self.sections = []
        self.symbols = []
        self.aliases = []
        self.files = []
        self._file_index = {}
        self._current_section = None
        self._pending_base_address = 0
        self._section_bytes_emitted = 0
        self._pending_emit_bytes = 0
        self._has_explicit_position = False
        self._pending_bss = False
        self.pool_decls = []
        self.pool_allocs = []
        self.bus_mappings = []
        self.asserts = []
        self.path_names = []

    def mark_emitted(self, count: int) -> None:
        """Advance the per-section emit cursor by ``count`` bytes.

        Reloc emit sites query relocation_offset() before write_block is
        called for the surrounding block, so this method lets the emit
        driver advance the cursor in lockstep with bytes returned from
        each NodeProtocol.emit().
        """
        self._pending_emit_bytes += count

    def start_section(self, base_address: int, explicit: bool = False, bss: bool = False) -> None:
        """Open a new section at base_address, closing any current section.

        `explicit=True` marks this as the result of a `*=` directive — the
        module loses single-section relocatability once any explicit section
        is opened. `bss=True` opens a byte-less reservation section (a `.alloc`
        into a `bss` pool): it is force-created and protected from the
        drop-empty pass so it survives with no `code`.
        """
        # Drop empty pending sections instead of leaving zero-byte placeholders,
        # but never drop a bss section: empty `code` is its whole point.
        if self._current_section is not None and not self._current_section.code and not self._current_section.bss:
            self.sections.pop()
        self._pending_base_address = base_address
        self._current_section = None
        self._section_bytes_emitted = 0
        self._pending_emit_bytes = 0
        self._pending_bss = bss
        if explicit:
            self._has_explicit_position = True
        if bss:
            # Materialize immediately: a bss body emits no bytes, so nothing
            # else would trigger section creation, and the PoolAlloc must
            # reference a real section index.
            self._ensure_section()

    def relocation_offset(self, pending_block_bytes: int = 0) -> int:
        """Byte offset where the next emitted byte will land in the section."""
        return self._section_bytes_emitted + self._pending_emit_bytes + pending_block_bytes

    def add_file(self, path: str) -> int:
        if path in self._file_index:
            return self._file_index[path]
        idx = len(self.files)
        self.files.append(path)
        self._file_index[path] = idx
        return idx

    def add_line(
        self, offset: int, file_path: str, line: int, column: int, flags: int = 0, *, coalesce: bool = False
    ) -> None:
        """Record where the bytes at `offset` come from. `coalesce` skips the
        entry when the previous one already names the same source position
        (the values of one `.db` line)."""
        file_idx = self.add_file(file_path)
        lines = self._ensure_section().lines
        if coalesce and lines and lines[-1][1:4] == (file_idx, line, column):
            return
        lines.append((offset, file_idx, line, column, flags))

    def write_block_header(self, block: bytes, block_address: int) -> None:
        del block, block_address

    def write_block(self, block: bytes, block_address: int) -> None:
        del block_address  # object files key code by section, not absolute address
        section = self._ensure_section()
        section.code = section.code + block
        self._section_bytes_emitted = len(section.code)
        # Bytes are now part of the section, drop the pending counter.
        self._pending_emit_bytes = 0

    def add_symbol(
        self, name: str, address: int, symbol_type: SymbolType, section: SymbolSection = SymbolSection.CODE
    ) -> None:
        self.symbols.append((name, address, symbol_type, section))

    def add_relocation(self, offset: int, symbol_name: str, relocation_type: RelocationType) -> None:
        self._ensure_section().relocations.append((offset, symbol_name, relocation_type))

    def add_expression_relocation(self, offset: int, expression: str, size_bytes: int) -> None:
        self._ensure_section().expression_relocations.append((offset, expression, size_bytes))

    def add_alias(self, name: str, expression: str) -> None:
        """Record a link-time alias once: codegen may register one early and
        its symbol node registers it again while resolving."""
        if (name, expression) not in self.aliases:
            self.aliases.append((name, expression))

    def end(self) -> None:
        # Strip a trailing empty section (e.g. trailing `*=` with no code),
        # but keep a bss section: it is byte-less by design.
        if self.sections and not self.sections[-1].code and not self.sections[-1].bss:
            self.sections.pop()
        relocatable = not self._has_explicit_position
        obj_file = ObjectFile(
            self.sections,
            self.symbols,
            aliases=self.aliases,
            files=self.files,
            relocatable=relocatable,
            pool_decls=list(self.pool_decls),
            pool_allocs=list(self.pool_allocs),
            bus_mappings=list(self.bus_mappings),
            asserts=list(self.asserts),
            path_names=self.path_names,
            arch=current_arch.get(),
        )
        obj_file.write(self.output_file)

    def _ensure_section(self) -> Section:
        if self._current_section is None:
            section = Section.anonymous_pinned(base_address=self._pending_base_address, code=b"")
            section.bss = self._pending_bss
            self.sections.append(section)
            self._current_section = section
            self._section_bytes_emitted = 0
        return self._current_section
