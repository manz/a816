# Splitting a project into modules

Once a patch grows past one screen, splitting it into modules pays off:
faster incremental builds, scoped symbol namespaces, and reusable
helpers across hacks. This walkthrough builds a two-module project
end to end.

## Starting layout

```
my-hack/
├── a816.toml
├── src/
│   ├── main.s
│   └── modules/
│       └── vwf.s
└── build/
```

`a816.toml`:

```toml
entrypoint    = "src/main.s"
include-paths = ["src/include"]
module-paths  = ["src/modules"]
```

## A reusable module

`src/modules/vwf.s` exports a small variable-width-font init routine:

```ca65
"""VWF helpers shared across the hack."""

.scope vwf {
    init:
        """Initialise VRAM tile slots used by the VWF renderer."""
        lda.b #0x00
        sta.w 0x2115
        rts

    ; private — only callable from inside this module.
    _zero_pad:
        rep #0x20
        lda.w #0x0000
        rts
}
```

Symbols inside `.scope vwf { ... }` export as `vwf.init`. The leading
`_` on `_zero_pad` keeps it LOCAL to the module — other modules cannot
reference it through the linker.

## The entrypoint pulls it in

`src/main.s`:

```ca65
"""Top-level patch."""

.import "vwf"

*= 0x008000
main:
    """Entry — run the VWF init then loop."""
    jsl vwf.init
    bra main
```

`.import "vwf"` resolves in this order:

1. `vwf.o` in `--obj-dir` (default `build/obj`).
2. `vwf.s` on a search path (`-I` / `--module-path` or the same directory).

When only `.s` is available, the build driver compiles to `.o` first
on the fly.

## Build

A single command does compile + link with auto-imports:

```
$ a816 build src/main.s -o build/patch.ips
```

If you want to inspect the intermediates:

```
$ a816 build --compile-only src/modules/vwf.s
$ xobj --regions --symbols src/modules/vwf.o
```

## Cross-module references with `.extern`

When module A defines a symbol module B needs, declare it on the
caller side. Sub-symbols (`name.sub`) need their own declaration:

```ca65
.extern vwf.init
.extern vwf.init.commands_list   ; nested name needs its own .extern

main:
    jsr.w vwf.init
    rts
```

The linker verifies every `.extern` resolves to a definition and
fails the build (with a useful message) if any are missing.

## Constants over externs

You can compute compile-time constants from external symbols. The
expression is recorded in the `.o`'s alias table and resolved at link
time:

```ca65
.extern target

font_ptr  = target + 0x40
font_high = (target >> 16) & 0xFF
```

Caveats: the alias's expression has to evaluate to a constant at link
time. Macros that reference externals via `source >> 16` generally
work; constant assignments that the assembler tries to fold into a
single literal during compile (e.g., `font_ptr := assets_menu_font_dat`)
don't, so use the external symbol directly in the instruction.

## Prelude

A `--prelude path.s` argument prepends the file to every module
compilation. Useful for project-wide feature flags or `.table`
defaults so each module doesn't have to repeat them.

```
$ a816 build src/main.s --prelude src/prelude.s -o build/patch.ips
```

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
