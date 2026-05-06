# Language Server

`a816-lsp-server` implements the Language Server Protocol over stdio.
It speaks to any LSP-capable editor (VS Code, Neovim, Helix, Emacs).

## Features

- **Diagnostics** on open / save / change. Parser errors plus
  `a816 fluff` lint hits (DOC*, E501, N801, N802) reported with rule
  codes and `source = "a816 fluff"`.
- **Goto-definition** for labels, symbols, macros, struct fields, and
  `.import` / `.include` targets.
- **Find references** across the workspace.
- **Rename** with prepare-rename validation.
- **Hover** on labels, symbols, macros, struct fields, `.import` /
  `.include` tokens (shows the target module's leading docstring).
- **Completions** for opcodes, keywords, registers, labels, scopes,
  macros, and imported symbols.
- **Signature help** while typing macro invocations.
- **Document symbols** + **workspace symbol** search.
- **Semantic tokens** (full document).
- **Document / range formatting** — runs the same fluff formatter.
- **Workspace-aware module resolution** via `a816.toml`.

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

### Auto-discovering the entrypoint

Drop a one-line pragma at the top of the entrypoint source instead of
declaring it in `a816.toml` — the server picks the first matching file
in the workspace:

```ca65
;! a816-lsp entrypoint
"""Top-level module for the patch."""
*= 0x008000
    jsr.l init
```

Useful for monorepos with several patches; each patch's main file
self-identifies and the server compiles whichever one owns the
currently open document.

## Running standalone

```
$ a816-lsp-server
```

Reads LSP messages from stdin, writes to stdout. Use `--verbose` for log
output on stderr.
