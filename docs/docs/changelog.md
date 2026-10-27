# Changelog

## Unreleased

- **Breaking (Python API):** direct assembly is gone. Every build
  compiles to objects and links, as `a816 build` already did.
  `Program.assemble`, `assemble_as_patch`, `assemble_with_emitter`,
  `assemble_string_with_emitter` and `emit`, `AssemblyMode.DIRECT`,
  `WriteAuditor` / `OverlapError` and `LinkedModuleNode` are removed.
  To assemble a source string to bytes, use
  `a816.snippet.assemble_source` (see [Python usage](python_usage.md));
  it raises `AssemblyError` with the diagnostics. Unplaced code is
  refused (E0310): the source sets its own `*=` or `.alloc`. The CLI is
  unchanged.
- The Renovate preset `github>manz/a816//renovate/a816` is a whole
  config: a816, katsuji and the actions in one pull request, the other
  dependencies in another, each group's majors apart. Renovate merges the first two
  once their checks pass; majors wait for a human. A project's
  `renovate.json` only extends it. Presets are read from a816's default
  branch, so this applies as soon as it merges, not at release.
- a816 assembles SPC700 code, the SNES sound CPU. A `*.spc.s` file is
  SPC700; `.embed "driver"` in the 65816 build links `driver.spc.s` and
  its imports as an ARAM program, places its image, and defines
  `driver.__base`, `driver.__size` and `driver.LABEL`. A build whose
  entry is `*.spc.s` (or `--arch spc700`) writes the ARAM image (`-f
  aram`). Objects record their CPU and the linker refuses to mix them.
  The LSP, `a816 check` and `a816 format` take SPC700 files. See
  [SPC700](spc700.md).
- `@std/snes/spc`: `spc_ipl_upload(src, size, aram, entry)`, the S-CPU
  side of the SPC700 boot upload. `@std/spc700/dsp`, for SPC700 code:
  the I/O and S-DSP registers and `dsp_write` / `dsp_write_a` /
  `dsp_read`.
- Unary `<x` and `>x` take the low and high byte of a word (`x & 0xFF`,
  `(x >> 8) & 0xFF`), in both CPUs' code and through the linker. Every
  unary operator now binds tightest: `-x * 2` was `-(x * 2)`, the same
  value.
