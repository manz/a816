"""Extended tests for linker functionality."""

import unittest

import pytest

from a816.exceptions import (
    DuplicateSymbolError,
    ExpressionEvaluationError,
    RelocationError,
    UnresolvedSymbolError,
)
from a816.linker import Linker
from a816.object_file import ObjectFile, RelocationType, SymbolSection, SymbolType


class LinkerRelocationTestCase(unittest.TestCase):
    """Tests for different relocation types."""

    def test_absolute_24_relocation(self) -> None:
        """Test 24-bit absolute relocation."""
        obj = ObjectFile(
            b"\x22\x00\x00\x00",  # jsl placeholder (4 bytes: opcode + 3 address)
            [("far_func", 0x128000, SymbolType.GLOBAL, SymbolSection.CODE)],
            [(1, "far_func", RelocationType.ABSOLUTE_24)],
        )

        linker = Linker([obj])
        linked = linker.link()

        # Check that the 24-bit address was written correctly (little-endian)
        self.assertEqual(linked.code[1], 0x00)  # Low byte
        self.assertEqual(linked.code[2], 0x80)  # Middle byte
        self.assertEqual(linked.code[3], 0x12)  # High byte

    def test_absolute_24_relocation_out_of_range(self) -> None:
        """Test that 24-bit relocation rejects values over 0xFFFFFF."""
        obj = ObjectFile(
            b"\x22\x00\x00\x00",
            [("too_far", 0x1000000, SymbolType.GLOBAL, SymbolSection.CODE)],  # > 24 bits
            [(1, "too_far", RelocationType.ABSOLUTE_24)],
        )

        linker = Linker([obj])
        with pytest.raises(RelocationError) as exc_info:
            linker.link()

        error = exc_info.value
        self.assertEqual(error.symbol_name, "too_far")
        self.assertIn("24-bit", error.relocation_type)

    def test_relative_24_relocation(self) -> None:
        """Test 24-bit relative relocation (BRL-style)."""
        # Code at offset 0, target at offset 0x100
        obj = ObjectFile(
            b"\x82\x00\x00\x00" + b"\x00" * 0xFC + b"\xea",  # brl + padding + nop
            [("target", 0x100, SymbolType.GLOBAL, SymbolSection.CODE)],
            [(1, "target", RelocationType.RELATIVE_24)],
        )

        linker = Linker([obj])
        linked = linker.link()

        # Relative offset: target - (offset + 3) = 0x100 - 4 = 0xFC
        # Little-endian 24-bit
        offset = linked.code[1] | (linked.code[2] << 8) | (linked.code[3] << 16)
        self.assertEqual(offset, 0xFC)

    def test_relative_16_negative(self) -> None:
        """Test 16-bit relative relocation with negative offset (backward jump)."""
        # target at offset 0, jump instruction at offset 0x10
        code = b"\xea" + b"\x00" * 0x0F + b"\x80\x00\x00"  # nop + padding + bra placeholder
        obj = ObjectFile(
            code,
            [("target", 0, SymbolType.GLOBAL, SymbolSection.CODE)],
            [(0x11, "target", RelocationType.RELATIVE_16)],
        )

        linker = Linker([obj])
        linked = linker.link()

        # Relative offset: 0 - (0x11 + 2) = -0x13 = 0xFFED (signed 16-bit)
        import struct

        offset = struct.unpack_from("<h", bytes(linked.code), 0x11)[0]
        self.assertEqual(offset, -0x13)


class LinkerExpressionTestCase(unittest.TestCase):
    """Tests for expression relocations."""

    def test_expression_relocation_byte(self) -> None:
        """Test 1-byte expression relocation."""
        obj = ObjectFile(
            b"\xa9\x00",  # lda #placeholder
            [("BASE", 0x10, SymbolType.GLOBAL, SymbolSection.DATA)],
            [],
        )
        obj.expression_relocations = [(1, "BASE + 5", 1)]

        linker = Linker([obj])
        linked = linker.link()

        # BASE (0x10) + 5 = 0x15
        self.assertEqual(linked.code[1], 0x15)

    def test_expression_relocation_word(self) -> None:
        """Test 2-byte expression relocation."""
        obj = ObjectFile(
            b"\xad\x00\x00",  # lda absolute placeholder
            [("TABLE_BASE", 0x8000, SymbolType.GLOBAL, SymbolSection.DATA)],
            [],
        )
        obj.expression_relocations = [(1, "TABLE_BASE + 0x100", 2)]

        linker = Linker([obj])
        linked = linker.link()

        # TABLE_BASE (0x8000) + 0x100 = 0x8100
        import struct

        value = struct.unpack_from("<H", bytes(linked.code), 1)[0]
        self.assertEqual(value, 0x8100)

    def test_expression_relocation_long(self) -> None:
        """Test 3-byte expression relocation."""
        obj = ObjectFile(
            b"\xaf\x00\x00\x00",  # lda long placeholder
            [("ROM_BASE", 0x108000, SymbolType.GLOBAL, SymbolSection.DATA)],
            [],
        )
        obj.expression_relocations = [(1, "ROM_BASE + 0x200", 3)]

        linker = Linker([obj])
        linked = linker.link()

        # ROM_BASE (0x108000) + 0x200 = 0x108200
        value = linked.code[1] | (linked.code[2] << 8) | (linked.code[3] << 16)
        self.assertEqual(value, 0x108200)

    def test_expression_relocation_complex(self) -> None:
        """Test complex expression with multiple symbols."""
        obj = ObjectFile(
            b"\xad\x00\x00",
            [
                ("START", 0x100, SymbolType.GLOBAL, SymbolSection.DATA),
                ("OFFSET", 0x20, SymbolType.GLOBAL, SymbolSection.DATA),
            ],
            [],
        )
        obj.expression_relocations = [(1, "START + OFFSET * 2", 2)]

        linker = Linker([obj])
        linked = linker.link()

        # START (0x100) + OFFSET (0x20) * 2 = 0x100 + 0x40 = 0x140
        import struct

        value = struct.unpack_from("<H", bytes(linked.code), 1)[0]
        self.assertEqual(value, 0x140)

    def test_expression_relocation_out_of_range(self) -> None:
        """Test expression that results in out-of-range value for size."""
        obj = ObjectFile(
            b"\xaf\x00\x00\x00",
            [("BIG_VALUE", 0x1000000, SymbolType.GLOBAL, SymbolSection.DATA)],
            [],
        )
        obj.expression_relocations = [(1, "BIG_VALUE", 3)]

        linker = Linker([obj])
        with pytest.raises(ExpressionEvaluationError):
            linker.link()

    def test_expression_relocation_invalid_syntax(self) -> None:
        """Test expression with invalid syntax."""
        obj = ObjectFile(
            b"\xa9\x00",
            [],
            [],
        )
        obj.expression_relocations = [(1, "invalid $$$ expression", 1)]

        linker = Linker([obj])
        with pytest.raises(ExpressionEvaluationError) as exc_info:
            linker.link()

        self.assertIn("invalid $$$ expression", exc_info.value.expression)

    def test_expression_relocation_unsupported_size(self) -> None:
        """Test expression with unsupported operand size."""
        obj = ObjectFile(
            b"\x00\x00\x00\x00\x00",
            [("VAL", 0x100, SymbolType.GLOBAL, SymbolSection.DATA)],
            [],
        )
        obj.expression_relocations = [(1, "VAL", 4)]  # 4 bytes not supported

        linker = Linker([obj])
        with pytest.raises(ExpressionEvaluationError) as exc_info:
            linker.link()

        self.assertIn("unsupported", exc_info.value.reason)


