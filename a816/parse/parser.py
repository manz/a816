import logging
from collections.abc import Callable
from pathlib import Path

from a816.error_codes import E_PARSER_EXPECTED_TOKEN
from a816.parse.ast.nodes import AstNode
from a816.parse.errors import ParserSyntaxError
from a816.parse.tokens import Token, TokenType

# Human-readable names for token types — used in parser error messages
# instead of the raw `TokenType.X` repr that leaks Python internals.
_TOKEN_FRIENDLY_NAMES: dict[TokenType, str] = {
    TokenType.EOF: "end of input",
    TokenType.COMMENT: "comment",
    TokenType.LABEL: "label",
    TokenType.IDENTIFIER: "identifier",
    TokenType.QUOTED_STRING: "quoted string",
    TokenType.DOCSTRING: "docstring",
    TokenType.OPERATOR: "operator",
    TokenType.LPAREN: "`(`",
    TokenType.RPAREN: "`)`",
    TokenType.SHARP: "`#`",
    TokenType.RBRAKET: "`]`",
    TokenType.LBRAKET: "`[`",
    TokenType.RBRACE: "`}`",
    TokenType.LBRACE: "`{`",
    TokenType.ADDRESSING_MODE_INDEX: "addressing index (X/Y/S)",
    TokenType.OPCODE_SIZE: "opcode size (.b/.w/.l)",
    TokenType.OPCODE_NAKED: "opcode without operand",
    TokenType.OPCODE: "opcode",
    TokenType.COMMA: "`,`",
    TokenType.KEYWORD: "directive keyword",
    TokenType.NUMBER: "number literal",
    TokenType.STAR_EQ: "`*=`",
    TokenType.AT_EQ: "`@=`",
    TokenType.EQUAL: "`=`",
    TokenType.ASSIGN: "`:=`",
    TokenType.DOUBLE_LBRACE: "`{{`",
    TokenType.DOUBLE_RBRACE: "`}}`",
    TokenType.BOOLEAN: "boolean",
    TokenType.TYPE: "type identifier",
    TokenType.IMPORT: "`.import`",
    TokenType.FROM: "`from`",
    TokenType.DOT: "`.`",
}


def _token_label(token_type: TokenType) -> str:
    return _TOKEN_FRIENDLY_NAMES.get(token_type, token_type.name)


def _got_label(token: Token) -> str:
    """User-facing description of an actual token."""
    base = _token_label(token.type)
    if token.value and token.type not in (TokenType.EOF, TokenType.COMMENT):
        return f"{base} `{token.value}`"
    return base


logger = logging.getLogger("a816.parser")


class Parser:
    def __init__(self, tokens: list[Token], initial_state: "StateFunc", include_paths: list[Path] | None = None):
        self.tokens: list[Token] = tokens
        self.pos = 0
        self.initial_state: StateFunc = initial_state
        self.include_paths: list[Path] = include_paths or []
        # Errors collected during recovery. `parse_initial` appends every
        # ParserSyntaxError it survives so callers can render *all* of them
        # instead of only the first one.
        self.errors: list[ParserSyntaxError] = []

    def parse(self) -> list[AstNode]:
        try:
            return self.initial_state(self)
        except ParserSyntaxError as e:
            logger.debug("Parser syntax error: %s", e)
            raise

    def current(self) -> Token:
        try:
            return self.tokens[self.pos]
        except IndexError:
            return Token(TokenType.EOF, "")

    def peek(self) -> Token:
        try:
            token: Token = self.tokens[self.pos + 1]
            return token
        except IndexError:
            return Token(TokenType.EOF, "")

    def next(self) -> Token:
        token = self.current()
        self.pos += 1
        return token

    def backup(self) -> Token:
        token = self.current()
        self.pos -= 1
        return token


def expect_token(token: Token, token_type: TokenType) -> None:
    if token.type != token_type:
        raise ParserSyntaxError(
            f"expected {_token_label(token_type)}, found {_got_label(token)}",
            token,
            token_type,
            code=str(E_PARSER_EXPECTED_TOKEN),
        )


def expect_tokens(token: Token, token_types: list[TokenType]) -> None:
    if token.type not in token_types:
        expected = " or ".join(_token_label(t) for t in token_types)
        raise ParserSyntaxError(
            f"expected {expected}, found {_got_label(token)}",
            token,
            token_types[0],
            code=str(E_PARSER_EXPECTED_TOKEN),
        )


def accept_tokens(token: Token, token_types: list[TokenType]) -> bool:
    return token.type in token_types


def accept_token(token: Token, token_type: TokenType) -> bool:
    return token.type == token_type


StateFunc = Callable[[Parser], list[AstNode]]
