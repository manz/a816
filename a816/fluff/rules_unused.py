"""Unused rules: a module-private name nothing in its translation unit references.

No other module can see a `_` name, so one its own module never uses is dead
code; the check is exact within the unit, with no whole-program analysis.
`F001` covers constants, macros, structs, pools, `.label` and `.reserve`;
`F002` covers code labels and scopes, opt-in because a bare `_label:` is often
a marker for the debugger or xdds.

The unit is the file, the files it includes, and every file that includes it
along with what those include: a name defined in a shared `.i` is used when
any of its includers uses it, and reported once, in the `.i`. References are
read from tokens, so anything spelling the name counts: operands, expressions,
macro arguments, `sizeof`, casts, `_x.field`, `scope._x`, and the `{expr}` /
`${NAME}` interpolations of `.debug` and `.text` strings.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from a816.fluff.core import Applicability, Diagnostic, Fix, LintContext, Rule, TextEdit, line_col_to_offset
from a816.parse.tokens import Token, TokenType

F001 = "F001"
F002 = "F002"
# The keyword before a name that defines it, and what the name is.
_DEFINING_KEYWORDS = {
    "macro": (F001, "macro"),
    "struct": (F001, "struct"),
    "pool": (F001, "pool"),
    "label": (F001, "label"),
    "reserve": (F001, "reservation"),
    "scope": (F002, "scope"),
}
_BLOCK_KINDS = frozenset({"macro", "struct", "pool", "scope"})
_INTERPOLATION_RE = re.compile(r"\{([^{}]*)\}")
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# (path, mtime) -> tokens; a project's files are rescanned only when they change.
_TOKENS: dict[tuple[str, int], list[Token]] = {}
# (path, mtime, include paths) -> the files its `.include`s resolve to.
_EDGES: dict[tuple[str, int, tuple[str, ...]], tuple[Path, ...]] = {}


@dataclass(frozen=True)
class _Definition:
    name: str
    code: str
    kind: str
    index: int  # into the file's tokens


class _UnusedPrivateName(Rule):
    """Shared body of F001 / F002: one scan per file, split by code."""

    def check(self, ctx: LintContext) -> Iterable[Diagnostic]:
        return (diag for diag in ctx.unused_hits() if diag.code == self.code)


class UnusedPrivateName(_UnusedPrivateName):
    code = F001
    description = "module-private constant / macro / struct / pool / `.label` / `.reserve` is never used"
    rationale = (
        "No other module can see a `_` name, so one its own module never references is dead. "
        "References through `.include` count both ways: a name in a shared `.i` is used when any "
        "file including it uses it. `.alloc _name` is a placement handle and never flagged. "
        "The unsafe fix deletes the definition."
    )
    bad = '"""Module."""\n_UNUSED = 3\n'
    good = '"""Module."""\n_STEP = 3\nlda #_STEP\n'


class UnusedPrivateLabel(_UnusedPrivateName):
    code = F002
    description = "module-private code label / scope is never used"
    rationale = (
        "Like F001 for `_label:` and `.scope _name`. Opt-in: a private label nothing branches to "
        "is often a marker for the debugger or xdds, so it runs only when selected by its exact "
        "code (`a816 check --select F002`), not by the `F` prefix."
    )
    bad = '"""Module."""\n_dead:\n    rts\n'
    good = '"""Module."""\n_loop:\n    bne _loop\n'
    opt_in = True


def unused_private_names(ctx: LintContext) -> list[Diagnostic]:
    """F001 / F002 hits for `ctx`'s file, first definition of each name only."""
    tokens = _scan(ctx.path, ctx.text)
    definitions = list(_definitions(tokens))
    if not definitions:
        return []
    own = {definition.index for definition in definitions}
    used = _references(tokens, skip=own)
    for path in _unit_files(ctx.path, tokens, ctx.include_paths_for_lookup or []):
        used |= _references(_file_tokens(path), skip=set())
    reported: set[str] = set()
    hits: list[Diagnostic] = []
    for definition in definitions:
        if definition.name in used or definition.name in reported:
            continue
        reported.add(definition.name)
        hits.append(_diagnostic(ctx, tokens, definition))
    return hits


def _diagnostic(ctx: LintContext, tokens: list[Token], definition: _Definition) -> Diagnostic:
    token = tokens[definition.index]
    position = token.position
    assert position is not None
    return Diagnostic(
        path=ctx.path,
        line=position.line + 1,
        column=position.column + 1,
        code=definition.code,
        message=f"module-private {definition.kind} `{definition.name}` is never used",
        fix=_delete_fix(ctx.text, tokens, definition),
    )


