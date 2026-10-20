"""`a816 ips seal`: the SNES header checksum record, from the base ROM.

An IPS patch is built without the base ROM, so the header checksum of the
ROM it gives is unknown at build time. `seal` applies the patch in memory
and appends the record that writes it.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from a816 import ips
from a816.bps_cli import run_patch_command
from a816.error_codes import E_SEAL_NO_CARTRIDGE
from a816.exceptions import A816ConfigError
from a816.seal import seal_ips
from a816.snes_header import cartridge_at, checksum_fields


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="a816 ips", description="Seal IPS patches.")
    commands = parser.add_subparsers(dest="command", required=True)
    seal = commands.add_parser("seal", help="Append the SNES header checksum record.")
    seal.add_argument("patch", type=Path, help="The patch `a816 build` wrote")
    seal.add_argument("--source", type=Path, required=True, help="The headerless base ROM the patch applies to")
    seal.add_argument("-o", "--output", type=Path, help="Sealed patch (default: PATCH, in place)")
    seal.set_defaults(run=_seal)
    return parser


def _seal(args: argparse.Namespace, _parser: argparse.ArgumentParser) -> int:
    cartridge = cartridge_at(Path.cwd())
    if cartridge is None:
        raise A816ConfigError(
            E_SEAL_NO_CARTRIDGE,
            "sealing needs the cartridge: run in a project whose a816.toml sets `board` or `[map.N]` and `rom_size`",
        )
    base = args.source.read_bytes()
    sealed = seal_ips(args.patch.read_bytes(), base, cartridge)
    output = args.output or args.patch
    output.write_bytes(sealed)
    fields = checksum_fields(ips.apply(sealed, base), cartridge)
    print(f"sealed {output} (header checksum {int.from_bytes(fields[2:], 'little'):04X})")
    return 0


def ips_main(argv: list[str]) -> int:
    """Run one `a816 ips` command. 0 on success, 1 on a patch, ROM, config or file error."""
    return run_patch_command(_parser(), argv)
