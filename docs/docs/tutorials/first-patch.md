# Your first patch

Goal: write a tiny IPS patch that overwrites a few bytes in a SNES
ROM. We'll cover the toolchain, the canonical project layout, and the
build / lint / format loop.

## Install

```
$ pip install a816
```

The package ships several binaries:

- `a816` / `x816`: the assembler CLI.
- `a816-lsp-server`: Language Server, see [LSP](../lsp.md).
- `xdds`: SNES-aware hex dump and disassembler.
- `xobj`: object-file inspector.

## Project layout

Anything resembling this works:

```
my-patch/
├── a816.toml          # project config (entrypoint + paths)
├── src/
│   └── main.s
└── build/             # outputs land here
```

`a816.toml` declares the entrypoint and search paths the toolchain
uses for module resolution and the LSP:

```toml
entrypoint    = "src/main.s"
include-paths = ["src/include"]
module-paths  = ["src/modules"]
```

## Write the patch

`src/main.s`:

```ca65
"""Tutorial patch: overwrite a single instruction."""

*= 0x008000          ; SNES bus address; LowROM maps to ROM offset 0x000000
    lda.b #0x00      ; replaces the original `lda.b #0x42`
    rts
```

The `"""docstring"""` at the top satisfies the [DOC001](../fluff.md)
lint rule and gives the LSP something to surface in module-level
hover.

The legacy `*= ADDR` shape is shown here for continuity with older
patches in the wild. The equivalent modern form is:

```ca65
"""Tutorial patch: overwrite a single instruction."""

.alloc at 0x008000 {
    lda.b #0x00
    rts
}
```

`.alloc at` makes the placement explicit and surfaces bank-edge
overflow as a hard error instead of silently wrapping. Both shapes
build into the same patch; `a816 fix --select UP001 --unsafe-fixes`
converts one to the other.

## Build

```
$ a816 build src/main.s -o build/patch.ips
```

`build` accepts mixed `.s` / `.o` inputs; the bare `a816 src/main.s
-o build/patch.ips` form still works and routes through `build` for
back-compat.

To verify what the patch contains:

```
$ xdds rom.sfc --ips build/patch.ips -s '$00:8000' -d -n 16
```

`xdds` reads the ROM, applies the patch in memory (the ROM file is
untouched) and disassembles from the given address.

## Lint and format

```
$ a816 check src/         # report lint hits (see Fluff for the rules)
$ a816 format src/        # rewrite in place (idempotent)
$ a816 format --check src/   # CI mode: exit non-zero if changes pending
```

See [Fluff (lint + format)](../fluff.md) for the full rule list and
how to suppress with `; noqa`.

## Editor support

Point your editor at `a816-lsp-server` and you get diagnostics
(parser errors *and* fluff lint hits), goto-definition,
find-references, rename, hover, completion, signature help, and
formatting on save. Concrete editor configs live in
[Editor setup](../editor-setup.md).

## What's next

- [Splitting a project into modules](modules-walkthrough.md): separate
  compilation, `.import`, `.extern`.
- [Directives](../directives.md): full reference for every assembler
  directive.
- [Modules](../modules.md): visibility rules, search paths, prelude.