def _scan(path: Path, text: str) -> list[Token]:
    from a816.parse.scanner import Scanner
    from a816.parse.scanner_states import lex_initial

    return Scanner(lex_initial).scan(str(path), text)


def _file_tokens(path: Path) -> list[Token]:
    key = (str(path), path.stat().st_mtime_ns)
    if key not in _TOKENS:
        _TOKENS[key] = _scan(path, path.read_text(encoding="utf-8", errors="replace"))
    return _TOKENS[key]


def _definitions(tokens: list[Token]) -> Iterator[_Definition]:
    for index, token in enumerate(tokens):
        if not token.value.startswith("_"):
            continue
        if token.type is TokenType.LABEL:
            yield _Definition(token.value, F002, "label", index)
        elif token.type is TokenType.IDENTIFIER:
            found = _defined_by(tokens, index)
            if found is not None:
                yield _Definition(token.value, *found, index)


def _defined_by(tokens: list[Token], index: int) -> tuple[str, str] | None:
    """(code, kind) when `tokens[index]` is the name a statement defines."""
    previous = tokens[index - 1] if index else None
    if previous is not None and previous.type is TokenType.KEYWORD:
        return _DEFINING_KEYWORDS.get(previous.value)
    following = tokens[index + 1].type if index + 1 < len(tokens) else None
    if following in (TokenType.EQUAL, TokenType.ASSIGN) and _starts_statement(tokens, index):
        return F001, "constant"
    return None


def _starts_statement(tokens: list[Token], index: int) -> bool:
    if index == 0:
        return True
    previous = tokens[index - 1]
    return previous.type in (TokenType.LBRACE, TokenType.RBRACE) or _end_line(previous) != _line(tokens[index])


def _line(token: Token) -> int:
    position = token.position
    assert position is not None
    return position.line


def _end_line(token: Token) -> int:
    return _line(token) + token.value.count("\n")


def _references(tokens: list[Token], skip: set[int]) -> set[str]:
    """Every name segment `tokens` spell outside definitions: `a.b` names both."""
    names: set[str] = set()
    for index, token in enumerate(tokens):
        if index in skip:
            continue
        if token.type is TokenType.IDENTIFIER:
            names.update(token.value.split("."))
        elif token.type is TokenType.QUOTED_STRING:
            for match in _INTERPOLATION_RE.finditer(token.value):
                names.update(_WORD_RE.findall(match.group(1)))
    return names


def _unit_files(path: Path, tokens: list[Token], include_paths: list[Path]) -> set[Path]:
    """The other files sharing a translation unit with `path`: its includes,
    its includers (transitively), and what those include."""
    graph = _IncludeGraph(include_paths)
    here = path.resolve()
    roots = {here, *graph.includers_of(here, path)}
    files: set[Path] = set()
    for root in roots:
        files |= graph.closure(root, tokens if root == here else None)
    files.discard(here)
    return files


class _IncludeGraph:
    """`.include` edges, read from tokens, resolved the way the parser does:
    next to the including file first, then the include paths."""

    def __init__(self, include_paths: list[Path]) -> None:
        self._include_paths = include_paths

    def includes(self, path: Path, tokens: list[Token] | None = None) -> tuple[Path, ...]:
        if tokens is not None:
            return tuple(self._resolve(path, tokens))
        if not path.is_file():
            return ()
        key = (str(path), path.stat().st_mtime_ns, tuple(str(directory) for directory in self._include_paths))
        if key not in _EDGES:
            _EDGES[key] = tuple(self._resolve(path, _file_tokens(path)))
        return _EDGES[key]

    def _resolve(self, path: Path, tokens: list[Token]) -> Iterator[Path]:
        for keyword, target in pairwise(tokens):
            if keyword.type is TokenType.KEYWORD and keyword.value == "include" and target.value[:1] == '"':
                found = self._locate(path, target.value[1:-1])
                if found is not None:
                    yield found

    def _locate(self, path: Path, name: str) -> Path | None:
        for directory in (path.parent, *self._include_paths):
            candidate = directory / name
            if candidate.is_file():
                return candidate.resolve()
        return None

    def closure(self, root: Path, tokens: list[Token] | None) -> set[Path]:
        seen = {root}
        stack = list(self.includes(root, tokens))
        while stack:
            current = stack.pop()
            if current not in seen:
                seen.add(current)
                stack.extend(self.includes(current))
        return seen

    def includers_of(self, here: Path, path: Path) -> set[Path]:
        """Project files whose includes reach `here`."""
        if not path.is_file():
            return set()
        sources = _project_sources(path)
        reverse: dict[Path, set[Path]] = {}
        for source in sources:
            for target in self.includes(source):
                reverse.setdefault(target, set()).add(source)
        found: set[Path] = set()
        stack = [here]
        while stack:
            for includer in reverse.get(stack.pop(), ()):
                if includer not in found:
                    found.add(includer)
                    stack.append(includer)
        return found


