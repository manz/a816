from enum import Enum, auto


class Position:
    line = 0
    column = 0
    filename = None

    def __init__(self, line, column, file):
        self.line = line
        self.column = column
        self.file = file

    def __str__(self):
        return f'{self.file.filename}:{self.line}:{self.column}'

    def get_line(self):
        return self.file.get(self.line)


class File:
    def __init__(self, filename):
        self.filename = filename
        self.lines = []

    def append(self, line):
        self.lines.append(line)

    def get(self, lineno):
        return self.lines[lineno]


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

    LEFT_SHIFT = auto()
    RIGHT_SHIFT = auto()

    DOUBLE_LBRACE = auto()
    DOUBLE_RBRACE = auto()


class Token:
    type = ''
    value = ''
    position = None
    int_value = None

    def __init__(self, type_, value=None, position=None):
        self.type = type_
        self.value = value
        self.position = position

    def __str__(self):
        return f'Token({self.type}, {self.value})'  # {self.position}'

    def display(self):
        if self.position is not None:
            if self.type == TokenType.EOF:
                print(self.position.file.lines[-1])
            else:
                print(str(self.position), self.type)
                print(self.position.get_line())
                print(' ' * self.position.column + '~' * len(self.value))


class TokenContext:
    def __init__(self, tokens):
        self.tokens = tokens

    def display(self):
        start_position = self.start_token


EOF = '\0'
