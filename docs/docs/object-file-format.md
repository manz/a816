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

The header is followed by four tables in this order:

1. **Regions** — emitted code plus per-region relocations and debug lines.
2. **Symbol table** — names defined or referenced by the module.
3. **Alias table** — deferred constant expressions.
4. **File table** — source paths referenced by the line tables.

### Regions

A new region is opened on every `*=` directive during compilation, so
the layout is faithful to the author's intent (no concatenation of
disjoint memory ranges).

```
count : u16

per region:
    base_address          : u32   logical SNES address of the region's first byte
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

Indices are referenced from each region's line table.

## Stability

The format version is bumped on every breaking change. Past versions:

- v6 (current): region-aware code layout — `*=` produces a new region,
  preserving disjoint address ranges across the same module.
- v5: added per-region line tables for `.adbg` debug info.

Older versions are not read by current builds. Always recompile
sources after upgrading the toolchain.

## Producer notes

- `Program.assemble_as_object(asm_file, output_file)` builds an `.o`.
- The `Linker` consumes a list of `ObjectFile` instances and produces a
  single linked object whose code can be written by `IPSWriter` or
  `SFCWriter`.
- See [Debug info (.adbg)](adbg-format.md) for the linked-output debug
  format that piggybacks on the per-region line tables.
