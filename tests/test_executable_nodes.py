"""Tests for executable nodes in a816/parse/nodes.py."""

import tempfile
import unittest
from pathlib import Path

from a816.cpu.mapping import Address
from a816.parse.nodes import (
    BinaryNode,
    ByteNode,
    DebugNode,
    LabelNode,
    LongNode,
    NodeError,
    RegisterSizeNode,
    ValueNode,
    WordNode,
)
from a816.parse.tokens import File, Position, Token, TokenType
from a816.symbols import Resolver


def get_address(resolver: Resolver, logical: int) -> Address:
    """Helper to get an Address from the resolver's bus."""
    return resolver.get_bus().get_address(logical)


class ValueNodeTestCase(unittest.TestCase):
    """Tests for ValueNode class."""

    def test_get_value_hex(self) -> None:
        """Test getting value from hex string."""
        node = ValueNode("FF")
        self.assertEqual(node.get_value(), 0xFF)

    def test_get_value_multi_digit(self) -> None:
        """Test getting value from multi-digit hex."""
        node = ValueNode("1234")
        self.assertEqual(node.get_value(), 0x1234)

    def test_get_value_string_len(self) -> None:
        """Test getting string length of value."""
        node = ValueNode("ABCD")
        self.assertEqual(node.get_value_string_len(), 4)

    def test_str_representation(self) -> None:
        """Test string representation."""
        node = ValueNode("42")
        self.assertEqual(str(node), "ValueNode(42)")


class LabelNodeTestCase(unittest.TestCase):
    """Tests for LabelNode class."""

    def test_emit_returns_empty(self) -> None:
        """Test that emit returns empty bytes."""
        resolver = Resolver()
        node = LabelNode("my_label", resolver)
        result = node.emit(get_address(resolver, 0x8000))
        self.assertEqual(result, b"")

    def test_pc_after_registers_label(self) -> None:
        """Test that pc_after registers the label in scope."""
        resolver = Resolver()
        resolver.set_position(0x8000)
        node = LabelNode("test_label", resolver)
        current_pc = get_address(resolver, 0x8100)
        result = node.pc_after(current_pc)

        # PC should not change
        self.assertEqual(result.logical_value, current_pc.logical_value)
        # Label should be registered
        self.assertEqual(resolver.current_scope["test_label"], 0x8100)

    def test_str_representation(self) -> None:
        """Test string representation."""
        resolver = Resolver()
        node = LabelNode("some_label", resolver)
        self.assertEqual(str(node), "LabelNode(some_label)")
        self.assertEqual(repr(node), "LabelNode(some_label)")


class RegisterSizeNodeTestCase(unittest.TestCase):
    """Tests for RegisterSizeNode class."""

    def test_emit_sets_a_size_8(self) -> None:
        """Test setting accumulator to 8-bit."""
        resolver = Resolver()
        resolver.a_size = 16  # Start with 16-bit
        node = RegisterSizeNode("a", 8, resolver)
        node.emit(get_address(resolver, 0x8000))
        self.assertEqual(resolver.a_size, 8)

    def test_emit_sets_a_size_16(self) -> None:
        """Test setting accumulator to 16-bit."""
        resolver = Resolver()
        resolver.a_size = 8  # Start with 8-bit
        node = RegisterSizeNode("a", 16, resolver)
        node.emit(get_address(resolver, 0x8000))
        self.assertEqual(resolver.a_size, 16)

    def test_emit_sets_i_size_8(self) -> None:
        """Test setting index register to 8-bit."""
        resolver = Resolver()
        resolver.i_size = 16
        node = RegisterSizeNode("i", 8, resolver)
        node.emit(get_address(resolver, 0x8000))
        self.assertEqual(resolver.i_size, 8)

    def test_emit_sets_i_size_16(self) -> None:
        """Test setting index register to 16-bit."""
        resolver = Resolver()
        resolver.i_size = 8
        node = RegisterSizeNode("i", 16, resolver)
        node.emit(get_address(resolver, 0x8000))
        self.assertEqual(resolver.i_size, 16)

    def test_emit_returns_empty(self) -> None:
        """Test that emit returns empty bytes."""
        resolver = Resolver()
        node = RegisterSizeNode("a", 8, resolver)
        result = node.emit(get_address(resolver, 0x8000))
        self.assertEqual(result, b"")

    def test_pc_after_unchanged(self) -> None:
        """Test that PC is unchanged after register size directive."""
        resolver = Resolver()
        node = RegisterSizeNode("a", 8, resolver)
        current_pc = get_address(resolver, 0x8000)
        result = node.pc_after(current_pc)
        self.assertEqual(result, current_pc)

    def test_str_representation(self) -> None:
        """Test string representation."""
        resolver = Resolver()
        node = RegisterSizeNode("a", 16, resolver)
        self.assertEqual(str(node), "RegisterSizeNode(a16)")


class ByteNodeTestCase(unittest.TestCase):
    """Tests for ByteNode class."""

    def test_emit_single_byte(self) -> None:
        """Test emitting a single byte."""
        resolver = Resolver()
        value_node = ValueNode("42")
        node = ByteNode(value_node)
        result = node.emit(get_address(resolver, 0x8000))
        self.assertEqual(result, b"\x42")

    def test_emit_truncates_to_byte(self) -> None:
        """Test that value is truncated to byte."""
        resolver = Resolver()
        value_node = ValueNode("1234")
        node = ByteNode(value_node)
        result = node.emit(get_address(resolver, 0x8000))
        self.assertEqual(result, b"\x34")  # Low byte only

    def test_pc_after_increments_by_one(self) -> None:
        """Test that PC increments by 1."""
        resolver = Resolver()
        value_node = ValueNode("00")
        node = ByteNode(value_node)
        current_pc = get_address(resolver, 0x8000)
        result = node.pc_after(current_pc)
        self.assertEqual(result.logical_value, 0x8001)


