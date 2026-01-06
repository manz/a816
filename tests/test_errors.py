import unittest

import pytest

from a816.exceptions import (
    DuplicateSymbolError,
    UnresolvedSymbolError,
)
from a816.linker import Linker
from a816.object_file import ObjectFile, RelocationType, SymbolSection, SymbolType
from a816.parse.nodes import NodeError
from a816.program import Program
from tests import StubWriter


class ErrorsTest(unittest.TestCase):
    def test_addressing_mode_error(self) -> None:
        program = Program()
        emitter = StubWriter()

        def should_raise_node_error() -> None:
            program.assemble_string_with_emitter("nop #0x00", "test.s", emitter)

        self.assertRaises(NodeError, should_raise_node_error)

    def test_opcode_size_error(self) -> None:
        program = Program()
        emitter = StubWriter()

        def should_raise_node_error() -> None:
            program.assemble_string_with_emitter("lda.l 0x000000, y\n", "test.s", emitter)

        self.assertRaises(NodeError, should_raise_node_error)

    def test_symbol_not_found(self) -> None:
        emitter = StubWriter()
        program = Program()
        try:
            program.assemble_string_with_emitter("jsr.l unknown_symbol\n", "test_undefined_symbol.s", emitter)
        except NodeError as e:
            v = str(e)
            # Error should contain the error message, filename, and source line
            self.assertIn("unknown_symbol", v, "error should mention the undefined symbol")
            self.assertIn("not defined", v, "error should explain the symbol is not defined")
            self.assertIn("test_undefined_symbol.s", v, "error should contain filename")
            self.assertIn("jsr.l unknown_symbol", v, "error should show the source line")

    def test_symbol_not_found_db(self) -> None:
        emitter = StubWriter()
        program = Program()
        try:
            program.assemble_string_with_emitter(".db unknown_symbol\n", "test_undefined_symbol_db.s", emitter)
        except NodeError as e:
            v = str(e)
            # Error should contain the error message, filename, and source line
            self.assertIn("unknown_symbol", v, "error should mention the undefined symbol")
            self.assertIn("not defined", v, "error should explain the symbol is not defined")
            self.assertIn("test_undefined_symbol_db.s", v, "error should contain filename")
            self.assertIn(".db unknown_symbol", v, "error should show the source line")


class LinkerErrorsTest(unittest.TestCase):
    """Tests for improved linker error messages."""

    def test_duplicate_global_symbol_error(self) -> None:
        """Test that duplicate global symbol errors have clear messages."""
        obj1 = ObjectFile(
            b"\xa9\x01\x60",  # lda #1, rts
            [("my_function", 0, SymbolType.GLOBAL, SymbolSection.CODE)],
            [],
        )
        obj2 = ObjectFile(
            b"\xa9\x02\x60",  # lda #2, rts
            [("my_function", 0, SymbolType.GLOBAL, SymbolSection.CODE)],
            [],
        )

        linker = Linker([obj1, obj2])
        with pytest.raises(DuplicateSymbolError) as exc_info:
            linker.link()

        error = exc_info.value
        assert error.symbol_name == "my_function"
        assert "my_function" in str(error)
        assert "duplicate" in str(error).lower()

    def test_unresolved_external_symbol_error(self) -> None:
        """Test that unresolved external symbol errors are specific."""
        obj = ObjectFile(
            b"\x20\x00\x00",  # jsr placeholder
            [("missing_func", 0, SymbolType.EXTERNAL, SymbolSection.CODE)],
            [(1, "missing_func", RelocationType.ABSOLUTE_16)],
        )

        linker = Linker([obj])
        with pytest.raises(UnresolvedSymbolError) as exc_info:
            linker.link()

        error = exc_info.value
        assert "missing_func" in error.symbols
        assert "missing_func" in str(error)

    def test_unresolved_multiple_symbols_error(self) -> None:
        """Test that multiple unresolved symbols are all reported."""
        obj = ObjectFile(
            b"\x20\x00\x00\x20\x00\x00",  # jsr x2
            [
                ("missing1", 0, SymbolType.EXTERNAL, SymbolSection.CODE),
                ("missing2", 0, SymbolType.EXTERNAL, SymbolSection.CODE),
            ],
            [
                (1, "missing1", RelocationType.ABSOLUTE_16),
                (4, "missing2", RelocationType.ABSOLUTE_16),
            ],
        )

        linker = Linker([obj])
        with pytest.raises(UnresolvedSymbolError) as exc_info:
            linker.link()

        error = exc_info.value
        assert "missing1" in error.symbols
        assert "missing2" in error.symbols
        # Both symbols should appear in the error message
        error_str = str(error)
        assert "missing1" in error_str
        assert "missing2" in error_str

    def test_relocation_out_of_range_error_message(self) -> None:
        """Test that relocation range errors include the symbol name."""
        from a816.exceptions import RelocationError

        # Create object with a symbol value that exceeds 16-bit range
        obj = ObjectFile(
            b"\x20\x00\x00",  # jsr placeholder
            [("far_symbol", 0x12345, SymbolType.GLOBAL, SymbolSection.CODE)],
            [(1, "far_symbol", RelocationType.ABSOLUTE_16)],
        )

        linker = Linker([obj])
        with pytest.raises(RelocationError) as exc_info:
            linker.link()

        error = exc_info.value
        assert error.symbol_name == "far_symbol"
        assert "far_symbol" in str(error)
        assert "16-bit" in str(error) or "range" in str(error).lower()

    def test_linker_errors_are_a816_errors(self) -> None:
        """Test that all linker errors inherit from LinkerError."""
        from a816.exceptions import A816Error

        obj = ObjectFile(
            b"\x20\x00\x00",
            [("missing", 0, SymbolType.EXTERNAL, SymbolSection.CODE)],
            [(1, "missing", RelocationType.ABSOLUTE_16)],
        )

        linker = Linker([obj])
        with pytest.raises(A816Error):
            linker.link()
