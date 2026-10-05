# Freespace pools

ROM hacks routinely relocate functions to free up space at their
original location. The freespace pool API lets you declare reusable
chunks of ROM, request space inside them, reclaim ranges from
previously-occupied code, and let the assembler place everything
deterministically.

## Quick start

```ca65
; Declare a pool of free bytes the assembler may use.
.pool bank01_slack {
    range 0x01ff35 0x01ffff
    fill 0xea          ; optional; default 0x00
    strategy order     ; or `pack` (default — largest-first)
}

; Drop a new routine into the pool. Allocator picks the address.
.alloc draw_vwf_message in bank01_slack {
    jsr.l items_description.draw_trampoline
    rts
}

; Move an existing routine into the pool. Old range is reclaimed
; into the pool (its bytes become reusable for later allocs).
.relocate fn_old 0x02c000 0x02c17f into bank01_slack {
    pha
    rts
}

; Add a raw byte range to a pool (rarely needed — most reclaims
; happen via .relocate). Useful for slack with no original label.
.reclaim bank01_slack 0x01ebd2 0x01ed44
```

After build, every `.alloc` / `.relocate` symbol resolves to the
address the allocator picked. Callers reference the symbol normally
(`jsr.l draw_vwf_message`) — the address is determined at link time
(object mode) or at the end of the resolver's first pass (direct
mode).

## Concepts

- **Pool** — a named bag of free `(start, end)` ranges in a single
  ROM, plus a `fill` byte and an allocation strategy. Ranges of one
  pool may sit in different banks, but each range must stay inside
  one bank, and ranges of the same pool must not overlap.
- **Bank rule**: an allocation is always placed inside a single
  free chunk, so a block never straddles a bank boundary or spans
  two separate (non-adjacent) ranges. An alloc larger than the
  pool's largest range can never fit, however much space is free
  in total.
- **Allocation** — a named request for `N` bytes inside a specific
  pool. After `Pool.allocate()` runs, every allocation has a final
  ROM address.
- **Reclaim** — adding a fresh range to a pool (typically the old
  location of a function that just moved). Reclaimed ranges merge
  with adjacent existing ranges automatically.
- **Strategy** — `pack` (largest allocation first, default) minimises
  fragmentation; `order` (declaration order) keeps placements stable
  when you reorder the source.

Both strategies are deterministic: identical input → identical
placement → byte-identical output.

## Directives

### `.pool NAME { ... }`

```ca65
.pool bank02_slack {
    range 0x028000 0x028fff
    range 0x02a100 0x02a4c0   ; multiple ranges allowed
    range 0x03f000 0x03ffff   ; ...in other banks too
    fill 0xea                  ; optional
    strategy order             ; optional (pack | order)
}
```

Ranges are tried first-fit in address order. Adjacent ranges in the
same bank merge into one chunk (`range 0x028000 0x028007` +
`range 0x028008 0x02800f` is one 16-byte chunk); ranges in
different banks never do, even when their addresses touch, so
`range 0x01fff0 0x01ffff` + `range 0x020000 0x02000f` is two
16-byte chunks, not one 32-byte chunk.

A `range` may span several banks: it is shorthand for one range per
bank, clipped to the windows the bus serves for the pool's kind (ROM,
or writable memory for a `bss` pool). On LoROM
`range 0x228000 0x2fffff` becomes the `$8000-$FFFF` half of each bank
`$22-$2F`, never the low halves. A bank the range covers that no `.map`
serves is an error rather than a quietly smaller pool. Blocks stay
bank-local either way.

`range`, `fill`, and `strategy` accept constant expressions; literal
arithmetic resolves at code-generation time. Constants declared
earlier in the same source bind eagerly so `range BASE BASE + 0xff`
works.

#### Memory pools: `bss` and `contexts`

`bss` makes a pool byte-less: it lays out RAM (WRAM, SRAM, VRAM),
reserves addresses with `.reserve` / `.res`, and emits nothing into
the image.

Reservations in two different bss pools may not share memory; the
linker rejects it with `E0406`, naming both reservations. Emitted
bytes are already covered by the writer's overlap check, so this
closes the gap for memory. Pool *ranges* may still overlap: a pool
nobody reserves in (a window over other pools, read for its
`.capacity`) cannot collide.

Memory used in turns, by screens or modes that never run at the
same time, belongs in one pool that lists them as `contexts`:

