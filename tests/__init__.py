from pathlib import Path

from a816.symbols import Resolver
from a816.writers import Writer

# A ROM pool in bank 0 that places allocs in source order.
BANK_40_MAP = ".map identifier=1 bank_range=0x40, 0x7d addr_range=0x0000, 0xffff mask=0x10000\n"
"""A flat 64 KB-per-bank map for `.alloc at 0x40xxxx` tests: ROM offset = address - 0x400000."""

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


def build_rom(tmp_path: Path, files: dict[str, str], experimental: list[str] | None = None) -> tuple[int, bytes]:
    """Write `files` under `tmp_path`, build `main.s` to an SFC; return (exit code, ROM bytes)."""
    from a816.module_builder import build_with_imports

    for name, src in files.items():
        (tmp_path / name).write_text(src)
    out = tmp_path / "out.sfc"
    result = build_with_imports(
        tmp_path / "main.s",
        out,
        output_format="sfc",
        output_dir=tmp_path / "obj",
        use_a816_toml=False,
        experimental=experimental,
    )
    return result.exit_code, out.read_bytes() if out.exists() else b""
