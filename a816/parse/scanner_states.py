from a816.cpu.cpu_65c816 import (
    AddressingMode,
    get_opcodes_with_addressing,
    snes_opcode_table,
)
from a816.parse.errors import ScannerException
from a816.parse.scanner import Scanner
from a816.parse.tokens import EOF, TokenType

opcodes = snes_opcode_table.keys()
opcodes_without_operand = get_opcodes_with_addressing(AddressingMode.none)


def lex_identifier(s: "Scanner") -> None:
    identifier_chars = "_ABCEDFGHIJKLMNOPQRSTUVWXYZabcedfghijklmnopqrstuvwxyz0123456789"
    s.accept_run(identifier_chars)

    if s.peek() == ":" and s.peek(1) != "=":
        s.emit(TokenType.LABEL)

        s.next()
        s.ignore()
    else:
        # handle scoped identifiers
        if s.peek() == ".":
            s.next()
            s.accept_run(identifier_chars)

        s.emit(TokenType.IDENTIFIER)


def lex_quoted_string(s: "Scanner") -> None:
    c = s.next()
    while c != "'":
        if c == "\n" or c is None:
            raise ScannerException("Unterminated String", s.get_position())

        if c == "\\" and s.peek() == "'":
            s.next()

        c = s.next()

    s.emit(TokenType.QUOTED_STRING)


def accept_opcode(s: "Scanner") -> bool:
    opcode_candidate = s.input[s.start : s.pos + 3].lower()
    is_ws = s.peek(3)
    if opcode_candidate in snes_opcode_table.keys() and is_ws in (
        " ",
        "\n",
        "\t",
        ".",
        EOF,
    ):
        s.pos += 3
        return True
    return False


def lex_expression(s: "Scanner") -> None:
    while s.pos < len(s.input):
        s.ignore_run(" ")
        if s.accept("0123456789"):
            lex_number(s)
        elif s.accept("_ABCEDFGHIJKLMNOPQRSTUVWXYZabcedfghijklmnopqrstuvwxyz"):
            lex_identifier(s)
        elif s.accept("+-*/&|") or s.accept_prefix("<<") or s.accept_prefix(">>"):
            s.emit(TokenType.OPERATOR)
        elif s.accept("("):
            s.emit(TokenType.LPAREN)
        elif s.accept(")"):
            s.emit(TokenType.RPAREN)
        else:
            break


def lex_operand(s: "Scanner") -> None:
    p = s.peek()

    if p == "#":
        s.next()
        s.emit(TokenType.SHARP)
    elif p == "(":
        s.next()
        s.emit(TokenType.LPAREN)
    elif p == "[":
        s.next()
        s.emit(TokenType.LBRAKET)

    s.ignore_run(" ")

    lex_expression(s)

    s.ignore_run(" ")

    if s.accept(","):
        lex_opcode_index(s)

    p = s.peek()

    if p == ")":
        s.next()
        s.emit(TokenType.RPAREN)
    elif p == "]":
        s.next()
        s.emit(TokenType.RBRAKET)

    s.ignore_run(" ")
    if s.accept(","):
        lex_opcode_index(s)


def lex_opcode_index(s: "Scanner") -> None:
    s.ignore()
    s.ignore_run(" ")
    if s.accept("xXyYsS"):
        s.emit(TokenType.ADDRESSING_MODE_INDEX)
    else:
        raise ScannerException("Invalid index", s.get_position())


def lex_opcode_size(s: "Scanner") -> None:
    s.ignore()
    if s.accept("bBwWlL"):
        s.emit(TokenType.OPCODE_SIZE)
        s.ignore_run(" ")

        return lex_operand(s)
    else:
        s.next()
        raise ScannerException("Invalid Size Specifier", s.get_position())


def lex_opcode(s: "Scanner") -> None:
    opcode_candidate = s.input[s.start : s.pos].lower()
    if opcode_candidate in opcodes_without_operand and s.peek() != ".":
        saved_pos = s.pos

        s.accept_run(" \t")
        if s.accept(";"):
            s.accept_run("\n\0", negate=True)

        if s.peek() == "\n" or s.peek() == EOF:
            s.pos = saved_pos
            s.emit(TokenType.OPCODE_NAKED)
            return
        else:
            s.pos = saved_pos
            s.emit(TokenType.OPCODE)
    else:
        s.emit(TokenType.OPCODE)

    if s.accept("."):
        lex_opcode_size(s)

    s.ignore_run(" ")
    lex_operand(s)


