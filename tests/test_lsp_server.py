"""
Unit tests for A816 LSP Server functionality
"""

import asyncio
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock

from lsprotocol.types import (
    CompletionItemKind,
    DiagnosticSeverity,
    HoverParams,
    Position,
    ReferenceContext,
    ReferenceParams,
    TextDocumentIdentifier,
    WorkspaceSymbolParams,
)
from lsprotocol.types import (
    FormattingOptions as LSPFormattingOptions,
)

from a816.exceptions import FormattingError
from a816.formatter import FormattingOptions
from a816.lsp.server import A816Document, A816LanguageServer, WorkspaceIndex


class TestA816Document(TestCase):
    """Test A816Document functionality"""

    def test_document_creation(self) -> None:
        """Test basic document creation"""
        content = """; Test document
main:
    lda #42
    sta 0x2000
    rts"""

        doc = A816Document("test://example.s", content)

        self.assertEqual(doc.uri, "test://example.s")
        self.assertEqual(doc.content, content)
        self.assertEqual(len(doc.lines), 5)

    def test_document_label_detection(self) -> None:
        """Test that document detects labels correctly"""
        content = """main:
    lda #42
loop:
    dec
    bne loop
subroutine:
    rts"""

        doc = A816Document("test://example.s", content)

        self.assertEqual(len(doc.labels), 3)
        self.assertIn("main", doc.labels)
        self.assertIn("loop", doc.labels)
        self.assertIn("subroutine", doc.labels)

        # Check label positions
        self.assertEqual(doc.labels["main"][0].line, 0)
        self.assertEqual(doc.labels["loop"][0].line, 2)
        self.assertEqual(doc.labels["subroutine"][0].line, 5)

    def test_document_diagnostics(self) -> None:
        """Test that document generates diagnostics for parse errors"""
        content = """main:
    lda.Q #0x42"""  # Invalid size specifier

        doc = A816Document("test://example.s", content)

        # Should have diagnostics for parse errors
        self.assertGreater(len(doc.diagnostics), 0)

        # Check diagnostic messages - now using actual parser errors
        diagnostic_messages = [diag.message for diag in doc.diagnostics]
        self.assertTrue(any("Invalid Size Specifier" in msg for msg in diagnostic_messages))

        # Check diagnostic severity
        for diag in doc.diagnostics:
            self.assertEqual(diag.severity, DiagnosticSeverity.Error)

    def test_document_diagnostics_line_column(self) -> None:
        """Test that diagnostics have correct line and column positions"""
        # Error is on line 2 (0-indexed: 1), column 5 (after the space and at 'Q')
        content = """main:
    lda.Q #0x42"""

        doc = A816Document("test://example.s", content)

        self.assertGreater(len(doc.diagnostics), 0)
        diag = doc.diagnostics[0]

        # The error should point to line 2 (index 1) where .Q is
        self.assertEqual(diag.range.start.line, 1, "Error should be on line 2 (0-indexed: 1)")

    def test_document_diagnostics_unterminated_string(self) -> None:
        """Test diagnostics for unterminated string errors point to correct line"""
        content = ".incbin 'assets/file.bin\n"

        doc = A816Document("test://example.s", content)

        self.assertGreater(len(doc.diagnostics), 0)
        diag = doc.diagnostics[0]

        # Error should be on line 1 (index 0), not line 2
        self.assertEqual(diag.range.start.line, 0, "Unterminated string error should be on line 1")
        self.assertIn("Unterminated String", diag.message)

    def test_document_update_content(self) -> None:
        """Test document content update"""
        initial_content = """main:
    lda #42"""

        doc = A816Document("test://example.s", initial_content)
        initial_label_count = len(doc.labels)

        updated_content = """main:
    lda #42
new_label:
    rts"""

        doc.update_content(updated_content)

        self.assertEqual(doc.content, updated_content)
        self.assertEqual(len(doc.lines), 4)
        self.assertGreater(len(doc.labels), initial_label_count)
        self.assertIn("new_label", doc.labels)

    def test_document_comment_handling(self) -> None:
        """Test that document handles comments properly"""
        content = """; Header comment
main:
    lda #42    ; Inline comment
    ; Standalone comment
    rts
; Footer comment"""

        doc = A816Document("test://example.s", content)

        # Should not generate diagnostics for comments
        diagnostic_messages = [diag.message for diag in doc.diagnostics]
        self.assertFalse(any("comment" in msg.lower() for msg in diagnostic_messages))

    def test_document_with_directives(self) -> None:
        """Test document with assembler directives"""
        content = """.extern external_func
main:
    lda #42
    jsr external_func
    rts"""

        doc = A816Document("test://example.s", content)

        # Should not generate diagnostics for known directives
        self.assertEqual(len(doc.diagnostics), 0)

    def test_document_mixed_case_opcodes(self) -> None:
        """Test document with mixed case opcodes"""
        content = """main:
    LDA #42
    sta 0x2000
    JSR subroutine
    RTS"""

        doc = A816Document("test://example.s", content)

        # Should not generate diagnostics for case variations
        self.assertEqual(len(doc.diagnostics), 0)

    def test_document_label_docstring_association(self) -> None:
        """Docstrings immediately after labels should be associated with the label"""
        content = """lookup_dakuten:
    \"\"\"
    input: A 8bit: current char
    output: A 16bits: the resolved char
    \"\"\"
    lda #0"""

        doc = A816Document("test://doc.s", content)

        self.assertIn("lookup_dakuten", doc.labels)
        self.assertIn("lookup_dakuten", doc.label_docstrings)
        docstring = doc.label_docstrings["lookup_dakuten"]
        self.assertIn("input: A 8bit: current char", docstring)
        self.assertIn("output: A 16bits: the resolved char", docstring)


