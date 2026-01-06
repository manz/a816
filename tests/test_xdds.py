"""Tests for xdds - SNES hex dump tool with bus mapping support."""

import tempfile
from pathlib import Path

import pytest

from a816.cpu.cpu_65c816 import RomType
from a816.symbols import high_rom_bus, low_rom_bus
from a816.xdds import (
    apply_ips_patch,
    create_parser,
    get_bus_for_rom_type,
    hexdump,
    logical_to_physical,
    parse_address,
    physical_to_logical,
)


class TestCreateParser:
    """Tests for the argument parser."""

    def test_parser_accepts_low_rom_flag(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["--low-rom", "input.sfc"])
        assert args.rom_type == RomType.low_rom

    def test_parser_accepts_lorom_alias(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["--lorom", "input.sfc"])
        assert args.rom_type == RomType.low_rom

    def test_parser_accepts_high_rom_flag(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["--high-rom", "input.sfc"])
        assert args.rom_type == RomType.high_rom

    def test_parser_accepts_hirom_alias(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["--hirom", "input.sfc"])
        assert args.rom_type == RomType.high_rom

    def test_parser_accepts_low_rom_2_flag(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["--low-rom-2", "input.sfc"])
        assert args.rom_type == RomType.low_rom_2

    def test_parser_accepts_lorom2_alias(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["--lorom2", "input.sfc"])
        assert args.rom_type == RomType.low_rom_2

    def test_parser_default_no_mapping(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["input.sfc"])
        assert args.rom_type is None

    def test_parser_mutually_exclusive_mappings(self) -> None:
        parser = create_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--low-rom", "--high-rom", "input.sfc"])

    def test_parser_accepts_ips_file(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["--ips", "patch.ips", "input.sfc"])
        assert str(args.ips_file) == "patch.ips"

    def test_parser_accepts_start_offset(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["-s", "0x1000", "input.sfc"])
        assert args.start == "0x1000"

    def test_parser_accepts_start_offset_decimal(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["-s", "4096", "input.sfc"])
        assert args.start == "4096"

    def test_parser_accepts_snes_address(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["-s", "$01:FF40", "input.sfc"])
        assert args.start == "$01:FF40"

    def test_parser_accepts_length(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["-l", "0x100", "input.sfc"])
        assert args.length == 0x100

    def test_parser_accepts_cols(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["-c", "32", "input.sfc"])
        assert args.cols == 32

    def test_parser_accepts_no_ascii(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["--no-ascii", "input.sfc"])
        assert args.no_ascii is True

    def test_parser_accepts_show_mappings(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["--show-mappings", "input.sfc"])
        assert args.show_mappings is True

    def test_parser_accepts_verbose(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["--verbose", "input.sfc"])
        assert args.verbose is True


class TestGetBusForRomType:
    """Tests for get_bus_for_rom_type function."""

    def test_low_rom_returns_low_rom_bus(self) -> None:
        bus = get_bus_for_rom_type(RomType.low_rom)
        assert bus is low_rom_bus

    def test_low_rom_2_returns_low_rom_bus(self) -> None:
        bus = get_bus_for_rom_type(RomType.low_rom_2)
        assert bus is low_rom_bus

    def test_high_rom_returns_high_rom_bus(self) -> None:
        bus = get_bus_for_rom_type(RomType.high_rom)
        assert bus is high_rom_bus


class TestPhysicalToLogical:
    """Tests for physical to logical address conversion."""

    def test_lorom_physical_0_to_logical(self) -> None:
        # Physical 0x0000 -> LoROM $00:8000
        logical = physical_to_logical(low_rom_bus, 0x0000)
        assert logical == 0x008000

    def test_lorom_physical_0x8000_to_logical(self) -> None:
        # Physical 0x8000 -> LoROM $01:8000
        logical = physical_to_logical(low_rom_bus, 0x8000)
        assert logical == 0x018000

    def test_lorom_physical_0x10000_to_logical(self) -> None:
        # Physical 0x10000 -> LoROM $02:8000
        logical = physical_to_logical(low_rom_bus, 0x10000)
        assert logical == 0x028000

    def test_hirom_physical_0_to_logical(self) -> None:
        # Physical 0x0000 -> HiROM $40:0000
        logical = physical_to_logical(high_rom_bus, 0x0000)
        assert logical == 0x400000

    def test_hirom_physical_0x10000_to_logical(self) -> None:
        # Physical 0x10000 -> HiROM $41:0000
        logical = physical_to_logical(high_rom_bus, 0x10000)
        assert logical == 0x410000