class LinkerSymbolTestCase(unittest.TestCase):
    """Tests for symbol handling."""

    def test_data_section_symbols_not_relocated(self) -> None:
        """Test that DATA section symbols keep absolute values."""
        obj1 = ObjectFile(
            b"\xa9\x00",  # 2 bytes of code
            [("CODE_LABEL", 0, SymbolType.GLOBAL, SymbolSection.CODE)],
            [],
        )
        obj2 = ObjectFile(
            b"\xea\xea",  # 2 more bytes
            [("CONSTANT", 0x42, SymbolType.GLOBAL, SymbolSection.DATA)],
            [],
        )

        linker = Linker([obj1, obj2])
        linker.link()

        # CODE_LABEL should be at offset 0
        # CONSTANT should remain 0x42 (absolute value, not relocated)
        self.assertEqual(linker.symbol_map["CODE_LABEL"], 0)
        self.assertEqual(linker.symbol_map["CONSTANT"], 0x42)

    def test_code_section_symbols_relocated(self) -> None:
        """Test that CODE section symbols are relocated based on position."""
        obj1 = ObjectFile(
            b"\xea" * 10,  # 10 bytes
            [("first", 5, SymbolType.GLOBAL, SymbolSection.CODE)],
            [],
        )
        obj2 = ObjectFile(
            b"\xea" * 10,
            [("second", 5, SymbolType.GLOBAL, SymbolSection.CODE)],
            [],
        )

        linker = Linker([obj1, obj2])
        linker.link()

        # first should be at offset 5
        # second should be at offset 10 + 5 = 15
        self.assertEqual(linker.symbol_map["first"], 5)
        self.assertEqual(linker.symbol_map["second"], 15)


class ExceptionFormatTestCase(unittest.TestCase):
    """Tests for exception format methods."""

    def test_duplicate_symbol_error_format(self) -> None:
        """Test DuplicateSymbolError.format() method."""
        error = DuplicateSymbolError("my_func")
        formatted = error.format()

        self.assertIn("linker error", formatted.lower())
        self.assertIn("my_func", formatted)
        self.assertIn("defined", formatted.lower())

    def test_unresolved_symbol_error_single_format(self) -> None:
        """Test UnresolvedSymbolError.format() for single symbol."""
        error = UnresolvedSymbolError({"missing_func"})
        formatted = error.format()

        self.assertIn("linker error", formatted.lower())
        self.assertIn("missing_func", formatted)
        self.assertIn("hint", formatted.lower())

    def test_unresolved_symbol_error_multiple_format(self) -> None:
        """Test UnresolvedSymbolError.format() for multiple symbols."""
        error = UnresolvedSymbolError({"func_a", "func_b"})
        formatted = error.format()

        self.assertIn("2", formatted)  # Number of symbols
        self.assertIn("func_a", formatted)
        self.assertIn("func_b", formatted)

    def test_relocation_error_format(self) -> None:
        """Test RelocationError.format() method."""
        error = RelocationError(
            symbol_name="far_label",
            relocation_type="16-bit absolute",
            value=0x12345,
            reason="value out of range",
        )
        formatted = error.format()

        self.assertIn("linker error", formatted.lower())
        self.assertIn("far_label", formatted)
        self.assertIn("16-bit absolute", formatted)
        self.assertIn("0x12345", formatted)
        self.assertIn("value out of range", formatted)

    def test_expression_evaluation_error_format(self) -> None:
        """Test ExpressionEvaluationError.format() method."""
        error = ExpressionEvaluationError(
            expression="INVALID + SYNTAX",
            reason="name 'INVALID' is not defined",
        )
        formatted = error.format()

        self.assertIn("linker error", formatted.lower())
        self.assertIn("INVALID + SYNTAX", formatted)
        self.assertIn("not defined", formatted)
