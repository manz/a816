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
# path -> (mtime, tokens); a project's files are rescanned only when they
# change, and a rescan replaces the entry so a long-lived LSP does not grow.
_TOKENS: dict[str, tuple[int, list[Token]]] = {}
# (path, include paths) -> (mtime, the files its `.include`s resolve to).
_EDGES: dict[tuple[str, tuple[str, ...]], tuple[int, tuple[Path, ...]]] = {}


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
    description = "private (`_`) constant / macro / struct / pool / `.label` / `.reserve` is never used"
    rationale = (
        "No other module can see a `_` name, so one its own module never references is dead. "
        "References through `.include` count both ways: a name in a shared `.i` is used when any "
        "file including it uses it. `.alloc _name` is a placement handle and never flagged. "
        "The unsafe fix deletes the definition; a `.reserve` has none, since removing it moves "
        "every later reservation in its pool."
    )
    bad = '"""Module."""\n_UNUSED = 3\n'
    good = '"""Module."""\n_STEP = 3\nlda #_STEP\n'


class UnusedPrivateLabel(_UnusedPrivateName):
    code = F002
    description = "private (`_`) code label / scope is never used"
    rationale = (
        "Like F001 for `_label:` and `.scope _name`. Opt-in: a private label nothing branches to "
        "is often a marker for the debugger or xdds, so it runs only when selected by its exact "
        "code (`a816 check --select F002`), not by the `F` prefix. A name reached only through "
        "debug symbols (a test hooking `module._label`) looks unused. The fix unwraps a scope "
        "into an anonymous `{ }`, since its body emits bytes."
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
    unused: dict[str, _Definition] = {}
    for definition in definitions:
        if definition.name not in used:
            unused.setdefault(definition.name, definition)
    fixes = _fixes(ctx.text, tokens, list(unused.values()))
    return [_diagnostic(ctx, tokens, definition, fixes[definition.index]) for definition in unused.values()]


def _diagnostic(ctx: LintContext, tokens: list[Token], definition: _Definition, fix: Fix | None) -> Diagnostic:
    token = tokens[definition.index]
    position = token.position
    assert position is not None
    return Diagnostic(
        path=ctx.path,
        line=position.line + 1,
        column=position.column + 1,
        code=definition.code,
        message=f"private {definition.kind} `{definition.name}` is never used",
        fix=fix,
    )


def _scan(path: Path, text: str) -> list[Token]:
    from a816.parse.scanner import Scanner
    from a816.parse.scanner_states import lex_initial

    return Scanner(lex_initial).scan(str(path), text)


def _file_tokens(path: Path) -> list[Token]:
    stamp = path.stat().st_mtime_ns
    cached = _TOKENS.get(str(path))
    if cached is None or cached[0] != stamp:
        cached = _TOKENS[str(path)] = (stamp, _scan(path, path.read_text(encoding="utf-8", errors="replace")))
    return cached[1]


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
        key = (str(path), tuple(str(directory) for directory in self._include_paths))
        stamp = path.stat().st_mtime_ns
        cached = _EDGES.get(key)
        if cached is None or cached[0] != stamp:
            cached = _EDGES[key] = (stamp, tuple(self._resolve(path, _file_tokens(path))))
        return cached[1]

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


def _fixes(text: str, tokens: list[Token], definitions: list[_Definition]) -> dict[int, Fix | None]:
    """Unsafe fixes, by definition token index. A definition is deleted (its
    lines, a block one through its `}`, with the comment directly above it),
    a scope unwrapped since its body emits bytes. None when the statement
    shares a line with other code, and for a reservation, whose removal moves
    every later one in its pool."""
    fixes: dict[int, Fix | None] = {}
    deletions: list[tuple[int, int, _Definition]] = []
    for definition in definitions:
        fixes[definition.index] = None
        if definition.kind == "scope":
            fixes[definition.index] = _unwrap_fix(text, tokens, definition)
        elif definition.kind != "reservation":
            span = _deletion(text, tokens, definition)
            if span is not None:
                deletions.append((*span, definition))
    for start, end, definition in _without_blank_runs(text, sorted(deletions, key=lambda item: item[0])):
        fixes[definition.index] = Fix(
            edits=(TextEdit(start=start, end=end, replacement=""),),
            applicability=Applicability.UNSAFE,
            description=f"delete unused `{definition.name}`",
        )
    return fixes


def _statement_start(text: str, tokens: list[Token], index: int) -> tuple[int, int]:
    """(line start, statement start) offsets of the statement defining `tokens[index]`."""
    first = tokens[index] if _starts_statement(tokens, index) else tokens[index - 1]
    position = first.position
    assert position is not None
    return (
        line_col_to_offset(text, position.line + 1, 1),
        line_col_to_offset(text, position.line + 1, position.column + 1),
    )


def _deletion(text: str, tokens: list[Token], definition: _Definition) -> tuple[int, int] | None:
    """The lines a deletion removes, the comment directly above included."""
    start, lead = _statement_start(text, tokens, definition.index)
    last = _statement_end(tokens, definition)
    if last is None or text[start:lead].strip(" \t."):
        return None
    return _attached_comment(text, start), line_col_to_offset(text, _end_line(last) + 2, 1)


def _unwrap_fix(text: str, tokens: list[Token], definition: _Definition) -> Fix | None:
    """`.scope _name {` becomes an anonymous `{`: the body still emits, and its
    labels stay local to the block."""
    start, lead = _statement_start(text, tokens, definition.index)
    indent = text[start:lead]
    brace = next((token for token in tokens[definition.index :] if token.type is TokenType.LBRACE), None)
    if brace is None or indent.strip(" \t."):
        return None
    brace_position = brace.position
    assert brace_position is not None
    end = line_col_to_offset(text, brace_position.line + 1, brace_position.column + 1) + 1
    return Fix(
        edits=(TextEdit(start=start, end=end, replacement=indent.rstrip(".") + "{"),),
        applicability=Applicability.UNSAFE,
        description=f"unwrap unused scope `{definition.name}`",
    )


def _line_start(text: str, offset: int) -> int:
    """Start of the line before the one starting at `offset`."""
    return text.rfind("\n", 0, offset - 1) + 1


def _next_line(text: str, offset: int) -> int:
    """Start of the line after the one holding `offset`."""
    newline = text.find("\n", offset)
    return len(text) if newline < 0 else newline + 1


def _attached_comment(text: str, start: int) -> int:
    """Move `start` up over the `;` lines directly above it."""
    while start and text[_line_start(text, start) : start].lstrip().startswith(";"):
        start = _line_start(text, start)
    return start


def _is_blank(text: str, start: int, end: int) -> bool:
    return not text[start:end].strip()


def _without_blank_runs(
    text: str, deletions: list[tuple[int, int, _Definition]]
) -> Iterator[tuple[int, int, _Definition]]:
    """Widen sorted deletions so they leave no longer run of blank lines than
    the text had. Deletions only blank lines apart form a group: the later
    ones take the blank lines above them, and the first takes those above the
    group when a blank line (or the end of the file) follows it. Each edit
    stays on its own lines, so applying only some of them never merges two
    blank runs either."""
    groups: list[list[tuple[int, int, _Definition]]] = []
    for deletion in deletions:
        if groups and _is_blank(text, groups[-1][-1][1], deletion[0]):
            groups[-1].append(deletion)
        else:
            groups.append([deletion])
    for group in groups:
        end = group[-1][1]
        below = end
        while below < len(text) and _is_blank(text, below, _next_line(text, below)):
            below = _next_line(text, below)
        start = group[0][0]
        if below != end or below == len(text):
            while start and _is_blank(text, _line_start(text, start), start):
                start = _line_start(text, start)
        previous_end = start
        for index, (_, deletion_end, definition) in enumerate(group):
            last = index == len(group) - 1
            yield previous_end, below if last and below == len(text) else deletion_end, definition
            previous_end = deletion_end


def _statement_end(tokens: list[Token], definition: _Definition) -> Token | None:
    """The statement's last token, provided nothing else follows it on its line."""
    index = definition.index
    if definition.kind in _BLOCK_KINDS:
        last = _closing_brace(tokens, index)
    elif tokens[index].type is TokenType.LABEL:
        last = _label_line_end(tokens, index)
        if last is not None:
            last = _docstring_below(tokens, last)
    else:
        last = _line_end(tokens, index)
    if last is None:
        return None
    following = tokens[tokens.index(last) + 1]
    if following.type is not TokenType.EOF and _line(following) == _end_line(last):
        return None
    return last


def _docstring_below(tokens: list[Token], last: Token) -> Token:
    """The docstring on the line right under a label documents it: it goes too."""
    following = tokens[tokens.index(last) + 1]
    if following.type is TokenType.DOCSTRING and _line(following) == _end_line(last) + 1:
        return following
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
