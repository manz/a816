# Placement

Every byte a816 emits, and every byte of memory it reserves, is placed
by one directive with one grammar:

```
.alloc [NAME] [at ADDR] [size N | in POOL [cross_bank] [align N]] { body }
```

| Form | Meaning |
|---|---|
| `.alloc NAME in POOL { … }` | the allocator picks the address inside `POOL` |
| `.alloc NAME at ADDR { … }` | pinned at `ADDR`, outside any pool |
| `.alloc NAME at ADDR size N { … }` | pinned, bounded to `N` bytes (overflow is an error) |
| `.alloc NAME at ADDR in POOL { … }` | pinned **inside** `POOL`: carved out before the pool places its floating allocs |
| `… cross_bank` | a data blob that may straddle bank edges where the ROM is contiguous |
| `… align N` | the block starts on a multiple of `N` |

`NAME` is optional for pinned allocs (a 3-byte hijack needs no name).
The other placement directives are special cases of the same thing:

| Directive | Is |
|---|---|
| `.reserve NAME SIZE [at ADDR] in POOL` | `.alloc NAME [at ADDR] in POOL { .res SIZE }` in a `bss` pool |
| `.reserve NAME as TYPE [at ADDR] in POOL` | the same, sized from the struct, publishing `NAME.<field>` |
| `*= ADDR` (legacy) | `.alloc at ADDR { … }` up to the next placement |

## Pools

A pool is the space placement draws from:

```
.pool NAME { range LO HI ... [fill BYTE] [strategy pack|order] [bss] [contexts A, B] }
```

- `range`s may span several banks; each bank's piece is clipped to
  the windows the bus serves (ROM, or writable memory for `bss`).
- `bss` pools reserve memory and emit nothing.
- `contexts A, B` lets mutually exclusive users of a `bss` pool share
  its memory (`.reserve x 4 in POOL.A`).

## What the allocator guarantees

- Blocks never overlap inside a pool, and reservations of two `bss`
  pools never overlap unless they are contexts of one pool (`E0406`).
  Emitted bytes from any two placements meet the writer's overlap
  check.
- A block stays inside one bank, unless it is `cross_bank`: then it
  may run on across edges where the last byte of one bank and the
  first of the next are consecutive in the ROM. Code can't run
  through an edge, so a `cross_bank` body holds data only (`E0336`).
- Pinned spans are carved first, then floating blocks are placed
  first-fit (`order`) or largest-first (`pack`), each on its `align`
  boundary; the gaps alignment leaves stay free.
- An empty body binds its label and takes no space.

## Checking the layout

`.assert EXPR, "message"` states an invariant the final layout must
hold, checked at link time with final addresses:

```ca65
.assert (items_vwf & 0xFFFF) == 0, "items_vwf must open a bank"
.assert dialogue_stream + sizeof(dialogue_stream) <= 0x5d0000, "the stream overruns the gap"
```

## A whole ROM gap, declared

dq6's `$50-$5C` upper gap: a dialogue stream pinned at the start and
running over several banks, every other blob placed behind it:

```ca65
.pool upper_gap { range 0x500000 0x5cffff  strategy pack }

.alloc dialogue_stream at 0x500000 in upper_gap cross_bank {
    .incbin "assets/stream.dat"
}
.alloc keep_font in upper_gap {
    .incbin "assets/keep_font_packed.dat"
}
.alloc items_chunk_tbl in upper_gap {
    .dw items_vwf_0 & 0xFFFF
    .dw items_vwf_0 >> 16
}

.assert dialogue_stream + sizeof(dialogue_stream) <= 0x5d0000, "the stream overruns the gap"
```

`sizeof(dialogue_stream)` is the alloc's byte count: the blob's size,
since it fills the alloc alone.

A longer script pushes the other blobs along; a gap that no longer
fits fails the link instead of overwriting text.

See [Freespace pools](freespace-pools.md) for every directive in detail.
