"""
xdds - SNES hex dump tool with bus mapping support.

Like xxd but displays SNES logical addresses based on the selected memory mapping mode:
- LoROM (Mode 20): Banks $00-$7D mapped to $8000-$FFFF
- HiROM (Mode 21): Banks $40-$7D mapped to full 64KB
"""

import argparse
import logging
import shutil
import sys
import tempfile
from pathlib import Path

from a816.cpu.cpu_65c816 import RomType
from a816.cpu.disassembler import Disassembler, format_disassembly
from a816.cpu.mapping import Bus
from a816.symbols import high_rom_bus, low_rom_bus

logger = logging.getLogger("xdds")


def get_bus_for_rom_type(rom_type: RomType) -> Bus | None:
    """Get the Bus instance for a given RomType."""
    if rom_type == RomType.low_rom or rom_type == RomType.low_rom_2:
        return low_rom_bus
    elif rom_type == RomType.high_rom:
        return high_rom_bus
    return None


def parse_address(addr_str: str) -> tuple[int, bool]:
    """
    Parse an address string, supporting both physical offsets and SNES logical addresses.

    Formats supported:
    - Physical: 0x1234, 1234, 0xFF40
    - SNES logical: $01:FF40, 01:FF40, $01FF40

    Returns:
        tuple of (address_value, is_snes_address)
    """
    addr_str = addr_str.strip()

    # Check for SNES format with colon: $BB:AAAA or BB:AAAA
    if ":" in addr_str:
        addr_str = addr_str.lstrip("$")
        parts = addr_str.split(":")
        if len(parts) == 2:
            bank = int(parts[0], 16)
            offset = int(parts[1], 16)
            return (bank << 16) | offset, True

    # Check for $ prefix (SNES style hex)
    if addr_str.startswith("$"):
        value = int(addr_str[1:], 16)
        # If it's 6 digits (3 bytes), treat as SNES address
        if len(addr_str) > 5:  # $BBAAAA
            return value, True
        return value, False

    # Standard numeric (physical offset)
    return int(addr_str, 0), False


def logical_to_physical(bus: Bus, logical_addr: int) -> int | None:
    """Convert SNES logical address to physical ROM address."""
    addr = bus.get_address(logical_addr)
    return addr.physical


def physical_to_logical(bus: Bus, physical_addr: int) -> int:
    """Convert physical ROM address to SNES logical address."""
    for mapping in bus.mappings.values():
        if mapping.writable:
            continue
        logical = mapping.logical_address(physical_addr)
        return logical
    return physical_addr


def apply_ips_patch(rom_path: Path, ips_path: Path, output_path: Path) -> None:
    """Apply an IPS patch to a ROM file."""
    shutil.copy(rom_path, output_path)

    with open(ips_path, "rb") as ips_file:
        header = ips_file.read(5)
        if header != b"PATCH":
            raise ValueError(f"Invalid IPS file: {ips_path}")

        with open(output_path, "r+b") as rom_file:
            while True:
                record_offset = ips_file.read(3)
                if record_offset == b"EOF":
                    break
                if len(record_offset) < 3:
                    raise ValueError("Unexpected end of IPS file")

                offset = int.from_bytes(record_offset, "big")
                size_bytes = ips_file.read(2)
                if len(size_bytes) < 2:
                    raise ValueError("Unexpected end of IPS file")

                size = int.from_bytes(size_bytes, "big")

                if size == 0:
                    # RLE encoded
                    rle_size_bytes = ips_file.read(2)
                    rle_size = int.from_bytes(rle_size_bytes, "big")
                    rle_value = ips_file.read(1)
                    rom_file.seek(offset)
                    rom_file.write(rle_value * rle_size)
                else:
                    data = ips_file.read(size)
                    rom_file.seek(offset)
                    rom_file.write(data)


