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

## What `.import` actually brings in

`.import "module"` pairs two views of the imported module:

- **Compile-time content** comes from `module.s` (struct defs, macros,
  constants, typed binds, nested `.import`, `.pool` decls, `.scope`
  bodies). These get inlined into the importer's resolver so codegen
  sees their effects (`(addr as Type).field` resolves, `MyMacro()`
  expands, pool names register).
- **Runtime symbols** come from `module.o` (label addresses, alloc
  placements, `.incbin` byte content). These surface as `ExternNode`
  stubs in the importer's `.o`; the linker resolves each to the
  owner's single GLOBAL definition during merge.

Neither half is complete on its own — `.o` can't carry a struct def
(structs never get emitted as bytes); inlining the source would
duplicate the runtime symbols the `.o` already owns. The paired flow
lets a sub-module reach a parent's typed binds without needing
explicit `.extern` declarations for every label.

You can still write `.extern name` for symbols you want to reference
without `.import`ing the owning module — useful for build-script
injected constants or third-party `.o` drops.

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

## Pools across modules

`.pool NAME { range ... }` declared in one module is visible to any
module that `.import`s it. The decl serialises into both files'
`.o`; the linker merges decls by name (identical shape required —
mismatched ranges / fill / strategy is a hard error) so a shared
preamble can hand out pool names like `client` and `engine` and
sub-modules `.alloc … in client` against them without redeclaring.

See [Freespace pools](freespace-pools.md) for `.alloc`, `.relocate`,
and `.reclaim` semantics.

## Prelude

`--prelude PRELUDE_FILE` prepends the file to every module compilation.
Useful for project-wide feature flags, register-size hints, or
`.table` configuration. Prefer an explicit `.import "preamble"` at
the top of each module — the inline classifier picks up the
preamble's structs / pools / typed binds the same way, without the
text-prepend tax on every per-module compile.
