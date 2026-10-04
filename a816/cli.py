"""Command-line interface for the a816/x816 65c816 assembler.

This module provides the main CLI entry point for assembling SNES/Super Famicom
ROM code. Supports both direct assembly and separate compilation/linking workflows.

Usage:
    # Direct assembly to IPS patch (auto-discovers .import dependencies)
    x816 main.s -o output.ips

    # With additional module search paths
    x816 main.s -o output.ips -I src/ -I lib/

    # Compile to object files only
    x816 -c file1.s file2.s

    # Link object files explicitly (no auto-import)
    x816 file1.o file2.o -o output.ips

    # Mixed compilation and linking (explicit mode)
    x816 file1.s file2.o -o output.ips -f sfc

    # Disable auto-imports for single file
    x816 --no-auto-imports main.s -o output.ips
"""

import argparse
import gc
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from a816.config import A816Config, discover_a816_config
from a816.error_codes import E_CONFIG_MAPPER_MISMATCH
from a816.exceptions import A816ConfigError, A816Error, LinkerError
from a816.linker import Linker
from a816.mappers import CLI_MAPPERS, MAPPER_CLI_FLAGS
from a816.module_builder import apply_experimental_flags
from a816.object_file import ObjectFile
from a816.parse.nodes import NodeError
from a816.program import Program

logger = logging.getLogger("x816")


_DEFAULT_MAPPING = "low"


def _apply_a816_toml(args: argparse.Namespace) -> None:
    """Merge `a816.toml` settings into `args` (CLI flags win over file).

    `-m` and the toml `mapper` must agree: the toml regions are seeded
    into every object, so letting `-m` silently win would build a bus
    that matches neither. Without `-m`, the toml `mapper` picks it.
    """
    config = _discover_config(args)
    if config is not None:
        _merge_config(args, config)
    if args.mapping is None:
        args.mapping = _DEFAULT_MAPPING


def _discover_config(args: argparse.Namespace) -> A816Config | None:
    if not args.input_files:
        return None
    start = args.input_files[0]
    return discover_a816_config(start if start.is_file() else start.parent)


def _merge_mapper(args: argparse.Namespace, config: A816Config) -> None:
    if config.mapper is None:
        return
    cli_mapping = args.mapping
    if cli_mapping is None:
        args.mapping = MAPPER_CLI_FLAGS[config.mapper]
        return
    if CLI_MAPPERS[cli_mapping] != config.mapper:
        raise A816ConfigError(
            E_CONFIG_MAPPER_MISMATCH,
            f"`-m {cli_mapping}` disagrees with `mapper = {config.mapper!r}`; drop one of them",
            config.config_path,
        )


def _merge_config(args: argparse.Namespace, config: A816Config) -> None:
    _merge_mapper(args, config)
    args.bus_map = list(config.bus_map)
    if config.include_paths and not args.include_paths:
        args.include_paths = [str(p) for p in config.include_paths]
    if config.module_paths and not args.module_paths:
        args.module_paths = [str(p) for p in config.module_paths]
    # Mirror [experimental] from a816.toml. CLI --experimental wins
    # on overlap (already in args.experimental as a list of flag names).
    cli_flags = set(args.experimental or [])
    for flag, enabled in config.experimental.items():
        if enabled and flag not in cli_flags:
            args.experimental = (args.experimental or []) + [flag]