def hexdump(
    data: bytes,
    bus: Bus,
    start_offset: int = 0,
    bytes_per_line: int = 16,
    show_ascii: bool = True,
) -> None:
    """Print hex dump with SNES logical addresses."""
    for i in range(0, len(data), bytes_per_line):
        physical_addr = start_offset + i
        logical_addr = physical_to_logical(bus, physical_addr)

        # Format: $BB:AAAA where BB is bank and AAAA is address
        bank = (logical_addr >> 16) & 0xFF
        addr = logical_addr & 0xFFFF

        # Hex bytes
        line_data = data[i : i + bytes_per_line]
        hex_part = " ".join(f"{b:02x}" for b in line_data)
        hex_part = hex_part.ljust(bytes_per_line * 3 - 1)

        if show_ascii:
            # ASCII representation
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in line_data)
            print(f"${bank:02x}:{addr:04x}  {hex_part}  {ascii_part}")
        else:
            print(f"${bank:02x}:{addr:04x}  {hex_part}")


def show_mapping_info(rom_type: RomType) -> None:
    """Display information about the selected bus mapping."""
    bus = get_bus_for_rom_type(rom_type)
    if bus is None:
        logger.info(f"ROM Type: {rom_type.name} (no predefined bus mapping)")
        return

    logger.info(f"ROM Type: {rom_type.name}")
    logger.info(f"Bus Name: {bus.name}")
    for identifier, mapping in bus.mappings.items():
        bank_start, bank_end = mapping.bank_range
        addr_start, addr_end = mapping.address_range
        logger.info(
            f"  {identifier}: Banks ${bank_start:02X}-${bank_end:02X} "
            f"Address ${addr_start:04X}-${addr_end:04X} "
            f"Mask ${mapping.mask:04X} "
            f"{'(writable)' if mapping.writable else '(ROM)'}"
        )


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xdds",
        description="SNES hex dump tool - like xxd but with SNES logical addresses",
        epilog="Bus mapping determines how physical ROM addresses map to SNES logical addresses.",
    )

    parser.add_argument("input_file", type=Path, help="Input ROM file to dump")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output")

    # Bus mapping group - mutually exclusive
    mapping_group = parser.add_mutually_exclusive_group()
    mapping_group.add_argument(
        "--low-rom",
        "--lorom",
        action="store_const",
        const=RomType.low_rom,
        dest="rom_type",
        help="Use LoROM mapping (Mode 20): Banks $00-$6F at $8000-$FFFF, mirrored at $80-$CF",
    )
    mapping_group.add_argument(
        "--low-rom-2",
        "--lorom2",
        action="store_const",
        const=RomType.low_rom_2,
        dest="rom_type",
        help="Use LoROM variant 2 mapping",
    )
    mapping_group.add_argument(
        "--high-rom",
        "--hirom",
        action="store_const",
        const=RomType.high_rom,
        dest="rom_type",
        help="Use HiROM mapping (Mode 21): Banks $40-$7F at $0000-$FFFF, mirrored at $C0-$FF",
    )

    parser.add_argument(
        "--ips",
        type=Path,
        dest="ips_file",
        help="Apply IPS patch to input file before dumping",
    )

    parser.add_argument(
        "-s",
        "--start",
        type=str,
        default="0",
        help="Start address: physical (0x1234) or SNES ($01:FF40, 01:FF40)",
    )
    parser.add_argument(
        "-l",
        "--length",
        type=lambda x: int(x, 0),
        default=None,
        help="Number of bytes to dump (default: entire file)",
    )
    parser.add_argument(
        "-c",
        "--cols",
        type=int,
        default=16,
        help="Number of bytes per line (default: 16)",
    )
    parser.add_argument(
        "--no-ascii",
        action="store_true",
        help="Don't show ASCII representation",
    )
    parser.add_argument(
        "--show-mappings",
        action="store_true",
        help="Display the memory mapping configuration being used",
    )

    # Disassembly options
    parser.add_argument(
        "-d",
        "--disasm",
        action="store_true",
        help="Disassemble as 65c816 code instead of hex dump",
    )
    parser.add_argument(
        "-n",
        "--count",
        type=int,
        default=None,
        help="Number of instructions to disassemble (default: all in range)",
    )
    parser.add_argument(
        "--m8",
        action="store_true",
        dest="m_flag",
        default=True,
        help="Assume 8-bit accumulator (M=1, default)",
    )
    parser.add_argument(
        "--m16",
        action="store_false",
        dest="m_flag",
        help="Assume 16-bit accumulator (M=0)",
    )
    parser.add_argument(
        "--x8",
        action="store_true",
        dest="x_flag",
        default=True,
        help="Assume 8-bit index registers (X=1, default)",
    )
    parser.add_argument(
        "--x16",
        action="store_false",
        dest="x_flag",
        help="Assume 16-bit index registers (X=0)",
    )
    parser.add_argument(
        "--no-bytes",
        action="store_true",
        help="Don't show raw bytes in disassembly output",
    )
    parser.add_argument(
        "--asm",
        action="store_true",
        help="Output a816-compatible assembly syntax (use with -d)",
    )

    return parser


