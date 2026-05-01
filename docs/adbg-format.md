# `.adbg` debug info format

`.adbg` is a compact binary file that maps emitted bytes back to source
locations, modules, and symbols. It is written next to the linked output
(IPS, SFC) on every successful link and is a structured superset of the
`.sym` file consumed by bsnes-style debuggers.

## Goals

- Map every emitted byte (or instruction) to `(file, line, column)`.
- List every linked module with its load base address.
- Carry every symbol — labels, constants, aliases — with type, scope,
  and owning module.
- Stay easy to produce and parse from Python and C; no DWARF, no ELF.

## Layout

All integers are little-endian.

### Header

```
magic         : char[4] = "ADBG"
version       : u16     = 1
flags         : u16     reserved, always 0 in v1
section_count : u32
```

### Section header

Each section starts with:

```
kind   : u32
length : u32   payload byte count
payload: bytes[length]
```

Unknown section kinds must be skipped by readers; v1 producers emit the
sections defined below in this order.

#### `FILES` (kind = 1)

```
count : u32
entries:
    str_len : u16
    bytes   : utf-8[str_len]   path (absolute or workspace-relative)
```

Index 0 is always the entry-point source file.

#### `MODULES` (kind = 2)

```
count : u32
entries:
    name_idx : u32   string-table offset
    file_idx : u32   FILES index
    base     : u32   logical load address (SNES address space)
```

Module 0 is reserved for the main translation unit (no `.import`, just
the entry-point file). Imported modules follow in compilation order.

#### `SYMBOLS` (kind = 3)

```
count : u32
entries:
    name_idx   : u32
    address    : u32   final logical address
    scope      : u8    0=local, 1=global, 2=external
    module_idx : u32   MODULES index, 0xFFFFFFFF when unknown
    kind       : u8    0=label, 1=constant, 2=alias
```

#### `LINES` (kind = 4)

```
count : u32
entries (sorted by ascending address):
    address    : u32
    file_idx   : u32
    line       : u32   1-based
    column     : u16   1-based
    module_idx : u32   0xFFFFFFFF when unknown
    flags      : u8    bit 0 = synthetic (macro expansion)
```

### String table

Appended as the last section (kind = 5):

```
table_size : u32
bytes      : utf-8[table_size]   null-separated UTF-8 strings
```

`name_idx` values stored in `MODULES` and `SYMBOLS` are byte offsets
into this blob. Always include a leading null byte so offset 0 maps to
the empty string.

## Producer notes (v1)

- The producer walks the resolved node graph during emit and records
  one `LINES` entry per emitting node that carries a `file_info`
  position. Macro expansions emit a single entry at the call site with
  `flags & 1` set.
- `MODULES` is filled from the module builder graph plus the load
  address each `LinkedModuleNode` resolves to during the second
  resolution pass.
- `SYMBOLS` is filled from the resolver's labels and symbols at the
  end of assembly, after module GLOBAL symbols have been merged in.
- The CLI link path (`a816 file1.o file2.o -o out.ips`) currently
  produces a `.adbg` only for the symbols and modules it can see in
  the linked object file; line tables in that path are limited to what
  was already baked into each `.o`. Direct-mode builds
  (`build_with_imports_direct`) emit full line info.
