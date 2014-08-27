import os
from a816.cpu.cpu_65c816 import snes_opcode_table
from ply import lex
from ply.lex import TOKEN

this_dir = os.path.dirname(os.path.abspath(__file__))


class A816Lexer(object):
    opcodes_pattern = '(?i)' + '(' + '|'.join(snes_opcode_table.keys()) + ')' + '\s+'
    opcodes_with_size = '(?i)' + '(' + '|'.join(snes_opcode_table.keys()) + ')' + '\.[bwl]\s+'

    @TOKEN(opcodes_pattern)
    def t_OPCODE_NAKED(self, t):
        t.lexer.lineno += t.value.count('\n')
        t.value = t.value.lower().strip()

        return t


    @TOKEN(opcodes_with_size)
    def t_OPCODE_WITH_SIZE(self, t):
        t.lexer.lineno += t.value.count('\n')
        t.value = t.value.lower().strip().split('.')
        return t

    tokens = (
        "LABEL",
        "SYMBOL",
        "OPCODE_NAKED",
        "OPCODE_WITH_SIZE",
        "SHARP",
        "LPAREN",
        "RPAREN",
        "COMMENT",
        "HEXNUMBER",
        "BINARYNUMBER",
        "NUMBER",
        "RBRACE",
        "LBRACE",
        "RBRAKET",
        "LBRAKET",
        "COLON",
        "EQUAL",
        "QUOTED_STRING",
        "COMMA",
        "MACRO",
        "TABLE",
        "POINTER",
        "TEXT",
        "DB",
        "DW",
        "INCBIN",
        "INCLUDE",
        "PLUS",
        "MINUS",
        "MULT",
        "LSHIFT",
        "RSHIFT",
        "AND",
        "STAREQ",
        "INDEX",
        "NEWLINE"

    )

    # t_WHITE_SPACE = r'[\t ]+'
    t_RBRAKET = r'\]'
    t_LBRAKET = r'\['
    t_LPAREN = r'\('
    t_RPAREN = r'\)'
    t_LBRACE = r'\{'
    t_RBRACE = r'\}'
    t_SHARP = r'\#'
    t_HEXNUMBER = r'0x[0-9a-fA-F]+'
    t_NUMBER = r'[0-9]+'
    t_BINARYNUMBER = r'0b[01]+'
    # t_NEWLINE = r'\n'
    t_SYMBOL = r'[_a-zA-Z][_a-zA-Z0-9]*'
    t_LABEL = r'[_a-zA-Z][_a-zA-Z0-9]*:'
    t_QUOTED_STRING = r"'[^']*'"
    t_COLON = r':'
    t_COMMA = r','



    t_MACRO = r'\.macro'
    t_TABLE = r'\.table'
    t_TEXT = r'\.text'
    t_DB = r'\.db'
    t_DW = r'\.dw'
    t_INCBIN = r'\.incbin'
    t_INCLUDE = r'\.include'
    t_POINTER = r'\.pointer'

    t_STAREQ = r'\*='

    t_PLUS = r'\+'
    t_MINUS = r'\-'
    t_MULT = r'\*'
    t_LSHIFT = r'<<'
    t_RSHIFT = r'>>'
    t_AND = r'&'


    t_EQUAL = r'='

    # Define a rule so we can track line numbers


    def t_newline(self, t):
        r'\n+'
        t.lexer.lineno += len(t.value)
        # return t

    # A string containing ignored characters (spaces and tabs)
    t_ignore = ' \t'


    def t_COMMENT(self, t):
        r';[^\n]*'
        pass


    def t_INDEX(self, t):
        r',\s*[XxYySs]\s+'
        t.lexer.lineno += t.value.count('\n')
        t.value = t.value[1:].lower().strip()
        return t


    def t_error(self, t):
        print()
        print("%d Illegal character '%s'" % (t.lexer.lineno, t.value[0]))
        t.lexer.skip(1)

    def __init__(self, lexer=None):
        self.lexer = lexer or lex.lex(debug=0, module=self, outputdir=this_dir)

    def clone(self):
        new_lexer = self.lexer.clone()
        new_lexer.begin(state='INITIAL')
        return A816Lexer(new_lexer)