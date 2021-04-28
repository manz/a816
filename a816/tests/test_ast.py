import struct
from unittest.case import TestCase

from a816.cpu.cpu_65c816 import AddressingMode
from a816.parse import codegen
from a816.parse.codegen import code_gen
from a816.parse.mzparser import MZParser
from a816.program import Program
from a816.symbols import Resolver
from a816.tests import StubWriter


class TestParse(TestCase):
    maxDiff = None

    @staticmethod
    def _get_result_for(program_text):
        return MZParser.parse_as_ast(program_text, 'memory.s')

    def _get_ast_for(self, program_text):
        return self._get_result_for(program_text).ast

    def test_label(self):
        ast = self._get_ast_for('my_cute_label:')
        self.assertEqual(ast, [('label', 'my_cute_label')])

    def test_immediate_instruction(self):
        ast = self._get_ast_for('lda #0x00')
        self.assertEqual(ast, [('opcode', AddressingMode.immediate, 'lda', '0x00', None)])

    def test_direct_instruction_with_size(self):
        ast_b = self._get_ast_for('lda.b 0x00')
        ast_w = self._get_ast_for('lda.w 0x0000')
        ast_l = self._get_ast_for('lda.l 0x000000')

        self.assertEqual(ast_b,
                         [('opcode', AddressingMode.direct, ('lda', 'b'), '0x00', None)])

        self.assertEqual(ast_w,
                         [('opcode', AddressingMode.direct, ('lda', 'w'), '0x0000', None)])

        self.assertEqual(ast_l,
                         [('opcode', AddressingMode.direct, ('lda', 'l'), '0x000000', None)])

    def test_direct_instruction(self):
        ast = self._get_ast_for('lda 0x00')
        self.assertEqual(ast,
                         [('opcode', AddressingMode.direct, 'lda', '0x00', None)]
                         )

    def test_direct_indexed_instruction(self):
        ast = self._get_ast_for('lda 0x00, y\n')
        self.assertEqual(ast,
                         [('opcode', AddressingMode.direct_indexed, 'lda', '0x00', 'y')])

    def test_indirect_instruction(self):
        ast = self._get_ast_for('lda (0x00)')
        self.assertEqual(ast,
                         [('opcode', AddressingMode.indirect, 'lda', '0x00', None)])

    def test_indirect_indexed_instruction(self):
        ast = self._get_ast_for('lda (0x00), s\n')
        self.assertEqual(ast,
                         [('opcode', AddressingMode.indirect_indexed, 'lda', '0x00', 's')])

    def test_indirect_long_instruction(self):
        ast = self._get_ast_for('lda [0x00]')
        self.assertEqual(ast,
                         [('opcode', AddressingMode.indirect_long, 'lda', '0x00', None)])

    def test_indirect_long_indexed_instruction(self):
        ast = self._get_ast_for('lda [0x00], x\n')
        self.assertEqual(ast,
                         [('opcode', AddressingMode.indirect_indexed_long, 'lda', '0x00', 'x')])

    def test_none_instruction(self):
        ast = self._get_ast_for('nop\n')
        self.assertEqual(ast,
                         [('opcode', AddressingMode.none, 'nop', None, None)])

    def test_symbol_define(self):
        ast = self._get_ast_for('toto = 0x00 + 0x00')
        self.assertEqual([('assign', 'toto', '0x00 + 0x00')], ast)

    def test_macro(self):
        ast = self._get_ast_for('.macro test_macro(arg) {\n lda #arg\n }')
        self.assertEqual(ast, [
            ('macro', 'test_macro', ('args', ['arg']),
             ('block',
              [
                  ('opcode', AddressingMode.immediate, 'lda', 'arg', None)
              ]
              ))])

    def test_macro_apply(self):
        ast = self._get_ast_for('shift_char(base, dest)')
        self.assertEqual(ast, [('macro_apply', 'shift_char', ('apply_args', ['base', 'dest']))])

    def test_named_scope(self):
        ast = self._get_ast_for('.scope toto {\n .db 0\n }')
        self.assertEqual(ast, [('scope', 'toto', (('block', [('db', ['0'])]),))]
                         )

    def test_incbin(self):
        ast = self._get_ast_for(".incbin 'binary_file.bin'")
        self.assertEqual(ast, [('incbin', 'binary_file.bin')])

    def test_table(self):
        ast = self._get_ast_for(".table 'dialog.tbl'")
        self.assertEqual(ast, [('table', 'dialog.tbl')])

    def test_text(self):
        ast = self._get_ast_for(".text 'PUSH START'")
        self.assertEqual(ast, [('text', 'PUSH START')])

    def test_star_eq(self):
        ast = self._get_ast_for('*=0xc00000')
        self.assertEqual(ast,
                         [('star_eq', '0xc00000')])

    def test_at_eq(self):
        ast = self._get_ast_for('@=0x7e0000')
        self.assertEqual(ast, [('at_eq', '0x7e0000')])

    def test_dp_or_sr_indirect_indexed(self):
        ast = self._get_ast_for('lda (0x00,x)\n')
        self.assertEqual(ast,
                         [('opcode', AddressingMode.dp_or_sr_indirect_indexed, 'lda', '0x00', 'x')])

    def test_eor_addressing_modes(self):
        program = '''
        EOR (0x01,x)
        EOR 0x01, s
        EOR 0x01
        EOR [0x01]
        EOR #0x01
        EOR (0x01), y
        EOR (0x01,s),y
        EOR 0x01, x
        EOR [0x02], y
        EOR 0x02, y
        EOR 0x02, x
        EOR 0x010203, x
        '''
        ast = self._get_ast_for(program)
        expected = [
            ('opcode', AddressingMode.dp_or_sr_indirect_indexed, 'EOR', '0x01', 'x'),
            ('opcode', AddressingMode.direct_indexed, 'EOR', '0x01', 's'),
            ('opcode', AddressingMode.direct, 'EOR', '0x01', None),
            ('opcode', AddressingMode.indirect_long, 'EOR', '0x01', None),
            ('opcode', AddressingMode.immediate, 'EOR', '0x01', None),
            ('opcode', AddressingMode.indirect_indexed, 'EOR', '0x01', 'y'),
            ('opcode', AddressingMode.stack_indexed_indirect_indexed, 'EOR', '0x01', 'y'),
            ('opcode', AddressingMode.direct_indexed, 'EOR', '0x01', 'x'),
            ('opcode', AddressingMode.indirect_indexed_long, 'EOR', '0x02', 'y'),
            ('opcode', AddressingMode.direct_indexed, 'EOR', '0x02', 'y'),
            ('opcode', AddressingMode.direct_indexed, 'EOR', '0x02', 'x'),
            ('opcode', AddressingMode.direct_indexed, 'EOR', '0x010203', 'x')]
        self.assertEqual(ast, expected)

    def test_string_quote_escape(self):
        ast = self._get_ast_for(".text 'I\\'m hungry'")
        self.assertEqual([('text', 'I\\\'m hungry')], ast)

    def test_scan_error(self):
        result = self._get_result_for('a')
        self.assertEqual(result.error, '\nmemory.s:0:1 TokenType.EOF\na\n ')

    def test_recursive_macros(self):
        program = """
.macro recursive(length) {
    .if length  {
        .db length
        recursive(length - 1)
    } .else {
        .db 0
    }
}
        recursive(4)
        """

        ast = self._get_ast_for(program)
        self.assertEqual(ast, [('macro',
                                'recursive',
                                ('args', ['length']),
                                ('block',
                                 [('if',
                                   'length',
                                   ('compound', [('db', ['length']),
                                                 ('macro_apply', 'recursive', ('apply_args', ['length - 1']))]),
                                   ('compound', [('db', ['0'])]))])),
                               ('macro_apply', 'recursive', ('apply_args', ['4']))])
        nodes = code_gen(ast, Resolver())

        # self.assertEqual(nodes, [])

    def test_dw(self):
        program = """
.dw 0x00
mac(0)
"""
        ast = self._get_ast_for(program)
        self.assertEqual(
            ast,
            [('dw', ['0x00']), ('macro_apply', 'mac', ('apply_args', ['0']))])

    def test_if(self):
        program = """
DEBUG := 1
.if DEBUG {
    .db 0x00
}
        """

        ast = self._get_ast_for(program)
        self.assertEqual(ast, [
            ('assign', 'DEBUG', '1'),
            ('if', 'DEBUG', ('compound', [('db', ['0x00'])
                                          ]), None)])

    def test_if_else(self):
        program = """
    DEBUG := 1
    .if DEBUG {
    .db 0x00
    } .else {
    .db 0x85
    }
        """

        ast = self._get_ast_for(program)
        self.assertEqual(ast, [
            ('assign', 'DEBUG', '1'),
            ('if', 'DEBUG', ('compound', [('db', ['0x00'])]), ('compound', [('db', ['0x85'])]))])

    def test_for(self):
        program = """
        .macro generate_power_of_twos_table(min, max) {
            .for k := min, max  {
                .dw 1 << k
            }
        }
        generate_power_of_twos_table(0, 8)
        """

        ast = self._get_ast_for(program)
        #      self.assertEqual(ast, [
        #          ('for', 'k', '0', '5', ('compound', [('db', ['k'])]))
        #      ])
        p = Program()

        nodes = code_gen(ast, p.resolver)
        #   self.assertEqual(nodes, [])
        writer = StubWriter()
        p.resolve_labels(nodes)
        p.emit(nodes, writer)

        self.assertEqual(struct.unpack('<8H', writer.data[0]), (1, 2, 4, 8, 16, 32, 64, 128))
