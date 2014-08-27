import os
from a816.cpu.cpu_65c816 import AddressingMode
from a816.parse.lexer import A816Lexer
from ply import yacc as yacc

this_dir = os.path.dirname(os.path.abspath(__file__))


class A816Parser(object):
    precedence = (
        ('left', 'PLUS', 'MINUS'),
        ('left', 'MULT'),
        ('left', 'RSHIFT', 'LSHIFT'),
        ('left', 'AND')
    )

    def __init__(self, filename='', lexer=None, parser=None):
        self.lexer = lexer or A816Lexer()
        self.filename = filename
        self.tokens = self.lexer.tokens
        self.parser = parser or yacc.yacc(module=self, tabmodule='ply_generated_rules', outputdir=this_dir)

    def clone(self, filename):
        return A816Parser(filename, lexer=self.lexer.clone(), parser=self.parser)

    def parse(self, source):
        ast_nodes = self.parser.parse(source, lexer=self.lexer.lexer)
        # print(ast_nodes)
        return ast_nodes


    def p_program(self, p):
        """program : block_statement"""
        p[0] = p[1]


    def p_statement(self, p):
        """statement : label
                    | direct_instruction
                    | direct_indexed_instruction
                    | indirect_instruction
                    | indirect_long_instruction
                    | indirect_long_indexed_instruction
                    | immediate_instruction
                    | none_instruction
                    | symbol_define
                    | macro
                    | macro_apply
                    | directive_with_string
                    | data
                    | include
                    | pointer
                    | stareq
                    | compound_statement
                    """
        p[0] = p[1]


    def p_block_statement(self, p):
        """block_statement : statement
                            | block_statement statement"""
        if len(p) == 3:
            p[0] = p[1] + (p[2],)
        else:
            p[0] = ('block', p[1])


    def p_symbol_define(self, p):
        'symbol_define : SYMBOL EQUAL expression'
        p[0] = ('symbol', p[1], p[3])


    def p_macro(self, p):
        """macro : MACRO SYMBOL macro_args compound_statement """
        p[0] = ('macro', p[2], p[3], p[4])

    def p_macro_apply(self, p):
        """macro_apply : SYMBOL macro_apply_args"""
        p[0] = ('macro_apply', p[1], p[2])

    def p_macro_apply_args(self, p):
        """macro_apply_args : LPAREN apply_args RPAREN
                        """
        p[0] = ('apply_args', p[2])


    def p_apply_args(self, p):
        """apply_args : apply_args COMMA expression
                    | expression
                    """
        if len(p) == 4:
            p[0] = p[1] + (p[3],)
        else:
            p[0] = (p[1],)


    def p_directive_with_string(self, p):
        """directive_with_string : INCBIN QUOTED_STRING
                                 | TABLE QUOTED_STRING
                                 | TEXT QUOTED_STRING"""
        p[0] = (p[1][1:], p[2][1:-1])


    def p_include(self, p):
        """include : INCLUDE QUOTED_STRING"""

        filename = p[2][1:-1]
        with open(filename, encoding='utf-8') as fd:
            source = fd.read()
            # new_lexer = self.lexer.clone()
            new_parser = self.clone(filename)
            # A816Parser(filename, lexer=new_lexer, parser=self.parser)
            p[0] = new_parser.parse(source)


        # p[0] = ('include', p[2][1:-1])


    def p_stareq(self, p):
        'stareq : STAREQ number'
        p[0] = ('stareq', p[2])

    def p_pointer(self, p):
        'pointer : POINTER expression'
        p[0] = ('pointer', p[2])

    def p_macro_args(self, p):
        """macro_args : LPAREN args RPAREN
                        """
        p[0] = ('args', p[2])


    def p_args(self, p):
        """args : args COMMA SYMBOL
                    | SYMBOL
                    """
        if len(p) == 4:
            p[0] = p[1] + (p[3],)
        else:
            p[0] = (p[1],)


    def p_expression_list(self, p):
        """expression_list : expression_list COMMA expression
                           | expression"""

        if len(p) == 4:
            p[0] = p[1] + (p[3],)
        else:
            p[0] = (p[1],)


    def p_data(self, p):
        """data : DB expression_list
                | DW expression_list"""
        p[0] = (p[1][1:], p[2])


    def p_compound_statement(self, p):
        """compound_statement : LBRACE block_statement RBRACE"""
        p[0] = ('compound', p[2])


    def p_opcode(self, p):
        """opcode : OPCODE_NAKED
                  | OPCODE_WITH_SIZE"""
        p[0] = p[1]


    def p_none_instruction(self, p):
        'none_instruction : opcode'
        p[0] = ('opcode', AddressingMode.none, p[1])


    def p_immediate_instruction(self, p):
        'immediate_instruction : opcode SHARP expression'
        p[0] = ('opcode', AddressingMode.immediate, p[1], p[3])


    def p_direct_instruction(self, p):
        "direct_instruction : opcode expression"
        p[0] = ('opcode', AddressingMode.direct, p[1], p[2])


    def p_direct_indexed_instruction(self, p):
        "direct_indexed_instruction : opcode expression INDEX"
        p[0] = ('opcode', AddressingMode.direct_indexed, p[1], p[2], p[3])


    def p_indirect_instruction(self, p):
        'indirect_instruction : opcode LPAREN expression RPAREN'
        p[0] = ('opcode', AddressingMode.indirect, p[1], p[3])


    def p_indirect_long_instruction(self, p):
        'indirect_long_instruction : opcode LBRAKET expression RBRAKET'
        p[0] = ('opcode', AddressingMode.indirect_long, p[1], p[3])


    def p_indirect_long_indexed_instruction(self, p):
        'indirect_long_indexed_instruction : opcode LBRAKET expression RBRAKET INDEX'
        p[0] = ('opcode', AddressingMode.indirect_indexed_long, p[1], p[3], p[5])


    def p_label(self, p):
        'label : LABEL'
        p[0] = ('label', p[1][:-1])


    def p_number(self, p):
        """number : HEXNUMBER
                  | BINARYNUMBER
                  | NUMBER"""
        p[0] = p[1]


    def p_expression(self, p):
        """expression : number
                    | SYMBOL
                    | paren_expression PLUS paren_expression
                    | paren_expression MINUS paren_expression
                    | paren_expression MULT paren_expression
                    | paren_expression LSHIFT paren_expression
                    | paren_expression RSHIFT paren_expression
                    | paren_expression AND paren_expression
                    """

        p[0] = ''.join([p[k] for k in range(1, len(p))])


    def p_paren_expression(self, p):
        """paren_expression : LPAREN expression RPAREN
                            | expression"""

        p[0] = ''.join([p[k] for k in range(1, len(p))])




    # Error rule for syntax errors
    def p_error(self, p):
        if p:
            FAIL = '\033[91m'
            ENDC = '\033[0m'

            print('%s Unexcepted Token at line %d' % (self.filename, self.lexer.lexer.lineno))

            before = p.lexer.lexdata[p.lexpos-10: p.lexpos]
            after = p.lexer.lexdata[p.lexpos+len(p.value): p.lexpos+10]

            print(before + FAIL + p.value + ENDC + after)

            print(p.lexer.lineno)
        else:
            print('End of input encountered, you may need to check closing braces or parenthesis.')
        raise Exception()