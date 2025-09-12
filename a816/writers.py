import struct
from typing import BinaryIO, Protocol

from a816.object_file import ObjectFile, RelocationType, SymbolSection, SymbolType


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

    def write_block(self, block: bytes, block_address: int) -> None:
        self.file.seek(block_address)
        self.file.write(block)

    def end(self) -> None:
        """SFC is contiguous it only needs to implement write_block."""


class ObjectWriter(Writer):
    def __init__(self, output_file: str) -> None:
        self.output_file = output_file
        self.code_blocks: list[tuple[bytes, int]] = []
        self.symbols: list[tuple[str, int, SymbolType, SymbolSection]] = []
        self.relocations: list[tuple[int, str, RelocationType]] = []
        self.expression_relocations: list[tuple[int, str, int]] = []  # (offset, expression_str, size_bytes)
        self.current_offset = 0

    def begin(self) -> None:
        """Initialize object file creation"""
        self.code_blocks = []
        self.symbols = []
        self.relocations = []
        self.expression_relocations = []
        self.current_offset = 0

    def write_block_header(self, block: bytes, block_address: int) -> None:
        """Object files don't use block headers"""
        pass

    def write_block(self, block: bytes, block_address: int) -> None:
        """Collect code blocks for object file"""
        self.code_blocks.append((block, self.current_offset))
        self.current_offset += len(block)

    def add_symbol(
        self, name: str, address: int, symbol_type: SymbolType, section: SymbolSection = SymbolSection.CODE
    ) -> None:
        """Add a symbol to the object file"""
        self.symbols.append((name, address, symbol_type, section))

    def add_relocation(self, offset: int, symbol_name: str, relocation_type: RelocationType) -> None:
        """Add a relocation entry to the object file"""
        self.relocations.append((offset, symbol_name, relocation_type))

    def add_expression_relocation(self, offset: int, expression: str, size_bytes: int) -> None:
        """Add an expression relocation entry to the object file"""
        self.expression_relocations.append((offset, expression, size_bytes))

    def end(self) -> None:
        """Write the object file to disk"""
        # Combine all code blocks into a single byte array
        total_code = bytearray()
        for block, _ in self.code_blocks:
            total_code.extend(block)

        # Create and write the object file
        obj_file = ObjectFile(bytes(total_code), self.symbols, self.relocations, self.expression_relocations)
        obj_file.write(self.output_file)
