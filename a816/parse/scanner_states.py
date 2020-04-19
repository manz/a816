from a816.cpu.cpu_65c816 import snes_opcode_table, AddressingMode, get_opcodes_with_addressing
from a816.parse.scanner import Scanner
from a816.parse.errors import ScannerException
from a816.parse.tokens import TokenType, EOF

opcodes = snes_opcode_table.keys()
opcodes_without_operand = get_opcodes_with_addressing(AddressingMode.none)


def lex_identifier(s: 'Scanner'):
    identifier_chars = '_ABCEDFGHIJKLMNOPQRSTUVWXYZabcedfghijklmnopqrstuvwxyz0123456789'
    s.accept_run(identifier_chars)

    if s.peek() == ':':
        s.emit(TokenType.LABEL)

        s.next()
        s.ignore()
    else:
        # handle scoped identifiers
        if s.peek() == '.':
            s.next()
            s.accept_run(identifier_chars)

        s.emit(TokenType.IDENTIFIER)


def lex_quoted_string(l):
    c = l.next()
    while c != '\'':
        if c == '\n' or c is None:
            raise ScannerException('Unterminated String', l.get_position())

        if c == '\\':
            if l.peek() == '\'':
                l.next()

        c = l.next()

    l.emit(TokenType.QUOTED_STRING)


def accept_opcode(l: 'Scanner'):
    opcode_candidate = l.input[l.start: l.pos + 3].lower()
    is_ws = l.peek(3)
    if opcode_candidate in snes_opcode_table.keys() and is_ws in (' ', '\n', '\t', '.', EOF):
        l.pos += 3
        return True
    return False


def lex_expression(l):
    while l.pos < len(l.input):
        l.ignore_run(' ')
        if l.accept('0123456789'):
            lex_number(l)
        elif l.accept('_ABCEDFGHIJKLMNOPQRSTUVWXYZabcedfghijklmnopqrstuvwxyz'):
            lex_identifier(l)
        elif l.accept('+-*/') or l.accept_prefix('<<') or l.accept_prefix('>>'):
            l.emit(TokenType.OPERATOR)
        elif l.accept('('):
            l.emit(TokenType.LPAREN)
        elif l.accept(')'):
            l.emit(TokenType.RPAREN)
        else:
            break


def lex_operand(l):
    p = l.peek()

    if p == '#':
        l.next()
        l.emit(TokenType.SHARP)
    elif p == '(':
        l.next()
        l.emit(TokenType.LPAREN)
    elif p == '[':
        l.next()
        l.emit(TokenType.LBRAKET)

    l.ignore_run(' ')

    lex_expression(l)

    l.ignore_run(' ')

    if l.accept(','):
        lex_opcode_index(l)

    p = l.peek()

    if p == ')':
        l.next()
        l.emit(TokenType.RPAREN)
    elif p == ']':
        l.next()
        l.emit(TokenType.RBRAKET)

    l.ignore_run(' ')
    if l.accept(','):
        lex_opcode_index(l)


def lex_opcode_index(l):
    l.ignore()
    l.ignore_run(' ')
    if l.accept('xXyYsS'):
        l.emit(TokenType.ADDRESSING_MODE_INDEX)
    else:
        raise ScannerException('Invalid index', l.get_position())


def lex_opcode_size(l):
    l.ignore()
    if l.accept('bBwWlL'):
        l.emit(TokenType.OPCODE_SIZE)
        l.ignore_run(' ')

        return lex_operand(l)
    else:
        l.next()
        raise ScannerException('Invalid Size Specifier', l.get_position())


def lex_opcode(l):
    opcode_candidate = l.input[l.start: l.pos].lower()
    if opcode_candidate in opcodes_without_operand and l.peek() != '.':
        saved_pos = l.pos

        l.accept_run(' \t')
        if l.accept(';'):
            l.accept_run('\n\0', negate=True)

        if l.peek() == '\n' or l.peek() == EOF:
            l.pos = saved_pos
            l.emit(TokenType.OPCODE_NAKED)
            return
        else:
            l.pos = saved_pos
            l.emit(TokenType.OPCODE)
    else:
        l.emit(TokenType.OPCODE)

    if l.accept('.'):
        lex_opcode_size(l)

    l.ignore_run(' ')
    lex_operand(l)