- `xdds --debug` names an address only after a label. Constants in the
  `.adbg` (`boot.__size`, an embedded driver's `driver.start`) no longer
  show up as labels at addresses that merely equal their value; `--sym`
  still finds them by name.
- `build_with_imports(objects={"name": obj})` links objects a Python
  generator built in memory with the compiled modules; source names
  their labels with `.extern`. See [Python usage](python_usage.md#generated-objects).
- N801 accepts digit-only private labels (`_1`, `_2_0`): a name with
  no letters has no case to get wrong. `_Foo` is still flagged.
- `a816 format --arch spc700 -` formats SPC700 code read from stdin.
  Files still take their CPU from the `.spc.s` suffix; `--arch` with
  file paths is an error.
- E0003 names an unknown uppercase directive (`.MEMORYMAP`, `.DB`)
  with the caret on it; it used to read "unknown directive `.`".
- `.db` / `.dw` / `.dl`, `.istruct` and `.patch` bytes record their
  source line in objects and in the `.adbg`: an alloc holding only data
  used to carry none. One entry per directive line, not per value.
- Link-time E0404 names the `.alloc` (or `.reserve`) that does not
  fit, by `file:line`, even when its body is only data or bss.
- `.incbin` no longer binds names derived from its path
  (`assets_vwf_bin`, `assets_vwf_bin__size`), deprecated since 1.1 under
  `W0001`. A leftover reference is undefined (`E0200`, or the linker's
  unresolved symbol), and the error names the `.incbin` it came from and
  what to write: the alloc's `NAME` / `sizeof(NAME)`, or a label.
  `a816 fix` still rewrites them. `E0347` is retired.
- A build driven through `build_with_imports()` with no logging
  configured prints its warnings again. The build's warning recorder
  counted as a handler, which turns off Python's default stderr output,
  so a project that never called `logging.basicConfig()` saw none.
- The linker refuses a one-byte operand that cannot hold its value
  (E0402) instead of keeping its low byte. `lda.b far` with `far` at
  $01:8000 assembled to `lda.b $00`, and `lda #far` with an 8-bit A to
  `lda #$00`, when `far` came from another module; a local one was
  already refused at compile time. A direct-page operand must name an
  address the direct page reaches: $0000-$7FFF of banks $00-$3F /
  $80-$BF, or $7E:0000-$1FFF; a stack-relative one an offset 0..0xFF.
  Objects are rebuilt.
- Objects store link-time expressions (operands, aliases, `.assert`s)
  as operator lists instead of source text, and the linker evaluates
  them without re-parsing. A branch inside a precompiled `.o`
  imported by a direct build lands on its label again; it was off by
  the operand's address. `xobj` prints them back fully parenthesised,
  numbers from 10 up in hex. Objects from earlier a816 are rebuilt.
- Link-time operands, aliases and `.assert`s are built from the parsed
  expression instead of rewritten source text. Three names the text
  rewrite got wrong now link right: a number whose digits spell a nearby
  local label (`0x1FFF` beside a label `x1FFF` linked as 0), a scoped
  name beside a local label of the same last part (`Foo.loop` beside a
  local `loop`), and a quoted string in an `.assert` that matched a label
  name, and a macro parameter that shares a name with one an alias reads
  (`ptr = base + ext` used in `.macro m(base)`) no longer replaces it.
  A macro argument passed on to an inner
  macro is written to the object once; it used to appear twice in the
  `.adbg` symbols.
- The linker no longer reads a name no module exports from another
  module's private label of the same name (`_loop`): it is unresolved,
  as written. A module that `.extern`s another's `_name` gets an
  unresolved symbol: drop the underscore where it is defined to export
  it.

## 1.2.0 (2026-10-25)

- `a816 format` keeps comments where the author put them in a block
  body. A label with a trailing comment stays at column 0 like a bare
  one, a comment trailing a block's `{` stays on that line, a comment
  hanging under a trailing comment stays under it, and an indented
  comment takes the body indent (one at column 0 stays there). A line
  whose trailing comment ends in `:` is no longer taken for a label.
- `a816 fix --select` narrows the report too: hits of other rules no
  longer show (and no longer fail the run). It takes prefixes and an
  unknown code is `E0516`, as in `check`. `fix` prints one line per
  file with its count (`Fixed 3 hit(s) in src/x.s (OP001 x2,
  DOC004)`). `--diff` and `--check` say what would be fixed and would
  remain, and exit 1 only while a fix is pending, as `format --diff`
  (`--check` used to fail on hits no fix covers too). `--diff` keeps
  stdout for the diff, hits and summary go to stderr, and the
  `fix --diff` / `format --diff` output carries no colour codes when
  piped.
- `[lint.per-file-ignores]` in `a816.toml` drops rule codes in the
  files a glob matches: `"src/disasm/*.s" = ["N801"]`. Globs are
  relative to the toml, a directory covers its files, `{a,b}` lists
  alternatives, and `check`, `fix` and the LSP honour it. `check` and
  `fix` warn about a glob that matches no source.
- `.pad_to OFFSET [, BYTE]` fills the current `.alloc` body or `*=`
  section with `BYTE` (default 0) until it is `OFFSET` bytes long. A
  block already longer than `OFFSET` is `E0349`.
- `a816 check` flags dead module-private names: `F001` for a `_`
  constant, macro, struct, pool, `.label` or `.reserve` nothing in its
  translation unit references (uses through `.include` count both
  ways), `F002` (opt-in, `--select F002`) for `_` code labels and
  scopes. Both carry an unsafe fix that deletes the definition (a
  scope is unwrapped, a `.reserve` and a label heading a routine have
  no fix). A project with dead private names gets new `F001` hits on the bump.
- a816 depends on `katsuji` (no extras: no numpy or pillow) and its
  `.table` directive reads tables with `katsuji.script.Table`. The
  `script` package moved there: `from script import ...` still works
  but warns, and a816 1.3 removes it. Switch to
  `from katsuji.script import ...`; `script.formulas.long_low_rom_pointer`
  becomes `katsuji.script.formulas.long_pointer(bus.get_address(base))`.
- Lint rules look inside `.alloc` and `.relocate` bodies. They used to
  skip them, so `OP001`, `N801`, `N802` and `DOC007` checked almost
  nothing in a project whose modules own their placement. Expect new
  hits on the bump; `a816 fix` removes the redundant suffixes
  (`OP001`) and re-indents docstrings (`DOC007`).
- `OP001` no longer flags `.w` / `.l` on an immediate: its width is
  the M/X register width, which the suffix states. Literal addresses
  (`sta.w 0x2115`) are still flagged.
- a816 requires Python 3.14 or later; CI only ever tested 3.14, and
  3.10 already failed to import.
- `@std/snes/sdd1` and `@std/snes/spc7110` name the S-DD1 and SPC7110
  registers (and the Epson RTC on SPC7110-RTC boards), with small
  macros: decompress, bank select, data port seek.
- `sizeof(buf.field)` and `countof(buf.field)` on a reservation typed
  `.reserve buf as T` size the field like `sizeof(T.field)`, imported
  reservations included. They used to fail with E0321, or at link with
  "cannot resolve `buf.field.__size`" and no source line.
- A legacy `.map` with `mirror_bank_range` answers
  `logical_address(offset, near=...)` in the mirror `near` sits in,
  like board regions already did. `xdds.physical_to_logical` takes
  `near` too, and skips ROM regions that cannot reach the offset
  (ExHiROM's low banks) instead of raising.

### Upgrading from 1.1

- New lint hits fail `a816 check` (and the shared lint action, which
  runs the project's own a816) on the bump: rules now look inside
  `.alloc` bodies, and `F001` is on by default. `a816 fix` clears
  `OP001` and `DOC007`. `F001` deletes code, so its fix is unsafe:
  review it with `a816 fix --select F001 --unsafe-fixes --diff`, or
  set `[lint] ignore = ["F001"]` to deal with it later. `N801` and
  `N802` have no fix: rename the names, or give disassembled,
  generated or legacy-named sources (`L_D3541D`, a hack's CamelCase
  routines) `[lint.per-file-ignores]`.
- `from script import ...` warns: switch to `from katsuji.script import
  ...` (see above). katsuji ships type hints, so a mypy override for
  `script` can go.
- Python 3.14 or later is required.
- Run `a816 format` once after the bump and commit it on its own. A
  project formatted before 1.1.4, or never, gets the block-body
  re-indent: a large, whitespace-only diff.
- 1.2 stops moving comments, but comments an older format pass already
  moved stay where it left them. Find them in that format commit's diff
  (comment lines, `^[-+]\s*;`) and put them back by hand.

## 1.1.7 (2026-10-20)

- `pr-artifact` writes the comment the game repos' own workflows wrote
  ("Build Ready!", the artifact, its link, retention, the commit), now
  with the files and their sizes and a link straight to the artifact
  rather than the run. Two runs starting together no longer leave two
  comments: the extras are deleted, the oldest kept. A failure deletes
  them all. `marker` is optional: it defaults to `A816-BUILD-COMMENT`;
  a repo's old marker keeps its existing comments updated.
- `nightly` defaults to the repos' own title (`Nightly Build <run>`) and
  notes; `title` takes `{run}`, `{sha}` and `{version}`.
- `--dry-run` prints the comment or notes it would post.

## 1.1.6 (2026-10-20)

- The wheel ships `script/py.typed`, so a project importing `script`
  (`Table`, `script.pointers`) type-checks under strict mypy instead of
  failing with `import-untyped`. Only an editable install hid it.

## 1.1.5 (2026-10-20)

- S-DD1 boards (`SHVC-LN3B-01`): the mcu windows `boards.bml` leaves
  unmasked take the power-on MMC bank masks (`0x808000` for
  `00-3f,80-bf:8000-ffff`, `0xC00000` for `c0-ff`), so their addresses
  resolve to the right ROM offsets. An explicit mask still wins; banks
  a game remaps at runtime are not modelled.
- Sealing writes the SNES header checksum, which only the base ROM
  can give, when `a816.toml` declares the cartridge: the header is
  found through the bus map (`$00:FFC0`), the sum covers the ROM padded
  to `rom_size`, mirrored when that is not a power of two. `a816 bps
  seal` encodes the patch again with it (`--no-checksum` to skip); the
  new `a816 ips seal` appends the record that writes it, byte for byte
  what ff6's `tools/checksum.py` wrote. `E0517` to `E0519` cover a bad
  IPS, a copier-headered base ROM and a project with no cartridge.
- `a816 ips seal` also pads: when the patched ROM falls short of
  `rom_size`, a one-byte zero record at `rom_size - 1` comes before the
  checksum. ff4's 2 MB IPS over its 1 MB base gave 0x18A3E7 bytes, which
  the Analogue Pocket SNES core read as carrying a copier header
  (`size & 0x200`). `a816 build` leaves IPS output as it was: an IPS
  used as a byte container (ff6's intro) stays contiguous.
- `a816 bps diff --source ROM --target PATCHED -o x.bps` writes a
  sealed linear patch between two ROMs (`--metadata FILE` for its
  metadata), for a patch a816's linker does not build alone.
- `[lint]` in `a816.toml` picks the lint rules: `select` and `ignore`
  take rule codes or prefixes (`ignore = ["N802"]`). `check`, `fix` and
  the LSP honour it; `a816 check --select/--ignore` replace its lists
  for one run. A code no rule starts with is `E0516`.
- Shared GitHub actions under `manz/a816/actions/`: `setup`,
  `decrypt-rom`, `lint`, `build`, `seal`, `pr-artifact`, `nightly` and
  `release`. Each calls `a816 ci <step>`, which also runs locally, with
  `--dry-run` for the publishing steps. A Renovate preset,
  `github>manz/a816//renovate/a816`, bumps the actions and the package
  together. See [GitHub actions](ci.md).

## 1.1.4 (2026-10-20)

- `a816 build -f bps` writes a BPS patch without the base ROM, from a
  `[bps]` table in `a816.toml` (the base ROM's size and CRC32, and
  `<patch/>` metadata filled from the defines). The patch is unsealed:
  its patched-ROM CRC32 waits for the base ROM.
- `a816 bps seal` fills that CRC32 in from the base ROM; `a816 bps
  apply` and `a816 bps info` apply and inspect any BPS patch (Flips'
  delta ones included), so a project needs no Flips build.
- `-o` repeats to write several outputs from one link, each in the
  format its extension names: `-o game.ips -o game.bps.unsealed`.
  `build_with_imports()` takes `extra_outputs=[(format, path)]`. A
  single `-o` still follows `-f`.
- `a816 format` keeps a string in single quotes when its text holds a
  `"`: `.text 'l"ordre'` became `.text "l"ordre"`, which no longer
  parses.
- `a816 format` keeps the offset of `.include_ips "x.ips", -0x200`;
  it dropped `, -0x200`, which the parser requires.
- `a816 format` keeps nested bodies nested: `.if` / `.for` / `.scope`
  bodies inside an `.alloc` came out at the alloc's indent, and a macro
  block argument `m(x, { .dw 1 })` as `.dw 1}` then `)`. Files with such
  bodies need one `a816 format`; leading whitespace only.
- `a816 fix --select UP001` leaves a `*=` run holding `.include_ips` to a
  manual migration, like `.include` / `.incbin`; it wrapped every
  `.include_ips` after the `*=` in its pinned alloc.
- OP001's safe fix keeps the bytes: `.w` on a value above `0xFFFF`
  (`jsr.w 0xC006AC`, an in-bank call) and `.l` on `jmp` / `jsr` are no
  longer called redundant.
- `script.Table.to_bytes` honours a subclass's `joker_regex` again
  (1.0.4 did): a table writing jokers as `<NN>` had every one dropped,
  silently.
- Two files pinning an anonymous `.alloc at` to one address on the same
  line fail with E0408 "two blocks pinned at ..." naming both, not E0400
  "symbol already defined" on the generated alloc name.
- `a816 format` formats a file whose `.include` is not generated yet
  (`build/gen`): it failed E0500, so lint needed a build first. `a816
  check` still needs the file.
- A failed `.assert` on a name defined nowhere says "`NAME` is not
  defined" and where a `-D` name goes, not "cannot resolve `NAME` at
  link time".

## 1.1.3 (2026-10-15)

- Labels after `@= ADDR` count from ADDR, forward ones included: with a
  ROM-mapped ADDR (`@= 0x0000`), a label after any byte took the ROM
  window's address (`0x8004`, not `0x0004`), silently. A branch under
  `@=` keeps its offset instead of being re-aimed at link from the
  placement, which also makes it work over a WRAM `@=`.
- `@=` works inside an `.alloc` body (pinned or pooled): the body was
  sized from the `@=` address (`E0337`, an 8 MB slot), so a linted source
  had no way to place bytes here and count labels from there.
- A `.for` variable beside a label in an expression the linker evaluates
  (`.dw (grid_font + k * 320) & 0xFFFF`) folds to its value; the link
  failed with "cannot resolve `k`".
- `build_with_imports()` returns a failed module's own errors in
  `diagnostics` (e.g. the located `E0310`), not just "Failed to compile
  module '__main__'".
- `a816 format` writes `*= ADDR` / `@= ADDR` with their space (it wrote
  `*=0x008000`), and a full-line comment written flush-left after them
  stays flush-left instead of being indented as section body.

## 1.1.2 (2026-10-14)

- `a816 check` / `format` / `fix` with no paths leave out what git
  ignores, and an `exclude` list in `a816.toml` leaves out files and
  directories by glob. Bahamut Lagoon's bare `check` reported 531 hits,
  530 from gitignored or scratch sources.
- `lda #NAME`, NAME an alias whose value is known only at link
  (`BANK = (song >> 16) & 0xFF`, `song` imported or placed later in the
  same module), takes the register's width like the expression written
  inline; it failed with `E0313`. That error now names operand widths
  ("a 16-bit operand ... the value needs 8-bit") instead of instruction
  lengths. An object records each alias once, so the `.adbg` loses
  repeated copies of the same symbol.
- A failed `.assert` in an `.alloc` body shows its names as written
  (`_run_bits - _pool_runs`), not as the body exports them
  (`__sc16___run_bits`).
- A `.struct` takes a docstring as its first statement, as a `.scope`
  or `.macro` does; the formatter keeps it and the LSP shows it on
  hover. It was a parse error (`E0101`).
- `a816 --version` (or `-V`) prints the installed version, the Windows
  binary's included.
- LSP hover (nvim `K`) shows the docstring of a label inside an `.alloc`
  body, where it answered "No information available"; a top-level label
  shows its own docstring instead of the module's, and a macro shows its
  parameters, with or without a docstring.
- LSP goto-definition on a struct member (`Entry.ptr`, `thing.ptr` of a
  `.reserve thing as Entry`, a typed bind's `view.ptr`, also through its
  scope as `render.view.ptr`, `(addr as Entry).ptr`, `Outer.head.ptr`, an
  `.istruct` field) lands on the field's line, across files; it found
  nothing.

## 1.1.1 (2026-10-12)

- `[defines]` in `a816.toml` declares the names a build passes with
  `-D`, each with its default: `a816 check` counts them as defined (no
  `W0002`), and a build binds the default unless `-D` overrides it.
  A bad entry is `E0510`.
- `a816 check`, `a816 format` and `a816 fix` with no paths take the
  project the nearest `a816.toml` describes: the `.s` / `.i` files under
  the entrypoint's directory and `module-paths`, without `build/`,
  `obj/` and dot directories.
- `a816 explain` takes an error code too (`a816 explain E0317`) and
  prints its entry from the error catalog.
- `W0001` reaches every reference to a path-derived `.incbin` name: the
  linker warns on one a module makes without importing the blob's module
  (ff4 had three), and a module reused from the build cache prints its
  warnings again, so a warm build shows what a cold one does. Objects
  change: caches rebuild once.
- `.patch Type at ADDR { field = value }` writes only the fields it
  names, each pinned at its offset, so a ROM hack can change a
  header's title or one field of a table record and keep the rest of
  the original bytes. A partly given bit-field byte is `E0348`.
- A `.assert` inside an `.alloc` body sees the body's `_` labels (it
  failed at link with "cannot resolve"); one outside the body says the
  label is private to it. `.adbg` names a private label after its
  module, `battle._loop`, where two modules both showed `__sc1___loop`.
  An `.alloc` body's `_` constant in an expression over an imported
  symbol (`text + _CELLS * 2`) folds at compile time instead of failing
  the link. Objects change: caches rebuild once.
- The Windows binary ships the package's data files (`boards.bml`, the
  standard library): the 1.1.0 one failed any build naming a `board`.

## 1.1.0 "Swift-Tuttle" (2026-10-10)

Separate compilation and linking, declared memory layout (freespace and
`bss` pools), structs, a build cache, and fluff (lint, format, fix) plus
an LSP. Tested over the alphas against ff4, dq6, cacheguard, Bahamut
Lagoon and kintsuki.

### Highlights

Freespace pools: pinned blocks are carved first, the rest is placed
around them, and the layout is checked at link.

```ca65
.pool upper_gap { range 0x500000 0x5cffff  strategy pack }

.alloc dialogue_stream at 0x500000 in upper_gap cross_bank {
    .incbin "assets/stream.dat"
}
.alloc keep_font in upper_gap {
    .incbin "assets/keep_font_packed.dat"
}

.assert dialogue_stream + sizeof(dialogue_stream) <= 0x5d0000, "the stream overruns the gap"
```

`bss` pools lay out WRAM, SRAM and VRAM without emitting bytes. Memory
used in turns goes in one pool's `contexts`; other overlaps are errors.

```ca65
.pool menu_ram { bss  range 0x7e9800 0x7e990f  contexts field_menu, treasure }

.reserve vwf_state as VwfState in menu_ram.field_menu
.reserve treasure_hdma 0x40 in menu_ram.treasure
```

Structs with bit fields, arrays and nesting; typed views; initialized
instances; `@std/snes/*` hardware register definitions.

```ca65
.import "@std/snes/ppu"

ppu := (PPU_BASE as PPU)
    lda ppu.INIDISP

player:
    .istruct Sprite {
        name = "HERO"
        pos = { x = 0x80, y = 0x60 }
        palette = 3
    }
```

### Upgrading from 1.0

- `a816 fix` rewrites `*= ADDR` placements as `.alloc at ADDR` (`UP001`).
  `*=` still assembles; the lint flags it.
- Every emitted byte needs a placement: an `.alloc` or a preceding `*=`.
  Bytes before the first `*=` used to land at `0x008000`, over whatever
  was there; they are now `E0310`.
- `brk`, `cop` and `wdm` take their signature byte (`brk #0x00`). 1.0
  emitted the lone opcode, so returning from the handler skipped the
  next byte; a bare `brk` is now an error instead.
- Errors that 1.0 printed and then ignored (a parse error, an unknown
  directive such as `.dd`) now fail the build. 1.0 exited 0 with
  missing output, so a build that "passed" may now report what it
  always got wrong.
- Overlapping writes fail the build (`E0408`). In 1.0, two `*=` blocks
  writing the same bytes silently overwrote each other; `--overlap-mode
  warn | off` brings back a warning or silence.
- A `.b` immediate that doesn't fit a byte is `E0309`; 1.0 masked it
  (`lda.b #0x1234` emitted `a9 34`). `.w` and `.l` still mask.
- A bare `jsr` / `jmp` takes its form from the target's bank: a label in
  the caller's own bank assembles to the absolute `JSR` / `JMP`, and one
  in another bank is `E0346`, which names `jsl` / `jml`. Alphas 44 to 51
  sized it by value, so every 24-bit label (all of them in HiROM) became
  a long call, whose callee must return with `rtl`.
- `a816 check` fails a file that doesn't parse, printing each error with
  its code and line. It used to exit 0 while `format --check` failed the
  same file.
- `a816 file.s` builds through modules: it writes objects and their
  dependency sidecars under `build/obj` (`--obj-dir`, `--no-cache`) and
  `.sym` / `.adbg` next to the output.
- Two fixes change output: `ora.w #imm` now encodes `$09` (1.0 emitted
  `lda`'s `$A9`), and a `-D` value that reads as a number is a number.
- A `-D` name must be a symbol name (`scope.name` allowed). A name no
  source can spell is a usage error: `-D " DEBUG=1"` passed as one shell
  word used to define ` DEBUG`, leading space included, so every
  `.if DEBUG` dropped out without a word.
- Python API:
  - `assemble_string_with_emitter` raises `A816Error` (`AssemblyError`,
    `NodeError`) instead of returning an error string, and
    `assemble_with_emitter` no longer swallows errors.
  - `MZParser` is now `A816Parser`; the old name is a deprecated alias,
    removed in 1.2.
  - `assemble` / `assemble_as_patch` and direct (single-pass) assembly
    are deprecated; build through `build_with_imports` or the CLI.

### Placement

- `.pool NAME { range LO HI ... }` declares freespace, with
  `strategy pack | order` and ranges over several banks, split along
  the windows the bus serves. `<pool>.capacity`, `.fragments` and
  `.largest_chunk` read its state, e.g. in an `.if` guard, also from a
  module that imports the pool.
- `.alloc NAME in POOL { ... }` lets the allocator choose the address;
  `.alloc [NAME] at ADDR [size N] { ... }` pins it, with `size` as a hard
  bound.
- A pool places its blocks around every pin inside its ranges:
  `.alloc at ADDR` with or without `in POOL`, from any module, `*=`
  blocks and other pools' pins.
- `align N` places a block on an `N`-byte boundary.
- `cross_bank` lets a data blob run over bank edges where the ROM is
  contiguous on the bus; code and labels are refused (`E0336`).
- `.relocate SYMBOL OLD_START OLD_END into POOL` moves a routine and
  gives its old space back; `.reclaim POOL START END` adds slack.
- `.assert EXPR, "message"` checks layout invariants once every address
  is final; every failed assert is reported (`E0407`), with the size of
  each alloc it mentions. An `.assert` without its message says so.
- An alloc whose body emits more or fewer bytes than the slot it was
  given is `E0337`, instead of shifting what follows.
- Alloc names are global: the same pool and name from two different
  places is a duplicate symbol (`E0400`), not one block silently
  replacing the other.
- Pools merge across modules by name, whether the build goes through
  `a816 build` or links objects, and the linker places allocs from every
  object in one pass. Fill, strategy, `bss` and contexts must agree; a
  second declaration in the same file is still an error.
- Overlapping writes fail the build by default (`E0408`), checked
  before any byte is written. The error names each block (alloc and
  pool, or the address of an anonymous one), its `file:line` and the
  bytes they share (`--overlap-mode warn | off`).
- An empty alloc body binds its label and takes no space.

### Memory pools

- `bss` pools reserve address space and emit nothing; `.res N`
  reserves `N` bytes inside an alloc body.
- `.reserve NAME SIZE [at ADDR] in POOL` and `.reserve NAME as TYPE`,
  which publishes `NAME.<field>` for each struct field.
- `contexts A, B` on a pool lets memory used in turns overlap; overlap
  between two different pools is `E0406`, with both reservations and
  their source lines.

### Language

- `.struct` with `byte`, `word`, `long`, `dword`, `uN` bit fields
  (with `.mask` and `.shift`), nested structs and `TYPE[N]` arrays,
  where `N` may be a constant expression (`byte[ROWS * 16]`); every
  field publishes its offset. Redeclaring an identical struct is a no-op.
- `sizeof(T)`, `sizeof(T.field)`, `sizeof(NAME)` for a reservation or a
  named alloc, and `countof(T.arr)` for an array's element count, in any
  expression (`.assert`, `.for` bounds and array lengths included). An
  alloc or reservation from another module resolves at link. Misuse is
  `E0321`.
- `(expr as T).field` casts and typed binds, over constants, labels and
  `.extern` symbols alike; the operand size follows the bind's base.
  `view := (expr as T)` binds eagerly; `view = (expr as T)` is a lazy
  view, which also works over a module's own pooled labels (placed at
  link): its fields relocate with the label. A module's `:=` over a name
  placed at link (`font_ptr := target + 0x40`) reaches its importers as
  that module's symbol, instead of failing there.
- `.istruct Type { ... }` emits an initialized instance: strings,
  lists, nested structs and bit fields, zero-filling the rest.
- `.label NAME = ADDR` names an address for debuggers and the LSP
  without emitting anything.
- `.a8` / `.a16` / `.i8` / `.i16`, and `rep` / `sep` tracking behind
  `--experimental track_register_size`, size immediates; an immediate
  whose width disagrees with the known size warns. A size a tracked
  `rep` / `sep` set ends at `rts`, `jmp`, `bra`, `plp` and the like,
  where the next routine starts; a declared `.a16` holds until
  redeclared.
- The rest of the 65c816 instruction set: `brl`, `bvc`, `bvs`, `cld`,
  `cli`, `clv`, `cop`, `mvn`, `mvp`, `per` and `wdm`, plus `jsl` / `jml`
  as aliases of `jsr.l` / `jmp.l`. `brk`, `cop` and `wdm` take their
  signature byte explicitly.
- One expression grammar everywhere, with C precedence and semantics:
  truncating `/`, `%`, comparisons, `~` within 8, 16 or 32 bits;
  division by zero is `E0312`.
- `.scope NAME { ... }` publishes its labels, constants, aliases and
  the labels of macros called inside it as `NAME.x`; `_` names stay
  private.
- A bare operand naming a label further down its block (`jsr fwd`,
  `lda fwd`) is sized as a backward reference would be. If the label
  lands where that size is wrong, `E0313` names the suffix to write.
- An addressing mode with a single size widens a narrower operand to
  it, as ca65 and asar do: `pea 0x0000` is `F4 00 00`, `lda 0x12, y` is
  `B9 12 00`. It never narrows.
- A `.for` variable is known while its body expands, so `:=` and `.if`
  in the body can use it (`_c := i << 8`).

### Modules and linking

- Separate compilation: `a816 build -c` writes objects, and the linker
  combines them. `a816 build main.s` discovers and builds every import.
  New flags: `-f obj`, `-I` / `--module-path`, `--include-path`,
  `--obj-dir`, `--no-auto-imports`.
- `.import "@std/snes/ppu"` (and `cpu`, `dma`, `apu`, `joypad`, `wram`,
  `header`): typed SNES registers.
- `.extern` symbols work in any expression, macro or alias, declared at
  the top of a module or inside an `.alloc` body or block; the linker
  evaluates them once placed.
- Transitive imports are deduplicated, and modules are found on the
  module paths only, so a same-named file next door can't shadow one.
- A module's `.map` regions travel with `.import` and its object; the
  linker merges identical ones, and a conflicting one is `E0308`.
- `.incbin` symbols and `scope.label` names reach importers.
- The names `.incbin` derives from its path (`assets_vwf_bin`,
  `assets_vwf_bin__size`) are deprecated and go in 1.2. A reference warns
  (`W0001`, a816's first warning code), in the build and in
  `a816 check`, which still exits 0 on warnings alone. `a816 fix`
  rewrites the reference to `vwf_font` / `sizeof(vwf_font)` when the
  blob is alone in `.alloc vwf_font`; otherwise label the blob. A
  reference to such a name that two blobs bind is `E0347`: it read the
  first one.
- A module sees the constants of what it imports, directly or through
  another import, and nothing else. Using a constant of a module it
  doesn't import is `E0200`, with the `.import` to add as the hint
  (alphas 42 to 51 resolved it with a warning).
- A module's `_` names are private. Its importers can't name them
  (the error says which module owns the name), and a module's own
  `_name` shadows an imported one. A `_label` is also private to its
  alloc.
- The linker writes `.sym` and `.adbg` debug info with source mapping.
  Each source file has one name in the `.adbg`, relative to the working
  directory when it lies under it, so the file doesn't change with the
  checkout's location.
- An `.import` inside an `.if` is `E0311`: imports are read before any
  condition runs, so the module was linked whatever the condition. Import
  it unconditionally and put the `.if` inside the module.
- `.include` searches the `include-paths` everywhere: in imported
  modules, in `a816 check` and `a816 format`, and in the LSP.

### Builds

- The build cache rebuilds a module when anything that shaped its object
  changed: defines, include and module paths, the bus map, file contents
  (a touch alone doesn't count), a lookup that would now find a new file,
  an import's output, or the toolchain's codegen revision. `--no-cache`
  compiles everything.
- Identical sources give an identical ROM, independent of
  `PYTHONHASHSEED`. With `SOURCE_DATE_EPOCH` set, `BUILD_DATE` is that
  time (UTC) rather than the clock, so two builds match byte for byte.
- `a816.toml` holds the project: `entrypoint`, `include-paths`,
  `module-paths`, a cartridge `board` from ares' `boards.bml` (LoROM,
  HiROM, ExHiROM, SA-1 and the rest), `[map.N]` regions in bsnes form,
  `rom_size` and `[experimental]` flags.
- An `.sfc` image is padded to `rom_size`.
- Cold multi-module builds scale with the project instead of its square:
  each imported module's object, source and import plan are read once
  per build, not once per importer. A 100-module project went from 19 s
  to 2.3 s, ff4 from 3.7 s to 1.5 s, cacheguard from 1.8 s to 0.8 s.
  Scanning is about 1.8x faster, and parsed nodes are immutable, so the
  build shares them between modules safely.
- `script.Table.to_bytes`, the table encoder text tools build on, is 4 to
  9x faster with the same output (Bahamut Lagoon's dialog reflow spent 75%
  of its time there).

### Diagnostics

- Errors carry a stable code (`E0001` to `E0509`), a caret on the
  offending token, a hint and, for names, a did-you-mean; the
  [error codes](errors.md) page documents each one. Every diagnostic
  carries a code, and a failed module is reported once, without a
  trailing `Build failed` line.
- An unsized operand naming a symbol resolved at link (`jmp target`
  with `target` from another module) asks for its size (`E0313`), since
  the right form depends on where the linker puts it.
- An undefined symbol passed to a macro is reported where the caller
  wrote it, with the macro parameter it was bound to.
- Several errors from one pass are reported together.
- An unterminated `/* ...` block comment is `E0004`, pointing at where
  it opens, instead of hanging the scanner.
- Pool overflows say whether the block is too big, the pool fragmented
  or full, and what to try.
- A duplicate global names both definitions and their values (`E0400`).
- A missing `.include` or `.incbin` file is `E0500` at the path, with
  the directories searched, instead of a bare `[Errno 2]`.
- An alloc's size asked for before layout (`.for i := 0, 8 - sizeof(dte)`)
  is `E0321`, and any name missed while expanding is located and coded,
  never a bare `Build failed: NAME`.
- Logs stay quiet by default; `--verbose` shows tracebacks.

### Fluff: lint, format, fix

- `a816 format [--check]`: a canonical formatter that keeps comments,
  docstrings and strings intact and settles in one pass; it takes
  several paths, or `-` for stdin. A unary operator stays against its
  operand (`~3`, `-x`).
- `a816 check`: docstring rules (`DOC001` to `DOC007`), naming (`N801`,
  `N802`), struct casts (`S001`, `S003`, `S004`), line length (`E501`),
  redundant size suffixes (`OP001`), program structure (`ST001` for a
  placement nested in an `.alloc`, `ST002` for an `.import` outside the
  prelude) and legacy placement (`UP001`); `; noqa: RULE` to suppress.
  `W0002` flags an `.if` on a name the project defines nowhere: it reads
  as false, so a deleted or misspelt flag drops its block silently.
- `a816 fix` applies the autofixes, also offered as LSP code actions.

### Language server

- Hover with docstrings (modules included), struct fields, bit fields
  and pools; go-to-definition into includes, imports and `.incbin`
  assets; completion, rename, workspace symbols and semantic tokens.
- Diagnostics with error codes and hints, recovery from broken input,
  and an index that builds in the background.

### Tools

- `xobj` inspects object files: sections, symbols, relocations, pools
  and the format identity.
- `xdds` (new) disassembles with the assembler's instruction table:
  `--func` walks a routine's control flow tracking M/X, `--follow-calls`
  follows its calls, `--debug FILE.adbg` / `--sym` name addresses, and
  every `--asm` line reassembles to its bytes: a size suffix is printed
  only where a bare operand would assemble differently, and long calls
  and jumps print as `jsl` / `jml`.
- `A816_EMIT_TRACE=1` logs where every region landed.

### Fixes from 1.0

- `ora.w #imm16` encoded `lda`'s opcode (`$A9`), silently loading
  instead of or-ing.
- About 25 addressing modes were missing on `ora`, `and`, `eor`, `adc`,
  `sbc`, `cmp`, `sta` and `jsr`; `cmp 0x03, s` crashed.
- A `.map` line swallowed the next line when it began with a name.
- `|`, `^`, `~`, `/` and `%` failed or crashed in constants, `.db`,
  `.if` and immediates (`X = 1|2`, `lda.w #10/2`).
- One-line blocks (`.scope x { rts }`, `{ nop nop }`) didn't parse.
- A constant inside `.scope sc { K = 6 }` was unreachable as `sc.K`.
- `-D NAME=VALUE` always defined a string, so `lda #NAME` failed.
- Repeated `-D` flags kept only the last one's values: `-D A=1 -D B=2`
  defined `B` alone, and every `.if A` silently dropped out.
- `-f sfc` ignored `-m`, so SFC output was always LoROM.
- An unknown `-m` value crashed; it now lists `low`, `low2`, `high`.
- An `.if` on a `.for` variable inside the loop was false on every
  iteration, without a word.

### Documentation

- Every example in the docs is checked by the test suite: snippets
  parse, complete programs build, and the multi-file tutorials are real
  projects under `docs/examples/` built against a golden patch.
- New pages for placement and this changelog; `.map`, expressions,
  modules and the lint rules are documented against the code.