def _project_sources(path: Path) -> list[Path]:
    """The sources of the project `path` belongs to: its `a816.toml` root,
    else its own directory (not below: a stray path must not walk a tree).

    Walked with `os.walk` rather than `walk_sources`: this runs once per
    linted file, and resolving every path there costs more than the lint."""
    from a816.config import find_a816_toml
    from a816.fluff.project import SKIPPED_DIRS, SOURCE_SUFFIXES

    toml = find_a816_toml(path.parent)
    root = (toml.parent if toml is not None else path.parent).resolve()
    sources: list[Path] = []
    for directory, subdirectories, files in os.walk(root):
        subdirectories[:] = [name for name in subdirectories if name not in SKIPPED_DIRS and name[:1] != "."]
        if toml is None:
            subdirectories.clear()
        sources.extend(Path(directory, name) for name in files if Path(name).suffix in SOURCE_SUFFIXES)
    return sorted(sources)


def _delete_fix(text: str, tokens: list[Token], definition: _Definition) -> Fix | None:
    """Unsafe: delete the defining statement's lines, a block one through its
    `}`. None when the statement shares a line with other code."""
    index = definition.index
    first = tokens[index] if _starts_statement(tokens, index) else tokens[index - 1]
    first_position = first.position
    assert first_position is not None
    start = line_col_to_offset(text, first_position.line + 1, 1)
    lead = line_col_to_offset(text, first_position.line + 1, first_position.column + 1)
    last = _statement_end(tokens, definition)
    if last is None or text[start:lead].strip(" \t."):
        return None
    end = line_col_to_offset(text, _end_line(last) + 2, 1)
    return Fix(
        edits=(TextEdit(start=start, end=end, replacement=""),),
        applicability=Applicability.UNSAFE,
        description=f"delete unused `{definition.name}`",
    )


def _statement_end(tokens: list[Token], definition: _Definition) -> Token | None:
    """The statement's last token, provided nothing else follows it on its line."""
    index = definition.index
    if definition.kind in _BLOCK_KINDS:
        last = _closing_brace(tokens, index)
    elif tokens[index].type is TokenType.LABEL:
        last = _label_line_end(tokens, index)
    else:
        last = _line_end(tokens, index)
    if last is None:
        return None
    following = tokens[tokens.index(last) + 1]
    if following.type is not TokenType.EOF and _line(following) == _end_line(last):
        return None
    return last


def _closing_brace(tokens: list[Token], index: int) -> Token | None:
    depth = 0
    for token in tokens[index:]:
        if token.type is TokenType.LBRACE:
            depth += 1
        elif token.type is TokenType.RBRACE:
            depth -= 1
            if depth == 0:
                return token
    return None


def _same_line(tokens: list[Token], index: int) -> list[Token]:
    """The tokens after `tokens[index]` on its line."""
    line = _line(tokens[index])
    out: list[Token] = []
    for token in tokens[index + 1 :]:
        if token.type is TokenType.EOF or _line(token) != line:
            break
        out.append(token)
    return out


def _line_end(tokens: list[Token], index: int) -> Token | None:
    """The last token of a one-line statement; None when a bracket stays open
    (the statement goes on below)."""
    rest = _same_line(tokens, index)
    opened = sum(token.type in (TokenType.LPAREN, TokenType.LBRAKET) for token in rest)
    closed = sum(token.type in (TokenType.RPAREN, TokenType.RBRAKET) for token in rest)
    if opened != closed:
        return None
    return rest[-1] if rest else tokens[index]


def _label_line_end(tokens: list[Token], index: int) -> Token | None:
    """A label alone on its line (a trailing comment allowed): code after it stays."""
    rest = _same_line(tokens, index)
    if any(token.type is not TokenType.COMMENT for token in rest):
        return None
    return rest[-1] if rest else tokens[index]
