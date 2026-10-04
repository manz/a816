from a816.symbols import Resolver
from a816.writers import Writer

# A ROM pool in bank 0 that places allocs in source order.
CLIENT_POOL = ".pool client {\n range 0x008000 0x00FFEF\n strategy order\n}\n"


def label_address(resolver: Resolver, name: str) -> int | None:
    """Address `name` is bound to in any scope, None when unbound."""
    return next((scope.labels[name] for scope in resolver.scopes if name in scope.labels), None)


class StubWriter(Writer):
    def __init__(self) -> None:
        self.data: list[bytes] = []
        self.data_addresses: list[int] = []

    def begin(self) -> None:
        """not needed by StubWriter"""

    def write_block(self, block: bytes, block_address: int) -> None:
        self.data_addresses.append(block_address)
        self.data.append(block)

    def write_block_header(self, block: bytes, block_address: int) -> None:
        return None

    def end(self) -> None:
        """not needed by StubWriter"""
