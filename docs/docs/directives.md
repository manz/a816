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

### Write-overlap detection

When two `*=` regions (or one `*=` block plus a `.alloc` placement,
etc.) produce byte spans that share addresses, the assembler emits a
diagnostic so a routine that silently grew past its expected end is
caught early. **Default mode is `error`**: the build fails on the
first overlap, naming both byte ranges. Override with
`--overlap-mode warn` (logged, build continues) or
`--overlap-mode off` (silent) on the CLI, or via
`Program(overlap_mode=...)` from the Python API.

The check runs on the final link (`a816 build`, object + link), across
every module's sections, so two modules pinning bytes at the same spot
fail the build too. Addresses in the message are ROM file offsets. On
error no output file is written.

```
WARNING write at $000004..$00000b overlaps previous write at
        $000000..$000009 ($000004..$000009 would be silently overwritten)
```

### `.map` — memory map

Declares one bus region. Affects how `*=` / `.alloc` addresses
translate into a physical ROM offset and which banks are writable.

```ca65
.map identifier=1 bank_range=0xc0, 0xfd addr_range=0x0000, 0xffff mask=0x10000 mirror_bank_range=0x40, 0x7d
.map identifier=3 bank_range=0x7e, 0x7f addr_range=0x0000, 0xffff mask=0x10000 writable=1
```

Without any region the bus falls back to the `-m` default (LoROM).
The layout is usually project-wide: declare it once in `a816.toml`
(`mapper` / `[[map]]`) instead of repeating it in every module.

## Expressions

Every place that takes an expression (opcode operands, `name =`,
`name :=`, `.db` / `.dw` / `.dl`, `.if`, `.for` bounds) accepts the
same operators with the same results, and so does the linker when it
resolves an expression that references an `.extern`.

### Literals

| Form       | Example        | Value |
|------------|----------------|-------|
| decimal    | `42`           | 42    |
| hex        | `0x2A`         | 42    |
| binary     | `0b101010`     | 42    |
| octal      | `0o52`         | 42    |
| string     | `"abc"`        | compared with `==` / `!=` only |

Prefixes are lowercase. `$2A` and `%101010` are not literals.

### Operators

Tightest first. Binary operators of the same level are left-associative.

| Level | Operators              | Meaning |
|-------|------------------------|---------|
| 1     | `-x` `~x`              | negate, bitwise not |
| 2     | `*` `/` `%`            | multiply, divide, remainder |
| 3     | `+` `-`                | add, subtract |
| 4     | `<<` `>>`              | shift left, shift right |
| 5     | `<` `<=` `>` `>=`      | comparison (1 or 0) |
| 6     | `==` `!=`              | equality (1 or 0) |
| 7     | `&`                    | bitwise and |
| 8     | `^`                    | bitwise xor |
| 9     | `\|`                   | bitwise or |

As in C, comparisons bind tighter than `&` / `^` / `|`: write
`(flags & MASK) == MASK`, not `flags & MASK == MASK`.

### Integer semantics

Values are unbounded integers; the operand or data size masks the
result when it is emitted.

- `/` truncates toward zero and `%` takes the sign of the dividend
  (C / ca65 rules): `-7 / 2` is `-3`, `-7 % 2` is `-1`, and
  `a == (a / b) * b + a % b` always holds.
- `/` or `%` by zero is error `E0312`, reported at the operator.
- `~` complements within the smallest of 8, 16 or 32 bits that holds
  the value: `~0x0F` is `0xF0`, `~0x8000` is `0x7FFF`. Values wider
  than 32 bits are rejected.
- `>>` is an arithmetic shift: `-8 >> 1` is `-4`.

```ca65
TEXT_BYTES_PER_ITEM = 12
HALF  = TEXT_BYTES_PER_ITEM / 2      ; 6
SLOT  = (index + 1) % 8
FLIP  = attributes ^ 0xC0
    lda.w #(table_end - table) / 3
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

### `.a8` / `.a16` / `.i8` / `.i16` — register width

Tell the assembler whether the accumulator (`A`) and index (`X`/`Y`)
registers are currently 8-bit or 16-bit. Width drives immediate-mode
opcode sizing: under `.a16`, `lda #0x42` emits `A9 42 00` (3 bytes);
under `.a8`, the same line emits `A9 42` (2 bytes).

```ca65
.a16
.i16
lda #0x1234       ; A9 34 12
ldx #0x5678       ; A2 78 56
```

#### Inference from `rep` / `sep`

`rep #N` and `sep #N` mutate the CPU's `M` / `X` flags at runtime;
the assembler mirrors that at assembly time so source doesn't have
to repeat itself:

```ca65
rep #0x30         ; clears M+X -> A and X are 16-bit
lda #0x42         ; A9 42 00  (widened because M=16, not because of value)
sep #0x20         ; sets M    -> A back to 8-bit
lda #0x42         ; A9 42
```

Bit `0x20` controls `A`, bit `0x10` controls `X`/`Y`. `rep` clears
(16-bit), `sep` sets (8-bit). The inference only fires for constant
immediate operands; symbolic constants resolved at assembly time
count, but forward references and non-immediate forms are left
alone (and explicit `.a*` / `.i*` always wins).

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

### `.alloc [NAME] at ADDR [size N] { body }`

Pinned placement: `body` lands at the literal `ADDR`. `NAME` is
optional (3-byte hijacks shouldn't tax with names); the assembler
auto-generates a stable identifier for anonymous allocs.

`size N` upper-bounds the body. Overflow past `ADDR + N - 1` is a
hard error pointing at the byte that no longer fits. Omit `size`
for an unbounded body (stops at the bank boundary, matching the
legacy `*=` shape).

```ca65
.alloc vector_table at 0x00FFE0 size 0x20 {
    .dw 0, 0
    .dw brk_handler, brk_handler, brk_handler, nmi_handler
    .dw 0, brk_handler
    .dw 0, 0
    .dw brk_handler, 0, brk_handler, 0
    .dw reset, brk_handler
}

.alloc at 0x07FFFF size 0x01 {
    .db 0  ; pad ROM to 256KB
}
```

Overlap with any other pinned region (legacy `*=` included) trips
the overlap auditor with both locations named.

### `.reserve NAME SIZE [at ADDR] in POOL`

Byte-less reservation into a (typically `bss`) pool; lays out a RAM/VRAM
variable flat, no wrapper `.alloc` block. `NAME` binds at the reserved
address; nothing is emitted into the image.

* `.reserve NAME SIZE in POOL`: the allocator picks the address.
* `.reserve NAME SIZE at ADDR in POOL`: pins the slot at `ADDR`. The
  allocator validates the span lies within a pool range and overlaps no
  other allocation (pinned or floating), then carves it out. Use for fixed
  memory maps (VRAM, MMIO mirrors) where the address is the contract but
  you still want overlap checking across the whole layout.
* `.reserve NAME as TYPE in POOL`: reserves `sizeof(TYPE)` and publishes
  `NAME.<field>` at each struct offset.

```ca65
.pool vram { bss  range 0x0000 0x7fff  strategy order }
.reserve bg_char 0x2000 at 0x1000 in vram   ; pinned VRAM word
.reserve bg1_map 0x0800 at 0x6800 in vram
.reserve scratch 0x0040           in vram   ; allocator picks a free hole
```

Pinned spans that fall outside the pool or collide with another allocation
fail the build, naming the offending reservation.

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