class TestBusMappings:
    """Tests to verify the bus mappings are correctly configured."""

    def test_low_rom_bus_has_name(self) -> None:
        assert low_rom_bus.name == "low_rom_default_mapping"

    def test_high_rom_bus_has_name(self) -> None:
        assert high_rom_bus.name == "high_rom_default_mapping"

    def test_low_rom_bus_is_not_editable(self) -> None:
        assert low_rom_bus.editable is False

    def test_high_rom_bus_is_not_editable(self) -> None:
        assert high_rom_bus.editable is False

    def test_low_rom_bus_has_mappings(self) -> None:
        assert low_rom_bus.has_mappings() is True

    def test_high_rom_bus_has_mappings(self) -> None:
        assert high_rom_bus.has_mappings() is True


class TestHexdump:
    """Tests for the hexdump function."""

    def test_hexdump_output_format(self, capsys: pytest.CaptureFixture[str]) -> None:
        data = bytes([0x78, 0xA9, 0x00, 0x8D, 0x00, 0x21])
        hexdump(data, low_rom_bus, 0, 16, True)
        captured = capsys.readouterr()
        assert "$00:8000" in captured.out
        assert "78 a9 00 8d 00 21" in captured.out

    def test_hexdump_no_ascii(self, capsys: pytest.CaptureFixture[str]) -> None:
        data = bytes([0x78, 0xA9, 0x00])
        hexdump(data, low_rom_bus, 0, 16, False)
        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        # Without ASCII, line should not have the ASCII section
        assert len(lines) == 1
        # The hex part should be present
        assert "78 a9 00" in lines[0]

    def test_hexdump_with_offset(self, capsys: pytest.CaptureFixture[str]) -> None:
        data = bytes([0x00, 0x01, 0x02, 0x03])
        # Start at physical 0x8000 which is $01:8000 in LoROM
        hexdump(data, low_rom_bus, 0x8000, 16, True)
        captured = capsys.readouterr()
        assert "$01:8000" in captured.out

    def test_hexdump_hirom_addresses(self, capsys: pytest.CaptureFixture[str]) -> None:
        data = bytes([0x00, 0x01, 0x02, 0x03])
        hexdump(data, high_rom_bus, 0, 16, True)
        captured = capsys.readouterr()
        assert "$40:0000" in captured.out


