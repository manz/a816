# Freespace pools

ROM hacks routinely relocate functions to free up space at their
original location. The freespace pool API lets you declare reusable
chunks of ROM, request space inside them, reclaim ranges from
previously-occupied code, and let the assembler place everything
deterministically.

!!! warning "Preview — Python API only"

    The allocator core ships today (`a816.pool`). The corresponding
    assembler directives (`.pool`, `.relocate`, `.alloc`, `.reclaim`)
    are **not yet wired into the parser** — track progress in the
    follow-up PRs after #46. This page documents the design and the
    Python-level API you can drive directly.

## Concepts

- **Pool** — a named bag of free `(start, end)` ranges in a single
  ROM, plus a `fill` byte and an allocation strategy. Pool ranges
  must not cross bank boundaries.
- **Allocation** — a named request for `N` bytes inside a specific
  pool. After `Pool.allocate()` runs, every allocation has a final
  ROM address.
- **Reclaim** — adding a fresh range to a pool (typically the old
  location of a function that just moved). Reclaimed ranges merge
  with adjacent existing ranges automatically.
- **Strategy** — `PACK` (largest allocation first, default) minimises
  fragmentation; `ORDER` (declaration order) keeps placements stable
  when you reorder the source.

Both strategies are deterministic: identical input → identical
placement → byte-identical IPS output.

## Planned directive syntax

What the assembler will accept once the parser is wired up:

```ca65
.pool bank02_slack {
    0x028000..0x028fff
    0x02a100..0x02a4c0
    fill: 0xea          ; nop-fill unused tail
    strategy: pack
}

; relocate an existing labelled region into a pool;
; old range is reclaimed and fill-byted in the same step
.relocate fn_old into bank02_slack {
    pha
    lda.b 0x42
    ...
    rts
}

; fresh code into a pool, no reclaim
.alloc helper_fn in bank20_main {
    ...
}

; raw range reclaim (escape hatch for unlabelled holes)
.reclaim 0x02c000..0x02c17f into bank02_slack
```

Compile-time introspection will expose pool stats as scope symbols:

```ca65
.if bank02_slack.free < 0x100 {
    .debug 'bank02_slack almost full'
}
```

Stats available: `<pool>.free`, `<pool>.used`, `<pool>.capacity`,
`<pool>.fragments`, `<pool>.largest_chunk`.

## Python API today

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

# optionally reclaim the function's old location before placement
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

### Determinism rules

- `allocate()` is idempotent; calling it twice does nothing the second
  time.
- After `allocate()`, the pool is frozen: further `request()` or
  `reclaim()` calls raise `PoolError`. Build a new `Pool` for the next
  pass.
- `PACK` sorts by `(-size, name)` — name is the tiebreaker, so
  same-size allocations never flip on rebuild.

## Migrating from the manual pattern

The pattern in ff4-modules today:

```ca65
*= 0x01ff35
fn_a: ...
fn_b: ...
_end_of_free_space:
.if _end_of_free_space > 0x01ffff {
    .debug 'Error: end of free space reached!'
}
```

Once directives land, the same intent becomes:

```ca65
.pool bank01_slack {
    0x01ff35..0x01ffff
    fill: 0xff
}

.alloc fn_a in bank01_slack { ... }
.alloc fn_b in bank01_slack { ... }
```

The pool-level overflow check replaces the hand-rolled
`_end_of_free_space` guard, and unused tail bytes get filled
automatically.

## Roadmap

| Stage | Status | Scope |
|-------|--------|-------|
| Allocator core (Python) | ✅ shipped (PR #46) | `Pool`, `PoolRange`, `Allocation`, strategies, reclaim, stats |
| Parser directives        | ⏳ planned         | `.pool`, `.relocate`, `.alloc`, `.reclaim` |
| Codegen + fill emission  | ⏳ planned         | wire allocator into resolver pass-2, emit `fill` bytes through IPS writer |
| Pool stats as symbols    | ⏳ planned         | `<pool>.free` etc. usable in `.if` |
| Object / linker support  | ⏳ planned         | pool decl records in `.o`, cross-TU pool union + allocator |
| ff4-modules migration    | ⏳ planned         | reference sample replacing `free_space.s` |
