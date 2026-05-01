# Language Server

`a816-lsp-server` implements the Language Server Protocol over stdio.
It speaks to any LSP-capable editor (VS Code, Neovim, Helix, Emacs).

## Features

- Diagnostics on open / save (errors surfaced as structured LSP diagnostics,
  not parsed from strings).
- Goto-definition for labels, symbols, macros, and `.import` targets.
- Hover info on symbols.
- Workspace-aware module resolution.

## Project configuration

Drop an `a816.toml` at the project root:

```toml
entrypoint    = "src/main.s"
include-paths = ["src/include"]
module-paths  = ["src/modules"]
```

- `entrypoint` — the file the server compiles for diagnostics.
- `include-paths` — directories searched by `.include`.
- `module-paths` — directories searched by `.import`.

Without `a816.toml` the server falls back to same-directory lookup.

## Running standalone

```
$ a816-lsp-server
```

Reads LSP messages from stdin, writes to stdout. Use `--verbose` for log
output on stderr.