class TestWorkspaceIndex(TestCase):
    """Tests for workspace-level indexing"""

    def test_workspace_index_resolves_entrypoint_and_includes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            entry = root / "main.s"
            module = root / "module.s"

            entry.write_text(
                """;! a816-lsp entrypoint
FLAG := 1
.include 'module.s'

main_label:
    \"\"\"Main doc\"\"\"
    rtl
""",
                encoding="utf-8",
            )

            module.write_text(
                """module_label:
    \"\"\"Module doc\"\"\"
    rtl
""",
                encoding="utf-8",
            )

            index = WorkspaceIndex(root)
            index.rebuild()

            self.assertIsNotNone(index.entrypoint)
            assert index.entrypoint is not None
            self.assertEqual(index.entrypoint.name, "main.s")
            self.assertIn("module_label", index.labels)
            self.assertEqual(index.get_label_doc("module_label"), "Module doc")
            location = index.get_label_location("module_label")
            self.assertIsNotNone(location)


class TestA816LanguageServer(TestCase):
    """Test A816LanguageServer functionality"""

    def setUp(self) -> None:
        """Set up test fixtures"""
        self.server = A816LanguageServer()
        self.mock_ls = MagicMock()

    def test_server_creation(self) -> None:
        """Test basic server creation"""
        self.assertIsNotNone(self.server.server)
        self.assertIsNotNone(self.server.formatter)
        self.assertEqual(len(self.server.documents), 0)

    def test_server_has_completions(self) -> None:
        """Test that server has completion data"""
        self.assertGreater(len(self.server._opcode_completions), 0)
        self.assertGreater(len(self.server._keyword_completions), 0)
        self.assertGreater(len(self.server._register_completions), 0)

    def test_opcode_completions(self) -> None:
        """Test opcode completion generation"""
        completions = self.server._opcode_completions

        # Should have basic opcodes
        completion_labels = [item.label for item in completions]
        self.assertIn("LDA", completion_labels)
        self.assertIn("STA", completion_labels)
        self.assertIn("JMP", completion_labels)

        # Should have size variants
        self.assertIn("LDA.B", completion_labels)
        self.assertIn("LDA.W", completion_labels)
        self.assertIn("LDA.L", completion_labels)

        # Check completion item properties
        lda_items = [item for item in completions if item.label == "LDA"]
        self.assertGreater(len(lda_items), 0)

        lda_item = lda_items[0]
        self.assertEqual(lda_item.kind, CompletionItemKind.Keyword)
        self.assertEqual(lda_item.detail, "65c816 Instruction")

    def test_keyword_completions(self) -> None:
        """Test keyword completion generation"""
        completions = self.server._keyword_completions

        completion_labels = [item.label for item in completions]

        # Should have common directives
        self.assertIn("SCOPE", completion_labels)
        self.assertIn("MACRO", completion_labels)
        self.assertIn("IF", completion_labels)

        # Check completion item properties
        for item in completions:
            self.assertEqual(item.kind, CompletionItemKind.Keyword)
            self.assertEqual(item.detail, "Assembler directive")

    def test_register_completions(self) -> None:
        """Test register completion generation"""
        completions = self.server._register_completions

        completion_labels = [item.label for item in completions]

        # Should have basic registers
        self.assertIn("A", completion_labels)
        self.assertIn("X", completion_labels)
        self.assertIn("Y", completion_labels)
        self.assertIn("S", completion_labels)

        # Check completion item properties
        for item in completions:
            self.assertEqual(item.kind, CompletionItemKind.Variable)
            self.assertEqual(item.detail, "65c816 Register")

    def test_formatting_options_conversion(self) -> None:
        """Test LSP formatting options conversion"""
        lsp_options = LSPFormattingOptions(tab_size=8, insert_spaces=True)

        a816_options = self.server._create_formatting_options(lsp_options)

        self.assertIsInstance(a816_options, FormattingOptions)
        self.assertEqual(a816_options.indent_size, 8)
        self.assertEqual(a816_options.opcode_indent, 8)

    def test_publish_diagnostics_call(self) -> None:
        """Test diagnostics publishing"""
        # This would require mocking the server's publish_diagnostics method
        # For now, just test that the method exists
        self.assertTrue(hasattr(self.server, "_publish_diagnostics"))


