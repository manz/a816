"""Tests for the scanner/lexer."""

from unittest import TestCase

from a816.parse.errors import ScannerException
from a816.parse.scanner import Scanner
from a816.parse.scanner_states import lex_initial
from a816.parse.tokens import TokenType


class ScannerTest(TestCase):
    """Test scanner tokenization."""

    def _scan(self, text: str) -> list[tuple[TokenType, str]]:
        """Helper to scan text and return list of (type, value) tuples."""
        scanner = Scanner(lex_initial)
        tokens = scanner.scan("test.s", text)
        return [(t.type, t.value) for t in tokens if t.type != TokenType.EOF]

    def test_comparison_operators_greater_equal(self) -> None:
        """Test that >= is tokenized as a single operator, not > followed by =."""
        tokens = self._scan("a >= b")
        self.assertEqual(tokens[1], (TokenType.OPERATOR, ">="))

    def test_comparison_operators_less_equal(self) -> None:
        """Test that <= is tokenized as a single operator, not < followed by =."""
        tokens = self._scan("a <= b")
        self.assertEqual(tokens[1], (TokenType.OPERATOR, "<="))

    def test_comparison_operators_greater(self) -> None:
        """Test that > alone is tokenized correctly."""
        tokens = self._scan("a > b")
        self.assertEqual(tokens[1], (TokenType.OPERATOR, ">"))

    def test_comparison_operators_less(self) -> None:
        """Test that < alone is tokenized correctly."""
        tokens = self._scan("a < b")
        self.assertEqual(tokens[1], (TokenType.OPERATOR, "<"))

    def test_comparison_operators_equal(self) -> None:
        """Test that == is tokenized correctly."""
        tokens = self._scan("a == b")
        self.assertEqual(tokens[1], (TokenType.OPERATOR, "=="))

    def test_comparison_operators_not_equal(self) -> None:
        """Test that != is tokenized correctly."""
        tokens = self._scan("a != b")
        self.assertEqual(tokens[1], (TokenType.OPERATOR, "!="))

    def test_shift_operators_left(self) -> None:
        """Test that << is tokenized correctly."""
        tokens = self._scan("a << b")
        self.assertEqual(tokens[1], (TokenType.OPERATOR, "<<"))

    def test_shift_operators_right(self) -> None:
        """Test that >> is tokenized correctly."""
        tokens = self._scan("a >> b")
        self.assertEqual(tokens[1], (TokenType.OPERATOR, ">>"))

    def test_number_hex(self) -> None:
        """Test hexadecimal number parsing."""
        tokens = self._scan("0x1F")
        self.assertEqual(tokens[0], (TokenType.NUMBER, "0x1F"))

    def test_number_binary(self) -> None:
        """Test binary number parsing."""
        tokens = self._scan("0b1010")
        self.assertEqual(tokens[0], (TokenType.NUMBER, "0b1010"))

    def test_number_octal(self) -> None:
        """Test octal number parsing."""
        tokens = self._scan("0o777")
        self.assertEqual(tokens[0], (TokenType.NUMBER, "0o777"))

    def test_number_decimal(self) -> None:
        """Test decimal number parsing."""
        tokens = self._scan("12345")
        self.assertEqual(tokens[0], (TokenType.NUMBER, "12345"))

    def test_single_quoted_string(self) -> None:
        """Test single-quoted string parsing."""
        tokens = self._scan("'hello world'")
        self.assertEqual(tokens[0], (TokenType.QUOTED_STRING, "'hello world'"))

    def test_double_quoted_string(self) -> None:
        """Test double-quoted string parsing."""
        tokens = self._scan('"hello world"')
        self.assertEqual(tokens[0], (TokenType.QUOTED_STRING, '"hello world"'))

    def test_escaped_single_quote(self) -> None:
        """Test escaped single quote in string."""
        tokens = self._scan("'it\\'s'")
        self.assertEqual(tokens[0], (TokenType.QUOTED_STRING, "'it\\'s'"))

    def test_escaped_double_quote(self) -> None:
        """Test escaped double quote in string."""
        tokens = self._scan('"say \\"hello\\""')
        self.assertEqual(tokens[0], (TokenType.QUOTED_STRING, '"say \\"hello\\""'))

    def test_identifier_with_underscore(self) -> None:
        """Test identifier starting with underscore."""
        tokens = self._scan("_my_label")
        self.assertEqual(tokens[0], (TokenType.IDENTIFIER, "_my_label"))

    def test_label_definition(self) -> None:
        """Test label definition (identifier followed by colon)."""
        tokens = self._scan("my_label:")
        self.assertEqual(tokens[0], (TokenType.LABEL, "my_label"))

    def test_opcode_recognition(self) -> None:
        """Test that opcodes are recognized as OPCODE tokens."""
        tokens = self._scan("lda #0x00")
        self.assertEqual(tokens[0][0], TokenType.OPCODE)
        self.assertEqual(tokens[0][1].lower(), "lda")

    def test_line_comment(self) -> None:
        """Test line comment starting with semicolon."""
        tokens = self._scan("; this is a comment\nlda #0")
        # First token should be comment
        self.assertEqual(tokens[0][0], TokenType.COMMENT)

    def test_block_comment(self) -> None:
        """Test block comment /* ... */."""
        tokens = self._scan("/* comment */ lda #0")
        self.assertEqual(tokens[0][0], TokenType.COMMENT)

    def test_docstring_single(self) -> None:
        """Test triple single-quoted docstring."""
        tokens = self._scan("'''docstring'''")
        self.assertEqual(tokens[0][0], TokenType.DOCSTRING)

    def test_docstring_double(self) -> None:
        """Test triple double-quoted docstring."""
        tokens = self._scan('"""docstring"""')
        self.assertEqual(tokens[0][0], TokenType.DOCSTRING)

    def test_scoped_identifier(self) -> None:
        """Test scoped identifier (scope.member) parsing."""
        tokens = self._scan("scope.member")
        self.assertEqual(tokens[0], (TokenType.IDENTIFIER, "scope.member"))

    def test_keyword_include(self) -> None:
        """Test .include keyword parsing."""
        tokens = self._scan(".include")
        self.assertEqual(tokens[0], (TokenType.KEYWORD, "include"))

    def test_keyword_db(self) -> None:
        """Test .db keyword parsing."""
        tokens = self._scan(".db")
        self.assertEqual(tokens[0], (TokenType.KEYWORD, "db"))

    def test_assignment_operator(self) -> None:
        """Test := assignment operator."""
        tokens = self._scan("value := 42")
        self.assertEqual(tokens[0], (TokenType.IDENTIFIER, "value"))
        self.assertEqual(tokens[1], (TokenType.ASSIGN, ":="))
        self.assertEqual(tokens[2], (TokenType.NUMBER, "42"))

    def test_star_eq_operator(self) -> None:
        """Test *= operator for code position."""
        tokens = self._scan("*= 0x8000")
        self.assertEqual(tokens[0], (TokenType.STAR_EQ, "*="))
        self.assertEqual(tokens[1], (TokenType.NUMBER, "0x8000"))

    def test_at_eq_operator(self) -> None:
        """Test @= operator for relocation."""
        tokens = self._scan("@= 0x1000")
        self.assertEqual(tokens[0], (TokenType.AT_EQ, "@="))
        self.assertEqual(tokens[1], (TokenType.NUMBER, "0x1000"))

    def test_empty_input(self) -> None:
        """Test scanning empty input returns no tokens."""
        tokens = self._scan("")
        self.assertEqual(tokens, [])

    def test_unterminated_string_error(self) -> None:
        """Test that unterminated string raises ScannerException."""
        scanner = Scanner(lex_initial)
        with self.assertRaises(ScannerException) as ctx:
            scanner.scan("test.s", "'unterminated")
        self.assertIn("Unterminated", str(ctx.exception))
