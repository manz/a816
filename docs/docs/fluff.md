# Fluff — lint and format

`a816 check` and `a816 format` are the lint and format passes over
`.s` / `.i` sources. Both share the parser the assembler uses, so the
rules see real AST, not regex hits.

## Format

```
$ a816 format src/             # rewrite in place
$ a816 format --check src/     # exit non-zero if any file would change
$ a816 format --diff src/      # print unified diffs without writing
$ a816 format -                # read stdin, write formatted text to stdout
```

The formatter is a fixed-point pass: `format(format(x)) == format(x)`.
What it touches:

- Indents instructions, aligns operands, lowercases mnemonics.
- Wraps `.macro` definitions and macro invocations longer than the
  configured `max_line_length` (default 120) with hanging-indent
  parameter lists.
- Strips per-line trailing whitespace and dropped wrapper-blank lines
  inside docstrings.

What it deliberately does not touch:

- Docstring content (text between `"""..."""`). Author's prose stays
  verbatim — alignment with the target is enforced by `DOC007`, not
  rewritten.
- Comment text.
- Data-directive layout — `.dw` / `.db` lines stay how the author wrote
  them. Use `; noqa: E501` to silence line-length on long data rows.

## Lint rules

Codes follow `ruff` conventions: `DOC*` for docstring coverage and
placement, `E***` for physical layout, `N***` for naming.

| Code | What it flags |
|------|---------------|
| `DOC001` | Module is missing a leading docstring. |
| `DOC002` | Public macro / scope / label is missing a docstring. |
| `DOC003` | Docstring sits directly above a public macro / scope; should be moved inside the body (first statement after `{`). |
| `DOC004` | Orphan docstring used as a comment. Convert to `;` or attach to a target. |
| `DOC005` | A leading comment block (≥2 `;` lines, or a block comment with embedded newlines) sits where a docstring belongs. |
| `DOC006` | Public target carries both a leading comment block and a docstring; pick one. |
| `DOC007` | Docstring content is under- / over-indented relative to its opening `"""` (mirrors pydocstyle's D207 / D208). |
| `E501` | Source line longer than 120 characters. |
| `N801` | Label name is not snake_case. |
| `N802` | Constant name is not snake_case or SCREAMING_SNAKE_CASE. |

Names with a single leading underscore (`_loop`, `_private_macro`) are
treated as private and skipped by `DOC002` / `DOC003` / naming rules.

## Suppressing rules — `; noqa`

A trailing `; noqa` comment silences every rule on that line. Pass codes
to suppress selectively, ruff-style:

```ca65
.db 0x16, 0x20, 0x17, 0x20, 0x17, 0x20, ... ; noqa: E501
MyLabel:                                    ; noqa: N801
```

Code lists are case-insensitive: `; noqa: e501,n801` works the same.

## `a816.toml` discovery

Both `a816 check` and `a816 format` walk upwards from each input file
looking for `a816.toml`. When found, the config's `include-paths` are
forwarded to the parser so `.include "..."` directives resolve the
same way they do under `a816 build`.

```toml
include-paths = ["src/include"]
```

This means a project that already configures the LSP via `a816.toml`
gets the same module / include resolution from fluff with no extra
flags. The `entrypoint` and `module-paths` fields are read but only
consumed by the LSP today.

## Editor integration

The `a816-lsp-server` runs `lint_text` on every analyze and surfaces
results as `DiagnosticSeverity.Warning` with `source = "a816 fluff"` and
the rule code attached. No additional setup beyond the LSP — see
[LSP](lsp.md).
