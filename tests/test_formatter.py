"""
Unit tests for A816Formatter functionality
"""

from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest import TestCase

from a816.formatter import A816Formatter, FormattingOptions


class TestFormattingOptions(TestCase):
    """Test FormattingOptions configuration"""

    def test_default_options(self) -> None:
        """Test default formatting options"""
        options = FormattingOptions()

        self.assertEqual(options.indent_size, 4)
        self.assertEqual(options.opcode_indent, 4)
        self.assertEqual(options.operand_alignment, 16)
        self.assertEqual(options.comment_alignment, 40)
        self.assertTrue(options.preserve_empty_lines)
        self.assertEqual(options.max_empty_lines, 2)
        self.assertTrue(options.align_labels)
        self.assertTrue(options.space_after_comma)

    def test_custom_options(self) -> None:
        """Test custom formatting options"""
        options = FormattingOptions(indent_size=2, operand_alignment=12, space_after_comma=False, max_empty_lines=1)

        self.assertEqual(options.indent_size, 2)
        self.assertEqual(options.operand_alignment, 12)
        self.assertFalse(options.space_after_comma)
        self.assertEqual(options.max_empty_lines, 1)


class TestA816Formatter(TestCase):
    """Test A816Formatter functionality"""

    def setUp(self) -> None:
        """Set up test fixtures"""
        self.formatter = A816Formatter()
        self.custom_formatter = A816Formatter(FormattingOptions(indent_size=2, operand_alignment=12))

    def test_formatter_creation(self) -> None:
        """Test formatter creation with default options"""
        formatter = A816Formatter()
        self.assertIsNotNone(formatter.options)
        self.assertEqual(formatter.options.indent_size, 4)

    def test_formatter_with_custom_options(self) -> None:
        """Test formatter creation with custom options"""
        options = FormattingOptions(indent_size=8)
        formatter = A816Formatter(options)

        self.assertEqual(formatter.options.indent_size, 8)

    def test_format_simple_program(self) -> None:
        """Test formatting a simple assembly program"""
        input_code = """
; Test program
main:
lda #42
sta 0x2000
  rts
"""

        formatted = self.formatter.format_text(input_code)
        lines = formatted.strip().split("\n")

        # Should preserve comment
        self.assertTrue(any("; Test program" in line for line in lines))

        # Should have proper label formatting
        self.assertTrue(any("main:" == line for line in lines))

        # Should have proper instruction indentation
        instruction_lines = [
            line for line in lines if line.strip() and not line.strip().startswith(";") and ":" not in line
        ]
        for line in instruction_lines:
            if line.strip():  # Skip empty lines
                self.assertTrue(line.startswith("    "), f"Line not properly indented: {repr(line)}")

    def test_format_with_comments(self) -> None:
        """Test formatting preserves comments"""
        input_code = """; Header comment
main:
    lda #42    ; Inline comment
    ; Standalone comment
    rts
; Footer comment"""

        formatted = self.formatter.format_text(input_code)

        # Should preserve all comments
        self.assertIn("; Header comment", formatted)
        self.assertIn("; Footer comment", formatted)

    def test_format_opcodes_lowercase(self) -> None:
        """Test that opcodes are formatted as lowercase"""
        input_code = """
main:
lda #42
sta 0x2000
jsr subroutine
rts
"""

        formatted = self.formatter.format_text(input_code)

        # Should convert opcodes to lowercase
        self.assertIn("lda", formatted)
        self.assertIn("sta", formatted)
        self.assertIn("jsr", formatted)
        self.assertIn("rts", formatted)

    def test_format_opcodes_always_lowercase(self) -> None:
        """Test that opcodes are always lowercase regardless of input"""
        input_code = """
main:
LDA #42
STA 0x2000
RTS
"""

        # Should convert opcodes to lowercase
        formatted = self.custom_formatter.format_text(input_code)

        # Should convert all opcodes to lowercase
        self.assertIn("lda", formatted)
        self.assertIn("sta", formatted)
        self.assertIn("rts", formatted)

        # Should not contain uppercase opcodes
        self.assertNotIn("LDA", formatted)
        self.assertNotIn("STA", formatted)
        self.assertNotIn("RTS", formatted)

    def test_format_with_size_specifiers(self) -> None:
        """Test formatting instructions with size specifiers"""
        input_code = """
main:
lda.w #0x1234
sta.b 0x00
jsr.l 0x010000
"""

        formatted = self.formatter.format_text(input_code)

        # Should preserve size specifiers in lowercase
        self.assertIn(".w", formatted)
        self.assertIn(".b", formatted)
        self.assertIn(".l", formatted)

    def test_format_alignment(self) -> None:
        """Test operand alignment"""
        input_code = """
main:
lda #42
sta 0x2000
jsr subroutine
"""

        formatted = self.formatter.format_text(input_code)
        lines = formatted.split("\n")

        # Find instruction lines
        instruction_lines = []
        for line in lines:
            if line.strip() and not line.strip().startswith(";") and ":" not in line:
                instruction_lines.append(line)

        # Check that instructions have consistent spacing
        if len(instruction_lines) > 1:
            # All instruction lines should start with same indentation
            for line in instruction_lines:
                self.assertTrue(line.startswith("    "))

    def test_format_labels(self) -> None:
        """Test label formatting"""
        input_code = """
  main_loop:
     subroutine:
end:
"""

        formatted = self.formatter.format_text(input_code)
        lines = formatted.strip().split("\n")

        # Labels should be left-aligned and end with colon
        label_lines = [line for line in lines if ":" in line and not line.strip().startswith(";")]

        for line in label_lines:
            self.assertTrue(line.endswith(":"))
            # Labels should be at start of line (after any formatting)
            self.assertFalse(line.startswith("    "))  # Should not be indented like instructions

    def test_format_empty_lines(self) -> None:
        """Test empty line handling"""
        input_code = """
; Comment 1


main:



    lda #42


    rts


; End
"""

        formatted = self.formatter.format_text(input_code)

        # Should limit consecutive empty lines
        lines = formatted.split("\n")
        empty_count = 0
        max_consecutive_empty = 0

        for line in lines:
            if not line.strip():
                empty_count += 1
                max_consecutive_empty = max(max_consecutive_empty, empty_count)
            else:
                empty_count = 0

        # Should not have more than max_empty_lines consecutive empty lines
        self.assertLessEqual(max_consecutive_empty, self.formatter.options.max_empty_lines)

    def test_format_fallback_on_syntax_error(self) -> None:
        """Test that formatter falls back gracefully on syntax errors"""
        # Invalid syntax that should trigger fallback formatting
        input_code = """
; Valid comment
invalid_syntax_here ??? ###
main:
    lda #42
    invalid_instruction_xyz
    rts
"""

        formatted = self.formatter.format_text(input_code)

        # Should still produce some formatted output
        self.assertIsInstance(formatted, str)
        self.assertGreater(len(formatted), 0)

        # Should preserve comments
        self.assertIn("; Valid comment", formatted)

    def test_format_file(self) -> None:
        """Test formatting from file"""
        input_code = """
; Test file
main:
lda #42
rts
"""

        # Create temporary file
        with NamedTemporaryFile(mode="w", suffix=".s", delete=False) as f:
            f.write(input_code)
            temp_path = f.name

        try:
            # Format from file
            formatted = self.formatter.format_file(temp_path)

            self.assertIsInstance(formatted, str)
            self.assertIn("; Test file", formatted)
            self.assertIn("main:", formatted)

        finally:
            # Clean up
            Path(temp_path).unlink()

    def test_format_with_special_directives(self) -> None:
        """Test formatting assembly directives"""
        input_code = """
.text "Hello World"
.ascii "Test"
.db 0x01, 0x02, 0x03
.dw 0x1234, 0x5678
scope test
include "file.s"
"""

        formatted = self.formatter.format_text(input_code)

        # Should format directives with proper indentation
        self.assertIn(".text", formatted.lower())
        self.assertIn(".ascii", formatted.lower())
        self.assertIn(".db", formatted.lower())

    def test_format_preserves_structure(self) -> None:
        """Test that formatting preserves program structure"""
        input_code = """
; Program header
; Author: Test

main:
    ; Initialize
    lda #0
    sta 0x2000

    ; Main loop
loop:
    inc 0x2000
    lda 0x2000
    cmp #100
    bne loop

    ; Exit
    rts

; Subroutines
subroutine:
    php
    pha
    ; Do work
    pla
    plp
    rts
"""

        formatted = self.formatter.format_text(input_code)

        # Should preserve all labels
        self.assertIn("main:", formatted)
        self.assertIn("loop:", formatted)
        self.assertIn("subroutine:", formatted)

        # Should preserve comments
        self.assertIn("; Program header", formatted)
        self.assertIn("; Initialize", formatted)
        self.assertIn("; Main loop", formatted)
        self.assertIn("; Subroutines", formatted)

    def test_format_consistency(self) -> None:
        """Test that formatting is consistent across multiple runs"""
        input_code = """
; Test consistency
main:
lda #42
sta 0x2000
rts
"""

        formatted1 = self.formatter.format_text(input_code)
        formatted2 = self.formatter.format_text(formatted1)  # Format already formatted code

        # Should be identical
        self.assertEqual(formatted1, formatted2)

    def test_format_custom_indentation(self) -> None:
        """Test custom indentation settings"""
        options = FormattingOptions(indent_size=2, operand_alignment=10)
        formatter = A816Formatter(options)

        input_code = """
main:
lda #42
sta 0x2000
"""

        formatted = formatter.format_text(input_code)
        lines = formatted.split("\n")

        # Find instruction lines
        instruction_lines = [
            line for line in lines if line.strip() and not line.strip().startswith(";") and ":" not in line
        ]

        # Should use 2-space indentation
        for line in instruction_lines:
            if line.strip():
                self.assertTrue(
                    line.startswith("  ") and not line.startswith("   "), f"Expected 2-space indent, got: {repr(line)}"
                )

    def test_format_handles_mixed_content(self) -> None:
        """Test formatting mixed assembly content"""
        input_code = """
; Header
.extern external_func

main:
    *@ = 0x8000
    lda.w #data_table
    jsr.w external_func
    {{ some_expression }}

data_table:
    .db 0x01, 0x02, 0x03
.text "Hello"

.macro test_macro(param) {
    lda param
"""

        formatted = self.formatter.format_text(input_code)
        import logging

        logging.error(str)
        # Should handle various constructs without crashing
        self.assertIsInstance(formatted, str)
        self.assertGreater(len(formatted), 0)

    def test_idents(self) -> None:
        pass
