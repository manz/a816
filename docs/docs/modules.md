# Modules

`.import` brings symbols from another module into the current translation
unit. The build driver discovers dependencies, topologically sorts them,
and recompiles by mtime.

## Resolving a name

`.import "vwf"` resolves in this order:

1. `vwf.o` in `--obj-dir` (default `build/obj`).
2. `vwf.s` on a search path (`-I` / `--module-path` or the same directory).

If only `.s` is available it is compiled to `.o` first, then linked.

## Symbol visibility

- Names starting with `_` are **LOCAL** to their module.
- All other names are **GLOBAL** and exported in the object file.
- Names declared inside `named_scope { ... }` export as `named_scope.name`.
- Anonymous `{ ... }` blocks are scoped — labels declared inside never leak.

## Cross-module references

Declare symbols defined in another module with `.extern`:

```ca65
.extern external_func
.extern messages_vwf
.extern messages_vwf.init_commands_list   ; sub-symbols need their own decl

main:
    jsr.w external_func
    rts
```

The linker verifies all externs are resolved and reports missing ones.

## Constants over externs

`name = expression` is allowed even when `expression` references an extern.
The constant is recorded as a deferred alias and resolved at link time:

```ca65
.extern target

font_ptr  = target + 0x40
font_high = (target >> 16) & 0xFF
```

## Workflow

```
$ a816 build --compile-only file1.s file2.s
$ a816 build file1.o file2.o -o output.ips
```

Mixed source + object inputs work too:

```
$ a816 build file1.s file2.o -o output.ips
```

## Auto-generated symbols

`.incbin "data.bin"` defines both the data label and a `<label>__size`
symbol with the byte count, so callers can do bounds checks without
tracking the length manually.

## Prelude

`--prelude PRELUDE_FILE` prepends the file to every module compilation.
Useful for project-wide feature flags, register-size hints, or
`.table` configuration.
