from collections.abc import Callable

from a816.cpu.cpu_65c816 import (
    AddressingMode,
    get_opcodes_with_addressing,
    snes_opcode_table,
)
from a816.error_codes import (
    E_SCANNER_INVALID_INPUT,
    E_SCANNER_UNKNOWN_KEYWORD,
    E_SCANNER_UNTERMINATED_COMMENT,
    E_SCANNER_UNTERMINATED_STRING,
)
from a816.parse.errors import ScannerException
from a816.parse.scanner import Scanner
from a816.parse.tokens import EOF, TokenType

opcodes = snes_opcode_table.keys()
opcodes_without_operand = get_opcodes_with_addressing(AddressingMode.none)
implied_only_opcodes = {name for name in opcodes_without_operand if len(snes_opcode_table[name]) == 1}

# Character sets for identifier parsing
IDENTIFIER_START_CHARS = "_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
IDENTIFIER_CHARS = IDENTIFIER_START_CHARS + "0123456789"


def lex_identifier(s: "Scanner") -> None:
    s.accept_run(IDENTIFIER_CHARS)

    if s.peek() == ":" and s.peek(1) != "=":
        s.emit(TokenType.LABEL)

        s.next()
        s.ignore()
    else:
        # handle scoped identifiers, including nested struct fields (a.b.c).
        while s.peek() == "." and s.peek(1) is not None and s.peek(1) in IDENTIFIER_START_CHARS:
            s.next()
            s.accept_run(IDENTIFIER_CHARS)

        s.emit(TokenType.IDENTIFIER)


def _lex_string(s: "Scanner", quote_char: str) -> None:
    """Scan a string delimited by quote_char."""
    # Capture position at the start of the string (the opening quote)
    start_position = s.get_position()
    c = s.next()
    while c != quote_char:
        if c == "\n" or c is None:
            raise ScannerException(
                "unterminated string literal",
                start_position,
                code=str(E_SCANNER_UNTERMINATED_STRING),
                hint=f"add a closing `{quote_char}` to terminate the string",
            )

        if c == "\\" and s.peek() == quote_char:
            s.next()

        c = s.next()

    s.emit(TokenType.QUOTED_STRING)


def lex_quoted_string(s: "Scanner") -> None:
    """Scan a single-quoted string."""
    _lex_string(s, "'")


def lex_double_quoted_string(s: "Scanner") -> None:
    """Scan a double-quoted string."""
    _lex_string(s, '"')


def lex_docstring(s: "Scanner", quote_char: str) -> None:
    """Scan a triple-quoted docstring delimited by quote_char."""
    # Capture position at the start of the docstring
    start_position = s.get_position()
    while True:
        c = s.next()
        if c is None:
            raise ScannerException(
                "unterminated docstring",
                start_position,
                code=str(E_SCANNER_UNTERMINATED_STRING),
                hint=f"docstrings end with three `{quote_char}` characters",
            )
        if c == "\\":
            # Skip escaped characters so they don't terminate the string early
            s.next()
            continue
        if c == quote_char and s.peek() == quote_char and s.peek(1) == quote_char:
            # Consume the remaining two quote characters of the terminator
            s.pos += 2
            break
    s.emit(TokenType.DOCSTRING)


def accept_opcode(s: "Scanner") -> bool:
    opcode_candidate = s.input[s.start : s.pos + 3].lower()
    is_ws = s.peek(3)
    if opcode_candidate in snes_opcode_table and is_ws in (
        " ",
        "\n",
        "\t",
        ".",
        EOF,
    ):
        s.pos += 3
        return True
    return False


def _lex_postfix_dot_chain(s: "Scanner") -> None:
    """Emit `.field` postfix tokens after `)` or `]` for struct field access.

    Each `.IDENT` becomes a DOT token followed by an IDENTIFIER token so the
    parser can attach the access to the preceding expression. Multiple dots
    chain (`).a.b.c`) for nested struct fields.
    """
    while s.peek() == "." and s.peek(1) is not None and s.peek(1) in IDENTIFIER_START_CHARS:
        s.next()
        s.emit(TokenType.DOT)
        s.accept_run(IDENTIFIER_CHARS)
        s.emit(TokenType.IDENTIFIER)


