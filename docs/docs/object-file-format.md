# Object file format (`.o`)

The `.o` file is the unit of separate compilation: one `.s` source
compiled with `--compile-only` produces one `.o`. Linking takes a list
of `.o` files (and any source files compiled on the fly) and resolves
all cross-module references.

Inspect with [`xobj`](index.md#xobj).

## Header

All integers are little-endian.

```
magic     : u32 = 0x41383136 ('A816')
version   : u16 = 16          container version: how the rest is laid out
flags     : u8  bit 0 = relocatable (no `*=` in the source)
schema    : 16 bytes          digest of the wire schema (below)
revision  : u32               CODEGEN_REVISION of the a816 that wrote it
```

Every container version starts with `magic`, `version`, `flags`, so an
object from any a816 at least reports which version it is.

`version`, `schema` and `revision` together are the object's
**identity**. a816 reads only objects of its own identity; the build
cache rebuilds any other one instead of failing to load it.

- `schema` changes whenever a field of any table changes (added,
  removed, retyped, reordered). Nothing to bump by hand.
- `revision` (`CODEGEN_REVISION` in `a816/object_file.py`) is bumped
  when a816 emits different object bytes for unchanged source.
  `tests/test_codegen_revision.py` assembles a fixed corpus and fails
  when the output changed without a bump, or the revision moved
  without an output change.

## Body

After the header comes one `WireObject` record, encoded from its type
annotations by `a816/object_codec.py`:

<!-- example: skip -->
```python
@dataclass
class WireObject:
    sections: list[WireSection]
    symbols: list[tuple[str, int, SymbolType, SymbolSection]]
    aliases: list[tuple[str, str]]
    files: list[str]
    pool_decls: list[PoolDecl]
    pool_allocs: list[PoolAlloc]
    bus_mappings: list[BusMapping]
    asserts: list[LinkAssert] = []
```

The record types (`WireSection`, `PoolDecl`, `PoolAlloc`,
`BusMapping`, `LinkAssert`) are dataclasses in `a816/object_file.py`; their
annotations are the format. Encoding rules:

| Type | Bytes |
|---|---|
| `int` | i64 |
| `bool` | u8 |
| `Enum` | its value, as an `int` |
| `str`, `bytes` | u32 length + bytes (UTF-8 for `str`) |
| `X \| None` | u8 tag (0 = None) + `X` |
| dataclass, fixed `tuple` | its fields in order |
| `list[R]`, `R` a record of `int`/`bool`/`Enum`/`str` | u32 count, then one column per field: numbers as `count` i64s, strings as one u32-length blob joined by NUL |
| other `list[X]` | u32 count + items |

Columnar lists keep the big tables (symbols, relocations, line
entries) fast to read: each column decodes in one call.

Offsets in a section's `relocations`, `expression_relocations` and
`lines` are byte offsets into that section's `code`. The reader builds
an anonymous pinned `Section` from each `WireSection`; pooled sections
get their final address from the matching `PoolAlloc` at link time.

To add data to the format, add an annotated field (with a default, for
in-memory callers) to the right record: it is written, read and
versioned with no further code.

## Stability

Objects are build artifacts: only the a816 that wrote one reads it.
The container version changes only when the header or the encoding
rules do:

- v16 (current): tables encoded from their annotations; schema digest
  and codegen revision in the header.
- v15 and earlier: hand-written per-table layouts.

## Producer notes

- `Program.assemble_as_object(asm_file, output_file)` builds an `.o`.
- The `Linker` consumes a list of `ObjectFile` instances and produces a
  single linked object whose code can be written by `IPSWriter` or
  `SFCWriter`.
- See [Debug info (.adbg)](adbg-format.md) for the linked-output debug
  format that piggybacks on the per-section line tables.
