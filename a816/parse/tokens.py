from enum import Enum, auto
from typing import Any, List, Optional


class File:
    def __init__(self, filename: str):
        self.filename = filename
        self.lines: List[str] = []

    def append(self, line: str) -> None:
        self.lines.append(line)

    def get(self, lineno: int) -> str:
        return self.lines[lineno]


class Position:
    line = 0
    column = 0

    def __init__(self, line: int, column: int, file: File) -> None:
        self.line = line
        self.column = column
        self.file = file

    def __str__(self) -> str:
        return f"{self.file.filename}:{self.line}:{self.column}"

    def get_line(self) -> str:
        return self.file.get(self.line)


class TokenType(Enum):
    EOF = auto()
    COMMENT = auto()
    LABEL = auto()
    IDENTIFIER = auto()
    QUOTED_STRING = auto()
    OPERATOR = auto()
    LPAREN = auto()
    RPAREN = auto()
    SHARP = auto()
    RBRAKET = auto()
    LBRAKET = auto()
    RBRACE = auto()
    LBRACE = auto()
    ADDRESSING_MODE_INDEX = auto()
    OPCODE_SIZE = auto()
    OPCODE_NAKED = auto()
    OPCODE = auto()

    COMMA = auto()

    KEYWORD = auto()
    NUMBER = auto()

    STAR_EQ = auto()
    AT_EQ = auto()

    EQUAL = auto()

    ASSIGN = auto()

    DOUBLE_LBRACE = auto()
    DOUBLE_RBRACE = auto()

    MULTILINE_COMMENT_START = auto()
    MULTILINE_COMMENT_END = auto()

    BOOLEAN = auto()

    TYPE = auto()


class Token:
    def __init__(self, type_: TokenType, value: str, position: Optional[Position] = None) -> None:
        self.type: TokenType = type_
        self.value: str = value
        self.position: Optional[Position] = position

    def __repr__(self) -> str:
        return f"Token({self.type}, {self.value})"

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Token):
            return False

        return self.type == other.type and self.value == other.value

    def display(self) -> None:
        print(self.trace())

    def trace(self) -> Optional[str]:
        trace = None
        if self.position is not None:
            if self.type == TokenType.EOF:
                line = self.position.file.lines[-1]
            else:
                line = self.position.get_line()
            trace = f"""
{self.position} {self.type}
{line}
{' ' * self.position.column}{'^' * len(self.value)}"""
        return trace


EOF = "\0"
