# Editor setup

`a816-lsp-server` ships with the package and speaks LSP over stdio.
Any editor that can launch an LSP for a file extension can use it.
Configure it for `.s` and `.i` files.

See [LSP](lsp.md) for the full feature list and the `a816.toml` /
`;! a816-lsp entrypoint` discovery rules.

## Neovim (`nvim-lspconfig`)

```lua
vim.filetype.add({ extension = { s = "a816", i = "a816" } })

local lspconfig = require("lspconfig")
local configs = require("lspconfig.configs")

if not configs.a816 then
    configs.a816 = {
        default_config = {
            cmd = { "a816-lsp-server" },
            filetypes = { "a816" },
            root_dir = lspconfig.util.root_pattern("a816.toml", ".git"),
            single_file_support = true,
        },
    }
end

lspconfig.a816.setup({})
```

## VS Code

There's no published extension yet. Wire `a816-lsp-server` through a
generic LSP client extension (e.g., `mattn.lsp` or
`SteefH.external-formatters`), or write a 30-line extension:

```jsonc
// .vscode/settings.json
{
    "files.associations": {
        "*.s": "a816",
        "*.i": "a816"
    }
}
```

```typescript
// extension.ts (snippet)
import * as vscode from "vscode";
import { LanguageClient, ServerOptions, TransportKind } from "vscode-languageclient/node";

export function activate(context: vscode.ExtensionContext) {
    const serverOptions: ServerOptions = {
        run:   { command: "a816-lsp-server", transport: TransportKind.stdio },
        debug: { command: "a816-lsp-server", args: ["--verbose"], transport: TransportKind.stdio },
    };
    const client = new LanguageClient(
        "a816",
        "a816 LSP",
        serverOptions,
        { documentSelector: [{ scheme: "file", language: "a816" }] },
    );
    client.start();
    context.subscriptions.push(client);
}
```

## Helix

`languages.toml`:

```toml
[[language]]
name = "a816"
scope = "source.a816"
file-types = ["s", "i"]
roots = ["a816.toml"]
language-servers = ["a816-lsp-server"]
indent = { tab-width = 4, unit = "    " }

[language-server.a816-lsp-server]
command = "a816-lsp-server"
```

## Emacs (`eglot`)

```elisp
(define-derived-mode a816-mode prog-mode "a816"
  "Major mode for 65c816 assembly.")

(add-to-list 'auto-mode-alist '("\\.s\\'" . a816-mode))
(add-to-list 'auto-mode-alist '("\\.i\\'" . a816-mode))

(with-eval-after-load 'eglot
  (add-to-list 'eglot-server-programs
               '(a816-mode . ("a816-lsp-server"))))

(add-hook 'a816-mode-hook #'eglot-ensure)
```

## JetBrains IDEs

The repository ships an IntelliJ plugin under `intellij/`. Build and
install it from sources, or wire `a816-lsp-server` through the
Generic LSP plugin if you prefer a thinner integration.

## Format-on-save

Most LSP clients can route format-on-save through the server's
formatting capability. The server runs the same fluff formatter as
`a816 format`, so save-formatted output matches what CI's
`a816 format --check` expects.

Neovim:

```lua
vim.api.nvim_create_autocmd("BufWritePre", {
    pattern = { "*.s", "*.i" },
    callback = function() vim.lsp.buf.format({ async = false }) end,
})
```

VS Code:

```jsonc
{
    "[a816]": {
        "editor.formatOnSave": true
    }
}
```

## Sanity check

After wiring up, opening a `.s` file should produce:

- Parser errors as red squigglies (`source: a816 lsp`).
- Fluff lint hits as yellow squigglies (`source: a816 fluff`,
  carrying the rule code: `DOC001`, `E501`, `N801`, …).
- Hover over a label / macro / scope / `.import` token yields the
  attached docstring.
- Goto-definition hops across modules.
