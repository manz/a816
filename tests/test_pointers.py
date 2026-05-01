"""Tests for script/pointers.py - ROM translation pointer utilities."""

import io
import struct
import tempfile
from pathlib import Path
from unittest import TestCase

from script import Table
from script.pointers import (
    Pointer,
    Script,
    read_pointers_from_xml,
    recode_pointer_values,
    write_pointers_addresses_as_binary,
    write_pointers_as_xml,
    write_pointers_value_as_binary,
)


class PointerTestCase(TestCase):
    """Tests for the Pointer class."""

    def test_pointer_init_with_id_only(self) -> None:
        """Test creating a pointer with just an ID."""
        ptr = Pointer(42)
        self.assertEqual(ptr.id, 42)
        self.assertIsNone(ptr.address)
        self.assertIsNone(ptr.value)

    def test_pointer_init_with_address(self) -> None:
        """Test creating a pointer with ID and address."""
        ptr = Pointer(1, address=0x8000)
        self.assertEqual(ptr.id, 1)
        self.assertEqual(ptr.address, 0x8000)

    def test_pointer_get_address(self) -> None:
        """Test getting pointer address."""
        ptr = Pointer(1, address=0x1234)
        self.assertEqual(ptr.get_address(), 0x1234)

    def test_pointer_get_value(self) -> None:
        """Test getting pointer value."""
        ptr = Pointer(1)
        ptr.value = b"Hello"
        self.assertEqual(ptr.get_value(), b"Hello")


class ScriptTestCase(TestCase):
    """Tests for the Script class."""

    def test_read_fixed_text_list(self) -> None:
        """Test reading fixed-length text entries."""
        # Create mock ROM with fixed-length entries
        rom_data = b"AAAA" + b"BBBB" + b"CCCC"
        rom_file = io.BytesIO(rom_data)

        script = Script(rom_file)
        pointers = script.read_fixed_text_list(
            pointer_file=io.BytesIO(rom_data),
            address=0,
            count=3,
            bytes_length=4,
        )

        self.assertEqual(len(pointers), 3)
        self.assertEqual(pointers[0].id, 0)
        self.assertEqual(pointers[0].value, b"AAAA")
        self.assertEqual(pointers[1].id, 1)
        self.assertEqual(pointers[1].value, b"BBBB")
        self.assertEqual(pointers[2].id, 2)
        self.assertEqual(pointers[2].value, b"CCCC")

    def test_read_pointers(self) -> None:
        """Test reading pointer table with address formula."""
        # Create pointer table: 3 16-bit little-endian pointers
        ptr_data = struct.pack("<HHH", 0x1000, 0x2000, 0x3000)
        ptr_file = io.BytesIO(ptr_data)

        rom_file = io.BytesIO(b"")
        script = Script(rom_file)

        def formula(value: bytes) -> int:
            result: int = struct.unpack("<H", value)[0]
            return result

        pointers = script.read_pointers(
            pointer_file=ptr_file,
            address=0,
            count=3,
            length=2,
            formula=formula,
        )

        self.assertEqual(len(pointers), 3)
        self.assertEqual(pointers[0].address, 0x1000)
        self.assertEqual(pointers[1].address, 0x2000)
        self.assertEqual(pointers[2].address, 0x3000)

    def test_read_pointers_content(self) -> None:
        """Test reading content at pointer addresses."""
        # ROM layout:
        # 0x0000-0x0003: "AAA\x00"
        # 0x0004-0x0007: "BBB\x00"
        # 0x0008-0x000B: "CCC\x00"
        rom_data = b"\x00" * 0x100 + b"AAA\x00" + b"BBB\x00" + b"CCC\x00"
        rom_file = io.BytesIO(rom_data)

        script = Script(rom_file)

        # Create pointers at addresses 0x100, 0x104, 0x108
        pointers = [
            Pointer(0, address=0x100),
            Pointer(1, address=0x104),
            Pointer(2, address=0x108),
        ]

        result = script.read_pointers_content(pointers, end_of_script_address=0x10C)

        # Should be sorted by address and have content filled
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0].value, b"AAA\x00")
        self.assertEqual(result[1].value, b"BBB\x00")
        self.assertEqual(result[2].value, b"CCC\x00")

    def test_append_pointers(self) -> None:
        """Test appending two pointer tables."""
        rom_file = io.BytesIO(b"")
        script = Script(rom_file)

        table1 = [Pointer(0), Pointer(1), Pointer(2)]
        table2 = [Pointer(0), Pointer(1)]

        result = script.append_pointers(table1, table2)

        self.assertEqual(len(result), 5)
        # IDs should be adjusted: table2 IDs += last ID from table1
        ids = [p.id for p in result]
        self.assertEqual(ids[:3], [0, 1, 2])  # Original table1 IDs
        self.assertEqual(ids[3], 2)  # 0 + 2
        self.assertEqual(ids[4], 3)  # 1 + 2