_ASM_SUFFIXES = (".s", ".asm")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="a816", description="a816 usage", epilog="")
    parser.add_argument("--verbose", action="store_true", help="Displays all log levels.")
    parser.add_argument("-o", "--output", type=Path, dest="output_file", default="a.out", help="Output file")
    parser.add_argument("input_files", nargs="+", type=Path, help="Input files (asm files or object files for linking)")
    parser.add_argument("-f", dest="format", default="ips", help="Output format (ips, sfc, obj)")
    parser.add_argument(
        "-m",
        dest="mapping",
        default=None,
        choices=tuple(CLI_MAPPERS),
        help=(
            "Address mapping: low (LoROM, default), low2 (LoROM, alternate), high (HiROM). "
            "Must agree with `mapper` in a816.toml when both are set."
        ),
    )
    parser.add_argument(
        "--copier-header",
        action="store_true",
        help="Adds 0x200 address delta corresponding to copier header in ips writer.",
    )
    parser.add_argument("--dump-symbols", action="store_true", help="Dumps symbol table")
    parser.add_argument("-c", "--compile-only", action="store_true", help="Compile to object files without linking.")
    parser.add_argument("-D", "--defines", metavar="KEY=VALUE", nargs="+", help="Defines symbols.")
    parser.add_argument(
        "--no-auto-imports",
        action="store_true",
        help="Disable automatic import resolution (use explicit file list instead).",
    )
    parser.add_argument(
        "-I",
        "--module-path",
        metavar="PATH",
        action="append",
        dest="module_paths",
        default=[],
        help="Add directory to module search path (can be specified multiple times).",
    )
    parser.add_argument(
        "--obj-dir",
        type=Path,
        dest="obj_dir",
        default=None,
        help="Directory for compiled object files (default: build/obj).",
    )
    parser.add_argument(
        "--include-path",
        metavar="PATH",
        action="append",
        dest="include_paths",
        default=[],
        help="Add directory to include search path for .include directives.",
    )
    parser.add_argument(
        "--overlap-mode",
        choices=("error", "warn", "off"),
        dest="overlap_mode",
        default=None,
        help=(
            "How to react when two `*=` / `.alloc` sections write to "
            "overlapping bytes. Default `error` (build fails); `warn` "
            "logs + continues (legacy ROM compat); `off` skips the check."
        ),
    )
    parser.add_argument(
        "--experimental",
        metavar="FLAG",
        action="append",
        dest="experimental",
        default=[],
        help=(
            "Enable an experimental feature (repeatable). Known flags: "
            "`track_register_size` (rep/sep -> a_size/i_size inference). "
            "Mirrors the [experimental] table in `a816.toml`. CLI wins."
        ),
    )
    parser.set_defaults(bus_map=[])
    return parser


def _parse_defines(defines: list[str] | None) -> dict[str, int | str]:
    """Numeric values use int(., 0); string values pass through."""
    symbols: dict[str, int | str] = {}
    for item in defines or []:
        key, value = item.split("=", 1)
        try:
            symbols[key] = int(value, 0)
        except ValueError:
            symbols[key] = value
    return symbols


def _run_auto_imports(args: argparse.Namespace) -> int:
    from a816.module_builder import build_with_imports

    result = build_with_imports(
        main_source=args.input_files[0],
        output_file=args.output_file,
        output_format=args.format,
        module_paths=[Path(p) for p in args.module_paths],
        output_dir=args.obj_dir,
        symbols=_parse_defines(args.defines),
        copier_header=args.copier_header,
        include_paths=[Path(p) for p in args.include_paths],
        overlap_mode=args.overlap_mode,
        experimental=list(args.experimental or []),
        mapping=args.mapping,
        bus_map=list(args.bus_map),
    )
    return result.exit_code


def _new_compile_program(args: argparse.Namespace) -> Program:
    """A `Program` for one translation unit, carrying the CLI / toml settings."""
    program = Program(dump_symbols=args.dump_symbols, overlap_mode=args.overlap_mode)
    apply_experimental_flags(program, args.experimental)
    program.resolver.context.bus_map = list(args.bus_map)
    return program


def _run_compile_only(args: argparse.Namespace) -> int:
    exit_code = 0
    multi = len(args.input_files) > 1
    for input_file in args.input_files:
        if input_file.suffix not in _ASM_SUFFIXES:
            logger.error(f"Cannot compile non-assembly file: {input_file}")
            sys.exit(-1)
        obj_file = input_file.with_suffix(".o")
        if multi:
            logger.info(f"Compiling {input_file} -> {obj_file}")
        program = _new_compile_program(args)
        for inc_path in args.include_paths:
            program.add_include_path(inc_path)
        for key, value in _parse_defines(args.defines).items():
            program.resolver.current_scope.add_symbol(key, value)
        exit_code = program.assemble_as_object(str(input_file), obj_file)
        if exit_code != 0:
            break
    return exit_code


def _load_or_compile_object(input_file: Path, args: argparse.Namespace) -> ObjectFile:
    if input_file.suffix == ".o":
        return ObjectFile.from_file(str(input_file))
    if input_file.suffix not in _ASM_SUFFIXES:
        logger.error(f"Unknown file type: {input_file}")
        sys.exit(-1)

    program = _new_compile_program(args)
    for key, value in _parse_defines(args.defines).items():
        program.resolver.current_scope.add_symbol(key, value)
    temp_obj_file = input_file.with_suffix(".tmp.o")
    exit_code = program.assemble_as_object(str(input_file), temp_obj_file)
    if exit_code != 0:
        sys.exit(exit_code)
    try:
        return ObjectFile.from_file(str(temp_obj_file))
    finally:
        temp_obj_file.unlink()


