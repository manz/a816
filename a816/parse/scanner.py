from typing import List
from a816.parse.errors import ScannerException
from a816.parse.tokens import Position, TokenType, Token, EOF, File


class Scanner:
    input = None
    state = None
    start = 0
    pos = 0

    line = 0
    column = 0
    filename = None

    def __init__(self, initial_state):
        self.initial_state = initial_state
        self.tokens = []
        self.line_offset = 0
        self.file = None
        self.current_line = 0
        self.errors = []

    def add_error(self, scanner_exception):
        print(str(scanner_exception))
        position = scanner_exception.position
        position_str = str(position)
        line = position.get_line()
        print(f'{position_str} :\n{line}')
        print(' ' * position.column + '~')

    def scan(self, filename, input_) -> List[Token]:
        self.file = File(filename)
        self.input = input_
        self.state = self.initial_state
        self.tokens = []
        while self.pos < len(self.input):
            if self.state is not None:
                try:
                    self.state(self)
                except ScannerException as e:
                    # consume the rest of the current line
                    self.accept_run('\n\0', negate=True)
                    self._handle_line()
                    raise e
            else:
                break
        self.emit(TokenType.EOF)
        self._handle_line()
        return self.tokens

    def _handle_line(self):
        if self.line_offset <= self.pos:
            self.file.append(self.input[self.line_offset:self.pos])
            self.line_offset = self.pos + 1
            self.current_line += 1

    def next(self):
        if self.pos < len(self.input):
            data = self.input[self.pos]
            if data == '\n':
                self._handle_line()
            self.pos += 1

            return data
        else:
            return None

    def backup(self):
        self.pos -= 1

    def peek(self, k=0):
        try:
            ch = self.input[self.pos + k]
            return ch
        except IndexError:
            return EOF

    def accept(self, candidates, negate=False):
        ch = self.peek()
        if ch is None:
            return False
        else:
            result = ch in candidates
            if negate:
                result = not result

        if result is True:  # and not negate:
            self.next()
        return result

    def accept_prefix(self, prefix):
        if self.input[self.pos:self.pos + len(prefix)] == prefix:
            self.pos += len(prefix)
            return True

    def accept_run(self, candidates, negate=False):
        while self.accept(candidates, negate):
            # Accepts candidates until a non matching char is found.
            pass

    def ignore(self):
        self.start = self.pos

    def ignore_run(self, candidates):
        self.accept_run(candidates)
        self.ignore()

    def current_token_text(self):
        return self.input[self.start:self.pos]

    def get_token(self, token_type: TokenType):
        return Token(token_type, self.current_token_text(),
                     self.get_position())

    def get_position(self):
        return Position(self.current_line, self.start - self.line_offset, self.file)

    def emit(self, token_type: TokenType):
        self.tokens.append(
            self.get_token(token_type))
        self.start = self.pos
