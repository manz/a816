# Error codes

Assembler diagnostics carry a stable error code so you can search docs
by code, suppress individual rules in tooling, and correlate output
across CLI runs. Some older codegen diagnostics still render as a plain
`error:` with a located caret but no code; they get codes as they are
touched.

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
- `E0115` opcode needs an operand. The opcode is followed by `}` or
  the end of input where its operand should be (`{ lda }`).

### Symbols

- `E0200` symbol not defined — the resolver couldn't find this
  symbol; the error includes a did-you-mean suggestion when a close
  match exists in scope.
- `E0201` external reference outside object mode.
- `E0202` expression failed to evaluate — likely a forward
  reference the resolver couldn't bind.
- `E0205` placement into an undeclared pool. `.alloc`, `.reserve`,
  `.relocate` and `.reclaim` name a pool that no `.pool` declared
  before them. The caret sits on the pool name.
- `E0206` `.reserve NAME as TYPE in POOL` names a struct type that
  isn't declared. The caret sits on the type name.
- `E0207` macro not defined. The hint suggests the closest defined
  macro name.
- `E0208` macro called with the wrong number of arguments.
- `E0209` symbol names a block, not a value: a block argument
  (`m({ ... })`) used where an expression is expected.
- `E0210` `:=` references a symbol not yet defined. `:=` evaluates
  its right-hand side immediately (typed binds need the address up
  front); use `=` for a forward reference.

### Codegen

- `E0300` node failed during emission — generic codegen failure.
- `E0301` unknown struct field type — `.struct` references an
  identifier that isn't a primitive or a previously-declared struct.
- `E0302` struct field self-reference — a struct cannot embed
  itself.
- `E0303` struct redefined.
- `E0304` typed bind references unknown struct type.
- `E0305` typed bind or cast base must evaluate to an address
  (`(expr as T)` with a string `expr`).
- `E0306` operand size mismatch.
- `E0307` addressing mode not supported by opcode.
- `E0308` conflicting `.map` declaration. Two modules (or a module
  and one it `.import`s) declare the same identifier with different
  ranges, mask, writable flag or mirror.
- `E0309` byte immediate does not fit in 8 bits: an explicit `.b`
  immediate whose value exceeds `0xFF`.
- `E0310` code emitted outside any placement. Under `a816 build`,
  a module (entrypoint included) emitted bytes before any `*=` and
  outside every `.alloc`. Wrap them in `.alloc` or set `*=` first.
- `E0311` `.import` inside a placement context. `.import` must sit in
  the file prelude, before the first `*=` and outside any `.alloc` body.
- `E0312` division or modulo by zero. The right-hand side of a `/` or
  `%` evaluated to 0; the caret points at the operator. In a relocation
  resolved at link time the linker reports `cannot evaluate expression`
  with reason `division by zero`.
- `E0315` branch target out of range: the displacement does not fit
  the branch's signed 8-bit (`bra`, `bne`, ...) or 16-bit (`brl`)
  offset. The caret sits on the target.
- `E0316` branch target has no ROM address. Relative branches are
  computed in ROM space; a RAM target cannot be reached.
- `E0317` address in a bank no `.map` region covers (and the
  configured ROM type doesn't back). The hint lists the mapped banks.
- `E0318` alloc does not fit in its pool. Says whether the alloc is
  larger than any range, the pool is fragmented or out of room, with
  a matching hint; the caret sits on the `.alloc` name.
- `E0319` operator applied to a string and a number.
- `E0320` `~` operand wider than 32 bits.

### Linker

- `E0400` duplicate global symbol.
- `E0401` unresolved external symbol.
- `E0402` relocation out of range.
- `E0403` relocation expression failed.
- `E0404` alloc does not fit in its pool at link time (the pool is
  shared across modules, so the allocator only runs once every `.o`
  is in). Same message and hint as `E0318`, plus the pool and, when
  the body emitted code, the `file:line` of its first instruction.
- `E0405` an alloc request names a pool that no linked object
  declares.

### I/O / config

- `E0500` file not found.
- `E0501` invalid project config.
- `E0502` `.include_ips` file is not an IPS patch (no `PATCH`
  header). An unreadable `.include_ips` path reports `E0500`.

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