class WordNodeTestCase(unittest.TestCase):
    """Tests for WordNode class."""

    def test_emit_word_little_endian(self) -> None:
        """Test emitting a word in little-endian format."""
        resolver = Resolver()
        value_node = ValueNode("1234")
        node = WordNode(value_node)
        result = node.emit(get_address(resolver, 0x8000))
        self.assertEqual(result, b"\x34\x12")  # Little-endian

    def test_emit_truncates_to_word(self) -> None:
        """Test that value is truncated to word."""
        resolver = Resolver()
        value_node = ValueNode("123456")
        node = WordNode(value_node)
        result = node.emit(get_address(resolver, 0x8000))
        self.assertEqual(result, b"\x56\x34")  # Low word only

    def test_pc_after_increments_by_two(self) -> None:
        """Test that PC increments by 2."""
        resolver = Resolver()
        value_node = ValueNode("0000")
        node = WordNode(value_node)
        current_pc = get_address(resolver, 0x8000)
        result = node.pc_after(current_pc)
        self.assertEqual(result.logical_value, 0x8002)


class LongNodeTestCase(unittest.TestCase):
    """Tests for LongNode class."""

    def test_emit_long_value(self) -> None:
        """Test emitting a 24-bit long value."""
        resolver = Resolver()
        value_node = ValueNode("123456")
        node = LongNode(value_node)
        result = node.emit(get_address(resolver, 0x8000))
        # Little-endian: low word first, then high byte
        self.assertEqual(result, b"\x56\x34\x12")

    def test_pc_after_increments_by_three(self) -> None:
        """Test that PC increments by 3."""
        resolver = Resolver()
        value_node = ValueNode("000000")
        node = LongNode(value_node)
        current_pc = get_address(resolver, 0x8000)
        result = node.pc_after(current_pc)
        self.assertEqual(result.logical_value, 0x8003)


class BinaryNodeTestCase(unittest.TestCase):
    """Tests for BinaryNode class."""

    def test_emit_binary_content(self) -> None:
        """Test emitting binary file content."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(b"\x01\x02\x03\x04")
            temp_path = Path(f.name)

        try:
            resolver = Resolver()
            node = BinaryNode(str(temp_path), resolver)
            result = node.emit(get_address(resolver, 0x8000))
            self.assertEqual(result, b"\x01\x02\x03\x04")
        finally:
            temp_path.unlink()

    def test_pc_after_adds_file_size(self) -> None:
        """Test that PC advances by file size."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(b"\x00" * 100)
            temp_path = Path(f.name)

        try:
            resolver = Resolver()
            resolver.set_position(0x8000)
            node = BinaryNode(str(temp_path), resolver)
            current_pc = get_address(resolver, 0x8000)
            result = node.pc_after(current_pc)
            self.assertEqual(result.logical_value, 0x8064)  # 0x8000 + 100
        finally:
            temp_path.unlink()

    def test_pc_after_creates_size_symbol(self) -> None:
        """Test that pc_after creates __size symbol."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(b"\x00" * 50)
            temp_path = Path(f.name)

        try:
            resolver = Resolver()
            resolver.set_position(0x8000)
            node = BinaryNode(str(temp_path), resolver)
            current_pc = get_address(resolver, 0x8000)
            node.pc_after(current_pc)
            # Check that size symbol was created
            size_symbol = node.symbol_base + "__size"
            self.assertEqual(resolver.current_scope[size_symbol], 50)
        finally:
            temp_path.unlink()


class DebugNodeTestCase(unittest.TestCase):
    """Tests for DebugNode class."""

    def test_emit_prints_message(self, capsys: None = None) -> None:
        """Test that emit prints the debug message."""
        resolver = Resolver()
        node = DebugNode("Test message", resolver)
        result = node.emit(get_address(resolver, 0x8000))
        self.assertEqual(result, b"")

    def test_pc_after_unchanged(self) -> None:
        """Test that PC is unchanged."""
        resolver = Resolver()
        node = DebugNode("Test", resolver)
        current_pc = get_address(resolver, 0x8000)
        result = node.pc_after(current_pc)
        self.assertEqual(result, current_pc)


class NodeErrorTestCase(unittest.TestCase):
    """Tests for NodeError class."""

    def test_error_with_file_info(self) -> None:
        """Test error formatting with file info."""
        file = File("test.s")
        file.append("lda #0x42")
        file.append("jsr unknown")
        position = Position(1, 4, file)  # Line 1, column 4
        token = Token(TokenType.IDENTIFIER, "unknown", position)

        error = NodeError("Symbol 'unknown' not defined", token)
        formatted = str(error)

        self.assertIn("unknown", formatted)
        self.assertIn("not defined", formatted)

    def test_error_message_accessible(self) -> None:
        """Test that error message is accessible."""
        token = Token(TokenType.IDENTIFIER, "test")
        error = NodeError("Test error message", token)
        self.assertEqual(error.message, "Test error message")
