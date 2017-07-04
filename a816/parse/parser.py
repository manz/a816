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
        self.parser = parser or yacc.yacc(module=self, method='LALR', tabmodule='ply_generated_rules',
                                          outputdir=this_dir, debug=0, write_tables=1, errorlog=yacc.NullLogger())

    def clone(self, filename):
        return A816Parser(filename, lexer=self.lexer.clone(), parser=self.parser)

    def parse(self, source):
        ast_nodes = self.parser.parse(source, lexer=self.lexer.lexer, tracking=True)
        return ast_nodes

    def make_node(self, node, p):
        try:
            line = p.lexer.lexdata.split('\n')[p.lineno(1) - 1].strip()
        except:
            line = 'bogus'
        node += (('fileinfo', self.filename, p.lineno(1), line),)
        return node

    def p_program(self, p):
        """program : block_statement"""
        p[0] = p[1]

    def p_empty(self, p):
        """empty : """
        pass

    def p_statement(self, p):
        """statement : label
                    | direct_instruction
                    | direct_indexed_instruction
                    | indirect_instruction
                    | indirect_indexed_instruction
                    | indirect_long_instruction
                    | indirect_long_indexed_instruction
                    | immediate_instruction
                    | none_instruction
                    | symbol_define
                    | macro
                    | macro_apply
                    | named_scope
                    | directive_with_string
                    | data
                    | for
                    | include
                    | pointer
                    | stareq
                    | pluseq
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
        """symbol_define : SYMBOL EQUAL expression"""
        p[0] = self.make_node(('symbol', p[1], p[3]), p)

    def p_macro(self, p):
        """macro : MACRO SYMBOL macro_args compound_statement """
        p[0] = ('macro', p[2], p[3], p[4])

    def p_named_scope(self, p):
        """named_scope : NAMED_SCOPE SYMBOL LBRACE block_statement RBRACE"""
        p[0] = ('named_scope', p[2], p[4])

    # def p_if(self, p):
    #     """if : IF SYMBOL block_statement ELSE block_statement ENDIF
    #         | IF SYMBOL block_statement ENDIF
    #     """
    #     if len(p) > 5:
    #         p[0] = self.make_node(('if', p[2], p[3], p[5]), p)
    #     else:
    #         p[0] = self.make_node(('if', p[2], p[3]), p)

    def p_for(self, p):
        """for : FOR SYMBOL number number compound_statement"""
        p[0] = self.make_node(('for', p[2], p[3], p[4], p[5]), p)

    def p_macro_apply(self, p):
        """macro_apply : SYMBOL macro_apply_args"""
        p[0] = self.make_node(('macro_apply', p[1], p[2]), p)

    def p_macro_apply_args(self, p):
        """macro_apply_args : LPAREN apply_args RPAREN
                        """
        p[0] = ('apply_args', p[2])

    def p_apply_args(self, p):
        """apply_args : apply_args COMMA expression
                    | expression
                    | empty
                    """
        if len(p) == 4:
            p[0] = p[1] + (p[3],)
        else:
            p[0] = (p[1],) if p[1] else ()

    def p_directive_with_string(self, p):
        """directive_with_string : INCBIN QUOTED_STRING
                                 | TABLE QUOTED_STRING
                                 | TEXT QUOTED_STRING"""
        p[0] = self.make_node((p[1][1:], p[2][1:-1]), p)

    def p_include(self, p):
        """include : INCLUDE QUOTED_STRING"""

        filename = p[2][1:-1]
        with open(filename, encoding='utf-8') as fd:
            source = fd.read()
            new_parser = self.clone(filename)
            p[0] = new_parser.parse(source)

    def p_stareq(self, p):
        """stareq : STAREQ expression"""
        p[0] = self.make_node(('stareq', p[2]), p)

    def p_pluseq(self, p):
        """pluseq : PLUSEQ expression"""
        p[0] = self.make_node(('pluseq', p[2]), p)

    def p_tildaeq(self, p):
        """tildaeq : TILDAEQ expression"""
        p[0] = self.make_node(('tildaeq', p[2]), p)

    def p_pointer(self, p):
        """pointer : POINTER expression"""
        p[0] = self.make_node(('pointer', p[2]), p)

    def p_macro_args(self, p):
        """macro_args : LPAREN args RPAREN
                        """
        p[0] = ('args', p[2])

    def p_args(self, p):
        """args : args COMMA SYMBOL
                    | SYMBOL
                    | empty
                    """
        if len(p) == 4:
            p[0] = p[1] + (p[3],)
        else:
            p[0] = (p[1],) if p[1] else ()

    def p_expression_list(self, p):
        """expression_list : expression_list COMMA expression
                           | expression"""

        if len(p) == 4:
            p[0] = p[1] + (p[3],)
        else:
            p[0] = (p[1],)

    def p_data(self, p):
        """data : DB expression_list
                | DW expression_list
                | DL expression_list"""
        p[0] = (p[1][1:], p[2])

    def p_compound_statement(self, p):
        """compound_statement : LBRACE block_statement RBRACE"""
        p[0] = self.make_node(('compound', p[2]), p)

    def p_opcode(self, p):
        """opcode : OPCODE_NAKED
                  | OPCODE_WITH_SIZE"""
        p[0] = p[1]

    def p_none_instruction(self, p):
        """none_instruction : OPCODE_NONE"""
        p[0] = self.make_node(('opcode', AddressingMode.none, p[1]), p)

    def p_immediate_instruction(self, p):
        """immediate_instruction : opcode SHARP expression"""
        p[0] = self.make_node(('opcode', AddressingMode.immediate, p[1], p[3]), p)

    def p_direct_instruction(self, p):
        """direct_instruction : opcode expression"""
        p[0] = self.make_node(('opcode', AddressingMode.direct, p[1], p[2]), p)

    def p_direct_indexed_instruction(self, p):
        """direct_indexed_instruction : opcode expression INDEX"""
        p[0] = self.make_node(('opcode', AddressingMode.direct_indexed, p[1], p[2], p[3]), p)

    def p_indirect_instruction(self, p):
        """indirect_instruction : opcode LPAREN expression RPAREN"""
        p[0] = self.make_node(('opcode', AddressingMode.indirect, p[1], p[3]), p)

    def p_indirect_indexed_instruction(self, p):
        """indirect_indexed_instruction : opcode LPAREN expression RPAREN INDEX"""
        p[0] = self.make_node(('opcode', AddressingMode.indirect_indexed, p[1], p[3], p[5]), p)

    def p_indirect_long_instruction(self, p):
        """indirect_long_instruction : opcode LBRAKET expression RBRAKET"""
        p[0] = self.make_node(('opcode', AddressingMode.indirect_long, p[1], p[3]), p)

    def p_indirect_long_indexed_instruction(self, p):
        """indirect_long_indexed_instruction : opcode LBRAKET expression RBRAKET INDEX"""
        p[0] = self.make_node(('opcode', AddressingMode.indirect_indexed_long, p[1], p[3], p[5]), p)

    def p_label(self, p):
        """label : LABEL"""
        p[0] = self.make_node(('label', p[1][:-1]), p)

    def p_number(self, p):
        """number : HEXNUMBER
                  | BINARYNUMBER
                  | NUMBER"""
        p[0] = p[1]

    def p_expression(self, p):
        """expression : number
                    | SYMBOL
                    | SCOPE_SYMBOL
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

    def p_error(self, p):
        if p:
            FAIL = '\033[91m'
            ENDC = '\033[0m'

            print('%s Unexcepted Token at line %d' % (self.filename, self.lexer.lexer.lineno))

            before = p.lexer.lexdata[p.lexpos - 10: p.lexpos]
            after = p.lexer.lexdata[p.lexpos + len(p.value): p.lexpos + 10]

            if isinstance(p.value, list):
                value = '.'.join(p.value)
            else:
                value = p.value

            print(before + FAIL + value + ENDC + after)

        else:
            print('End of input encountered, you may need to check closing braces or parenthesis.')
        raise Exception()
