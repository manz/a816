import struct
from typing import BinaryIO, List, Protocol, Tuple


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
        self._regions: List[Tuple[int, int]] = []
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
