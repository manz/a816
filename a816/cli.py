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
import logging
import sys
from pathlib import Path

from a816.exceptions import LinkerError
from a816.linker import Linker
from a816.object_file import ObjectFile
from a816.parse.nodes import NodeError
from a816.program import Program

logger = logging.getLogger("x816")


def cli_main() -> None:
    """Main CLI entry point for the x816 assembler.

    Parses command-line arguments and executes the appropriate assembly
    or linking workflow. Handles both compile-only mode (-c) and direct
    linking mode.

    Exit codes:
        0: Success
        1: Error during assembly or linking
        -1: Invalid input or configuration error
    """
    parser = argparse.ArgumentParser(prog="x816", description="x816 usage", epilog="")
    parser.add_argument("--verbose", action="store_true", help="Displays all log levels.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        dest="output_file",
        default="a.out",
        help="Output file",
    )
    parser.add_argument("input_files", nargs="+", type=Path, help="Input files (asm files or object files for linking)")
    parser.add_argument("-f", dest="format", default="ips", help="Output format (ips, sfc, obj)")
    parser.add_argument("-m", dest="mapping", default="low", help="Address Mapping")
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
        "--prelude",
        type=Path,
        dest="prelude_file",
        default=None,
        help="Config file prepended to every module compilation (e.g., feature flags).",
    )

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")

    # Determine if we should use auto-imports (default for single source file)
    use_auto_imports = (
        not args.no_auto_imports
        and not args.compile_only
        and len(args.input_files) == 1
        and args.input_files[0].suffix in [".s", ".asm"]
    )

    try:
        if use_auto_imports:
            # Auto-import mode: discover and compile all dependencies
            from a816.module_builder import build_with_imports

            main_source = args.input_files[0]

            # Parse defines - only numeric values are supported for auto-import mode
            # String values like MY_TEXT=ABC need to be passed directly to resolver
            symbols: dict[str, int | str] = {}
            if args.defines:
                for item in args.defines:
                    key, value = item.split("=", 1)
                    try:
                        symbols[key] = int(value, 0)  # Support hex (0x) and decimal
                    except ValueError:
                        symbols[key] = value  # Keep string values as-is

            # Build module paths
            module_paths = [Path(p) for p in args.module_paths]
            include_paths = [Path(p) for p in args.include_paths]

            result = build_with_imports(
                main_source=main_source,
                output_file=args.output_file,
                output_format=args.format,
                module_paths=module_paths,
                output_dir=args.obj_dir,
                symbols=symbols,
                copier_header=args.copier_header,
                include_paths=include_paths,
                prelude_file=args.prelude_file,
            )
            sys.exit(result.exit_code)

        elif args.compile_only:
            # Compile each input file to an object file
            exit_code = 0
            for input_file in args.input_files:
                if input_file.suffix not in [".s", ".asm"]:
                    logger.error(f"Cannot compile non-assembly file: {input_file}")
                    sys.exit(-1)

                # Generate object file name
                obj_file = input_file.with_suffix(".o")
                if len(args.input_files) > 1:
                    logger.info(f"Compiling {input_file} -> {obj_file}")

                program = Program(dump_symbols=args.dump_symbols)
                for inc_path in args.include_paths:
                    program.add_include_path(inc_path)
                if args.defines:
                    for item in args.defines:
                        key, value = item.split("=", 1)
                        program.resolver.current_scope.add_symbol(key, value)

                exit_code = program.assemble_as_object(str(input_file), obj_file)
                if exit_code != 0:
                    break

            sys.exit(exit_code)

        else:
            # Link mode - handle mixed input files
            object_files = []

            for input_file in args.input_files:
                if input_file.suffix == ".o":
                    # Load existing object file
                    obj = ObjectFile.read(str(input_file))
                    object_files.append(obj)

                elif input_file.suffix in [".s", ".asm"]:
                    # Compile to temporary object file first
                    program = Program(dump_symbols=args.dump_symbols)
                    if args.defines:
                        for item in args.defines:
                            key, value = item.split("=", 1)
                            program.resolver.current_scope.add_symbol(key, value)

                    temp_obj_file = input_file.with_suffix(".tmp.o")
                    exit_code = program.assemble_as_object(str(input_file), temp_obj_file)
                    if exit_code != 0:
                        sys.exit(exit_code)

                    obj = ObjectFile.read(str(temp_obj_file))
                    object_files.append(obj)
                    # Clean up temp file
                    temp_obj_file.unlink()

                else:
                    logger.error(f"Unknown file type: {input_file}")
                    sys.exit(-1)

            if not object_files:
                logger.error("No input files to link")
                sys.exit(-1)

            # Link all object files
            # Default base address is 0x8000 for SNES LoROM
            base_address = 0x8000
            linker = Linker(object_files)
            linked_obj = linker.link(base_address=base_address)

            # Generate final output based on format
            program = Program(dump_symbols=args.dump_symbols)

            if args.format == "ips":
                exit_code = program.link_as_patch(linked_obj, args.output_file, args.mapping, args.copier_header)
            elif args.format == "sfc":
                exit_code = program.link_as_sfc(linked_obj, args.output_file)
            else:
                logger.error(f"Unknown output format: {args.format}")
                sys.exit(-1)

            sys.exit(exit_code)

    except LinkerError as e:
        # Use formatted error display for linker errors
        print(e.format(), file=sys.stderr)
        sys.exit(1)
    except NodeError as e:
        # Use formatted error display for node errors
        print(e.format(), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        # Fallback for unexpected errors
        from a816.errors import format_error_simple

        print(format_error_simple("error", str(e)), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    cli_main()
