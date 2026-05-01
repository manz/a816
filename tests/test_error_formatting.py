"""Tests for error formatting utilities in a816/errors.py."""

import os
import unittest
from unittest.mock import patch

from a816.errors import (
    SourceLocation,
    format_error,
    format_error_simple,
    format_linker_error,
    format_multiple_errors,
)


class SourceLocationTestCase(unittest.TestCase):
    """Tests for the SourceLocation dataclass."""

    def test_source_location_str(self) -> None:
        """Test string representation of source location."""
        loc = SourceLocation(
            filename="test.s",
            line=5,
            column=10,
            source_line="    lda #0x42",
        )
        # Line and column are 0-indexed internally, displayed 1-indexed
        self.assertEqual(str(loc), "test.s:6:11")

    def test_source_location_with_length(self) -> None:
        """Test source location with custom length."""
        loc = SourceLocation(
            filename="test.s",
            line=0,
            column=0,
            source_line="lda",
            length=3,
        )
        self.assertEqual(loc.length, 3)


class FormatErrorTestCase(unittest.TestCase):
    """Tests for format_error function."""

    def test_format_error_basic(self) -> None:
        """Test basic error formatting without location."""
        result = format_error("undefined symbol 'foo'")
        self.assertIn("error", result)
        self.assertIn("undefined symbol 'foo'", result)

    def test_format_error_with_location(self) -> None:
        """Test error formatting with source location."""
        loc = SourceLocation(
            filename="test.s",
            line=10,
            column=4,
            source_line="    jsr unknown_func",
        )
        result = format_error("undefined symbol 'unknown_func'", location=loc)
        self.assertIn("test.s:11:5", result)
        self.assertIn("jsr unknown_func", result)
        self.assertIn("^", result)  # Caret indicator

    def test_format_error_with_hint(self) -> None:
        """Test error formatting with hint."""
        result = format_error(
            "symbol not found",
            hint="did you mean 'my_label'?",
        )
        self.assertIn("hint:", result)
        self.assertIn("did you mean 'my_label'?", result)

    def test_format_error_with_note(self) -> None:
        """Test error formatting with note."""
        result = format_error(
            "type mismatch",
            note="expected 16-bit value",
        )
        self.assertIn("note:", result)
        self.assertIn("expected 16-bit value", result)

    def test_format_error_with_location_and_hint(self) -> None:
        """Test error with both location and hint."""
        loc = SourceLocation(
            filename="code.s",
            line=5,
            column=0,
            source_line="lda my_labe",
            length=7,
        )
        result = format_error(
            "undefined symbol 'my_labe'",
            location=loc,
            hint="did you mean 'my_label'?",
        )
        self.assertIn("code.s:6:1", result)
        self.assertIn("hint:", result)

    def test_format_error_with_location_and_note(self) -> None:
        """Test error with location and note."""
        loc = SourceLocation(
            filename="code.s",
            line=0,
            column=0,
            source_line="test",
        )
        result = format_error(
            "test error",
            location=loc,
            note="additional info",
        )
        self.assertIn("note:", result)
        self.assertIn("additional info", result)

    def test_format_error_warning_type(self) -> None:
        """Test warning error type."""
        result = format_error("deprecated feature", error_type="warning")
        self.assertIn("warning", result)
        self.assertIn("deprecated feature", result)

    def test_format_error_custom_type(self) -> None:
        """Test custom error type."""
        result = format_error("info message", error_type="info")
        self.assertIn("info", result)

    def test_format_error_empty_source_line(self) -> None:
        """Test error with empty source line."""
        loc = SourceLocation(
            filename="test.s",
            line=0,
            column=0,
            source_line="",
        )
        result = format_error("empty line error", location=loc)
        self.assertIn("test.s:1:1", result)


