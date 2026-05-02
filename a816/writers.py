import struct
from typing import BinaryIO, Protocol

from a816.object_file import ObjectFile, Region, RelocationType, SymbolSection, SymbolType


class Writer(Protocol):
    def begin(self) -> None:
        """Writes the header"""

    def write_block_header(self, block: bytes, block_address: int) -> None:
        """Writes the block header"""

    def write_block(self, block: bytes, block_address: int) -> None:
        """Writes the block"""

    def end(self) -> None:
        """Writes the footer."""


class IPSWriter(Writer):
    def __init__(self, file: BinaryIO, copier_header: bool = False) -> None:
        self.file = file
        self._regions: list[tuple[int, int]] = []
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


class ObjectWriter(Writer):
    """Collects regions, symbols, relocations into a v6 ObjectFile.

    Region lifecycle:
        start_region(base_address) opens a new region and resets the
        per-region byte counter. Subsequent write_block() appends to the
        current region; add_relocation/add_expression_relocation/add_line
        record their offsets relative to the current region's start.

    The first region opens lazily on first write_block() (or first add_*
    call) using `_pending_base_address`, which the emit driver seeds via
    `start_region(initial_base)` before emission begins.
    """

    def __init__(self, output_file: str) -> None:
        self.output_file = output_file
        self.regions: list[Region] = []
        self.symbols: list[tuple[str, int, SymbolType, SymbolSection]] = []
        self.aliases: list[tuple[str, str]] = []
        self.files: list[str] = []
        self._file_index: dict[str, int] = {}
        self._current_region: Region | None = None
        self._pending_base_address: int = 0
        self._region_bytes_emitted: int = 0
        # Bytes emitted from the active node but not yet flushed to the
        # region via write_block. The driver (emit_with_relocations) calls
        # mark_emitted(len(node_bytes)) after each node so relocation
        # offsets recorded mid-emit reflect the right intra-region position.
        self._pending_emit_bytes: int = 0
        self._has_explicit_position: bool = False

    def begin(self) -> None:
        self.regions = []
        self.symbols = []
        self.aliases = []
        self.files = []
        self._file_index = {}
        self._current_region = None
        self._pending_base_address = 0
        self._region_bytes_emitted = 0
        self._pending_emit_bytes = 0
        self._has_explicit_position = False

    def mark_emitted(self, count: int) -> None:
        """Advance the per-region emit cursor by ``count`` bytes.

        Reloc emit sites query relocation_offset() before write_block is
        called for the surrounding block, so this method lets the emit
        driver advance the cursor in lockstep with bytes returned from
        each NodeProtocol.emit().
        """
        self._pending_emit_bytes += count

    def start_region(self, base_address: int, explicit: bool = False) -> None:
        """Open a new region at base_address, closing any current region.

        `explicit=True` marks this as the result of a `*=` directive — the
        module loses single-region relocatability once any explicit region
        is opened.
        """
        # Drop empty pending regions instead of leaving zero-byte placeholders.
        if self._current_region is not None and not self._current_region.code:
            self.regions.pop()
        self._pending_base_address = base_address
        self._current_region = None
        self._region_bytes_emitted = 0
        self._pending_emit_bytes = 0
        if explicit:
            self._has_explicit_position = True

    def relocation_offset(self, pending_block_bytes: int = 0) -> int:
        """Byte offset where the next emitted byte will land in the region."""
        return self._region_bytes_emitted + self._pending_emit_bytes + pending_block_bytes

    def add_file(self, path: str) -> int:
        if path in self._file_index:
            return self._file_index[path]
        idx = len(self.files)
        self.files.append(path)
        self._file_index[path] = idx
        return idx

    def add_line(self, offset: int, file_path: str, line: int, column: int, flags: int = 0) -> None:
        file_idx = self.add_file(file_path)
        self._ensure_region().lines.append((offset, file_idx, line, column, flags))

    def write_block_header(self, block: bytes, block_address: int) -> None:
        del block, block_address

    def write_block(self, block: bytes, block_address: int) -> None:
        del block_address  # object files key code by region, not absolute address
        region = self._ensure_region()
        region.code = region.code + block
        self._region_bytes_emitted = len(region.code)
        # Bytes are now part of the region, drop the pending counter.
        self._pending_emit_bytes = 0

    def add_symbol(
        self, name: str, address: int, symbol_type: SymbolType, section: SymbolSection = SymbolSection.CODE
    ) -> None:
        self.symbols.append((name, address, symbol_type, section))

    def add_relocation(self, offset: int, symbol_name: str, relocation_type: RelocationType) -> None:
        self._ensure_region().relocations.append((offset, symbol_name, relocation_type))

    def add_expression_relocation(self, offset: int, expression: str, size_bytes: int) -> None:
        self._ensure_region().expression_relocations.append((offset, expression, size_bytes))

    def add_alias(self, name: str, expression: str) -> None:
        self.aliases.append((name, expression))

    def end(self) -> None:
        # Strip a trailing empty region (e.g. trailing `*=` with no code).
        if self.regions and not self.regions[-1].code:
            self.regions.pop()
        relocatable = not self._has_explicit_position
        obj_file = ObjectFile(
            self.regions,
            self.symbols,
            aliases=self.aliases,
            files=self.files,
            relocatable=relocatable,
        )
        obj_file.write(self.output_file)

    def _ensure_region(self) -> Region:
        if self._current_region is None:
            region = Region(base_address=self._pending_base_address, code=b"")
            self.regions.append(region)
            self._current_region = region
            self._region_bytes_emitted = 0
        return self._current_region
