# Error codes

Every user-facing assembler diagnostic carries a stable error code so
you can search docs by code, suppress individual rules in tooling, and
correlate output across CLI runs.

## Anatomy

```
error[E0200]: `my_routime` is not defined in the current scope
  --> dym.s:4:11
  |
3 |     rts
4 |     jsr.l my_routime
  |           ^^^^^^^^^^
5 |
  = hint: did you mean `my_routine`?
```

- `error[CODE]` — severity + stable identifier.
- `--> file:line:column` — the failure location.
- Source block with `±1` context lines and a caret pointing at the
  offending span.
- Optional `= hint:` / `= note:` lines with fix suggestions.

When multiple errors come out of one parse pass they are rendered as
separate blocks separated by a blank line.

## Categories

- `E0001..E0099` — scanner / lexing.
- `E0100..E0199` — parser.
- `E0200..E0299` — symbol resolution.
- `E0300..E0399` — codegen.
- `E0400..E0499` — linker / object files.
- `E0500..E0599` — I/O / config.

## Code catalog

### Scanner

- `E0001` invalid input character — the scanner met a character it
  doesn't know how to start a token with.
- `E0002` unterminated string literal — close the string with the
  matching quote character.
- `E0003` unknown directive keyword — `.directive` not in the
  supported set; see [directives.md](directives.md).

### Parser

- `E0100` unexpected token — generic structural failure.
- `E0101` missing expected token — the parser knew what it wanted
  next but found something else.
- `E0102` invalid expression — the expression couldn't be parsed at
  the given position.
- `E0103` duplicate struct field — each `.struct` field name must
  be unique within the block.
- `E0104` typed-cast bind requires `:=` — use `name := expr as T`
  instead of `=`.
- `E0105` field access requires typed cast — `(expr).field` only
  works on a typed cast: `(expr as Type).field`.
- `E0106` unknown directive attribute — the directive doesn't
  accept the attribute name.
- `E0107` pool declares no ranges — every `.pool` needs at least
  one `range LO HI`.
- `E0108` unknown pool strategy — accepted values: `pack`, `order`.
- `E0109` include file unreadable — the path resolution failed.

### Symbols

- `E0200` symbol not defined — the resolver couldn't find this
  symbol; the error includes a did-you-mean suggestion when a close
  match exists in scope.
- `E0201` external reference outside object mode.
- `E0202` expression failed to evaluate — likely a forward
  reference the resolver couldn't bind.

### Codegen

- `E0300` node failed during emission — generic codegen failure.
- `E0301` unknown struct field type — `.struct` references an
  identifier that isn't a primitive or a previously-declared struct.
- `E0302` struct field self-reference — a struct cannot embed
  itself.
- `E0303` struct redefined.
- `E0304` typed bind references unknown struct type.
- `E0305` typed bind base must evaluate to an address.
- `E0306` operand size mismatch.
- `E0307` addressing mode not supported by opcode.

### Linker

- `E0400` duplicate global symbol.
- `E0401` unresolved external symbol.
- `E0402` relocation out of range.
- `E0403` relocation expression failed.

### I/O / config

- `E0500` file not found.
- `E0501` invalid project config.

## LSP integration

The `a816-lsp-server` publishes diagnostics with the same `code` and
appends the `hint` to the message. Editors that recognise the `code`
field (VS Code, Helix, Neovim with `vim.diagnostic`) render it as the
familiar inline chip.

## Suppressing noise

There is no global suppression knob for `E*` errors — they signal real
failures, not style issues. Style-style suppression lives on the
`fluff` side (`; noqa: <RULE>` for `DOC*` / `S*` / `N*`); see
[fluff.md](fluff.md).