# Longest match first: two-char operators shadow their one-char prefixes.
EXPRESSION_OPERATORS: tuple[str, ...] = (
    "<<",
    ">>",
    "==",
    "!=",
    ">=",
    "<=",
    "+",
    "-",
    "*",
    "/",
    "%",
    "&",
    "|",
    "^",
    "~",
    "<",
    ">",
)


# First char -> candidate operators starting with it, longest first.
_OPERATORS_BY_FIRST_CHAR: dict[str, tuple[str, ...]] = {}
for _operator in EXPRESSION_OPERATORS:
    _OPERATORS_BY_FIRST_CHAR[_operator[0]] = (*_OPERATORS_BY_FIRST_CHAR.get(_operator[0], ()), _operator)


def lex_expression_operator(s: "Scanner") -> bool:
    """Emit one expression operator token. Shared by every expression context.

    `/*` opens a block comment, not a division, so it is left to the caller.
    """
    ch = s.peek()
    if ch == "/" and s.peek(1) == "*":
        return False
    for operator in _OPERATORS_BY_FIRST_CHAR.get(ch, ()):
        if s.accept_prefix(operator):
            s.emit(TokenType.OPERATOR)
            return True
    return False


def lex_expression(s: "Scanner") -> None:
    while s.pos < len(s.input):
        s.ignore_run(" ")
        for handler in _EXPRESSION_HANDLERS.get(s.peek(), ()):
            if handler(s):
                break
        else:
            break


def lex_standalone_expression(s: "Scanner") -> None:
    """Scanner entry state for a bare expression string (linker, pool specs).

    Unlike `lex_expression` inside an operand, nothing follows the
    expression, so a character it cannot consume is an error rather than
    the start of the next token.
    """
    lex_expression(s)
    if s.pos < len(s.input):
        s.next()
        raise ScannerException(
            f"invalid character `{s.input[s.start]}` in expression",
            s.get_position(),
            code=str(E_SCANNER_INVALID_INPUT),
        )


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
        _lex_postfix_dot_chain(s)
    elif p == "]":
        s.next()
        s.emit(TokenType.RBRAKET)
        _lex_postfix_dot_chain(s)

    s.ignore_run(" ")
    if s.accept(","):
        lex_opcode_index(s)


def lex_opcode_index(s: "Scanner") -> None:
    s.ignore()
    s.ignore_run(" ")
    if s.accept("xXyYsS"):
        s.emit(TokenType.ADDRESSING_MODE_INDEX)
    else:
        raise ScannerException(
            "invalid addressing index",
            s.get_position(),
            code=str(E_SCANNER_INVALID_INPUT),
            hint="expected X, Y, or S after `,` (e.g. `lda 0x12,x`)",
        )


def lex_opcode_size(s: "Scanner") -> None:
    s.ignore()
    if s.accept("bBwWlL"):
        s.emit(TokenType.OPCODE_SIZE)
        s.ignore_run(" ")

        return lex_operand(s)
    else:
        s.next()
        raise ScannerException(
            "invalid opcode size specifier",
            s.get_position(),
            code=str(E_SCANNER_INVALID_INPUT),
            hint="expected `.b`, `.w`, or `.l` after the opcode (e.g. `lda.w 0x1234`)",
        )


def _ends_statement(s: "Scanner") -> bool:
    """True when only blanks / a comment separate the cursor from a line end,
    end of input, or a closing `}` (`{ inc }` on one line)."""
    saved_pos = s.pos
    s.accept_run(" \t")
    if s.accept(";"):
        s.accept_run("\n\0", negate=True)
    ended = s.peek() in ("\n", "}", EOF)
    s.pos = saved_pos
    return ended


def _next_word_is_opcode(s: "Scanner") -> bool:
    """True when the next word on the line is a mnemonic (`nop nop`)."""
    saved_pos = s.pos
    s.accept_run(" \t")
    word_start = s.pos
    s.accept_run(IDENTIFIER_CHARS)
    word = s.input[word_start : s.pos].lower()
    s.pos = saved_pos
    return word in opcodes


def _is_naked_opcode(s: "Scanner", opcode_candidate: str) -> bool:
    """An operand-less opcode with nothing after it (no `.size` suffix).

    Implied-only mnemonics (`rts`, `nop`) never take an operand, so a
    following mnemonic starts a new statement (`{ nop nop }`).
    """
    if opcode_candidate not in opcodes_without_operand or s.peek() == ".":
        return False
    if _ends_statement(s):
        return True
    return opcode_candidate in implied_only_opcodes and _next_word_is_opcode(s)