class FormatErrorSimpleTestCase(unittest.TestCase):
    """Tests for format_error_simple function."""

    def test_format_error_simple_basic(self) -> None:
        """Test simple error without details."""
        result = format_error_simple("io error", "file not found")
        self.assertIn("io error:", result)
        self.assertIn("file not found", result)

    def test_format_error_simple_with_details(self) -> None:
        """Test simple error with details."""
        result = format_error_simple(
            "linker error",
            "relocation failed",
            details=[
                ("symbol", "my_func"),
                ("type", "ABSOLUTE_16"),
            ],
        )
        self.assertIn("linker error:", result)
        self.assertIn("relocation failed", result)
        self.assertIn("symbol:", result)
        self.assertIn("my_func", result)
        self.assertIn("type:", result)
        self.assertIn("ABSOLUTE_16", result)


class FormatLinkerErrorTestCase(unittest.TestCase):
    """Tests for format_linker_error function."""

    def test_format_linker_error_single_symbol(self) -> None:
        """Test linker error with single symbol."""
        result = format_linker_error(
            "symbol not found",
            symbol="external_func",
        )
        self.assertIn("linker error:", result)
        self.assertIn("symbol not found", result)
        self.assertIn("symbol:", result)
        self.assertIn("external_func", result)

    def test_format_linker_error_multiple_symbols(self) -> None:
        """Test linker error with multiple symbols."""
        result = format_linker_error(
            "unresolved symbols",
            symbols={"func_a", "func_b", "func_c"},
        )
        self.assertIn("linker error:", result)
        self.assertIn("symbols:", result)
        self.assertIn("func_a", result)
        self.assertIn("func_b", result)
        self.assertIn("func_c", result)

    def test_format_linker_error_with_hint(self) -> None:
        """Test linker error with hint."""
        result = format_linker_error(
            "duplicate symbol",
            symbol="my_label",
            hint="ensure symbol is only defined once",
        )
        self.assertIn("hint:", result)
        self.assertIn("ensure symbol is only defined once", result)

    def test_format_linker_error_no_symbols(self) -> None:
        """Test linker error without symbols."""
        result = format_linker_error("generic error")
        self.assertIn("linker error:", result)
        self.assertIn("generic error", result)


class FormatMultipleErrorsTestCase(unittest.TestCase):
    """Tests for format_multiple_errors function."""

    def test_format_multiple_errors_basic(self) -> None:
        """Test formatting multiple errors."""
        errors = [
            "error: first problem",
            "error: second problem",
        ]
        result = format_multiple_errors(errors)
        self.assertIn("first problem", result)
        self.assertIn("second problem", result)

    def test_format_multiple_errors_with_summary(self) -> None:
        """Test multiple errors with summary."""
        errors = [
            "error: problem 1",
            "error: problem 2",
            "error: problem 3",
        ]
        result = format_multiple_errors(errors, summary="assembly failed")
        self.assertIn("3 errors", result)
        self.assertIn("assembly failed", result)

    def test_format_multiple_errors_single_with_summary(self) -> None:
        """Test single error with summary uses singular 'error'."""
        errors = ["error: only one"]
        result = format_multiple_errors(errors, summary="check source")
        self.assertIn("1 error", result)
        self.assertNotIn("1 errors", result)


class ColorTestCase(unittest.TestCase):
    """Tests for color handling based on environment."""

    def test_no_color_environment(self) -> None:
        """Test that NO_COLOR disables colors."""
        with patch.dict(os.environ, {"NO_COLOR": "1"}, clear=False):
            # Need to reimport to pick up the new environment
            import importlib

            import a816.errors

            importlib.reload(a816.errors)
            result = a816.errors.format_error("test")
            # Should not contain ANSI escape codes
            self.assertNotIn("\033[", result)
            # Reload again to restore normal state
            del os.environ["NO_COLOR"]
            importlib.reload(a816.errors)

    def test_force_color_environment(self) -> None:
        """Test that FORCE_COLOR enables colors."""
        with patch.dict(os.environ, {"FORCE_COLOR": "1", "NO_COLOR": ""}, clear=False):
            import importlib

            import a816.errors

            importlib.reload(a816.errors)
            result = a816.errors.format_error("test")
            # Should contain ANSI escape codes (bold at minimum)
            self.assertIn("\033[", result)
            # Cleanup
            del os.environ["FORCE_COLOR"]
            importlib.reload(a816.errors)