def disassemble(
    data: bytes,
    bus: Bus,
    start_offset: int,
    m_flag: bool = True,
    x_flag: bool = True,
    count: int | None = None,
    show_bytes: bool = True,
    a816_syntax: bool = False,
) -> None:
    """Disassemble and print 65c816 code with SNES logical addresses."""
    start_logical = physical_to_logical(bus, start_offset)
    disasm = Disassembler(m_flag=m_flag, x_flag=x_flag)
    instructions = disasm.disassemble(data, start_logical, count)

    for inst in instructions:
        print(format_disassembly(inst, show_bytes=show_bytes, a816_syntax=a816_syntax))


def _apply_ips_to_temp(input_file: Path, ips_file: Path) -> tuple[Path, Path]:
    """Apply IPS patch to a temp copy. Returns (patched_path, temp_path) for cleanup."""
    if not ips_file.exists():
        logger.error(f"IPS file not found: {ips_file}")
        sys.exit(-1)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as tmp:
        tmp_path = Path(tmp.name)
    try:
        logger.info(f"Applying IPS patch: {ips_file}")
        apply_ips_patch(input_file, ips_file, tmp_path)
    except ValueError as e:
        logger.error(str(e))
        sys.exit(-1)
    return tmp_path, tmp_path


def _resolve_physical_start(args: argparse.Namespace, bus: Bus) -> int:
    start_addr, is_snes = parse_address(args.start)
    if not is_snes:
        return start_addr
    physical_start = logical_to_physical(bus, start_addr)
    if physical_start is None:
        logger.error(f"Cannot convert SNES address ${start_addr:06X} to physical offset (not in ROM range)")
        sys.exit(-1)
    logger.info(f"SNES address ${start_addr >> 16:02X}:{start_addr & 0xFFFF:04X} -> physical 0x{physical_start:X}")
    return physical_start


def _read_slice(input_file: Path, physical_start: int, length: int | None) -> bytes:
    with open(input_file, "rb") as f:
        f.seek(physical_start)
        return f.read(length) if length else f.read()


def _resolve_bus(args: argparse.Namespace) -> tuple[RomType, Bus]:
    rom_type = args.rom_type if args.rom_type else RomType.low_rom
    bus = get_bus_for_rom_type(rom_type)
    if bus is None:
        logger.error(f"No bus mapping available for {rom_type.name}")
        sys.exit(-1)
    return rom_type, bus


def xdds_main() -> None:
    args = create_parser().parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s - %(message)s")

    rom_type, bus = _resolve_bus(args)
    if args.show_mappings:
        show_mapping_info(rom_type)

    input_file = args.input_file
    tmp_path: Path | None = None
    if args.ips_file:
        input_file, tmp_path = _apply_ips_to_temp(input_file, args.ips_file)

    try:
        if not input_file.exists():
            logger.error(f"Input file not found: {input_file}")
            sys.exit(-1)

        physical_start = _resolve_physical_start(args, bus)
        data = _read_slice(input_file, physical_start, args.length)

        if args.disasm:
            disassemble(
                data,
                bus,
                physical_start,
                m_flag=args.m_flag,
                x_flag=args.x_flag,
                count=args.count,
                show_bytes=not args.no_bytes,
                a816_syntax=args.asm,
            )
        else:
            hexdump(data, bus, physical_start, args.cols, not args.no_ascii)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()


if __name__ == "__main__":
    xdds_main()