def lex_opcode(s: "Scanner") -> None:
    opcode_candidate = s.input[s.start : s.pos].lower()
    if _is_naked_opcode(s, opcode_candidate):
        s.emit(TokenType.OPCODE_NAKED)
        return
    s.emit(TokenType.OPCODE)

    if s.accept("."):
        lex_opcode_size(s)

    s.ignore_run(" ")
    if opcode_candidate in ("mvn", "mvp"):
        lex_block_move_operands(s)
    else:
        lex_operand(s)


def lex_block_move_operands(s: "Scanner") -> None:
    """`mvn src, dst` / `mvp src, dst`: two expressions separated by a plain
    comma. Unlike the normal operand lexer, the comma is a `COMMA` token (a
    second operand), not an addressing-mode index (`,x`/`,y`/`,s`)."""
    s.ignore_run(" ")
    lex_expression(s)
    s.ignore_run(" ")
    if s.accept(","):
        s.emit(TokenType.COMMA)
    s.ignore_run(" ")
    lex_expression(s)


DIRECTIVE_NAMES = {
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
    "extern",
    "import",
    "debug",
    "label",
    "pool",
    "alloc",
    "relocate",
    "reclaim",
    "assert",
    "res",
    "reserve",
    "a8",
    "a16",
    "i8",
    "i16",
}


def lex_directive(s: "Scanner") -> None:
    """Scan a `.NAME` directive token.

    Caller consumed the leading dot. Reads the name + emits a KEYWORD
    token when `NAME` is in `DIRECTIVE_NAMES`; raises a clean
    diagnostic otherwise.
    """
    s.ignore()
    s.accept_run("abcdefghijklmnopqrstuvwxyz_0123456789")
    if s.current_token_text() in DIRECTIVE_NAMES:
        s.emit(TokenType.KEYWORD)
    else:
        raise ScannerException(
            f"unknown directive `.{s.current_token_text()}`",
            s.get_position(),
            code=str(E_SCANNER_UNKNOWN_KEYWORD),
            hint="see https://a816.ringum.net/directives/ for the list of supported `.` directives",
        )


# Back-compat alias for callers that imported the old name. Drop in
# a follow-up once the rest of the codebase migrates.
lex_keyword = lex_directive
KEYWORDS = DIRECTIVE_NAMES


def lex_number(s: Scanner) -> None:
    acceptable_values = {"b": "01", "o": "01234567", "x": "0123456789ABCDEFabcdef"}

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


_ASSIGNMENT_OPERATORS: tuple[tuple[str, TokenType], ...] = (
    (":=", TokenType.ASSIGN),
    ("@=", TokenType.AT_EQ),
    ("*=", TokenType.STAR_EQ),
)

_SINGLE_CHAR_TOKENS: dict[str, TokenType] = {
    ",": TokenType.COMMA,
    "(": TokenType.LPAREN,
    ")": TokenType.RPAREN,
    "[": TokenType.LBRAKET,
    "]": TokenType.RBRAKET,
    "=": TokenType.EQUAL,
}


def _lex_line_comment(s: Scanner) -> bool:
    if not s.accept(";"):
        return False
    while s.peek() not in ["\n", EOF]:
        s.next()
    s.emit(TokenType.COMMENT)
    return True


def _lex_number(s: Scanner) -> bool:
    if not s.accept("0123456789"):
        return False
    lex_number(s)
    return True


def _lex_assignment_operator(s: Scanner) -> bool:
    for prefix, kind in _ASSIGNMENT_OPERATORS:
        if s.accept_prefix(prefix):
            s.emit(kind)
            return True
    return False


def _lex_identifier_or_opcode(s: Scanner) -> bool:
    if not s.accept(IDENTIFIER_START_CHARS):
        return False
    s.backup()
    if accept_opcode(s):
        lex_opcode(s)
    else:
        lex_identifier(s)
    return True


def _lex_dot_keyword(s: Scanner) -> bool:
    if not s.accept("."):
        return False
    lex_keyword(s)
    return True


def _lex_triple_quoted_docstring(s: Scanner) -> bool:
    for quote in ('"', "'"):
        if s.peek() == quote and s.peek(1) == quote and s.peek(2) == quote:
            s.pos += 3
            lex_docstring(s, quote)
            return True
    return False


