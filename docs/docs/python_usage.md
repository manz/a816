# Python usage

`build_with_imports` is what `a816 build` runs: it compiles the entry
file and every module it `.import`s, links, and writes the output. It
reads `a816.toml` from the entry file's directory upwards unless
`use_a816_toml=False`.

```python
from pathlib import Path

from a816.module_builder import build_with_imports

Path("main.s").write_text(
    ".alloc at 0x009C21 {\n"
    "    lda.l external_symbol\n"
    "}\n"
)

result = build_with_imports(
    "main.s",
    "patch.ips",
    output_format="ips",  # or "sfc"
    symbols={"external_symbol": 0xDE0134},  # like -D on the command line
)
assert result.exit_code == 0, result.diagnostics
print(result.symbol_map)
```

`BuildResult` carries `exit_code`, `symbol_map` (name -> address),
`diagnostics` (the formatted errors) and the path of the `.adbg` debug
info.

Other keyword arguments mirror the CLI: `module_paths`, `include_paths`,
`output_dir` (objects, default `build/obj`), `overlap_mode`,
`experimental`, `mapping` (`-m`), `copier_header`, `use_cache`.

## Generated objects

A Python asset generator (compressed text, rendered fonts) does not
know where its bytes will land. It hands its objects to the build with
`objects`, keyed by the name diagnostics give them; the linker places
them with the compiled modules, and source reads their labels with
`.extern`:

```python
from pathlib import Path

from a816.module_builder import build_with_imports
from a816.object_file import ObjectFile
from a816.program import Program

pool = ".pool rom {\n    range 0x018000 0x01ffff\n}\n"
Path("main.s").write_text(pool + ".extern message\n.alloc boot at 0x008000 {\n    lda.l message\n}\n")

# The generator's output, compiled to an object.
Path("text.s").write_text(pool + ".alloc text in rom {\nmessage:\n    .db 0x11, 0x22\n}\n")
Program().assemble_as_object("text.s", Path("text.o"))
text_object = ObjectFile.from_file("text.o")

result = build_with_imports("main.s", "game.sfc", "sfc", objects={"assets/text": text_object})
assert result.exit_code == 0, result.diagnostics
```

Their allocs take pools as source modules do: the object declares the
pool it places into, with the same ranges as the source. Pool merging,
overlap checks, `.assert`s and the `.adbg` cover them like any module.
They join at link, never at `.import`, and link afresh on every build:
no module's cached object depends on them. A name that a source module
already has is refused.

## A source string to bytes

For a few lines of assembly with no project on disk (a stub to drop in an
emulator's RAM, a fragment to compare), `assemble_source` compiles and
links the string as a build would. The source places its own code:

```python
from a816.snippet import assemble_source

stub = assemble_source(
    "*= 0x7E0000\njsr.l kerning_func\nstp\n",
    symbols={"kerning_func": 0x208000},  # like -D
)
stub.code     # b"\x22\x00\x80\x20\xdb"
stub.blocks   # [(rom_offset, bytes), ...]
stub.symbols  # linked labels
```

`files` serves its `.include`s and `.import`s (path -> text),
`include_paths` and `experimental` mirror the CLI. A failed build raises
`AssemblyError` carrying the diagnostics.
