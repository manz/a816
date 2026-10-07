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
- `E0120` struct array count must be a positive integer
  (`byte[0] x`); the caret sits on the count.
- `E0121` bit-field struct fields cannot be arrays (`u4[2] x`).
- `E0122` field initialized twice in one `.istruct` (or nested `{ }`)
  initializer; the caret sits on the second entry.
- `E0123` string inside a `[...]` initializer list. A byte array takes
  the string itself: `name = "TEXT"`.

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
  and one it `.import`s, or a module and the `a816.toml` bus map)
  declare the same identifier with different ranges, mask, writable
  flag or mirror.
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
- `E0313` an unsized operand names a symbol resolved at link (`jmp target`
  with `target` from another module): its value is unknown at compile time,
  so the form can't be chosen. Write the size; the hint lists the forms the
  opcode has (`jmp.w` in the same bank, `jmp.l` across). Register-sized
  immediates and single-form opcodes (`pea`, `rep`) need none.
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
- `E0321` `sizeof(...)` / `countof(...)` names nothing visible here it
  can size (a struct, struct field, reservation or named alloc), `sizeof`
  names a bit field, or `countof` names a field that is not an array.
  The caret sits on the argument; the hint suggests a close name or the
  `.import` that would make it visible.
- `E0330` `.istruct` names a struct type that is not declared (or
  imported) yet; the caret sits on the type.
- `E0331` `.istruct` initializer names a field the struct does not
  have; the hint lists the struct's fields.
- `E0332` initializer value does not fit the field: scalars take an
  expression, byte arrays a string or `[...]`, other arrays `[...]`,
  struct fields `{ ... }`.
- `E0333` string or list initializer longer than its array field.
- `E0334` non-ASCII character in a string initializer.
- `E0335` initialized bit-field run wider than 32 bits.
- `E0336` a `cross_bank` alloc body holds something other than data
  (code or a label); see
  [Freespace pools](freespace-pools.md#cross_bank-data-blobs-across-bank-edges).

### Linker

- `E0400` duplicate global symbol. Names each definition: the defining
  module (its source, or its object for a constant-only module) and value.
- `E0401` unresolved external symbol.
- `E0402` relocation out of range.
- `E0403` relocation expression failed.
- `E0404` alloc does not fit in its pool at link time (the pool is
  shared across modules, so the allocator only runs once every `.o`
  is in). Same message and hint as `E0318`, plus the pool and, when
  the body emitted code, the `file:line` of its first instruction.
- `E0405` an alloc request names a pool that no linked object
  declares.
- `E0406` reservations from two different `bss` pools share memory.
  Lists every clash with both reservations, their spans and their
  `file:line`. Memory used in turns goes in one pool's `contexts`
  (see [Freespace pools](freespace-pools.md#memory-pools-bss-and-contexts)).
- `E0407` a `.assert` is false once every address is final. Lists each
  failed assert with its message, expression and `file:line`.
- `E0408` two placed blocks would write the same ROM bytes. Names both
  blocks (an alloc and its pool, or an anonymous block by address) with
  their sizes, addresses and `file:line`, and the bytes they share.
  Pools place around every pin inside their ranges, so this is two pins
  on the same bytes, or two pools whose ranges hand out the same bytes.
  `--overlap-mode warn` reports it and builds anyway.

### I/O / config

- `E0500` file not found.
- `E0501` invalid project config: `a816.toml` is not valid TOML (a
  repeated `[map.N]` table is one way to get there).
- `E0502` `.include_ips` file is not an IPS patch (no `PATCH`
  header). An unreadable `.include_ips` path reports `E0500`.
- `E0503` `[experimental]` in `a816.toml` is not a table, or one of
  its flags is not `true` / `false`.
- `E0504` `a816.toml` still sets `mapper`, which was removed: the
  message names the `board` to write instead.
- `E0505` malformed `[map.N]` entry: not a table, an unknown key, a
  missing `address`, or two keys spelling the same number (`[map.1]`
  and `[map.0x1]`).
- `E0506` a `[map.N]` value is invalid: `N`, `mask` and `base` are
  integers, `address` is a `BANKS:WINDOW` hex string
  (`"00-3f,80-bf:8000-ffff"`), `writable` a boolean; or `rom_size` is
  missing (with a read-only region), not an integer, or not positive.
- `E0509` `board` names no board in `boards.bml`; the message
  suggests close names.

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