def _run_link(args: argparse.Namespace) -> int:
    object_files = [_load_or_compile_object(f, args) for f in args.input_files]
    if not object_files:
        logger.error("No input files to link")
        sys.exit(-1)

    linked_obj = Linker(object_files).link(base_address=0x8000)
    program = Program(dump_symbols=args.dump_symbols, overlap_mode=args.overlap_mode)
    apply_experimental_flags(program, args.experimental)
    if args.format == "ips":
        return program.link_as_patch(linked_obj, args.output_file, args.mapping, args.copier_header)
    if args.format == "sfc":
        return program.link_as_sfc(linked_obj, args.output_file, args.mapping)
    logger.error(f"Unknown output format: {args.format}")
    sys.exit(-1)


_SUBCOMMANDS: tuple[str, ...] = ("build", "check", "fix", "format", "explain")


def _dispatch_subcommand(argv: list[str]) -> int | None:
    """Return an exit code if `argv` starts with a known subcommand, else None.

    Bare assemble invocations (no subcommand) keep working — the caller
    falls through to the legacy parser. `build` is just an explicit
    alias for that path.
    """
    if not argv or argv[0] not in _SUBCOMMANDS:
        return None
    cmd, rest = argv[0], argv[1:]
    if cmd == "build":
        args = _build_arg_parser().parse_args(rest)
        return _run_assemble(args)
    if cmd in {"check", "fix", "format", "explain"}:
        from a816.fluff import fluff_main

        return fluff_main([cmd, *rest])
    return None


# Allocations between gen-0 collections during a build. The CPython default
# (2000) re-walks the growing token/AST/node heap thousands of times on a
# large file; 50k cut a cold 100k-line build by ~20% on 3.14 and 3.15.
_BUILD_GC_THRESHOLD = 50_000


@contextmanager
def _build_gc_threshold() -> Iterator[None]:
    """Collect gen 0 less often for the duration of a build, then restore."""
    previous = gc.get_threshold()
    gc.set_threshold(_BUILD_GC_THRESHOLD, *previous[1:])
    try:
        yield
    finally:
        gc.set_threshold(*previous)


def _run_assemble(args: argparse.Namespace) -> int:
    with _build_gc_threshold():
        return _assemble(args)


def _assemble(args: argparse.Namespace) -> int:
    _apply_a816_toml(args)
    use_auto_imports = (
        not args.no_auto_imports
        and not args.compile_only
        and len(args.input_files) == 1
        and args.input_files[0].suffix in _ASM_SUFFIXES
    )
    if use_auto_imports:
        return _run_auto_imports(args)
    if args.compile_only:
        return _run_compile_only(args)
    return _run_link(args)


def cli_main() -> None:
    """a816 CLI entry. Exit 0 success, 1 assembly/link error, -1 invalid input."""
    argv = sys.argv[1:]
    # `--verbose` flips root logger to DEBUG so caught NodeError/IPS/etc.
    # paths emit their stashed traceback via `logger.debug(exc_info=True)`.
    level = logging.DEBUG if "--verbose" in argv else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s - %(message)s")
    try:
        rc = _dispatch_subcommand(argv)
        if rc is not None:
            sys.exit(rc)
        args = _build_arg_parser().parse_args(argv)
        sys.exit(_run_assemble(args))
    except LinkerError as e:
        print(e.format(), file=sys.stderr)
        sys.exit(1)
    except NodeError as e:
        print(e.format(), file=sys.stderr)
        sys.exit(1)
    except A816Error as e:
        # Other assembler errors (e.g. UnmappedBankError) carry their own
        # `format()`; fall back to a simple render if a subclass lacks one.
        from a816.errors import format_error_simple

        message = e.format() if hasattr(e, "format") else format_error_simple("error", str(e))
        print(message, file=sys.stderr)
        sys.exit(1)
    except Exception as e:  # noqa: BLE001 - CLI boundary: any failure becomes a clean message
        from a816.errors import format_error_simple

        print(format_error_simple("error", str(e)), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    cli_main()
