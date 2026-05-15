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
  ROM, plus a `fill` byte and an allocation strategy. Each range
  must not cross a bank boundary; ranges of the same pool must not
  overlap.
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
    fill 0xea                  ; optional
    strategy order             ; optional (pack | order)
}
```

`range`, `fill`, and `strategy` accept constant expressions; literal
arithmetic resolves at code-generation time. Constants declared
earlier in the same source bind eagerly so `range BASE BASE + 0xff`
works.

### `.alloc NAME in POOL { body }`

```ca65
.alloc helper_fn in bank02_slack {
    rts
}
```

Allocator picks the address. `helper_fn` symbol resolves to that
address. Body bytes land there.

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

When an alloc doesn't fit any chunk, the allocator raises
`PoolOverflowError` carrying the alloc's name + size:

```
PoolOverflowError: alloc 'oversized' size 0x180 does not fit in any free chunk
```

`grep` the alloc name to find the offending source. Same error
surfaces in direct-mode (during resolver pass 2) and at link time
(cross-TU allocator).

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

After link, `fn_a` lands in module A's chunk, `fn_b` in module B's
chunk. Same-named pools must agree on `fill` and `strategy`;
mismatches raise at link time.

The `.o` format (version 0x0008) carries `PoolDecl` and `PoolAlloc`
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
