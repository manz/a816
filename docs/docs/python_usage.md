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

`Program` and its `assemble*` methods are the single-pass direct mode;
it is deprecated and new features land in the build path only.