KEYWORDS = {
    'scope': TokenType.KEYWORD,
    'table': TokenType.KEYWORD,
    'include': TokenType.KEYWORD,
    'include_ips': TokenType.KEYWORD,
    'incbin': TokenType.KEYWORD,
    'pointer': TokenType.KEYWORD,
    'text': TokenType.KEYWORD,
    'db': TokenType.KEYWORD,
    'dw': TokenType.KEYWORD,
    'dl': TokenType.KEYWORD,
    'macro': TokenType.KEYWORD,
    'map': TokenType.KEYWORD
}


def lex_macro_arg(l):
    l.ignore_run(' ')

    if l.next() == ',':
        l.emit(TokenType.COMMA)
    lex_identifier(l)


def lex_macro_args_def(l):
    l.emit(TokenType.LPAREN)
    while l.peek() != ')':
        lex_macro_arg(l)

    l.emit(TokenType.RPAREN)


def lex_keyword(l):
    l.ignore()
    l.accept_run('abcdefghijklmnopqrstuvwxyz_')
    if l.current_token_text() in KEYWORDS.keys():
        key = l.current_token_text()
        l.emit(KEYWORDS[key])
    else:
        raise ScannerException(f'Unknown Keyword {l.current_token_text()}', l.get_position())


def lex_number(l):
    l.backup()

    ch = l.next()
    prefixes = {
        'o': 8,
        'x': 16,
        'b': 2
    }

    if l.peek() in ['\n', EOF]:
        l.emit(TokenType.NUMBER)
        return

    if ch == '0':
        base_prefix = l.next()

        if base_prefix in ('b', 'o', 'x'):
            l.accept_run('0123456789ABCDEFabcdef')
        else:
            l.backup()
    else:
        l.accept_run('0123456789')

    l.emit(TokenType.NUMBER)


def lex_initial(l: 'Scanner'):
    """Scanner initial state"""

    l.ignore_run(' \t\n')
    if l.accept(';'):
        while l.next() not in ['\n', None]:
            pass

        l.emit(TokenType.COMMENT)
    elif l.accept('0123456789'):
        lex_number(l)
    elif l.accept('+-&'):
        l.emit(TokenType.OPERATOR)
    elif l.accept('('):
        l.emit(TokenType.LPAREN)
    elif l.accept(')'):
        l.emit(TokenType.RPAREN)
    elif l.accept('_ABCEDFGHIJKLMNOPQRSTUVWXYZabcedfghijklmnopqrstuvwxyz'):
        l.backup()
        # check if not an opcode
        if accept_opcode(l):
            lex_opcode(l)
        else:
            lex_identifier(l)
    elif l.accept('.'):
        lex_keyword(l)
    elif l.accept(','):
        l.emit(TokenType.COMMA)
    elif l.accept_prefix(':='):
        l.emit(TokenType.ASSIGN)
    elif l.accept_prefix('@='):
        l.emit(TokenType.AT_EQ)
    elif l.accept('*'):
        if l.accept('='):
            l.emit(TokenType.STAR_EQ)
        else:
            l.emit(TokenType.OPERATOR)
    elif l.accept('\''):
        lex_quoted_string(l)
    elif l.accept('('):
        l.emit(TokenType.LPAREN)
    elif l.accept(')'):
        l.emit(TokenType.RPAREN)
    elif l.accept('['):
        l.emit(TokenType.LBRAKET)
    elif l.accept(']'):
        l.emit(TokenType.RBRAKET)
    elif l.accept('{'):
        if l.accept('{'):
            l.emit(TokenType.DOUBLE_LBRACE)
        else:
            l.emit(TokenType.LBRACE)
    elif l.accept('}'):
        if l.accept('}'):
            l.emit(TokenType.DOUBLE_RBRACE)
        else:
            l.emit(TokenType.RBRACE)
    elif l.accept('='):
        l.emit(TokenType.EQUAL)
    elif l.accept_prefix('>>'):
        l.emit(TokenType.LEFT_SHIFT)
    elif l.accept_prefix('<<'):
        l.emit(TokenType.RIGHT_SHIFT)
    else:
        if l.next() is not None:
            raise ScannerException(f'Invalid Input {l.input[l.start:]}', l.get_position())