class WritePointersTestCase(TestCase):
    """Tests for pointer writing functions."""

    def test_write_pointers_value_as_binary(self) -> None:
        """Test writing pointer values to binary file."""
        pointers = [
            Pointer(2),
            Pointer(0),
            Pointer(1),
        ]
        pointers[0].value = b"CCC"
        pointers[1].value = b"AAA"
        pointers[2].value = b"BBB"

        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "output.bin"
            write_pointers_value_as_binary(pointers, str(output_file))

            content = output_file.read_bytes()
            # Should be sorted by ID: AAA, BBB, CCC
            self.assertEqual(content, b"AAABBBCCC")

    def test_write_pointers_addresses_as_binary(self) -> None:
        """Test writing pointer addresses to binary file."""
        pointers = [
            Pointer(0),
            Pointer(1),
            Pointer(2),
        ]
        pointers[0].value = b"AAAA"  # 4 bytes
        pointers[1].value = b"BB"  # 2 bytes
        pointers[2].value = b"CCCCCC"  # 6 bytes

        def formula(position: int) -> bytes:
            return struct.pack("<H", position)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "ptrs.bin"
            write_pointers_addresses_as_binary(pointers, formula, str(output_file))

            content = output_file.read_bytes()
            # Positions: 0, 4, 6
            expected = struct.pack("<HHH", 0, 4, 6)
            self.assertEqual(content, expected)


class XMLPointersTestCase(TestCase):
    """Tests for XML pointer I/O."""

    def _create_simple_table(self) -> Table:
        """Create a simple character table for testing."""
        table = Table()
        # Map ASCII A-Z to bytes 0x41-0x5A
        for i, char in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
            byte_val = 0x41 + i
            table.add_lookup(char, [byte_val])
            table.add_inverted_lookup([byte_val], char)
        # Add space
        table.add_lookup(" ", [0x20])
        table.add_inverted_lookup([0x20], " ")
        # Update max lengths
        table.max_bytes_length = 1
        table.max_text_length = 1
        return table

    def test_write_and_read_pointers_xml(self) -> None:
        """Test XML round-trip for pointers."""
        table = self._create_simple_table()

        pointers = [
            Pointer(0),
            Pointer(1),
            Pointer(2),
        ]
        pointers[0].value = b"ABC"
        pointers[1].value = b"DEF"
        pointers[2].value = b"GHI"

        with tempfile.TemporaryDirectory() as tmpdir:
            xml_file = Path(tmpdir) / "script.xml"

            # Write to XML
            write_pointers_as_xml(pointers, table, str(xml_file))

            # Verify XML structure
            content = xml_file.read_text()
            self.assertIn('<?xml version="1.0"', content)
            self.assertIn("sn:script", content)
            self.assertIn('id="0"', content)
            self.assertIn("ABC", content)

            # Read back from XML
            loaded = read_pointers_from_xml(str(xml_file), table)

            self.assertEqual(len(loaded), 3)
            # Note: XML uses 1-based IDs, so loaded IDs are id-1
            values = sorted([p.get_value() for p in loaded])
            self.assertIn(b"ABC", values)
            self.assertIn(b"DEF", values)
            self.assertIn(b"GHI", values)

    def test_read_pointers_from_xml_with_formatter(self) -> None:
        """Test reading pointers with a text formatter."""
        table = self._create_simple_table()

        # Create XML file
        xml_content = """<?xml version="1.0" encoding="utf-8"?>
<sn:script xmlns:sn="http://snes.ninja/ScriptNS">
<sn:pointer id="1">abc</sn:pointer>
</sn:script>
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            xml_file = Path(tmpdir) / "script.xml"
            xml_file.write_text(xml_content)

            # Formatter that uppercases text
            def uppercase_formatter(text: str) -> str:
                return text.upper()

            loaded = read_pointers_from_xml(str(xml_file), table, formatter=uppercase_formatter)

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].value, b"ABC")


class RecodePointersTestCase(TestCase):
    """Tests for pointer value recoding."""

    def test_recode_pointer_values(self) -> None:
        """Test recoding pointer values between tables."""
        # Source table: A=0x41, B=0x42
        from_table = Table()
        from_table.add_lookup("A", [0x41])
        from_table.add_inverted_lookup([0x41], "A")
        from_table.add_lookup("B", [0x42])
        from_table.add_inverted_lookup([0x42], "B")
        from_table.max_bytes_length = 1
        from_table.max_text_length = 1

        # Target table: A=0x01, B=0x02
        to_table = Table()
        to_table.add_lookup("A", [0x01])
        to_table.add_inverted_lookup([0x01], "A")
        to_table.add_lookup("B", [0x02])
        to_table.add_inverted_lookup([0x02], "B")
        to_table.max_bytes_length = 1
        to_table.max_text_length = 1

        pointers = [Pointer(0), Pointer(1)]
        pointers[0].value = b"\x41\x42"  # "AB" in source encoding
        pointers[1].value = b"\x42\x41"  # "BA" in source encoding

        recode_pointer_values(pointers, from_table, to_table)

        self.assertEqual(pointers[0].value, b"\x01\x02")  # "AB" in target encoding
        self.assertEqual(pointers[1].value, b"\x02\x01")  # "BA" in target encoding
