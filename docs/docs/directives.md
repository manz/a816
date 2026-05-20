# Directives reference

Quick reference for every assembler directive a816 understands. Each
section lists the syntax, what it emits (or doesn't), and a minimal
example.

## Layout

### `*=` — code position

Sets the *logical* address the next emitted byte targets. Drives where
the bytes land in the ROM image.

```ca65
*= 0x008000
    sei
    clc
    xce
```

### `@=` — reloc address

Sets the *runtime* address symbols resolve against, independent of
where the bytes are physically placed. Useful when code is copied to
RAM at runtime: emit at the ROM position with `*=`, but compute jumps
and label addresses for the RAM target with `@=`.

```ca65
*= 0x00C000   ; bytes go into ROM at C000
@= 0x7E2000   ; but symbols resolve as if running from WRAM 7E:2000
ram_routine:
    lda.w some_var
    rts
```

### `.map` — memory map

Selects the cartridge address mapping. Affects how `*=` translates
into a physical ROM offset.

```ca65
.map low_rom
.map low_rom_2
.map high_rom
```

## Symbols

### `name = expr` — constant

Defines a constant. Evaluated lazily; can reference externs (resolved
at link time).

```ca65
MAX_HP   = 0xFF
font_ptr = target + 0x40   ; target may be `.extern`
```

### `name := expr` — assign

Same shape as `=` but the resolver treats the binding as mutable
during a build (rebinds allowed). Prefer `=` unless you need this.

### `.label NAME = ADDR`

Names a constant address as a **label** without moving the position
counter and without emitting any bytes. Use it for original-ROM stubs,
WRAM scratch slots, hardware register aliases — anything you want
crash traces, the disassembler, and the LSP to symbolicate by name.

```ca65
"""Bank-2 hardware Mult8 entry. Input $26 * $28 → $2A. RTL."""
.label mult8_far = 0x02855C

"""WRAM byte at $7E:1BAE — field-menu HDMA channel-5 enable shadow."""
.label field_menu_hdma_enable = 0x1BAE
```

Differences vs `name = expr`:

| Property | `.label` | `=` (constant) |
|----------|----------|----------------|
| Position counter | untouched | untouched |
| Emits bytes | no | no |
| `.adbg` LABEL record | **yes** | no |
| `lookup_label(addr)` resolves | **yes** | no |
| Cross-module via `.extern` | yes | yes |
| Documentable (fluff) | yes (docstring above) | no |

The RHS must evaluate to an int at the current resolution pass —
external references are not allowed (use `.extern` for that).

### `.extern name`

Declares a symbol defined in another module. Required for cross-module
references. Sub-symbols (`name.sub`) need their own `.extern`. See
[Modules](modules.md).

```ca65
.extern external_func
.extern messages_vwf
.extern messages_vwf.init_commands_list
```

### `.struct Name { ... }`

Layout-only declaration; emits no bytes. Field names export as
`Name.field` byte offsets plus `Name.__size`. Primitive field types:
`byte`, `word`, `long` (24-bit), `dword` (32-bit). A field type can
also be the name of another previously declared struct, in which
case the nested layout flattens into dotted offsets
(`Outer.pos.x`, `Outer.pos.y`).

```ca65
.struct OAM {
    word x
    byte y
    byte tile
    byte attr
}

.struct Inner {
    word x
    word y
}
.struct Outer {
    byte tag
    Inner pos
    byte flags
}

; Bit fields — `uN` (any positive N) declares an N-bit field that
; packs into the surrounding byte run. Mixing with byte/word/long
; flushes the current byte before the primitive lands.
.struct INIDISP {
    u4 brightness
    u3 unused
    u1 force_blank
}
; → INIDISP.force_blank       = 0     (byte offset)
;   INIDISP.force_blank.mask  = 0x80  (pre-shifted)
;   INIDISP.force_blank.shift = 7     (LSB position)
;   INIDISP.__size            = 1
; → Outer.tag = 0, Outer.pos = 1, Outer.pos.x = 1, Outer.pos.y = 3,
;   Outer.flags = 5, Outer.__size = 6
```

#### Typed access: `as` casts and `:=` binds

A `(expr as T)` cast tags an address with a struct type so a postfix
`.field` resolves through the struct's layout. The two forms share one
mechanism:

```ca65
; Inline cast, single use.
    lda.w (0x2100 as PPU).OAMADDR    ; → lda.w $2102

; Typed bind, reusable across many accesses.
p := (0x7e0000 as OAM)
    lda.l p.x                         ; → lda.l $7e0000
    lda.l p.y                         ; → lda.l $7e0002

; Bare form (no parens) also works for `:=`.
q := 0x010000 as Pt
```

`p := (...)` eager-expands one constant per (possibly nested) field
of `T`, so `p.field` is just a flat symbol after the bind. Nested
struct fields chain cleanly: `(o as Outer).pos.y` and
`o.pos.y` both resolve to `base + Outer.pos + Inner.y`.

#### Auto-sized opcodes on typed accesses

When a typed instance is referenced directly as an operand
(`lda p.field`), the assembler picks the addressing mode (`lda` /
`lda.w` / `lda.l`) from the binding's base bank — no operand-string
guessing involved. The mapping is:

| Base value | Addressing mode |
|------------|-----------------|
| `< 0x100`   | direct page (`lda`) |
| `< 0x10000` | absolute (`lda.w`) |
| otherwise   | long (`lda.l`)     |

An explicit `.b` / `.w` / `.l` on the opcode always wins. Compound
operands (`p.field + 1`, raw addresses, casts) keep using the
existing operand-string heuristic.

If the field's declared width disagrees with the current REP/SEP
register width (e.g. `lda p.word_field` while `.a8` is in effect),
the assembler emits a warning suggesting the `rep` / `sep` flip
the user probably wants.

Lint hooks:

- `S001` — cast targets a struct type the file never declared.
- `S003` — `(p as T).field` when `p` is already bound as `T`.
- `S004` — same `(expr as T)` repeated more than once; promote to `:=`.

## Code

### `.scope name { ... }` and `{ ... }`

Named scopes export labels as `name.label`. Anonymous `{ ... }` blocks
keep labels strictly local; nothing inside leaks to the parent.

Inside any scope, names starting with `_` are LOCAL (private to the
module); other names are GLOBAL.

```ca65
.scope vwf {
    init:
        rts
    _private_helper:
        rts
}

; usable from outside
    jsr.l vwf.init
```

### `.macro name(args) { ... }`

Parameterised expansion. Arguments are textual at expansion time;
docstring as first body statement attaches to the macro.

```ca65
.macro store_byte_at(addr, val) {
    """Stash a byte at `addr`."""
    lda.b #val
    sta.l addr
}

store_byte_at(0x2100, 0x80)
```

### `.if expr { ... } else { ... }`

Static conditional. The expression is evaluated at assembly time;
the unselected branch isn't emitted.

```ca65
.if FEATURE_A == 1 {
    jsr.l feature_a_init
} else {
    jsr.l feature_a_stub
}
```

### `.for var := lo, hi { ... }`

Compile-time loop. Body is expanded once per integer in
`[lo, hi]`. `var` is a binding visible inside the body.

```ca65
.for i := 0, 7 {
    lda.b #i
    sta.l 0x2100 + i
}
```

## Data

### `.db` / `.dw` / `.dl` / `.dd`

Emit raw bytes / words / 24-bit longs / 32-bit dwords.

```ca65
.db 0x16, 0x20, 0x17, 0x20
.dw 0x2000, 0x2500
.dl 0x010000
```

### `.text "..."` and `.table "path"`

Encodes a string using the active character map. Set the map per
scope with `.table`. Strings expand `${VAR}` references against
defined symbols.

```ca65
.table "text/menus.tbl"
    .text "Hello"
    .text "Score: ${PLAYER_SCORE}"
```

### `.ascii "..."`

Emits the literal bytes of a string with no character-map translation.

### `.incbin "data.bin"`

Includes a binary file verbatim. Defines the named label *and*
`<label>__size` with the byte count.

```ca65
assets_intro_map:
.incbin "assets/intro.map"
; symbols emitted: assets_intro_map, assets_intro_map__size
```

### `.include "file.s"`

Lexically inlines the file at this position. Symbols defined inside
join the current scope. Use `.import` for module-style separation.

### `.include_ips "patch.ips"`

Replays the records of an existing IPS patch into the current build.

## Modules

See the dedicated [Modules](modules.md) page for `.import` / `.extern`
semantics, the build workflow, and prelude usage.

## Freespace pools

See the dedicated [Freespace pools](freespace-pools.md) page for the
full reference. Quick form:

### `.pool NAME { ... }`

Declares a named freespace pool with one or more ranges, optional
fill byte, and allocation strategy (`pack` | `order`).

### `.alloc NAME in POOL { body }`

Reserves space for `body` in the named pool; the allocator picks the
address and binds `NAME` there.

### `.relocate SYMBOL OLD_START OLD_END into POOL { body }`

Moves `SYMBOL` from `[OLD_START, OLD_END]` into the pool — old range
is reclaimed before the new body is placed.

### `.reclaim POOL START END`

Adds `[START, END]` to the named pool. Escape hatch for slack with
no original label.

## Comments and docstrings

```ca65
; line comment
/* block comment
   spans lines */

"""one-line docstring"""

"""
multi-line docstring
attached to the next public target
"""
my_label:
```

Docstrings attach to modules, scopes, macros, and labels. See
[Fluff (lint + format)](fluff.md) for the placement rules.