class TestApplyIpsPatch:
    """Tests for IPS patch application."""

    def test_apply_simple_ips_patch(self) -> None:
        # Create a simple ROM file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as rom_file:
            rom_file.write(b"\x00" * 16)
            rom_path = Path(rom_file.name)

        # Create a simple IPS patch: write 0xAB at offset 0x05
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ips") as ips_file:
            ips_file.write(b"PATCH")  # Header
            ips_file.write(b"\x00\x00\x05")  # Offset 5
            ips_file.write(b"\x00\x01")  # Size 1
            ips_file.write(b"\xab")  # Data
            ips_file.write(b"EOF")  # Footer
            ips_path = Path(ips_file.name)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as out_file:
            output_path = Path(out_file.name)

        try:
            apply_ips_patch(rom_path, ips_path, output_path)

            with open(output_path, "rb") as f:
                data = f.read()

            assert data[5] == 0xAB
            assert data[4] == 0x00  # Unchanged
            assert data[6] == 0x00  # Unchanged
        finally:
            rom_path.unlink()
            ips_path.unlink()
            output_path.unlink()

    def test_apply_rle_ips_patch(self) -> None:
        # Create a simple ROM file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as rom_file:
            rom_file.write(b"\x00" * 32)
            rom_path = Path(rom_file.name)

        # Create an IPS patch with RLE: fill 8 bytes of 0xFF at offset 0x10
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ips") as ips_file:
            ips_file.write(b"PATCH")  # Header
            ips_file.write(b"\x00\x00\x10")  # Offset 16
            ips_file.write(b"\x00\x00")  # Size 0 = RLE
            ips_file.write(b"\x00\x08")  # RLE size 8
            ips_file.write(b"\xff")  # RLE value
            ips_file.write(b"EOF")  # Footer
            ips_path = Path(ips_file.name)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as out_file:
            output_path = Path(out_file.name)

        try:
            apply_ips_patch(rom_path, ips_path, output_path)

            with open(output_path, "rb") as f:
                data = f.read()

            # Check RLE was applied
            for i in range(16, 24):
                assert data[i] == 0xFF
            # Check surrounding bytes unchanged
            assert data[15] == 0x00
            assert data[24] == 0x00
        finally:
            rom_path.unlink()
            ips_path.unlink()
            output_path.unlink()

    def test_invalid_ips_header_raises(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as rom_file:
            rom_file.write(b"\x00" * 16)
            rom_path = Path(rom_file.name)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".ips") as ips_file:
            ips_file.write(b"NOTOK")  # Invalid header
            ips_path = Path(ips_file.name)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as out_file:
            output_path = Path(out_file.name)

        try:
            with pytest.raises(ValueError, match="Invalid IPS file"):
                apply_ips_patch(rom_path, ips_path, output_path)
        finally:
            rom_path.unlink()
            ips_path.unlink()
            if output_path.exists():
                output_path.unlink()


class TestParseAddress:
    """Tests for SNES address parsing."""

    def test_parse_physical_hex(self) -> None:
        addr, is_snes = parse_address("0x1000")
        assert addr == 0x1000
        assert is_snes is False

    def test_parse_physical_decimal(self) -> None:
        addr, is_snes = parse_address("4096")
        assert addr == 4096
        assert is_snes is False

    def test_parse_snes_with_colon_and_dollar(self) -> None:
        addr, is_snes = parse_address("$01:FF40")
        assert addr == 0x01FF40
        assert is_snes is True

    def test_parse_snes_with_colon_no_dollar(self) -> None:
        addr, is_snes = parse_address("01:FF40")
        assert addr == 0x01FF40
        assert is_snes is True

    def test_parse_snes_full_address_with_dollar(self) -> None:
        addr, is_snes = parse_address("$01FF40")
        assert addr == 0x01FF40
        assert is_snes is True

    def test_parse_snes_bank_00(self) -> None:
        addr, is_snes = parse_address("$00:8000")
        assert addr == 0x008000
        assert is_snes is True

    def test_parse_hirom_address(self) -> None:
        addr, is_snes = parse_address("$40:0000")
        assert addr == 0x400000
        assert is_snes is True

    def test_parse_short_dollar_hex_not_snes(self) -> None:
        # Short $ addresses are treated as physical hex
        addr, is_snes = parse_address("$1000")
        assert addr == 0x1000
        assert is_snes is False


class TestLogicalToPhysical:
    """Tests for logical to physical address conversion."""

    def test_lorom_008000_to_physical(self) -> None:
        # LoROM $00:8000 -> physical 0x0000
        physical = logical_to_physical(low_rom_bus, 0x008000)
        assert physical == 0x0000

    def test_lorom_01FF40_to_physical(self) -> None:
        # LoROM $01:FF40 -> physical 0xFF40
        physical = logical_to_physical(low_rom_bus, 0x01FF40)
        assert physical == 0xFF40

    def test_lorom_028000_to_physical(self) -> None:
        # LoROM $02:8000 -> physical 0x10000
        physical = logical_to_physical(low_rom_bus, 0x028000)
        assert physical == 0x10000

    def test_hirom_400000_to_physical(self) -> None:
        # HiROM $40:0000 -> physical 0x0000
        physical = logical_to_physical(high_rom_bus, 0x400000)
        assert physical == 0x0000

    def test_hirom_410000_to_physical(self) -> None:
        # HiROM $41:0000 -> physical 0x10000
        physical = logical_to_physical(high_rom_bus, 0x410000)
        assert physical == 0x10000
