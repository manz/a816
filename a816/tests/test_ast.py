from unittest.case import TestCase

from a816.cpu.cpu_65c816 import AddressingMode
from a816.program import Program


class TestParse(TestCase):
    maxDiff = None

    @staticmethod
    def _get_ast_for(program_text):
        program = Program()
        return program.parser.parse_as_ast(program_text)

    def test_label(self):
        ast = self._get_ast_for('my_cute_label:')
        self.assertEqual(ast, ('block',
                               ('label', 'my_cute_label', ('fileinfo', '', 1, 'my_cute_label:'))))

    def test_immediate_instruction(self):
        ast = self._get_ast_for('lda #0x00')
        self.assertEqual(ast, ('block',
                               ('opcode', AddressingMode.immediate, 'lda', '0x00', ('fileinfo', '', 1, 'lda #0x00'))))

    def test_direct_instruction_with_size(self):
        ast_b = self._get_ast_for('lda.b 0x00')
        ast_w = self._get_ast_for('lda.w 0x0000')
        ast_l = self._get_ast_for('lda.l 0x000000')

        self.assertEqual(ast_b,
                         ('block',
                          ('opcode', AddressingMode.direct, ['lda', 'b'], '0x00', ('fileinfo', '', 1, 'lda.b 0x00')))
                         )

        self.assertEqual(ast_w,
                         ('block',
                          (
                              'opcode', AddressingMode.direct, ['lda', 'w'], '0x0000',
                              ('fileinfo', '', 1, 'lda.w 0x0000')))
                         )

        self.assertEqual(ast_l,
                         ('block',
                          ('opcode', AddressingMode.direct, ['lda', 'l'], '0x000000',
                           ('fileinfo', '', 1, 'lda.l 0x000000')))
                         )

    def test_direct_instruction(self):
        ast = self._get_ast_for('lda 0x00')
        self.assertEqual(ast,
                         ('block',
                          ('opcode', AddressingMode.direct, 'lda', '0x00', ('fileinfo', '', 1, 'lda 0x00')))
                         )

    def test_direct_indexed_instruction(self):
        ast = self._get_ast_for('lda 0x00, y\n')
        self.assertEqual(ast,
                         ('block',
                          ('opcode', AddressingMode.direct_indexed, 'lda', '0x00', 'y',
                           ('fileinfo', '', 1, 'lda 0x00, y'))))

    def test_indirect_instruction(self):
        ast = self._get_ast_for('lda (0x00)')
        self.assertEqual(ast,
                         ('block',
                          ('opcode', AddressingMode.indirect, 'lda', '0x00',
                           ('fileinfo', '', 1, 'lda (0x00)'))))

    def test_indirect_indexed_instruction(self):
        ast = self._get_ast_for('lda (0x00), s\n')
        self.assertEqual(ast,
                         ('block',
                          ('opcode', AddressingMode.indirect_indexed, 'lda', '0x00', 's',
                           ('fileinfo', '', 1, 'lda (0x00), s'))))

    def test_indirect_long_instruction(self):
        ast = self._get_ast_for('lda [0x00]')
        self.assertEqual(ast,
                         ('block',
                          ('opcode', AddressingMode.indirect_long, 'lda', '0x00',
                           ('fileinfo', '', 1, 'lda [0x00]'))))

    def test_indirect_long_indexed_instruction(self):
        ast = self._get_ast_for('lda [0x00], x\n')
        self.assertEqual(ast,
                         ('block',
                          ('opcode', AddressingMode.indirect_indexed_long, 'lda', '0x00', 'x',
                           ('fileinfo', '', 1, 'lda [0x00], x'))))

    def test_none_instruction(self):
        ast = self._get_ast_for('nop\n')
        self.assertEqual(ast,
                         ('block',
                          ('opcode', AddressingMode.none, 'nop',
                           ('fileinfo', '', 1, 'nop'))))

    def test_symbol_define(self):
        ast = self._get_ast_for('toto = 0x00 + 0x00')
        self.assertEqual(ast,
                         ('block',
                          ('symbol', 'toto', '0x00+0x00', ('fileinfo', '', 1, 'toto = 0x00 + 0x00'))))

    def test_macro(self):
        ast = self._get_ast_for('.macro test_macro(arg) {\n lda #arg\n }')
        self.assertEqual(ast, ('block',
                               ('macro', 'test_macro', ('args', ('arg',)),
                                ('compound',
                                 ('block',
                                  ('opcode', AddressingMode.immediate, 'lda', 'arg',
                                   ('fileinfo', '', 2, 'lda #arg'))),
                                 ('fileinfo', '', 1, '.macro test_macro(arg) {')))))

    def test_macro_apply(self):
        ast = self._get_ast_for('shift_char(base, dest)')
        self.assertEqual(ast, ('block',
                               ('macro_apply', 'shift_char',
                                ('apply_args', ('base', 'dest')),
                                ('fileinfo', '', 1, 'shift_char(base, dest)'))))

    def test_named_scope(self):
        ast = self._get_ast_for('.scope toto {\n .db 0\n }')
        self.assertEqual(ast, ('block', ('named_scope', 'toto', ('block', ('db', ('0',)))))
                         )

    def test_incbin(self):
        ast = self._get_ast_for(".incbin 'binary_file.bin'")
        self.assertEqual(ast, ('block',
                               ('incbin', 'binary_file.bin', ('fileinfo', '', 1, ".incbin 'binary_file.bin'"))))

    def test_table(self):
        ast = self._get_ast_for(".table 'dialog.tbl'")
        self.assertEqual(ast, ('block', ('table', 'dialog.tbl', ('fileinfo', '', 1, ".table 'dialog.tbl'"))))

    def test_text(self):
        ast = self._get_ast_for(".text 'PUSH START'")
        self.assertEqual(ast, ('block', ('text', 'PUSH START', ('fileinfo', '', 1, ".text 'PUSH START'"))))

    def test_stareq(self):
        ast = self._get_ast_for('*=0xc00000')
        self.assertEqual(ast,
                         ('block', ('stareq', '0xc00000', ('fileinfo', '', 1, '*=0xc00000'))))

    def test_tildaeq(self):
        ast = self._get_ast_for('@=0x7e0000, 0xc00000')
        self.assertEqual(ast,
                         ('block', ('ateq', '0x7e0000', '0xc00000', ('fileinfo', '', 1, '@=0x7e0000, 0xc00000'))))

    def test_dp_or_sr_indirect_indexed(self):
        ast = self._get_ast_for('lda (0x00,x)\n')
        self.assertEqual(ast,
                         ('block', ('opcode', AddressingMode.dp_or_sr_indirect_indexed, 'lda', '0x00', 'x',
                                    ('fileinfo', '', 1, 'lda (0x00,x)'))))

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
        expected = ('block',
                    ('opcode', AddressingMode.dp_or_sr_indirect_indexed, 'eor', '0x01', 'x',
                     ('fileinfo', '', 2, 'EOR (0x01,x)')),
                    ('opcode', AddressingMode.direct_indexed, 'eor', '0x01', 's', ('fileinfo', '', 3, 'EOR 0x01, s')),
                    ('opcode', AddressingMode.direct, 'eor', '0x01', ('fileinfo', '', 4, 'EOR 0x01')),
                    ('opcode', AddressingMode.indirect_long, 'eor', '0x01', ('fileinfo', '', 5, 'EOR [0x01]')),
                    ('opcode', AddressingMode.immediate, 'eor', '0x01', ('fileinfo', '', 6, 'EOR #0x01')),
                    ('opcode', AddressingMode.indirect_indexed, 'eor', '0x01', 'y',
                     ('fileinfo', '', 7, 'EOR (0x01), y')),
                    ('opcode', AddressingMode.stack_indexed_indirect_indexed, 'eor', '0x01', 'y',
                     ('fileinfo', '', 8, 'EOR (0x01,s),y')),

                    ('opcode', AddressingMode.direct_indexed, 'eor', '0x01', 'x', ('fileinfo', '', 9, 'EOR 0x01, x')),
                    ('opcode', AddressingMode.indirect_indexed_long, 'eor', '0x02', 'y',
                     ('fileinfo', '', 10, 'EOR [0x02], y')),
                    ('opcode', AddressingMode.direct_indexed, 'eor', '0x02', 'y', ('fileinfo', '', 11, 'EOR 0x02, y')),
                    ('opcode', AddressingMode.direct_indexed, 'eor', '0x02', 'x', ('fileinfo', '', 12, 'EOR 0x02, x')),
                    ('opcode', AddressingMode.direct_indexed, 'eor', '0x010203', 'x',
                     ('fileinfo', '', 13, 'EOR 0x010203, x')))
        self.assertEqual(ast, expected)

    def test_string_quote_escape(self):
        ast = self._get_ast_for(".text 'I\\'m hungry'")
        self.assertEqual(ast,
                         ('block', ('text', 'I\'m hungry',
                                    ('fileinfo', '', 1, ".text 'I\\'m hungry'"))))
