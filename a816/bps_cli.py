"""`a816 bps seal|apply|info`: BPS patches, where the base ROM is at hand.

`a816 build -f bps` never reads the base ROM, so its patch leaves the
patched ROM's CRC32 zero. `seal` fills it in, and the SNES header
checksum when a816.toml declares the cartridge; `apply` and `info` are
the same codec for a project that would otherwise need Flips.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from a816 import bps
from a816.bps import BpsError
from a816.error_codes import E_IO_FILE_NOT_FOUND
from a816.errors import format_error_simple
from a816.exceptions import A816ConfigError
from a816.ips import IpsError
from a816.seal import seal_bps
from a816.snes_header import cartridge_at

UNSEALED_SUFFIX = ".unsealed"
NO_CHECKSUM_HELP = (
    "Leave the SNES header checksum as the patch wrote it (default: fix it when a816.toml declares the cartridge)"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="a816 bps", description="Seal, apply and inspect BPS patches.")
    commands = parser.add_subparsers(dest="command", required=True)

    seal = commands.add_parser("seal", help="Write the patched ROM's CRC32 into an unsealed patch.")
    seal.add_argument("patch", type=Path, help="The patch `a816 build -f bps` wrote")
    seal.add_argument("--source", type=Path, required=True, help="The base ROM the patch applies to")
    seal.add_argument("-o", "--output", type=Path, help="Sealed patch (default: PATCH without .unsealed)")
    seal.add_argument("--no-checksum", action="store_true", help=NO_CHECKSUM_HELP)
    seal.set_defaults(run=_seal)

    apply = commands.add_parser("apply", help="Apply a BPS patch (any, Flips' delta ones included).")
    apply.add_argument("patch", type=Path)
    apply.add_argument("--source", type=Path, required=True, help="The base ROM")
    apply.add_argument("-o", "--output", type=Path, required=True, help="The patched ROM")
    apply.add_argument("--unsealed", action="store_true", help="Skip the patched ROM's CRC32 (unsealed patch)")
    apply.set_defaults(run=_apply)

    info = commands.add_parser("info", help="Print a patch's sizes, CRCs and metadata.")
    info.add_argument("patch", type=Path)
    info.set_defaults(run=_info)
    return parser


def _sealed_path(args: argparse.Namespace, parser: argparse.ArgumentParser) -> Path:
    if args.output is not None:
        return Path(args.output)
    patch = Path(args.patch)
    if patch.suffix != UNSEALED_SUFFIX:
        parser.error(f"{patch} does not end in {UNSEALED_SUFFIX}; name the sealed patch with -o")
    return patch.with_suffix("")


def _seal(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    output = _sealed_path(args, parser)
    cartridge = None if args.no_checksum else cartridge_at(Path.cwd())
    sealed = seal_bps(args.patch.read_bytes(), args.source.read_bytes(), cartridge)
    output.write_bytes(sealed)
    checksum_note = "" if cartridge is None else ", header checksum fixed"
    print(f"sealed {output} (patched ROM CRC32 {bps.info(sealed).target_crc32:08X}{checksum_note})")
    return 0


def _apply(args: argparse.Namespace, _parser: argparse.ArgumentParser) -> int:
    target = bps.apply(args.patch.read_bytes(), args.source.read_bytes(), unsealed=args.unsealed)
    args.output.write_bytes(target)
    return 0


def _info(args: argparse.Namespace, _parser: argparse.ArgumentParser) -> int:
    patch_info = bps.info(args.patch.read_bytes())
    target_crc = f"{patch_info.target_crc32:08X}" if patch_info.sealed else "unsealed"
    print(f"source: {patch_info.source_size:#x} bytes, CRC32 {patch_info.source_crc32:08X}")
    print(f"target: {patch_info.target_size:#x} bytes, CRC32 {target_crc}")
    print(f"patch CRC32: {patch_info.patch_crc32:08X}")
    if patch_info.metadata:
        print(f"metadata: {patch_info.metadata}")
    return 0


def bps_main(argv: list[str]) -> int:
    """Run one `a816 bps` command. 0 on success, 1 on a patch, ROM or file error."""
    return run_patch_command(_parser(), argv)


def run_patch_command(parser: argparse.ArgumentParser, argv: list[str]) -> int:
    """Parse `argv` and run its command, a patch, ROM, config or file error printed as exit 1."""
    args = parser.parse_args(argv)
    run: Callable[[argparse.Namespace, argparse.ArgumentParser], int] = args.run
    try:
        return run(args, parser)
    except (BpsError, IpsError, A816ConfigError) as error:
        print(error.format(), file=sys.stderr)
    except OSError as error:
        code = f"[{E_IO_FILE_NOT_FOUND}]" if isinstance(error, FileNotFoundError) else ""
        print(format_error_simple(f"io error{code}", f"{error.filename}: {error.strerror}"), file=sys.stderr)
    return 1
