"""`a816 build --arch spc700`: one source into an ARAM image the S-CPU uploads."""

from __future__ import annotations

from pathlib import Path

from a816.cpu.arch import using_arch
from a816.object_file import BusMapping
from a816.program import Program
from a816.writers import AramWriter

# ARAM is one flat 64 KB space: an address is its own offset in the image.
ARAM_BUS = BusMapping(identifier="1", bank_range=(0x00, 0x00), addr_range=(0x0000, 0xFFFF), mask=0x10000)


def build_spc700(
    source: Path,
    output: Path,
    *,
    include_paths: list[Path],
    symbols: dict[str, int | str],
    dump_symbols: bool = False,
) -> int:
    with using_arch("spc700"):
        program = Program(dump_symbols=dump_symbols)
        program.resolver.context.bus_map = [ARAM_BUS]
        for path in include_paths:
            program.add_include_path(path)
        for name, value in symbols.items():
            program.resolver.current_scope.add_symbol(name, value)
        with output.open("wb") as file:
            writer = AramWriter(file)
            writer.begin()
            exit_code = program.assemble_with_emitter(str(source), writer)
            writer.end()
    return exit_code
