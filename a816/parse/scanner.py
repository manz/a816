import re
from bisect import bisect_right
from collections.abc import Callable
from functools import cache
from typing import Optional

from a816.parse.errors import ScannerException
from a816.parse.tokens import EOF, File, Position, Token, TokenType


@cache
def _run_pattern(candidates: str, negate: bool) -> re.Pattern[str]:
    """Compiled `[candidates]*` (or `[^candidates]*`) for `accept_run`."""
    body = "".join(re.escape(ch) for ch in candidates)
    return re.compile(f"[{'^' if negate else ''}{body}]*")


class Scanner:
    state: Optional["ScannerStateFunc"] = None
    start = 0
    pos = 0
    end = 0  # len(input), read by every primitive

    line = 0
    column = 0
    filename: str | None = None
    file: File
    input: str

    def __init__(self, initial_state: "ScannerStateFunc") -> None:
        self.initial_state = initial_state
        self.tokens: list[Token] = []
        self._line_starts: list[int] = [0]
        # Per-line recovery: collected `ScannerException`s. Callers (mzparser)
        # surface these as multi-error diagnostics; scan() never raises on
        # the first failure anymore. An aborted scan with no usable tokens
        # is signalled by an empty `tokens` list combined with non-empty
        # `errors` — but in practice the recovery loop keeps tokenising
        # past the offending byte.
        self.errors: list[ScannerException] = []

    def scan(self, filename: str, input_: str) -> list[Token]:
        self.file = File(filename)
        # Eagerly populate file lines so error-rendering can pull context
        # for positions beyond where the scanner halts (otherwise only the
        # lines already consumed up to the failure point would be available).
        self.file.lines = input_.split("\n")
        self.input = input_
        self.end = len(input_)
        self._line_starts = [0]
        offset = 0
        for line in self.file.lines[:-1]:
            offset += len(line) + 1
            self._line_starts.append(offset)
        self.state = self.initial_state
        self.tokens = []
        self.errors = []
        while self.pos < self.end:
            if self.state is None:
                break
            try:
                self.state(self)
            except ScannerException as e:
                self.errors.append(e)
                # Skip to the next newline so the next iteration has a clean
                # starting point. Stops on EOF too.
                self.accept_run("\n\0", negate=True)
                if self.peek() == "\n":
                    self.next()
                self._sync_start()
        self.emit(TokenType.EOF)
        return self.tokens

    def next(self) -> str | None:
        pos = self.pos
        if pos < self.end:
            self.pos = pos + 1
            return self.input[pos]
        return None

    def backup(self) -> None:
        self.pos -= 1

    def peek(self, k: int = 0) -> str:
        i = self.pos + k
        return self.input[i] if i < self.end else EOF

    def accept(self, candidates: str, negate: bool = False) -> bool:
        pos = self.pos
        ch = self.input[pos] if pos < self.end else EOF
        if (ch in candidates) != negate:
            if pos < self.end:
                self.pos = pos + 1
            return True
        return False

    def accept_prefix(self, prefix: str) -> bool:
        if self.input.startswith(prefix, self.pos):
            self.pos += len(prefix)
            return True
        return False

    def accept_run(self, candidates: str, negate: bool = False) -> None:
        pos = self.pos
        # Most runs are empty (no blank before the next token): settle
        # those on one character before reaching for the regex.
        if pos >= self.end or (self.input[pos] in candidates) == negate:
            return
        match = _run_pattern(candidates, negate).match(self.input, pos)
        if match is not None:
            self.pos = match.end()

    def ignore(self) -> None:
        self._sync_start()

    def ignore_run(self, candidates: str) -> None:
        pos = self.pos
        if pos < self.end and self.input[pos] in candidates:
            self.accept_run(candidates)
        self.start = self.pos

    def current_token_text(self) -> str:
        return self.input[self.start : self.pos]

    def get_token(self, token_type: TokenType) -> Token:
        line = bisect_right(self._line_starts, self.start) - 1
        column = self.start - self._line_starts[line]
        return Token.located(token_type, self.input[self.start : self.pos], line, column, self.file)

    def get_position(self) -> Position:
        line = bisect_right(self._line_starts, self.start) - 1
        return Position(line, self.start - self._line_starts[line], self.file)

    def emit(self, token_type: TokenType) -> None:
        self.tokens.append(self.get_token(token_type))
        self.start = self.pos

    def _sync_start(self) -> None:
        self.start = self.pos


ScannerStateFunc = Callable[[Scanner], None]
