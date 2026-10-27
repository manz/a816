import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from a816.program import Program
from a816.symbols import Resolver

# A ROM pool in bank 0 that places allocs in source order.
BANK_40_MAP = ".map identifier=1 bank_range=0x40, 0x7d addr_range=0x0000, 0xffff mask=0x10000\n"
"""A flat 64 KB-per-bank map for `.alloc at 0x40xxxx` tests: ROM offset = address - 0x400000."""

CLIENT_POOL = ".pool client {\n range 0x008000 0x00FFEF\n strategy order\n}\n"


def label_address(resolver: Resolver, name: str) -> int | None:
    """Address `name` is bound to in any scope, None when unbound."""
    return next((scope.labels[name] for scope in resolver.scopes if name in scope.labels), None)


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


@dataclass
class Linked:
    """What `link_source` built: the exit code, the IPS records and the linked symbols."""

    exit_code: int
    blocks: list[tuple[int, bytes]] = field(default_factory=list)
    symbols: dict[str, int] = field(default_factory=dict)
    program: "Program | None" = None
    """The program that linked, for what the symbol map leaves out (the `.sym` export)."""

    @property
    def data(self) -> list[bytes]:
        return [block for _, block in self.blocks]

    @property
    def data_addresses(self) -> list[int]:
        return [address for address, _ in self.blocks]


def link_source(
    src: str,
    files: dict[str, str] | None = None,
    *,
    symbols: dict[str, int | str] | None = None,
    experimental: list[str] | None = None,
    overlap_mode: str | None = None,
) -> Linked:
    """Compile `src` as `main.s` (with `files` beside it) to objects and link them to an IPS, as `a816 build` does."""
    from a816.module_builder import build_with_imports
    from tests.ips_helpers import parse_ips_records

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "main.s").write_text(src)
        for name, text in (files or {}).items():
            (tmp / name).parent.mkdir(parents=True, exist_ok=True)
            (tmp / name).write_text(text)
        out = tmp / "out.ips"
        result = build_with_imports(
            tmp / "main.s",
            out,
            output_dir=tmp / "obj",
            use_a816_toml=False,
            use_cache=False,
            symbols=symbols,
            experimental=experimental,
            overlap_mode=overlap_mode,
        )
        blocks = parse_ips_records(out.read_bytes()) if out.exists() else []
        return Linked(result.exit_code, blocks, dict(result.symbol_map), result.program)
