# Fluff: lint, format, fix

`a816 check`, `a816 format`, and `a816 fix` are the lint, format, and
autofix passes over `.s` / `.i` sources. All three share the parser
the assembler uses, so the rules see real AST, not regex hits.

Given no paths, they take the project the nearest `a816.toml` (from
the working directory up) describes: the sources under the
entrypoint's directory and every `module-paths` directory, leaving
out `build/`, `obj/` and dot directories. In a git work tree, files
git ignores are left out too, and the toml's `exclude` patterns
(`exclude = ["scratch", "old_*.s"]`, relative to the toml) leave out
the files and directories they match. Outside a project, name the
files.

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
  verbatim: alignment with the target is enforced by `DOC007`, not
  rewritten.
- Comment text.
- Data-directive layout: `.dw` / `.db` lines stay how the author wrote
  them. Use `; noqa: E501` to silence line-length on long data rows.

## Lint rules

Codes follow `ruff` conventions: `DOC*` for docstring coverage and
placement, `E***` for physical layout, `F***` for unused names,
`N***` for naming, `S***` for
struct casts, `OP***` for opcodes, `ST***` for program structure and
`UP***` for upgrades from legacy syntax.

| Code | What it flags |
|------|---------------|
| `DOC001` | Module is missing a leading docstring. |
| `DOC002` | Public macro / scope / label / `.label` directive is missing a docstring. |
| `DOC003` | Docstring sits directly above a public target. For macros / scopes, move it inside the body (first statement after `{`); for code labels (`name:`), move it below the label (first statement after the colon). `.label` directives are body-less, so the docstring **above** is the canonical attach point and DOC003 does not fire. |
| `DOC004` | Orphan docstring used as a comment. Convert to `;` or attach to a target. |
| `DOC005` | A leading comment block (≥2 `;` lines, or a block comment with embedded newlines) sits where a docstring belongs. |
| `DOC006` | Public target carries both a leading comment block and a docstring; pick one. |
| `DOC007` | Docstring content is under- / over-indented relative to its opening `"""` (mirrors pydocstyle's D207 / D208). |
| `E501` | Source line longer than 120 characters. |
| `F001` | Module-private (`_`) constant, macro, struct, pool, `.label` or `.reserve` that nothing in its translation unit references. No other module can see it, so it is dead. A use in a file the module includes, or in a file including it, counts; a name in a shared `.i` that no includer uses is reported once, in the `.i`. `.alloc _name` is a placement handle and never flagged. |
| `F002` | Same for code labels (`_name:`, module-private or local to an alloc body) and `.scope _name`. **Opt-in**: a private label nothing branches to is often a marker for the debugger or xdds, so it runs only when selected by its exact code (`a816 check --select F002`), never by default or through the `F` prefix. A name reached only from outside the sources, like a test hooking `module._label` through the `.adbg`, looks unused to the rule. |
| `N801` | Label name is not snake_case (`_1`-style private labels pass). |
| `N802` | Constant name is not snake_case or SCREAMING_SNAKE_CASE. |
| `S001` | `(expr as T).field` / `p := (expr as T)` / `.istruct T { ... }` references a struct type that isn't declared in the current translation unit. |
| `S003` | Redundant cast: `(p as T).field` when `p` is already typed-bound to `T`. |
| `S004` | The same `(expr as T)` cast appears more than once in the file; promote it to a `:=` typed bind. |
| `OP001` | Explicit `.w` / `.l` size suffix a literal address would get anyway. Immediates are not flagged: `lda.w #0x1234` states the 16-bit accumulator the code expects. |
| `ST001` | Placement directive (`*=`, `.alloc ... at`, `.relocate`) nested inside an `.alloc` body: it re-anchors the code the alloc was placing. |
| `ST002` | `.import` outside the file prelude (after a placement or inside a block); the build rejects it with `E0311`. |
| `UP001` | Legacy `*= ADDR` placement should be `.alloc at ADDR { ... }`. |
| `W0002` | `.if` on a name the project defines nowhere: it reads as false, so a deleted or misspelt flag drops code silently. Declare a `-D`-only name under `[defines]` in `a816.toml`. |
| `W0001` | Reference to a name `.incbin` derived from its path (`assets_vwf_bin`, `assets_vwf_bin__size`); the build warns on it too. A warning: `a816 check` prints it but still exits 0. |

Per-node rules (`OP001`, `N801`, `N802`, `DOC007`, ...) look inside
`.alloc`, `.relocate`, `.scope`, macro, `.if` and `.for` bodies, so
code a module places itself is linted like top-level code.

Rules marked fixable in `a816 check` output carry `[*]` (safe) or
`[!]` (unsafe). Today: `S003`, `OP001`, `DOC003`, `DOC004`, `DOC006`,
`DOC007` ship a safe fix, and `W0001` does when the blob is alone in a
named alloc (`vwf_font`, `sizeof(vwf_font)`); `DOC005` and `UP001` ship
an unsafe fix, and so do `F001` / `F002`, which delete the definition
(a block one through its `}`, with the comment directly above it) when it
sits on lines of its own. A `.scope _name` is unwrapped into an anonymous
`{ }` instead, since its body emits bytes; an unused `.reserve` gets no
fix, since removing it moves every later reservation in its pool. A code
label loses only its own line and keeps the comment above, which
describes code that stays. A label heading a routine gets no fix: one
carrying a docstring, or starting code (an instruction or a macro call)
nothing falls into: at the file or block start, after an alloc body, or
after an `rts`, `rtl`, `rti`, `jmp`, `jml`, `bra`, `brl` or data, also as
the last statement of a block. Macro, struct and pool definitions in
between emit nothing and are looked through. The hit still reports;
deleting dead code is your call.

## Autofix: `a816 fix`

```
$ a816 fix src/                       # apply safe fixes in place
$ a816 fix --diff src/                # preview as unified diff, exit 1 if any
$ a816 fix --check src/               # exit 1 if any file would change
$ a816 fix --select DOC003,DOC004 src/  # only those rules
$ a816 fix --unsafe-fixes src/        # also apply UNSAFE fixes
```

`fix` prints one line per file it changes (`Fixed 3 hit(s) in src/x.s
(OP001 x2, DOC004)`, or `Would fix` under `--check`) and the hits no
fix covers. `--select` takes codes or prefixes and replaces the
`[lint]` table's `select`, as in `check`: it narrows the report as well
as the fixes, and names an opt-in rule to run it. `--diff` writes the
diff to stdout and its summary and the hits no fix covers to stderr, so
`a816 fix --diff | patch -p0` (or `| git apply -p0`) works; the headers
name the files as given, without git's `a/` `b/` prefixes. Diffs are
coloured only on a terminal (`FORCE_COLOR` and `NO_COLOR` override).

`--check` and `--diff` exit 1 only while a fix is pending, so a CI step
can gate on "nothing left to fix" in a tree that keeps hits no safe fix
covers; failing on those is `a816 check`'s job. A writing `a816 fix`
exits 1 while any hit remains after its fixes, and 0 once none does,
whether or not it changed a file; a script that needs to know whether
fixes were pending runs `a816 fix --check` first.

Safe fixes are guaranteed behaviour-preserving: they don't change
emitted bytes, drop comments, or restructure semantics. Unsafe fixes
might change runtime behaviour, repackage author whitespace, or
surface latent bugs as new build errors. `--unsafe-fixes` opts in;
without it, unsafe hits stay flagged but untouched.

Edits inside one file apply in reverse-offset order so an earlier
edit's replacement length can't invalidate a later edit's offsets.
Overlapping edits are dropped silently; re-run `a816 fix` and the
next pass produces non-overlapping edits.

### Migration recipe: `*=` → `.alloc at`

```
$ a816 fix --select UP001 --unsafe-fixes src/
$ a816 format src/
```

UP001 wraps each `*= ADDR` and the following body run (up to the next
placement directive) in `.alloc at ADDR { ... }`. The bank-edge
semantic shift is real: where legacy `*=` silently let body bytes
cross a bank boundary, the new form raises with the offending byte
named. Surfaces real bugs; you may want to walk through the build
errors after the fix.

### Editor integration: code actions

`a816-lsp-server` exposes every fluff fix as a `textDocument/codeAction`
quickfix. The editor's lightbulb (Ctrl-., Code Action menu) lists
each applicable fix; safe ones are marked `isPreferred=true` so most
editors default to them, unsafe ones carry an `(unsafe)` suffix in
the title.

Names with a single leading underscore (`_loop`, `_private_macro`) are
treated as private and skipped by `DOC002` / `DOC003` / naming rules.

## `a816 explain <CODE>`

Print a rule's rationale plus a minimal `bad` / `good` example pair.
Given a build diagnostic's code (`E0317`, `W0001`), it prints that
code's entry from the [error catalog](errors.md#code-catalog).

```
$ a816 explain DOC003
DOC003  docstring above macro/scope should live inside the body

For block-bodied targets (`.macro`, `.scope`) the canonical attach point is the
first statement inside the body. ...

Bad:

    """Module."""
    """Setup loop counter."""
    .macro setup_counter() {
        ldx.w #0
    }

Good:

    """Module."""
    .macro setup_counter() {
        """Setup loop counter."""
        ldx.w #0
    }
```

The examples are checked: tests round-trip every rule's `bad` / `good`
through `lint_text`, so a rule that drifts from its docs fails CI.

## Private symbols

Names with a single leading underscore (`_loop`, `_helper`, `_internal`)
are LOCAL to their module. Fluff doesn't require them to carry a
docstring (DOC002), and DOC005 / DOC006 don't apply to them: those
are public-API hygiene rules. They *can* still carry a docstring; if
they do, DOC003 / DOC004 / DOC007 enforce the same placement and
formatting rules as on public targets.

## Suppressing rules: `; noqa`

A trailing `; noqa` comment silences every rule on that line. Pass codes
to suppress selectively, ruff-style:

```ca65
.db 0x16, 0x20, 0x17, 0x20, 0x17, 0x20  ; long line of data  ; noqa: E501
MyLabel:                                ; noqa: N801
```

Code lists are case-insensitive: `; noqa: e501,n801` works the same.

## Selecting rules: `[lint]`

A rule the project breaks on purpose goes in `a816.toml` rather than on
every line:

```toml
[lint]
ignore = ["N802"]       # French CamelCase constants
# select = ["DOC", "N"] # only these; empty or absent runs every rule
```

Codes match by prefix, as in ruff: `N` is every naming rule. `check`,
`fix` and the LSP all honour the table. `a816 check --select CODES` and
`--ignore CODES` (comma-separated, or repeat the flag) replace the
table's list of the same name for one run. The other list still
applies: with `ignore = ["N802"]` in the table, `--select N802` shows
nothing; add `--ignore ""` to clear the table's ignore. A code no rule
starts with is an error (`E0516`). Parse errors always show.

An opt-in rule (`F002`) runs only when a `select` names its exact code:
`a816 check --select F002` audits it once, `a816 fix --select F002
--unsafe-fixes` deletes what it finds, and a project keeping it on lists
it next to the prefixes it wants, `select = ["DOC", "E", "F", "N", "OP",
"S", "ST", "UP", "W", "F002"]`.

A rule some files break on purpose (disassembled or generated sources
keeping their original names) is dropped for those files only:

```toml
[lint.per-file-ignores]
"src/disasm/*.s" = ["N801"]   # labels named after the original ROM
"src/gen" = ["N", "DOC"]      # a directory covers every file under it
"src/{menu,miss}.s" = ["N8"]  # {a,b} lists alternatives
```

Each glob is relative to `a816.toml` and matches like `exclude`; `*`
stays in one directory, `**` crosses them. The codes match by prefix
and an unknown one is `E0516`. `check`, `fix` and the LSP all honour
it, and `--select` / `--ignore` leave it in place. `check` and `fix`
warn about a glob no `.s` / `.i` file under the toml matches: a typo,
a wrong case, or a file that moved.

## `a816.toml` discovery

Both `a816 check` and `a816 format` walk upwards from each input file
looking for `a816.toml`. When found, the config's `include-paths` are
forwarded to the parser so `.include "..."` directives resolve the
same way they do under `a816 build`.

```toml
include-paths = ["src/include"]
```

`a816.toml` is the project file: `a816 build`, fluff and the LSP all
read it, so the three resolve `.include` and `.import` the same way.
`module-paths` lets struct-type rules follow `.import` chains;
`entrypoint` is only used by the LSP. See
[Project configuration](index.md#project-configuration-a816toml) for
every key.

## Editor integration

The `a816-lsp-server` runs `lint_text` on every analyze and surfaces
results as `DiagnosticSeverity.Warning` with `source = "a816 fluff"` and
the rule code attached. No additional setup beyond the LSP: see
[LSP](lsp.md).
