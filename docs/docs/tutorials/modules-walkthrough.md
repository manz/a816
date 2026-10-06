# Splitting a project into modules

Once a patch grows past one screen, splitting it into modules pays off:
faster incremental builds, scoped symbol namespaces, and reusable
helpers across hacks. This walkthrough builds a two-module project
end to end. The project is in the repository under
`docs/examples/modules-walkthrough/`; the test suite builds it and
checks the patch, so what you read here is what assembles.

## Starting layout

```
modules-walkthrough/
├── a816.toml
└── src/
    ├── main.s
    └── modules/
        └── vwf.s
```

`a816.toml` names the entry file, where `.import` looks, and the
cartridge (a 512 KB LoROM board):

```toml
--8<-- "modules-walkthrough/a816.toml"
```

## A reusable module

`src/modules/vwf.s` exports a small variable-width-font init routine:

```ca65
--8<-- "modules-walkthrough/src/modules/vwf.s"
```

The module owns its placement: `.alloc vwf_code at 0x018000 { ... }`
pins its bytes (use `.alloc vwf_code in POOL { ... }` to let the
linker pick the address instead). Code left outside any `.alloc` /
`*=` is rejected with `E0310`; see [Placement](../modules.md#placement).

Symbols inside `.scope vwf { ... }` export as `vwf.init`. The leading
`_` on `_zero_pad` keeps it LOCAL to the module — other modules cannot
reference it through the linker.

## The entrypoint pulls it in

`src/main.s`:

```ca65
--8<-- "modules-walkthrough/src/main.s"
```

`.import` sits in the file prelude, before any placement; inside an
`.alloc` or after a `*=` it is `E0311`. Importing a module does not
place it: `vwf.s` owns its address through its own `.alloc`.

`.import "vwf"` resolves `vwf.o` in `--obj-dir` (default `build/obj`)
or `vwf.s` on the module paths (`module-paths` in `a816.toml`, `-I` on
the command line), compiling it first when only the source exists. The
importing file's own directory is not searched: a module in a
subdirectory is imported by its path (`.import "ingame/items"`).

## Build

A single command does compile + link with auto-imports:

```
$ a816 build src/main.s -o build/patch.ips
```

The patch has two records: the entry at file offset `0x000000`
(`jsl $01:8000` then `bra main`) and the VWF routine at `0x008000`,
bank `$01` of the LoROM image.

If you want to inspect the intermediates:

```
$ a816 build --compile-only src/modules/vwf.s
$ xobj --sections --symbols src/modules/vwf.o
```

## Cross-module references

When module A `.import`s module B, every runtime symbol B exports
(GLOBAL labels, alloc names, `.incbin` auto-symbols) is automatically
available as an extern in A. No explicit `.extern` needed — the
per-node import classifier emits the extern stubs from B's `.o` and
inlines B's compile-time content (structs, macros, typed binds,
pool decls) for codegen.

`main.s` above does exactly that: `jsl vwf.init` resolves through the
extern stub the import created.

`.extern name` is still useful for symbols you don't want to import
the owning module for — build-script-injected constants, third-party
`.o` drops, or sub-symbols of a `.label`-declared name that the
auto-classifier doesn't reach.

The linker verifies every extern resolves to a definition and fails
the build (with a useful message) if any are missing.

## Constants over externs

You can compute compile-time constants from external symbols. The
expression is recorded in the `.o`'s alias table and resolved at link
time:

```ca65
.extern target

font_ptr  = target + 0x40
font_high = (target >> 16) & 0xFF
```

The expression may use any operator; the linker evaluates it once
`target` has its address. Use `=` here: `:=` wants its value at once,
which a module importing this one can't give it. Typed views over an
extern (`view := (target as T)`) are the exception and link as well.

## Preamble

Put shared compile-time material (feature flags, `.table` defaults,
pool decls, typed binds, structs) in a `.s` file and import it
explicitly from each entry point:

```ca65
.import "preamble"
```

The inline classifier picks up the preamble's compile-time
symbols; runtime symbols become externs the linker resolves.

## Lint as you go

```
$ a816 check src/
```

The relevant rules for module work:

- **DOC001** — every module needs a leading docstring.
- **DOC002** — public macros / scopes / labels need attached docs.
- **DOC003** — docstrings sitting outside their target's body get
  flagged. Move them inside `{ ... }` or above the label.

See [Fluff (lint + format)](../fluff.md) for the full rule set and
the `; noqa` suppression syntax.
