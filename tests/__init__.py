from typing import List

from a816.writers import Writer


class StubWriter(Writer):
    def __init__(self) -> None:
        self.data: List[bytes] = []
        self.data_addresses: List[int] = []

    def begin(self) -> None:
        """not needed by StubWriter"""

    def write_block(self, block: bytes, block_address: int) -> None:
        self.data_addresses.append(block_address)
        self.data.append(block)

    def write_block_header(self, block: bytes, block_address: int) -> None:
        return None

    def end(self) -> None:
        """not needed by StubWriter"""