def _lex_quoted_string(s: Scanner) -> bool:
    if s.accept("'"):
        lex_quoted_string(s)
        return True
    if s.accept('"'):
        lex_double_quoted_string(s)
        return True
    return False


def _lex_brace(s: Scanner) -> bool:
    if s.accept("{"):
        s.emit(TokenType.DOUBLE_LBRACE if s.accept("{") else TokenType.LBRACE)
        return True
    if s.accept("}"):
        s.emit(TokenType.DOUBLE_RBRACE if s.accept("}") else TokenType.RBRACE)
        return True
    return False


def _lex_block_comment(s: Scanner) -> bool:
    start = s.get_position()
    if not s.accept_prefix("/*"):
        return False
    while not s.accept_prefix("*/"):
        if s.next() is None:
            raise ScannerException(
                "unterminated block comment",
                start,
                code=str(E_SCANNER_UNTERMINATED_COMMENT),
                hint="close it with `*/`",
            )
    s.emit(TokenType.COMMENT)
    return True


def _lex_single_char_token(s: Scanner) -> bool:
    ch = s.peek()
    kind = _SINGLE_CHAR_TOKENS.get(ch) if ch is not None else None
    if kind is None:
        return False
    s.next()
    s.emit(kind)
    return True


def _lex_identifier(s: Scanner) -> bool:
    if not s.accept(IDENTIFIER_START_CHARS):
        return False
    lex_identifier(s)
    return True


def _lex_paren(s: Scanner) -> bool:
    if s.accept("("):
        s.emit(TokenType.LPAREN)
        return True
    if s.accept(")"):
        s.emit(TokenType.RPAREN)
        _lex_postfix_dot_chain(s)
        return True
    return False


Handler = Callable[[Scanner], bool]
_DIGITS = "0123456789"
_QUOTES = "'\""
_OPERATOR_STARTS = "".join(_OPERATORS_BY_FIRST_CHAR)


def _by_first_char(handlers: tuple[tuple[Handler, str], ...]) -> dict[str, tuple[Handler, ...]]:
    """Each character mapped to the handlers that can start on it, in table order.

    A handler that cannot start on a character consumes nothing and returns
    False there, so skipping it changes nothing but the time spent asking.
    """
    table: dict[str, tuple[Handler, ...]] = {}
    for handler, starts in handlers:
        for ch in starts:
            table[ch] = (*table.get(ch, ()), handler)
    return table


# Expression tokens inside an opcode operand: no opcodes, directives or
# assignment tokens. Each helper returns True when it consumed input, and
# is listed with the characters it can start on.
_EXPRESSION_HANDLERS = _by_first_char(
    (
        (_lex_number, _DIGITS),
        (_lex_identifier, IDENTIFIER_START_CHARS),
        (_lex_triple_quoted_docstring, _QUOTES),
        (_lex_quoted_string, _QUOTES),
        (lex_expression_operator, _OPERATOR_STARTS),
        (_lex_paren, "()"),
    )
)

# Order matters: triple-quote before single-quote, `:=` / `@=` / `*=`
# before the expression operators that share their first char, line
# comment (`;`) before any other punctuation handler. Each helper returns
# True when it consumed input, and is listed with the characters it can
# start on.
_LEX_HANDLERS = _by_first_char(
    (
        (_lex_line_comment, ";"),
        (_lex_number, _DIGITS),
        (_lex_assignment_operator, ":@*"),
        (lex_expression_operator, _OPERATOR_STARTS),
        (_lex_identifier_or_opcode, IDENTIFIER_START_CHARS),
        (_lex_dot_keyword, "."),
        (_lex_triple_quoted_docstring, _QUOTES),
        (_lex_quoted_string, _QUOTES),
        (_lex_brace, "{}"),
        (_lex_block_comment, "/"),
        (_lex_single_char_token, "".join(_SINGLE_CHAR_TOKENS)),
    )
)


def lex_initial(s: Scanner) -> None:
    """Scanner entry state. Dispatches to a small handler for each token shape."""
    s.ignore_run(" \t\n")
    for handler in _LEX_HANDLERS.get(s.peek(), ()):
        if handler(s):
            return
    if s.next() is not None:
        raise ScannerException(
            f"invalid character `{s.input[s.start]}`",
            s.get_position(),
            code=str(E_SCANNER_INVALID_INPUT),
        )