class TestA816LanguageServerHandlers(TestCase):
    """Test LSP server handlers (requires async testing)"""

    def setUp(self) -> None:
        """Set up test fixtures"""
        self.server = A816LanguageServer()
        self.mock_ls = MagicMock()

    def test_document_formatting_logic(self) -> None:
        """Test document formatting logic (without async)"""
        # Create a test document
        content = """; Test file
main:
lda #42
sta 0x2000
rts"""

        doc = A816Document("test://example.s", content)
        self.server.documents["test://example.s"] = doc

        # Test formatting
        formatted_content = self.server.formatter.format_text(content)

        self.assertIsInstance(formatted_content, str)
        self.assertNotEqual(formatted_content, content)  # Should be different after formatting
        self.assertIn("; Test file", formatted_content)
        self.assertIn("main:", formatted_content)

    def test_semantic_token_analysis(self) -> None:
        """Test semantic token analysis"""
        content = """; Comment
main:
    lda #42
    sta 0x2000"""

        doc = A816Document("test://example.s", content)
        tokens = self.server._analyze_semantic_tokens(doc)

        self.assertIsInstance(tokens, list)
        self.assertGreater(len(tokens), 0)

        # Tokens should be in groups of 5 (deltaLine, deltaStart, length, tokenType, tokenModifiers)
        self.assertEqual(len(tokens) % 5, 0)

    def test_hover_returns_label_docstring(self) -> None:
        """Hover should surface label docstrings"""
        uri = "test://hover_doc.s"
        content = """lookup_dakuten:
    \"\"\"
    input: A 8bit: current char
    output: A 16bits: the resolved char
    \"\"\"
    rtl"""

        doc = A816Document(uri, content)
        self.server.documents[uri] = doc

        handler = self.server.server.lsp._get_handler("textDocument/hover")
        params = HoverParams(
            text_document=TextDocumentIdentifier(uri=uri),
            position=Position(line=0, character=5),
        )

        import asyncio

        hover_result = asyncio.run(handler(params))
        self.assertIsNotNone(hover_result)
        self.assertIsNotNone(hover_result.contents)
        self.assertIn("resolved char", hover_result.contents.value)

    def test_workspace_symbol_lookup(self) -> None:
        """Workspace symbol search should find labels across files"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            main_path = root / "main.s"
            main_path.write_text(
                "lookup_dakuten:\n    lda #0\n    rtl\n",
                encoding="utf-8",
            )

            index = WorkspaceIndex(root)
            index.rebuild()

            self.server.workspace_index = index
            self.server._ensure_workspace_index = lambda: index

            handler = self.server.server.lsp._get_handler("workspace/symbol")
            params = WorkspaceSymbolParams(query="lookup")
            results = asyncio.run(handler(params))

            names = {symbol.name for symbol in results}
            self.assertIn("lookup_dakuten", names)
            for symbol in results:
                if symbol.name == "lookup_dakuten":
                    self.assertIn(symbol.container_name, {None, "main.s"})

    def test_workspace_references(self) -> None:
        """Reference search should include matches across workspace"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            def_path = root / "defs.s"
            ref_path = root / "ref.s"

            def_path.write_text(
                "lookup_label:\n    rtl\n",
                encoding="utf-8",
            )

            ref_path.write_text(
                "    jsr lookup_label\n    rts\n",
                encoding="utf-8",
            )

            def_doc = A816Document(def_path.as_uri(), def_path.read_text(encoding="utf-8"))
            ref_doc = A816Document(ref_path.as_uri(), ref_path.read_text(encoding="utf-8"))

            index = WorkspaceIndex(root)
            index.replace_document(def_doc)
            index.replace_document(ref_doc)
            index.built = True

            self.server.workspace_index = index
            self.server._ensure_workspace_index = lambda: index
            self.server.documents[ref_path.as_uri()] = ref_doc

            handler = self.server.server.lsp._get_handler("textDocument/references")
            params = ReferenceParams(
                text_document=TextDocumentIdentifier(uri=ref_path.as_uri()),
                position=Position(line=0, character=10),
                context=ReferenceContext(include_declaration=True),
            )
            results = asyncio.run(handler(params))

            uris = {loc.uri for loc in results}
            self.assertIn(def_path.as_uri(), uris)
            self.assertIn(ref_path.as_uri(), uris)

            params_no_decl = ReferenceParams(
                text_document=TextDocumentIdentifier(uri=ref_path.as_uri()),
                position=Position(line=0, character=10),
                context=ReferenceContext(include_declaration=False),
            )
            results_no_decl = asyncio.run(handler(params_no_decl))
            self.assertTrue(all(loc.uri != def_path.as_uri() for loc in results_no_decl))

    def test_tokenize_line(self) -> None:
        """Test line tokenization for semantic highlighting"""
        test_cases = [
            ("; This is a comment", 0),
            ("main:", 1),
            ("    lda #42", 2),
            ("    sta 0x2000,X", 3),
        ]

        for line, line_num in test_cases:
            tokens = self.server._tokenize_line(line, line_num)

            self.assertIsInstance(tokens, list)

            # Each token should have required fields
            for token in tokens:
                self.assertIn("line", token)
                self.assertIn("char", token)
                self.assertIn("length", token)
                self.assertIn("type", token)
                self.assertEqual(token["line"], line_num)

    def test_tokenize_instruction(self) -> None:
        """Test instruction tokenization"""
        test_cases = [
            ("lda #42", 0, 4),
            ("sta 0x2000,X", 1, 4),
            ("jsr.w subroutine", 2, 4),
        ]

        for instruction, line_num, start_pos in test_cases:
            tokens = self.server._tokenize_instruction(instruction, line_num, start_pos)

            self.assertIsInstance(tokens, list)
            self.assertGreater(len(tokens), 0)  # Should tokenize at least the opcode

    def test_tokenize_operand(self) -> None:
        """Test operand tokenization"""
        test_cases = [
            ("#42", 0, 4),
            ("0x2000,X", 0, 4),
            ("(address),Y", 0, 4),
            ("#0x1234", 0, 4),
        ]

        for operand, line_num, start_pos in test_cases:
            tokens = self.server._tokenize_operand(operand, line_num, start_pos)

            self.assertIsInstance(tokens, list)
            # May or may not have tokens depending on operand complexity


