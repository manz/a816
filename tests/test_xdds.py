"""Tests for xdds - SNES hex dump tool with bus mapping support."""

import tempfile
from pathlib import Path

import pytest

from a816.cpu.cpu_65c816 import RomType
from a816.symbols import high_rom_bus, low_rom_bus
from a816.xdds import (
    apply_ips_patch,
    create_parser,
    disassemble,
    get_bus_for_rom_type,
    hexdump,
    logical_to_physical,
    parse_address,
    physical_to_logical,
    show_mapping_info,
    xdds_main,
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


class TestShowMappingInfo:
    """Tests for the show_mapping_info function."""

    def test_show_mapping_info_lorom(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        with caplog.at_level(logging.INFO):
            show_mapping_info(RomType.low_rom)
        assert "low_rom" in caplog.text
        assert "low_rom_default_mapping" in caplog.text

    def test_show_mapping_info_hirom(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        with caplog.at_level(logging.INFO):
            show_mapping_info(RomType.high_rom)
        assert "high_rom" in caplog.text
        assert "high_rom_default_mapping" in caplog.text


class TestDisassemble:
    """Tests for the disassemble function."""

    def test_disassemble_basic(self, capsys: pytest.CaptureFixture[str]) -> None:
        # SEI (0x78), LDA #0x00 (0xA9 0x00), RTS (0x60)
        data = bytes([0x78, 0xA9, 0x00, 0x60])
        disassemble(data, low_rom_bus, 0, m_flag=True, x_flag=True, count=3)
        captured = capsys.readouterr()
        assert "sei" in captured.out.lower()
        assert "lda" in captured.out.lower()
        assert "rts" in captured.out.lower()

    def test_disassemble_with_count(self, capsys: pytest.CaptureFixture[str]) -> None:
        # Multiple NOPs
        data = bytes([0xEA, 0xEA, 0xEA, 0xEA])
        disassemble(data, low_rom_bus, 0, count=2)
        captured = capsys.readouterr()
        lines = [line for line in captured.out.strip().split("\n") if line]
        assert len(lines) == 2

    def test_disassemble_no_bytes(self, capsys: pytest.CaptureFixture[str]) -> None:
        data = bytes([0xEA])  # NOP
        disassemble(data, low_rom_bus, 0, show_bytes=False)
        captured = capsys.readouterr()
        # Without bytes, should not show raw hex values like "ea"
        # The output should just have the mnemonic
        assert "nop" in captured.out.lower()

    def test_disassemble_m16_mode(self, capsys: pytest.CaptureFixture[str]) -> None:
        # LDA #$1234 in 16-bit mode (A9 34 12)
        data = bytes([0xA9, 0x34, 0x12])
        disassemble(data, low_rom_bus, 0, m_flag=False, count=1)
        captured = capsys.readouterr()
        assert "lda" in captured.out.lower()
        # Should show 16-bit immediate
        assert "1234" in captured.out.lower() or "34 12" in captured.out.lower()

    def test_disassemble_x16_mode(self, capsys: pytest.CaptureFixture[str]) -> None:
        # LDX #$1234 in 16-bit index mode (A2 34 12)
        data = bytes([0xA2, 0x34, 0x12])
        disassemble(data, low_rom_bus, 0, m_flag=True, x_flag=False, count=1)
        captured = capsys.readouterr()
        assert "ldx" in captured.out.lower()

    def test_disassemble_a816_syntax(self, capsys: pytest.CaptureFixture[str]) -> None:
        # NOP
        data = bytes([0xEA])
        disassemble(data, low_rom_bus, 0, a816_syntax=True, count=1)
        captured = capsys.readouterr()
        # a816 syntax should output assembly-compatible format
        assert "nop" in captured.out.lower()

    def test_disassemble_hirom_addresses(self, capsys: pytest.CaptureFixture[str]) -> None:
        data = bytes([0xEA])  # NOP
        disassemble(data, high_rom_bus, 0, count=1)
        captured = capsys.readouterr()
        # HiROM physical 0 -> $40:0000
        assert "40" in captured.out


class TestParserDisasmOptions:
    """Tests for disassembly-related parser options."""

    def test_parser_accepts_disasm_flag(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["-d", "input.sfc"])
        assert args.disasm is True

    def test_parser_accepts_count_flag(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["-n", "10", "input.sfc"])
        assert args.count == 10

    def test_parser_m8_default(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["input.sfc"])
        assert args.m_flag is True

    def test_parser_m16_flag(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["--m16", "input.sfc"])
        assert args.m_flag is False

    def test_parser_x8_default(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["input.sfc"])
        assert args.x_flag is True

    def test_parser_x16_flag(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["--x16", "input.sfc"])
        assert args.x_flag is False

    def test_parser_no_bytes_flag(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["--no-bytes", "input.sfc"])
        assert args.no_bytes is True

    def test_parser_asm_syntax_flag(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["--asm", "input.sfc"])
        assert args.asm is True


class TestApplyIpsPatchErrors:
    """Tests for IPS patch error conditions."""

    def test_truncated_ips_offset_raises(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as rom_file:
            rom_file.write(b"\x00" * 16)
            rom_path = Path(rom_file.name)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".ips") as ips_file:
            ips_file.write(b"PATCH")
            ips_file.write(b"\x00\x00")  # Truncated offset (only 2 bytes instead of 3)
            ips_path = Path(ips_file.name)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as out_file:
            output_path = Path(out_file.name)

        try:
            with pytest.raises(ValueError, match="Unexpected end"):
                apply_ips_patch(rom_path, ips_path, output_path)
        finally:
            rom_path.unlink()
            ips_path.unlink()
            if output_path.exists():
                output_path.unlink()

    def test_truncated_ips_size_raises(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as rom_file:
            rom_file.write(b"\x00" * 16)
            rom_path = Path(rom_file.name)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".ips") as ips_file:
            ips_file.write(b"PATCH")
            ips_file.write(b"\x00\x00\x05")  # Valid offset
            ips_file.write(b"\x00")  # Truncated size (only 1 byte instead of 2)
            ips_path = Path(ips_file.name)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as out_file:
            output_path = Path(out_file.name)

        try:
            with pytest.raises(ValueError, match="Unexpected end"):
                apply_ips_patch(rom_path, ips_path, output_path)
        finally:
            rom_path.unlink()
            ips_path.unlink()
            if output_path.exists():
                output_path.unlink()


class TestXddsMain:
    """Tests for the xdds_main CLI function."""

    def test_hexdump_basic(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        """Test basic hexdump output."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as rom_file:
            rom_file.write(b"\x78\xa9\x00\x8d\x00\x21\x60")  # SEI, LDA #0, STA $2100, RTS
            rom_path = Path(rom_file.name)

        try:
            monkeypatch.setattr("sys.argv", ["xdds", str(rom_path)])
            xdds_main()
            captured = capsys.readouterr()
            assert "$00:8000" in captured.out
            assert "78" in captured.out
        finally:
            rom_path.unlink()

    def test_hexdump_with_length(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        """Test hexdump with length limit."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as rom_file:
            rom_file.write(b"\x00" * 100)
            rom_path = Path(rom_file.name)

        try:
            monkeypatch.setattr("sys.argv", ["xdds", "-l", "16", str(rom_path)])
            xdds_main()
            captured = capsys.readouterr()
            # Should only have one line of output (16 bytes)
            lines = [line for line in captured.out.strip().split("\n") if line]
            assert len(lines) == 1
        finally:
            rom_path.unlink()

    def test_hexdump_with_start_offset(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test hexdump with start offset."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as rom_file:
            rom_file.write(b"\x00" * 0x8000 + b"\xab\xcd")
            rom_path = Path(rom_file.name)

        try:
            monkeypatch.setattr("sys.argv", ["xdds", "-s", "0x8000", "-l", "2", str(rom_path)])
            xdds_main()
            captured = capsys.readouterr()
            assert "$01:8000" in captured.out  # Physical 0x8000 = $01:8000 in LoROM
            assert "ab cd" in captured.out.lower()
        finally:
            rom_path.unlink()

    def test_hexdump_with_snes_address(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test hexdump with SNES logical address."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as rom_file:
            rom_file.write(b"\x00" * 0x100 + b"\xef")
            rom_path = Path(rom_file.name)

        try:
            monkeypatch.setattr("sys.argv", ["xdds", "-s", "$00:8100", "-l", "1", str(rom_path)])
            xdds_main()
            captured = capsys.readouterr()
            assert "ef" in captured.out.lower()
        finally:
            rom_path.unlink()

    def test_hexdump_hirom(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        """Test hexdump with HiROM mapping."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as rom_file:
            rom_file.write(b"\x12\x34")
            rom_path = Path(rom_file.name)

        try:
            monkeypatch.setattr("sys.argv", ["xdds", "--hirom", "-l", "2", str(rom_path)])
            xdds_main()
            captured = capsys.readouterr()
            assert "$40:0000" in captured.out
        finally:
            rom_path.unlink()

    def test_hexdump_no_ascii(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        """Test hexdump without ASCII."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as rom_file:
            rom_file.write(b"ABCD")
            rom_path = Path(rom_file.name)

        try:
            monkeypatch.setattr("sys.argv", ["xdds", "--no-ascii", "-l", "4", str(rom_path)])
            xdds_main()
            captured = capsys.readouterr()
            # Hex values should be present
            assert "41 42 43 44" in captured.out
        finally:
            rom_path.unlink()

    def test_disassemble_mode(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        """Test disassembly mode."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as rom_file:
            rom_file.write(b"\x78\xea\x60")  # SEI, NOP, RTS
            rom_path = Path(rom_file.name)

        try:
            monkeypatch.setattr("sys.argv", ["xdds", "-d", "-n", "3", str(rom_path)])
            xdds_main()
            captured = capsys.readouterr()
            assert "sei" in captured.out.lower()
            assert "nop" in captured.out.lower()
            assert "rts" in captured.out.lower()
        finally:
            rom_path.unlink()

    def test_disassemble_m16_mode(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        """Test disassembly with 16-bit accumulator."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as rom_file:
            rom_file.write(b"\xa9\x34\x12")  # LDA #$1234 (16-bit)
            rom_path = Path(rom_file.name)

        try:
            monkeypatch.setattr("sys.argv", ["xdds", "-d", "--m16", "-n", "1", str(rom_path)])
            xdds_main()
            captured = capsys.readouterr()
            assert "lda" in captured.out.lower()
        finally:
            rom_path.unlink()

    def test_disassemble_no_bytes(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        """Test disassembly without bytes shown."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as rom_file:
            rom_file.write(b"\xea")  # NOP
            rom_path = Path(rom_file.name)

        try:
            monkeypatch.setattr("sys.argv", ["xdds", "-d", "--no-bytes", "-n", "1", str(rom_path)])
            xdds_main()
            captured = capsys.readouterr()
            assert "nop" in captured.out.lower()
        finally:
            rom_path.unlink()

    def test_disassemble_asm_syntax(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        """Test disassembly with a816 syntax."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as rom_file:
            rom_file.write(b"\xea")  # NOP
            rom_path = Path(rom_file.name)

        try:
            monkeypatch.setattr("sys.argv", ["xdds", "-d", "--asm", "-n", "1", str(rom_path)])
            xdds_main()
            captured = capsys.readouterr()
            assert "nop" in captured.out.lower()
        finally:
            rom_path.unlink()

    def test_show_mappings(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test show mappings option."""
        import logging

        with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as rom_file:
            rom_file.write(b"\x00")
            rom_path = Path(rom_file.name)

        try:
            with caplog.at_level(logging.INFO):
                monkeypatch.setattr("sys.argv", ["xdds", "--show-mappings", "-l", "1", str(rom_path)])
                xdds_main()
            assert "low_rom" in caplog.text
        finally:
            rom_path.unlink()

    def test_with_ips_patch(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        """Test hexdump with IPS patch applied."""
        # Create ROM file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as rom_file:
            rom_file.write(b"\x00" * 16)
            rom_path = Path(rom_file.name)

        # Create IPS patch that writes 0xAB at offset 0
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ips") as ips_file:
            ips_file.write(b"PATCH")
            ips_file.write(b"\x00\x00\x00")  # Offset 0
            ips_file.write(b"\x00\x01")  # Size 1
            ips_file.write(b"\xab")  # Data
            ips_file.write(b"EOF")
            ips_path = Path(ips_file.name)

        try:
            monkeypatch.setattr("sys.argv", ["xdds", "--ips", str(ips_path), "-l", "1", str(rom_path)])
            xdds_main()
            captured = capsys.readouterr()
            assert "ab" in captured.out.lower()
        finally:
            rom_path.unlink()
            ips_path.unlink()

    def test_file_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test error when input file not found."""
        monkeypatch.setattr("sys.argv", ["xdds", "/nonexistent/file.sfc"])
        with pytest.raises(SystemExit) as exc_info:
            xdds_main()
        assert exc_info.value.code == -1

    def test_ips_file_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test error when IPS file not found."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as rom_file:
            rom_file.write(b"\x00")
            rom_path = Path(rom_file.name)

        try:
            monkeypatch.setattr("sys.argv", ["xdds", "--ips", "/nonexistent.ips", str(rom_path)])
            with pytest.raises(SystemExit) as exc_info:
                xdds_main()
            assert exc_info.value.code == -1
        finally:
            rom_path.unlink()

    def test_verbose_mode(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test verbose mode enables debug logging."""
        import logging

        with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as rom_file:
            rom_file.write(b"\x00")
            rom_path = Path(rom_file.name)

        try:
            with caplog.at_level(logging.DEBUG):
                monkeypatch.setattr("sys.argv", ["xdds", "--verbose", "-l", "1", str(rom_path)])
                xdds_main()
            # Just verify it doesn't crash
        finally:
            rom_path.unlink()

    def test_custom_cols(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        """Test custom column width."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sfc") as rom_file:
            rom_file.write(b"\x00" * 32)
            rom_path = Path(rom_file.name)

        try:
            monkeypatch.setattr("sys.argv", ["xdds", "-c", "8", "-l", "16", str(rom_path)])
            xdds_main()
            captured = capsys.readouterr()
            # With 8 bytes per line, 16 bytes should give us 2 lines
            lines = [line for line in captured.out.strip().split("\n") if line]
            assert len(lines) == 2
        finally:
            rom_path.unlink()
