import argparse
import logging
import sys
from pathlib import Path

from a816.linker import Linker
from a816.object_file import ObjectFile
from a816.program import Program

logger = logging.getLogger("x816")


def cli_main() -> None:
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

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")

    try:
        if args.compile_only:
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
            linker = Linker(object_files)
            linked_obj = linker.link()

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

    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(-1)


if __name__ == "__main__":
    cli_main()
