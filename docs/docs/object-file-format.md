# Object file format (`.o`)

The `.o` file is the unit of separate compilation: one `.s` source
compiled with `--compile-only` produces one `.o`. Linking takes a list
of `.o` files (and any source files compiled on the fly) and resolves
all cross-module references.

Inspect with [`xobj`](index.md#xobj).

## Header

All integers are little-endian.

```
magic   : u32 = 0x41383136 ('A816')
version : u16 = current 6
flags   : u8  bit 0 = relocatable (1 when produced by --compile-only)
```

Reader rejects mismatched `version`. Bumping the version is always a
breaking change — old `.o` files cannot be linked by a newer
toolchain. Recompile from source when you upgrade.

## Sections

The header is followed by six tables in this order:

1. **Sections** — emitted code plus per-section relocations and debug lines.
2. **Symbol table** — names defined or referenced by the module.
3. **Alias table** — deferred constant expressions.
4. **File table** — source paths referenced by the line tables.
5. **Pool declarations** — `.pool NAME { range ... }` decls so the
   linker can re-create the pool object when merging modules.
6. **Pool allocations** — `.alloc NAME in POOL { ... }` requests the
   link-time allocator fulfils across the whole link, plus
   `.alloc at ADDR { ... }` pinned synthesised pools.

### Sections

A new section is opened on every `*=` directive (legacy form) or
`.alloc [NAME] at ADDR { ... }` / `.alloc NAME in POOL { ... }`
directive during compilation, so the layout is faithful to the
author's intent (no concatenation of disjoint memory ranges).

Each section carries a `placement` tag: `PINNED` (base address fixed
at parse time — `*=` and `.alloc at`) or `POOLED` (base address
chosen by the linker's cross-module pool allocator — `.alloc … in
POOL`). The placement tag drives whether the linker uses the
section's recorded `base_address` verbatim or re-bases it after pool
allocation.

```
count : u16

per section:
    base_address          : u32   logical SNES address of the section's first byte
    code_size             : u32
    num_relocations       : u16
    num_expression_relocs : u16
    num_lines             : u32
    code                  : bytes[code_size]

    per relocation:
        offset    : u32       byte offset into `code`
        name_len  : u8
        name      : utf-8[name_len]
        reloc_type: u8
                              0 = ABSOLUTE_16
                              1 = ABSOLUTE_24
                              2 = RELATIVE_16
                              3 = RELATIVE_24

    per expression relocation:
        offset    : u32       byte offset into `code`
        expr_len  : u16
        expression: utf-8[expr_len]
        size_bytes: u8        emit width (1, 2, 3 or 4)

    per line entry:
        offset    : u32       byte offset into `code`
        file_idx  : u32       index into the file table
        line      : u32       1-based
        column    : u16       1-based
        flags     : u8        bit 0 = synthetic (macro expansion)
```

Plain *relocations* hold a single symbol name; the linker resolves the
name to an address and writes back at `offset`. *Expression relocations*
hold a full a816 expression text; the linker re-parses and evaluates
the expression once every name in it is bound. They make
`name = target + 0x40` style aliases work across modules.

### Symbol table

```
count : u16

per entry:
    name_len    : u8
    name        : utf-8[name_len]
    address     : u32
    symbol_type : u8    0 = LOCAL, 1 = GLOBAL, 2 = EXTERNAL
    section     : u8    0 = CODE,  1 = DATA,   2 = BSS
```

LOCAL symbols are private to the module (names starting with `_`).
GLOBAL symbols export to other modules. EXTERNAL symbols are
declarations the linker is responsible for filling in.

### Alias table

```
count : u16

per entry:
    name_len    : u8
    name        : utf-8[name_len]
    expr_len    : u16
    expression  : utf-8[expr_len]
```

Aliases are constant-binding expressions deferred until link time.
They support `name = (target >> 16) & 0xFF` shapes where `target` is
defined in another module.

### File table

```
count : u16

per entry:
    path_len : u16
    path     : utf-8[path_len]
```

Indices are referenced from each section's line table.

### Pool declarations

```
count : u16

per entry:
    name_len      : u8
    name          : utf-8[name_len]
    num_ranges    : u16
    per range:
        start     : u32   inclusive logical SNES address
        end       : u32   inclusive
    fill          : u8    byte used to back-fill the pool's slack
    strategy_len  : u8
    strategy      : utf-8[strategy_len]   "pack" | "order"
```

The linker keys pools by `name` and merges identical decls across
modules. Mismatched shape (different ranges / fill / strategy under
the same name) is a hard error during merge.

### Pool allocations

```
count : u16

per entry:
    pool_name_len   : u8
    pool_name       : utf-8[pool_name_len]
    symbol_name_len : u8
    symbol_name     : utf-8[symbol_name_len]
    section_idx     : u32   index into the section table — the alloc's body
    size            : u32   byte length the allocator must reserve
```

Each entry binds one `.alloc NAME in POOL { ... }` to the section
holding its body bytes. The link-time cross-module pool allocator
walks every module's pool_allocs, places each in its pool's free
list, then rewrites the owning section's base address before emit.

## Stability

The format version is bumped on every breaking change. Past versions:

- v6 (current): section-aware code layout — `*=` produces a new section,
  preserving disjoint address ranges across the same module.
- v5: added per-section line tables for `.adbg` debug info.

Older versions are not read by current builds. Always recompile
sources after upgrading the toolchain.

## Producer notes

- `Program.assemble_as_object(asm_file, output_file)` builds an `.o`.
- The `Linker` consumes a list of `ObjectFile` instances and produces a
  single linked object whose code can be written by `IPSWriter` or
  `SFCWriter`.
- See [Debug info (.adbg)](adbg-format.md) for the linked-output debug
  format that piggybacks on the per-section line tables.
