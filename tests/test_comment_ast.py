"""
Unit tests for CommentAstNode and comment parsing functionality
"""

from unittest import TestCase

from a816.parse.ast.nodes import CommentAstNode
from a816.parse.mzparser import MZParser
from a816.parse.tokens import File, Position, Token, TokenType


class TestCommentAstNode(TestCase):
    """Test CommentAstNode functionality"""

    def setUp(self) -> None:
        """Set up test fixtures"""
        self.test_file = File("test.s")
        self.test_position = Position(0, 0, self.test_file)

    def test_comment_node_creation(self) -> None:
        """Test basic CommentAstNode creation"""
        comment_text = "; This is a test comment"
        token = Token(TokenType.COMMENT, comment_text, self.test_position)

        comment_node = CommentAstNode(comment_text, token)

        self.assertEqual(comment_node.kind, "comment")
        self.assertEqual(comment_node.comment, comment_text)
        self.assertEqual(comment_node.file_info, token)

    def test_comment_node_representation(self) -> None:
        """Test CommentAstNode to_representation method"""
        comment_text = "; Test comment"
        token = Token(TokenType.COMMENT, comment_text, self.test_position)

        comment_node = CommentAstNode(comment_text, token)
        representation = comment_node.to_representation()

        self.assertEqual(representation, ("comment", comment_text))

    def test_comment_node_canonical(self) -> None:
        """Test CommentAstNode to_canonical method"""
        comment_text = "; Test comment"
        token = Token(TokenType.COMMENT, comment_text, self.test_position)

        comment_node = CommentAstNode(comment_text, token)
        canonical = comment_node.to_canonical()

        self.assertEqual(canonical, comment_text)

    def test_comment_without_semicolon(self) -> None:
        """Test comment node with text that doesn't start with semicolon"""
        comment_text = "This is a comment without semicolon"
        token = Token(TokenType.COMMENT, comment_text, self.test_position)

        comment_node = CommentAstNode(comment_text, token)

        self.assertEqual(comment_node.comment, comment_text)
        self.assertEqual(comment_node.to_canonical(), comment_text)

    def test_empty_comment(self) -> None:
        """Test comment node with empty text"""
        comment_text = ""
        token = Token(TokenType.COMMENT, comment_text, self.test_position)

        comment_node = CommentAstNode(comment_text, token)

        self.assertEqual(comment_node.comment, comment_text)
        self.assertEqual(comment_node.to_canonical(), comment_text)


class TestCommentParsing(TestCase):
    """Test comment parsing into AST"""

    def test_single_comment_parsing(self) -> None:
        """Test parsing a single comment line"""
        program_text = "; This is a comment"

        result = MZParser.parse_as_ast(program_text)

        self.assertIsNone(result.error)
        self.assertEqual(len(result.nodes), 1)

        comment_node = result.nodes[0]
        self.assertIsInstance(comment_node, CommentAstNode)
        if isinstance(comment_node, CommentAstNode):
            self.assertEqual(comment_node.comment, "; This is a comment")

    def test_multiple_comments_parsing(self) -> None:
        """Test parsing multiple comment lines"""
        program_text = """; Header comment
; Another comment
; Final comment"""

        result = MZParser.parse_as_ast(program_text)

        self.assertIsNone(result.error)
        self.assertEqual(len(result.nodes), 3)

        for node in result.nodes:
            self.assertIsInstance(node, CommentAstNode)

        if isinstance(result.nodes[0], CommentAstNode):
            self.assertEqual(result.nodes[0].comment.strip(), "; Header comment")
        if isinstance(result.nodes[1], CommentAstNode):
            self.assertEqual(result.nodes[1].comment.strip(), "; Another comment")
        if isinstance(result.nodes[2], CommentAstNode):
            self.assertEqual(result.nodes[2].comment.strip(), "; Final comment")

    def test_comments_with_code(self) -> None:
        """Test parsing comments mixed with code"""
        program_text = """; Header comment
main:
    lda #42    ; Inline comment
    sta 0x2000
; Footer comment"""

        result = MZParser.parse_as_ast(program_text)

        self.assertIsNone(result.error)

        # Should have: comment, label, opcode, opcode, comment
        # Note: inline comments after opcodes may not be parsed as separate nodes
        # depending on scanner implementation
        comment_nodes = [node for node in result.nodes if isinstance(node, CommentAstNode)]

        # At minimum, should have the standalone comments
        self.assertGreaterEqual(len(comment_nodes), 2)

        # Check that standalone comments are parsed
        self.assertEqual(comment_nodes[0].comment.strip(), "; Header comment")
        self.assertEqual(comment_nodes[-1].comment, "; Footer comment")

    def test_comment_ast_representation(self) -> None:
        """Test that comment AST can be converted back to representation"""
        program_text = "; Test comment"

        result = MZParser.parse_as_ast(program_text)

        self.assertIsNone(result.error)
        self.assertEqual(len(result.nodes), 1)

        # Test AST representation
        ast_repr = result.ast
        self.assertEqual(len(ast_repr), 1)
        self.assertEqual(ast_repr[0], ("comment", "; Test comment"))

    def test_multiline_comment_structure(self) -> None:
        """Test parsing program with various comment structures"""
        program_text = """; File header
; Purpose: Test file
; Author: Test

main:
    ; Initialize accumulator
    lda #0
    ; Store to memory
    sta 0x2000
    rts

; End of program"""

        result = MZParser.parse_as_ast(program_text)

        self.assertIsNone(result.error)

        # Count comment nodes
        comment_nodes = [node for node in result.nodes if isinstance(node, CommentAstNode)]

        # Should have at least the standalone comments
        self.assertGreaterEqual(len(comment_nodes), 5)

        # Verify specific comments
        comment_texts = [node.comment.strip() for node in comment_nodes]
        self.assertIn("; File header", comment_texts)
        self.assertIn("; Purpose: Test file", comment_texts)
        self.assertIn("; End of program", comment_texts)

    def test_empty_lines_with_comments(self) -> None:
        """Test parsing comments with empty lines"""
        program_text = """; Comment 1

; Comment 2

main:
    nop

; Comment 3"""

        result = MZParser.parse_as_ast(program_text)

        self.assertIsNone(result.error)

        comment_nodes = [node for node in result.nodes if isinstance(node, CommentAstNode)]
        self.assertGreaterEqual(len(comment_nodes), 3)

    def test_comment_with_special_characters(self) -> None:
        """Test parsing comments with special characters"""
        program_text = "; Comment with special chars: @#$%^&*()[]{}|\\:;\"'<>?/.,`~"

        result = MZParser.parse_as_ast(program_text)

        self.assertIsNone(result.error)
        self.assertEqual(len(result.nodes), 1)

        comment_node = result.nodes[0]
        self.assertIsInstance(comment_node, CommentAstNode)
        if isinstance(comment_node, CommentAstNode):
            self.assertEqual(comment_node.comment, program_text)