class TestA816LSPIntegration(TestCase):
    """Integration tests for LSP server components"""

    def test_document_and_formatter_integration(self) -> None:
        """Test integration between document analysis and formatting"""
        content = """; Header
main:
unknown_instruction
    lda #42
    rts"""

        # Create document (should detect error)
        doc = A816Document("test://example.s", content)
        self.assertGreater(len(doc.diagnostics), 0)

        # Formatting should now raise because the content is invalid
        formatter = A816LanguageServer().formatter
        with self.assertRaises(FormattingError):
            formatter.format_text(content)

    def test_server_with_real_assembly_content(self) -> None:
        """Test server with realistic assembly content"""
        content = """; 65c816 Assembly Example
; Super Nintendo ROM hacking

.extern sound_routine

main:
    ; Set up processor state
    sei                    ; Disable interrupts
    clc                    ; Clear carry
    xce                    ; Switch to native mode
    
    ; Set up memory
    rep #0x30              ; Set A and Index to 16-bit
    lda.w #0x1FFF
    tcs                   ; Set stack pointer
    
    ; Main game loop
game_loop:
    jsr.w input_handler
    jsr.w game_logic
    jsr.w sound_routine
    bra game_loop

input_handler:
    ; Handle controller input
    lda 0x4016            ; Read controller
    and #0x80
    bne button_pressed
    rts

button_pressed:
    ; Handle button press
    lda #0x01
    sta 0x2000
    rts

game_logic:
    ; Game logic here
    nop
    rts"""

        # Test document analysis
        doc = A816Document("test://game.s", content)

        # Should detect labels
        expected_labels = {"main", "game_loop", "input_handler", "button_pressed", "game_logic"}
        found_labels = set(doc.labels.keys())
        self.assertTrue(expected_labels.issubset(found_labels))

        # Should not have errors for valid assembly
        error_diagnostics = [d for d in doc.diagnostics if d.severity == DiagnosticSeverity.Error]
        self.assertEqual(len(error_diagnostics), 0)

        # Test formatting
        server = A816LanguageServer()
        formatted = server.formatter.format_text(content)

        self.assertIsInstance(formatted, str)
        self.assertIn("; 65c816 Assembly Example", formatted)
        self.assertIn("main:", formatted)
        self.assertIn("rep", formatted)  # Should be lowercase

    def test_completion_with_document_context(self) -> None:
        """Test that completions include document context"""
        content = """main:
    lda #42
    jsr subroutine
    rts

subroutine:
    rts"""

        # Create server and document
        server = A816LanguageServer()
        doc = A816Document("test://example.s", content)
        server.documents["test://example.s"] = doc

        # Get all completions
        all_items = []
        all_items.extend(server._opcode_completions)
        all_items.extend(server._keyword_completions)
        all_items.extend(server._register_completions)

        # Add local symbols (this simulates what the completion handler does)
        for label in doc.labels:
            all_items.append(MagicMock(label=label, kind=CompletionItemKind.Function))

        completion_labels = [item.label for item in all_items]

        # Should include opcodes
        self.assertIn("LDA", completion_labels)

        # Should include local labels
        self.assertIn("main", completion_labels)
        self.assertIn("subroutine", completion_labels)

    def test_document_macro_docstring_storage(self) -> None:
        """Macro docstrings should be captured for tooling use"""
        content = '''
.macro greet() {
    """Say hi"""
    lda #0
}
'''

        doc = A816Document("test://docstring.s", content)
        self.assertEqual(doc.macro_docstrings.get("greet"), "Say hi")