```ca65
.pool menu_ram { bss  range 0x7e9800 0x7e990f  contexts field_menu, treasure, battle }

.reserve field_hdma    0x40 in menu_ram.field_menu
.reserve field_shadow  0x40 in menu_ram.field_menu   ; after field_hdma
.reserve treasure_hdma 0x40 in menu_ram.treasure     ; same bytes as field_hdma
```

Each context is its own allocator over the pool's ranges
(`POOL.CONTEXT`, usable anywhere a pool name is), so reservations
inside one context still never overlap, while different contexts of
the same pool may. The pool's footprint is its largest context.
Contexts are mutually exclusive only within the pool that lists
them; anything else that overlaps is an error, including a
reservation made directly in the pool next to its contexts.
`contexts` is only allowed on a `bss` pool: emitted bytes have one
owner.

Addresses are compared as written: a reservation reached through a
mirror (WRAM `$00:0000` for `$7E:0000`) is not matched against the
same bytes at their canonical address.

### `.alloc NAME in POOL { body }`

```ca65
.alloc helper_fn in bank02_slack {
    rts
}
```

Allocator picks the address. `helper_fn` symbol resolves to that
address. Body bytes land there.

### `.alloc [NAME] at ADDR [size N] { body }`

Pinned placement: `body` lands at the literal `ADDR`. `NAME` is
optional (3-byte hijacks shouldn't tax with names); the assembler
auto-generates a stable identifier for anonymous allocs. Optional
`size N` upper-bounds the body — overflow past `ADDR + N - 1` is a
hard error pointing at the offending byte. Without `size`, the body
extends to the bank end.

```ca65
.alloc vector_table at 0x00FFE0 size 0x20 {
    .dw 0, 0
    .dw brk, brk, brk, nmi_handler
    .dw 0, irq
    .dw 0, 0, brk, 0, brk, 0, reset, brk
}

.alloc at 0x07FFFF size 0x01 {
    .db 0  ; pad ROM to 256KB
}
```

Pinned allocs share the overlap auditor with the rest of the
placement system — two pinned regions whose byte ranges intersect
raise a hard error naming both source locations.

Legacy `*= ADDR` directives still work and have the same effect;
the fluff rule `UP001` plus `a816 fix --select UP001 --unsafe-fixes`
rewraps them mechanically when you're ready to migrate.

### `.relocate SYMBOL OLD_START OLD_END into POOL { body }`

```ca65
.relocate fn_old 0x02c000 0x02c17f into bank02_slack {
    pha
    rts
}
```

Same as `.alloc` plus the old `[OLD_START, OLD_END]` range is
reclaimed back into the pool *before* the new body is placed — so
the freed bytes can fund the move when the rest of the pool is
otherwise full.

### `.reclaim POOL START END`

```ca65
.reclaim bank01_slack 0x01ebd2 0x01ed44
```

Escape hatch for slack that has no original label. Adds the inclusive
range to the named pool. Overlap with existing ranges raises.

## Pool stats as scope symbols

Every `.pool` decl publishes three snapshot symbols at code-gen time:

```ca65
.pool bank01_slack {
    range 0x01ff35 0x01ffff
}

.if bank01_slack.capacity < 0x100 {
    .debug 'bank01_slack too small for what we plan'
}
```

Available stats: `<pool>.capacity`, `<pool>.fragments`,
`<pool>.largest_chunk`. Snapshot at declaration — live `.free` /
`.used` (post-allocator) are not exposed yet.

## Pool exhaustion

When an alloc doesn't fit, the build stops with an error naming the
pool, the alloc and its size. Direct mode reports `error[E0318]`
with a caret on the alloc name; link time reports
`linker error[E0404]` with the pool, the `file:line` of the alloc
body and a hint. The message says which of three cases you hit:

```
alloc 'big' (20 bytes) does not fit in pool 'slack': larger than its largest range (16 bytes); a block never spans a bank boundary or two separate ranges
alloc 'c' (8 bytes) does not fit in pool 'slack': 8 bytes free in total but fragmented; largest free chunk is 4 bytes
alloc 'c' (10 bytes) does not fit in pool 'slack': largest free chunk is 4 bytes
```

- **Larger than any range**: no free space helps; split the alloc
  or give the pool a range at least that big. The bank boundary is
  only mentioned when the pool's ranges sit in several banks. A
  single-range pool (including the one behind `.alloc at ADDR size
  N`, or adjacent same-bank ranges merged into one) says `larger
  than the pool (N bytes)` instead.
- **Fragmented**: the pool has the bytes, but not in one chunk;
  split the alloc or grow one of the ranges.
- **Out of room**: grow the pool or move code out of it.

## Object mode + cross-TU pool merging

In object compilation (`a816 --compile-only`), allocation is deferred
to link time. Two modules can declare the same pool name with
complementary ranges; the linker unions the ranges and runs the
allocator across all modules' deferred requests:

```ca65
; module_a.s
.pool slack { range 0x028000 0x0280ff }
.alloc fn_a in slack { rts }

; module_b.s
.pool slack { range 0x02a000 0x02a0ff }
.alloc fn_b in slack { rts }
```

The linker does not keep an alloc in the range its own module
declared: it unions every module's ranges into one pool, sorts them
by address and places all requests first-fit. Here both `fn_a` and
`fn_b` land in module A's `0x028000` range (`fn_a` at `0x028000`,
`fn_b` at `0x028001`), because it is the lowest range with room;
module B's range is only used once module A's runs out. Same-named
pools must agree on `fill` and `strategy`; mismatches raise at link
time.

