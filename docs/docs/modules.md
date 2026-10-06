# Modules

`.import` brings symbols from another module into the current translation
unit. The build driver discovers dependencies, topologically sorts them in a
stable order, and recompiles only the modules whose inputs changed (see
[Incremental builds](#incremental-builds)).

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
- A module sees the constants of the modules it imports, directly or
  through another import. A constant of a module it does not import
  still resolves during 1.1.0, with a warning naming the `.import` to
  add: that visibility depended on compile order and becomes an error.

## What `.import` actually brings in

`.import "module"` pairs two views of the imported module:

- **Compile-time content** comes from `module.s` (struct defs, macros,
  constants, typed binds, nested `.import`, `.pool` decls, `.scope`
  bodies). These get inlined into the importer's resolver so codegen
  sees their effects (`(addr as Type).field` resolves, `MyMacro()`
  expands, pool names register).
- **Runtime symbols** come from `module.o` (label addresses, alloc
  placements, `.incbin` byte content, link-time aliases such as
  `sc.fd = sc.here`). These surface as `ExternNode`
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

## Placement

`.import` goes in the file prelude, before the first placement: inside
an `.alloc` body or after a `*=` it is `E0311`. An import never places
the imported module; each module owns its placement. The old pattern
of `*= ADDR` followed by `.import "module"` to put the module at
`ADDR` does not work any more: give the module its own
`.alloc at ADDR` or `.alloc in POOL`.

Under `a816 build <entry>.s` every emitted byte needs an explicit home,
in the entrypoint and in every imported module alike: an `.alloc`
(`at ADDR` or `in POOL`) or a preceding `*= ADDR`. Bytes emitted before
the first `*=` and outside every `.alloc` fail the build with `E0310`,
pointing at the first such statement. They used to land silently at
`0x008000` and overwrite whatever else lived there.

Byte-less modules (constants, structs, macros, pool decls) need no
placement. Code after an `.alloc { ... }` block continues the enclosing
`*=` position, so it needs one too.

Explicit separate compilation (`a816 build --compile-only` then linking
`.o` files, or several sources on one command line) keeps unplaced
objects relocatable: the linker lays them out back to back from
`0x008000`. The link-time [overlap check](directives.md#write-overlap-detection)
still rejects them if they collide with pinned code.

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

The same holds inside a named scope, and for a right-hand side that
names a label of this module or an imported runtime symbol (a label, a
`.reserve` and its fields). The alias exports dotted, like any other
scope member, so both the owner and its importers write `sc.name`:

```ca65
; state.s
.reserve thing as S in st

; main.s
.import "state"
.scope sc {
    fd   = thing.b        ; imported .reserve field
    next = entry + 1      ; label of this module
}
    lda.l sc.fd
```

Nested named scopes publish the full path (`a.b.x`). Aliases declared in
anonymous blocks or macro bodies stay private to them.

## Workflow

```
$ a816 build --compile-only file1.s file2.s
$ a816 build file1.o file2.o -o output.ips
```

Mixed source + object inputs work too:

```
$ a816 build file1.s file2.o -o output.ips
```

## Incremental builds

Compiled objects are cached in `--obj-dir` (default `build/obj`) next to a
`<module>.deps` sidecar listing every file the object was built from. A module
recompiles when:

- its object or `.deps` sidecar is missing, or
- any recorded dependency (the module source, an `.include`d file, or an
  `.incbin` / `.table` asset) is newer than the object, or
- a module it `.import`s recompiled (the importer bakes in the importee's
  exported constants, so a stale object would carry old values).

Editing a constant in an `.include`d file therefore invalidates every module
that pulls it in; no `rm -rf build/obj` needed.

## Reproducible output

Builds are deterministic: identical source produces an identical ROM,
independent of `PYTHONHASHSEED`. Module discovery, compilation, and pool
placement order are stable (dependencies are sorted, not iterated from a set),
so an address-sensitive bug surfaces the same way on every rebuild instead of
flickering with the interpreter's hash seed.

## Auto-generated symbols

`.incbin "assets/data.bin"` defines a label and a size constant named
after its path, `assets_data_bin` and `assets_data_bin__size`, so
callers can do bounds checks without tracking the length manually.

## Pools across modules

`.pool NAME { range ... }` declared in one module is visible to any
module that `.import`s it. The decl serialises into both files'
`.o`; the linker merges decls by name: the ranges are unioned, and
`fill`, `strategy` and `contexts` must agree (a mismatch is an error),
so a shared
preamble can hand out pool names like `client` and `engine` and
sub-modules `.alloc … in client` against them without redeclaring.

See [Freespace pools](freespace-pools.md) for `.alloc`, `.relocate`,
and `.reclaim` semantics.

## Preamble

Shared compile-time material (feature flags, register-size hints,
`.table` configuration, pool decls, typed binds) lives in a `.s`
file imported explicitly from each entry point: `.import "preamble"`
at the top of `main.s`. The inline classifier picks up the
preamble's structs / pools / constants; runtime symbols become
externs the linker resolves.
