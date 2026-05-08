import logging
import struct
import unittest
from typing import cast

from a816.cpu.cpu_65c816 import AddressingMode
from a816.parse.ast.nodes import DocstringAstNode, IncludeIpsAstNode, MacroAstNode, ScopeAstNode, Term, UnaryOp
from a816.parse.codegen import code_gen
from a816.parse.mzparser import MZParser
from a816.parse.nodes import OpcodeNode
from a816.parse.tokens import Token, TokenType
from a816.program import Program
from a816.writers import Writer

logger = logging.getLogger("a816")

stream_formatter = logging.Formatter("%(levelname)s :: %(message)s")
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(stream_formatter)

logger.addHandler(stream_handler)


# logger.setLevel(logging.WARNING)


class StubWriter(Writer):
    def __init__(self) -> None:
        self.data: list[tuple[int, bytes]] = []

    def begin(self) -> None:
        """StubWriter only implements write_block."""

    def write_block(self, block: bytes, block_address: int) -> None:
        self.data.append((block_address, block))

    def write_block_header(self, block: bytes, block_address: int) -> None:
        return None

    def end(self) -> None:
        """StubWriter only implements write_block."""


class ParseTest(unittest.TestCase):
    def test_parse(self) -> None:
        program = Program()
        _, nodes = program.parser.parse("lda #0x1234")

        self.assertEqual(len(nodes), 1)

        node = nodes[0]
        assert isinstance(node, OpcodeNode)
        self.assertEqual(node.opcode, "lda")
        assert node.value_node is not None
        self.assertEqual(node.value_node.get_value(), 0x1234)
        self.assertEqual(node.addressing_mode, AddressingMode.immediate)

    def test_parse_comments(self) -> None:
        program = Program()
        _, nodes = program.parser.parse("; comment\nlda #0x1234\n; another comment")

        self.assertEqual(len(nodes), 1)

        node = nodes[0]
        assert isinstance(node, OpcodeNode)
        self.assertEqual(node.opcode, "lda")

    def test_unterminated_string(self) -> None:
        input_program = "'coucou"
        program = Program()

        result = program.parser.parse_as_ast(input_program)

        # Error should contain key elements
        assert result.error is not None
        self.assertIn("Unterminated String", result.error)
        self.assertIn("memory.s", result.error)
        self.assertIn("'coucou", result.error)

    def test_unterminated_string_with_newline(self) -> None:
        """Test that unterminated string error points to line 1, not line 2."""
        input_program = ".incbin 'assets/dakuten.bin\n"
        program = Program()

        result = program.parser.parse_as_ast(input_program, "test.s")

        # Error should be on line 1, not line 2
        assert result.error is not None
        self.assertIn("Unterminated String", result.error)
        self.assertIn("test.s:1:", result.error, "error should be on line 1")
        self.assertIn(".incbin 'assets/dakuten.bin", result.error)

    def test_invalid_size_specifier(self) -> None:
        input_program = "lda.Q #0x00"
        program = Program()

        result = program.parser.parse_as_ast(input_program)
        # Error should contain key elements
        assert result.error is not None
        self.assertIn("Invalid Size Specifier", result.error)
        self.assertIn("memory.s", result.error)
        self.assertIn("lda.Q #0x00", result.error)

    def test_invalid_index(self) -> None:
        input_program = "lda 0x00, O"
        program = Program()

        result = program.parser.parse_as_ast(input_program)
        # Error should contain key elements
        assert result.error is not None
        self.assertIn("Invalid index", result.error)
        self.assertIn("memory.s", result.error)
        self.assertIn("lda 0x00, O", result.error)

    def test_data_word(self) -> None:
        input_program = """{
            symbol=0x12345
            .dw 0x0000
            .dw 0x3450, 0x00, symbol & 0x00FF
            }
        """

        program = Program()

        _, nodes = program.parser.parse(input_program)
        program.resolve_labels(nodes)
        emitted_nodes = []
        for node in nodes:
            node_data = node.emit(program.resolver.reloc_address)
            if node_data:
                emitted_nodes.append(node_data)
        self.assertEqual(emitted_nodes, [b"\x00\x00", b"\x50\x34", b"\x00\x00", b"\x45\x00"])

    def test_expressions(self) -> None:
        input_program = """
            {
                my_symbol = 0x4567
                jmp.w my_symbol
            {
                my_symbol = 0x1234
                lda.w label
                pea.w my_symbol
                label:
            }
        }
        """

        program = Program()
        _, nodes = program.parser.parse(input_program)

        program.resolve_labels(nodes)

        writer = StubWriter()
        program.emit(nodes, writer)

        machine_code = writer.data[0][1]

        unpacked = struct.unpack("<BHBHBH", machine_code)

        self.assertEqual(unpacked[1], 0x4567)
        self.assertEqual(unpacked[3], 0x8009)
        self.assertEqual(unpacked[5], 0x1234)

    def test_blocks(self) -> None:
        input_program = """
        {
            my = 0x01
            {
            my = 0x00
            }
            {
            my = 0x12
            lda.w #0x00
            a:
            }
            }
        """

        program = Program()
        program.parser = MZParser(program.resolver)
        ast = program.parser.parse_as_ast(input_program)
        print(ast)
        nodes = program.parser.parse(input_program)
        program.resolve_labels(nodes[1])

    def test_macro(self) -> None:
        input_program = """
        .macro test_macro(a, b, c) {
            lda.b #a
            lda.b #b
            lda.b #c
        }
        test_macro(0, 1, 2)
        """

        expected_ast_nodes = [
            (
                "macro",
                "test_macro",
                ("args", ["a", "b", "c"]),
                (
                    "block",
                    [
                        ("opcode", AddressingMode.immediate, ("lda", "b"), "a", None),
                        ("opcode", AddressingMode.immediate, ("lda", "b"), "b", None),
                        ("opcode", AddressingMode.immediate, ("lda", "b"), "c", None),
                    ],
                ),
            ),
            ("macro_apply", "test_macro", ("apply_args", ["0", "1", "2"])),
        ]

        program = Program()

        result = program.parser.parse_as_ast(input_program)
        self.assertEqual(expected_ast_nodes, result.ast)

        nodes = code_gen(result.nodes, program.resolver)
        program.resolve_labels(nodes)
        program.resolver.dump_symbol_map()

    def test_macro_empty_args(self) -> None:
        input_program = """
                .macro test() {
                    sep #0x30
                }

                test()
                """

        program = Program()

        ast_nodes = program.parser.parse_as_ast(input_program)
        self.assertEqual(
            ast_nodes.ast,
            [
                (
                    "macro",
                    "test",
                    ("args", []),
                    (
                        "block",
                        [("opcode", AddressingMode.immediate, "sep", "0x30", None)],
                    ),
                ),
                ("macro_apply", "test", ("apply_args", [])),
            ],
        )

        code_gen(ast_nodes.nodes, program.resolver)

    def test_push_pull(self) -> None:
        input_program = """
        php
        pha
        display_text_in_menus:                  ; CODE XREF: new_game_screen_related+39p
                                                ; sub_197D3+4Ep ...
            phb
            phd
            phx
            ldx.w     #0x100
            phx
            pld
        ; D=100
            phk
            plb
        ; ds=1000 B=1

        loc_1830B:                              ; CODE XREF: display_text_in_menus+1Fj
            rep     #0x20 ; ' '
        ;.A16
            lda.w     0x0000 ,y
            clc
            adc     0x29
            tax
            sep     #0x20 ; ' '
        ;.A8
            iny
            iny

        loc_18318:                              ; CODE XREF: sub_182CD+15j
                                                ; .01:82F9j ...
        ;	lda     0,Y
            beq     loc_18332
            iny
            cmp     #1
            beq     loc_1830B
            jsr     0x8E32
            sta     0x7E0000,X
            xba
            sta     0x7E0040,X
            inx
            inx
            bra     loc_18318
        ; ---------------------------------------------------------------------------

        loc_18332:                              ; CODE XREF: display_text_in_menus+1Aj
            plx
            pld
            plb
            rts
        """

        program = Program()
        result = program.parser.parse_as_ast(input_program)
        nodes = code_gen(result.nodes, program.resolver)
        program.resolve_labels(nodes)

        program.emit(nodes, StubWriter())
        print(nodes[-2])

    def test_macro_definition_should_parse(self) -> None:
        input_program = """
        .macro waitforvblank(a) {
            pla
        }
        plb
        waitforvblank(0x123456)
        """

        program = Program()
        ast = program.parser.parse_as_ast(input_program)
        print(ast)

    def test_nini(self) -> None:
        input_program = """.macro wait_for_vblank_inline() {
            pha
        negative:
            lda.l 0x004212
            bmi negative
        positive:
            lda.l 0x004212
            bpl positive
            pla
        }"""
        program = Program()
        ast = program.parser.parse_as_ast(input_program)
        print(ast)

    def test_php_pha(self) -> None:
        input_program = """
        .macro dma_transfer_to_vram_call(source, vramptr, count, mode)
        {
            php
            pha
            phx
            pea.w return_addr-1
            pea.w source & 0xFFFF
            pea.w  0x00FF & (source >> 16)
            pea.w vramptr
            pea.w count
            pea.w mode
            jmp.l dma_transfer_to_vram
        return_addr:
            plx
            pla
            plp
            TAX            ; using math multiplication
            LDA.L vwf_shift_table,X
        }"""
        program = Program()
        ast = program.parser.parse_as_ast(input_program)
        print(ast)

    def test_named_scopes(self) -> None:
        input_program = """
                .scope newgame {
                    .db 0
                    .db 0

                    empty:


                }

                .dw newgame.empty"""

        program = Program()
        ast = program.parser.parse_as_ast(input_program)
        print(ast)
        nodes = code_gen(ast.nodes, program.resolver)
        program.resolve_labels(nodes)
        program.resolver.dump_symbol_map()
        writer = StubWriter()
        program.emit(nodes, writer)
        self.assertEqual(writer.data[0][1], b"\x00\x00\x02\x80")

    def test_include_ips(self) -> None:
        input_program = """.include_ips 'whee.ips', -0x200"""
        program = Program()
        ast = program.parser.parse_as_ast(input_program)
        print(ast)
        nodes = ast.nodes

        self.assertEqual(len(nodes), 1)
        node = nodes[0]
        self.assertIsInstance(node, IncludeIpsAstNode)
        ips_node = cast(IncludeIpsAstNode, node)
        self.assertEqual("whee.ips", ips_node.file_path)
        self.assertEqual(
            [
                UnaryOp(token=Token(TokenType.OPERATOR, "-")),
                Term(token=Token(TokenType.NUMBER, "0x200")),
            ],
            ips_node.expression.tokens,
        )

    def test_macro_docstring_attached(self) -> None:
        input_program = '''
.macro greet() {
    """Say hi"""
    lda #0
}
greet()
'''

        program = Program()
        ast_result = program.parser.parse_as_ast(input_program)
        macro_nodes = [node for node in ast_result.nodes if isinstance(node, MacroAstNode)]
        self.assertEqual(len(macro_nodes), 1)
        macro = macro_nodes[0]
        self.assertEqual(macro.docstring, "Say hi")
        self.assertTrue(all(not isinstance(n, DocstringAstNode) for n in macro.block.body))

    def test_scope_docstring_attached(self) -> None:
        input_program = '''
.scope player {
    """Player state"""
    lda #1
}
'''

        program = Program()
        ast_result = program.parser.parse_as_ast(input_program)

        scope_nodes = [node for node in ast_result.nodes if isinstance(node, ScopeAstNode)]
        self.assertEqual(len(scope_nodes), 1)
        scope = scope_nodes[0]
        self.assertEqual(scope.docstring, "Player state")
        self.assertTrue(all(not isinstance(n, DocstringAstNode) for n in scope.body.body))

    def test_module_docstring_retained(self) -> None:
        input_program = '''
"""Module description"""
lda #1
'''

        program = Program()
        ast_result = program.parser.parse_as_ast(input_program)

        self.assertIsInstance(ast_result.nodes[0], DocstringAstNode)
        assert isinstance(ast_result.nodes[0], DocstringAstNode)  # for type narrowing
        self.assertEqual(ast_result.nodes[0].text, "Module description")

    def test_parse_struct(self) -> None:
        input_program = """
        .struct a_struct {
            byte id
            word offset
            long pointer
        }"""

        program = Program()
        ast = program.parser.parse_as_ast(input_program)
        assert ast.error is None, ast.error
        self.assertEqual(
            [
                (
                    "struct",
                    "a_struct",
                    [("id", "byte"), ("offset", "word"), ("pointer", "long")],
                )
            ],
            ast.ast,
        )

    def test_symbol(self) -> None:
        program = Program()
        ast = program.parser.parse_as_ast("a = 3")
        self.assertEqual([("symbol", "a", "3")], ast.ast)

    def test_assign(self) -> None:
        program = Program()
        ast = program.parser.parse_as_ast("a := 3")
        self.assertEqual([("assign", "a", "3")], ast.ast)

    def test_label_decl_directive_parses(self) -> None:
        program = Program()
        ast = program.parser.parse_as_ast(".label mult8_far = 0x02855C")
        self.assertEqual([("label_decl", "mult8_far", "0x02855C")], ast.ast)

    def test_label_decl_registers_label(self) -> None:
        """`.label NAME = ADDR` lands in scope.absolute_labels (separate from
        scope.labels so the linker doesn't shift it by the module delta)."""
        program = Program()
        _, nodes = program.parser.parse(".label mult8_far = 0x02855C")
        program.resolve_labels(nodes)

        scope = program.resolver.current_scope
        self.assertEqual(scope.absolute_labels["mult8_far"], 0x02855C)
        # Symbol table also resolves the name (so call sites work).
        self.assertEqual(scope.value_for("mult8_far"), 0x02855C)
        # Real `name:` labels stay separate.
        self.assertNotIn("mult8_far", scope.labels)

    def test_label_decl_value_independent_of_pc(self) -> None:
        """`.label thing = 0x8000` after `*=0x018134` keeps value 0x8000,
        not the surrounding PC."""
        program = Program()
        _, nodes = program.parser.parse("""
        *=0x018134
        .label thing = 0x8000
        lda #0x42
        """)
        program.resolve_labels(nodes)
        self.assertEqual(program.resolver.current_scope.absolute_labels["thing"], 0x8000)

    def test_a8_directive_sets_8bit_accumulator(self) -> None:
        """Test that .a8 followed by lda #immediate emits 2 bytes (8-bit immediate)"""
        input_program = """
        .a8
        lda #0x42
        """
        program = Program()
        _, nodes = program.parser.parse(input_program)
        program.resolve_labels(nodes)

        writer = StubWriter()
        program.emit(nodes, writer)

        # lda immediate 8-bit: opcode (0xA9) + 1 byte = 2 bytes
        self.assertEqual(len(writer.data[0][1]), 2)
        self.assertEqual(writer.data[0][1], b"\xa9\x42")

    def test_a16_directive_sets_16bit_accumulator(self) -> None:
        """Test that .a16 followed by lda #immediate emits 3 bytes (16-bit immediate)"""
        input_program = """
        .a16
        lda #0x1234
        """
        program = Program()
        _, nodes = program.parser.parse(input_program)
        program.resolve_labels(nodes)

        writer = StubWriter()
        program.emit(nodes, writer)

        # lda immediate 16-bit: opcode (0xA9) + 2 bytes = 3 bytes
        self.assertEqual(len(writer.data[0][1]), 3)
        self.assertEqual(writer.data[0][1], b"\xa9\x34\x12")

    def test_i8_directive_sets_8bit_index(self) -> None:
        """Test that .i8 followed by ldx #immediate emits 2 bytes (8-bit immediate)"""
        input_program = """
        .i8
        ldx #0x42
        """
        program = Program()
        _, nodes = program.parser.parse(input_program)
        program.resolve_labels(nodes)

        writer = StubWriter()
        program.emit(nodes, writer)

        # ldx immediate 8-bit: opcode (0xA2) + 1 byte = 2 bytes
        self.assertEqual(len(writer.data[0][1]), 2)
        self.assertEqual(writer.data[0][1], b"\xa2\x42")

    def test_i16_directive_sets_16bit_index(self) -> None:
        """Test that .i16 followed by ldx #immediate emits 3 bytes (16-bit immediate)"""
        input_program = """
        .i16
        ldx #0x1234
        """
        program = Program()
        _, nodes = program.parser.parse(input_program)
        program.resolve_labels(nodes)

        writer = StubWriter()
        program.emit(nodes, writer)

        # ldx immediate 16-bit: opcode (0xA2) + 2 bytes = 3 bytes
        self.assertEqual(len(writer.data[0][1]), 3)
        self.assertEqual(writer.data[0][1], b"\xa2\x34\x12")

    def test_explicit_size_overrides_directive(self) -> None:
        """Test that explicit .w suffix overrides .a8 state"""
        input_program = """
        .a8
        lda.w #0x0000
        """
        program = Program()
        _, nodes = program.parser.parse(input_program)
        program.resolve_labels(nodes)

        writer = StubWriter()
        program.emit(nodes, writer)

        # Explicit .w should force 16-bit even in .a8 mode
        self.assertEqual(len(writer.data[0][1]), 3)
        self.assertEqual(writer.data[0][1], b"\xa9\x00\x00")

    def test_register_size_persists_across_instructions(self) -> None:
        """Test that register size state persists across multiple instructions"""
        input_program = """
        .a16
        lda #0x1234
        lda #0x5678
        """
        program = Program()
        _, nodes = program.parser.parse(input_program)
        program.resolve_labels(nodes)

        writer = StubWriter()
        program.emit(nodes, writer)

        # Both should be 16-bit (3 bytes each)
        self.assertEqual(len(writer.data[0][1]), 6)
        self.assertEqual(writer.data[0][1], b"\xa9\x34\x12\xa9\x78\x56")

    def test_register_size_can_be_changed(self) -> None:
        """Test that register size can be changed mid-program"""
        input_program = """
        .a16
        lda #0x1234
        .a8
        lda #0x42
        """
        program = Program()
        _, nodes = program.parser.parse(input_program)
        program.resolve_labels(nodes)

        writer = StubWriter()
        program.emit(nodes, writer)

        # First lda is 16-bit (3 bytes), second is 8-bit (2 bytes)
        self.assertEqual(len(writer.data[0][1]), 5)
        self.assertEqual(writer.data[0][1], b"\xa9\x34\x12\xa9\x42")
