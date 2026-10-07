# Changelog

## 1.1.0 "Swift-Tuttle"

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
- A module sees the constants of what it imports, directly or through
  another import, and nothing else. Using a constant of a module it
  doesn't import is `E0200`, with the `.import` to add as the hint
  (alphas 42 to 51 resolved it with a warning).
- A module's `_` names are private. Its importers can't name them
  (the error says which module owns the name), and a module's own
  `_name` shadows an imported one. A `_label` is also private to its
  alloc.
- The linker writes `.sym` and `.adbg` debug info with source mapping.

### Builds

- The build cache rebuilds a module when anything that shaped its object
  changed: defines, include and module paths, the bus map, file contents
  (a touch alone doesn't count), a lookup that would now find a new file,
  an import's output, or the toolchain's codegen revision. `--no-cache`
  compiles everything.
- Identical sources give an identical ROM, independent of
  `PYTHONHASHSEED`.
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
  the output reassembles through `a816 format -`.
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

### Documentation

- Every example in the docs is checked by the test suite: snippets
  parse, complete programs build, and the multi-file tutorials are real
  projects under `docs/examples/` built against a golden patch.
- New pages for placement and this changelog; `.map`, expressions,
  modules and the lint rules are documented against the code.