KEYWORDS = {
    "scope",
    "table",
    "include",
    "include_ips",
    "incbin",
    "pointer",
    "text",
    "ascii",
    "db",
    "dw",
    "dl",
    "macro",
    "map",
    "if",
    "else",
    "for",
    "struct",
    "istruct",
}


def lex_macro_arg(s: "Scanner") -> None:
    s.ignore_run(" ")

    if s.next() == ",":
        s.emit(TokenType.COMMA)
    lex_identifier(s)


def lex_macro_args_def(s: "Scanner") -> None:
    s.emit(TokenType.LPAREN)
    while s.peek() != ")":
        lex_macro_arg(s)

    s.emit(TokenType.RPAREN)


def lex_keyword(s: "Scanner") -> None:
    s.ignore()
    s.accept_run("abcdefghijklmnopqrstuvwxyz_")
    if s.current_token_text() in KEYWORDS:
        s.emit(TokenType.KEYWORD)
    else:
        raise ScannerException(f"Unknown Keyword {s.current_token_text()}", s.get_position())


def lex_number(s: Scanner) -> None:
    acceptable_values = {"b": "01", "o": "012345678", "x": "0123456789ABCDEFabcdef"}

    s.backup()

    ch = s.next()

    if s.peek() in ["\n", EOF]:
        s.emit(TokenType.NUMBER)
        return

    if ch == "0":
        base_prefix = s.next()

        if base_prefix in ("b", "o", "x"):
            s.accept_run(acceptable_values[base_prefix])
        else:
            s.backup()
    else:
        s.accept_run("0123456789")

    s.emit(TokenType.NUMBER)


def lex_initial(s: Scanner) -> None:
    """Scanner initial state"""

    s.ignore_run(" \t\n")
    if s.accept(";"):
        while s.next() not in ["\n", None]:
            # eat the comment until end of  line.
            pass

        s.emit(TokenType.COMMENT)
    elif s.accept("0123456789"):
        lex_number(s)
    elif s.accept("+-&"):
        s.emit(TokenType.OPERATOR)
    elif s.accept_prefix("=="):
        s.emit(TokenType.OPERATOR)
    elif s.accept_prefix("!="):
        s.emit(TokenType.OPERATOR)
    elif s.accept_prefix(">>"):
        s.emit(TokenType.OPERATOR)
    elif s.accept_prefix("<<"):
        s.emit(TokenType.OPERATOR)
    elif s.accept_prefix(">") or s.accept_prefix("<") or s.accept_prefix(">=") or s.accept_prefix("<="):
        s.emit(TokenType.OPERATOR)
    # elif l.accept_prefix('True') or l.accept_prefix('False'):
    #     l.emit(TokenType.BOOLEAN)
    # elif s.accept_prefix("byte") or s.accept_prefix("word") or s.accept_prefix("long"):
    #    s.emit(TokenType.TYPE)
    elif s.accept("_ABCEDFGHIJKLMNOPQRSTUVWXYZabcedfghijklmnopqrstuvwxyz"):
        s.backup()
        # check if not an opcode
        if accept_opcode(s):
            lex_opcode(s)
        else:
            lex_identifier(s)
    elif s.accept("."):
        lex_keyword(s)
    elif s.accept(","):
        s.emit(TokenType.COMMA)
    elif s.accept_prefix(":="):
        s.emit(TokenType.ASSIGN)
    elif s.accept_prefix("@="):
        s.emit(TokenType.AT_EQ)
    elif s.accept("*"):
        if s.accept("="):
            s.emit(TokenType.STAR_EQ)
        else:
            s.emit(TokenType.OPERATOR)
    elif s.accept("'"):
        lex_quoted_string(s)
    elif s.accept("("):
        s.emit(TokenType.LPAREN)
    elif s.accept(")"):
        s.emit(TokenType.RPAREN)
    elif s.accept("["):
        s.emit(TokenType.LBRAKET)
    elif s.accept("]"):
        s.emit(TokenType.RBRAKET)
    elif s.accept("{"):
        if s.accept("{"):
            s.emit(TokenType.DOUBLE_LBRACE)
        else:
            s.emit(TokenType.LBRACE)
    elif s.accept("}"):
        if s.accept("}"):
            s.emit(TokenType.DOUBLE_RBRACE)
        else:
            s.emit(TokenType.RBRACE)
    elif s.accept("="):
        s.emit(TokenType.EQUAL)
    elif s.accept_prefix("/*"):
        while not s.accept_prefix("*/"):
            s.next()
        s.emit(TokenType.COMMENT)
    else:
        if s.next() is not None:
            raise ScannerException(f"Invalid Input {s.input[s.start:]}", s.get_position())