Complementary ranges like these work with separate compilation
(`a816 -c module_a.s module_b.s`, then `a816 module_a.o module_b.o`).
Under `a816 build`, a module that `.import`s both sees two `.pool
slack` declarations with different ranges and rejects them; there,
declare the pool once in a shared include with all its ranges.

The `.o` format (version 0x000C) carries `PoolDecl` and `PoolAlloc`
records (visible via `xobj`).

## Python API

The allocator core is usable directly from Python — useful for
build-time tooling that wants to manage placement without a `.s`
source.

```python
from a816.pool import Pool, PoolRange, Strategy

pool = Pool(
    name="bank02_slack",
    ranges=[
        PoolRange(start=0x028000, end=0x028FFF),
        PoolRange(start=0x02A100, end=0x02A4C0),
    ],
    fill=0xEA,
    strategy=Strategy.PACK,
)

moved_fn = pool.request("moved_fn", size=0x180)
helper   = pool.request("helper", size=0x40)
pool.reclaim(PoolRange(start=0x02C000, end=0x02C17F))
pool.allocate()

print(f"{moved_fn.name} @ 0x{moved_fn.addr:06x}")
print(f"free={pool.free} used={pool.used} fragments={pool.fragments}")
```

### Errors

| Exception | Cause |
|-----------|-------|
| `PoolInvalidRangeError` | `start > end`, or range crosses bank boundary |
| `PoolOverlapError`      | declared / reclaimed ranges overlap each other |
| `PoolOverflowError`     | no chunk has enough room for an allocation |
| `PoolError`             | zero-size request, fill byte out of `0..0xff`, mutation after `allocate()` |

### Determinism

- `allocate()` is idempotent; calling it twice does nothing the second
  time.
- After `allocate()`, the pool is frozen: further `request()` or
  `reclaim()` calls raise `PoolError`. Build a new `Pool` for the next
  pass.
- `pack` sorts by `(-size, name)` — name is the tiebreaker, so
  same-size allocations never flip on rebuild.

## Migrating from the manual pattern

The legacy pattern:

```ca65
*= 0x01ff35
fn_a: ...
fn_b: ...
_end_of_free_space:
.if _end_of_free_space > 0x01ffff {
    .debug 'Error: end of free space reached!'
}
```

becomes:

```ca65
.pool bank01_slack {
    range 0x01ff35 0x01ffff
}

.alloc bank01_slack_block in bank01_slack {
    fn_a: ...
    fn_b: ...
}
```

The pool-level overflow check replaces the hand-rolled
`_end_of_free_space` guard, and the allocator picks each label's
final address.

See the ff4-modules dogfood for three real conversions:
`src/ingame/free_space.s`,
`src/ingame/inventory_rolling_trampolines.s`, and
`src/battle/inventory_rolling_patches.s` — byte-identical IPS output
to the legacy layout modulo build-date timestamp drift.

## What's not in yet

- **Fill-byte emission** — `fill` parses + stores but IPS records
  over unused chunk tails and reclaimed ranges aren't written. Pool
  ranges that aren't `.alloc`'d stay as whatever the unpatched ROM
  contained.
- **`.relocate` in object mode** — only direct mode for now; link-time
  reclaim coordination is a follow-up.
- **Live `.free` / `.used` stats** — only `.capacity` / `.fragments` /
  `.largest_chunk` are snapshotted at decl time.
- **LSP "find references" for pool / alloc names** — outline shows
  them, definition jumps work, but cross-document refs aren't
  resolved yet.
