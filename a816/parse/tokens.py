from enum import Enum, auto


class File:
    def __init__(self, filename: str):
        self.filename = filename
        self.lines: list[str] = []

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
    DOCSTRING = auto()
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

    IMPORT = auto()

    FROM = auto()

    DOT = auto()


class Token:
    """A scanned token. Its location is stored flat; `position` builds a `Position` on demand.

    A build keeps hundreds of thousands of tokens alive (the AST and nodes hold
    them as `file_info`); storing line/column/file inline instead of a separate
    `Position` per token halves the long-lived objects the cyclic GC re-walks.
    """

    __slots__ = ("_column", "_file", "_line", "type", "value")
    _line: int
    _column: int
    _file: File | None

    def __init__(self, type_: TokenType, value: str, position: Position | None = None) -> None:
        self.type: TokenType = type_
        self.value: str = value
        self.position = position

    @classmethod
    def located(cls, type_: TokenType, value: str, line: int, column: int, file: File) -> "Token":
        """Build a token at `line`/`column` of `file` without allocating a `Position`."""
        token = cls.__new__(cls)
        token.type = type_
        token.value = value
        token._line = line
        token._column = column
        token._file = file
        return token

    @property
    def position(self) -> Position | None:
        if self._file is None:
            return None
        return Position(self._line, self._column, self._file)

    @position.setter
    def position(self, position: Position | None) -> None:
        if position is None:
            self._line, self._column, self._file = 0, 0, None
        else:
            self._line, self._column, self._file = position.line, position.column, position.file

    @property
    def end_position(self) -> Position | None:
        """Position one column past the last character of `value`.

        Derived from `position` + the shape of `value`. Multi-line
        tokens (docstrings, block comments) walk past every `\\n` and
        the end column counts characters after the last newline.
        Fluff fix builders that need a byte range use this to bound
        the replacement without re-scanning source.
        """
        if self.position is None:
            return None
        newlines = self.value.count("\n")
        if newlines == 0:
            return Position(self.position.line, self.position.column + len(self.value), self.position.file)
        last_segment = self.value.rsplit("\n", 1)[1]
        return Position(self.position.line + newlines, len(last_segment), self.position.file)

    def __repr__(self) -> str:
        return f"Token({self.type}, {self.value})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Token):
            return False

        return self.type == other.type and self.value == other.value

    def display(self) -> None:
        print(self.trace())

    def trace(self) -> str | None:
        trace = None
        if self.position is not None:
            if self.type == TokenType.EOF:
                line = self.position.file.lines[-1]
            else:
                line = self.position.get_line()
            trace = f"""
{self.position} {self.type}
{line}
{" " * self.position.column}{"^" * len(self.value)}"""
        return trace


EOF = "\0"
