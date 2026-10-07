# a816

[![Build](https://github.com/manz/a816/actions/workflows/build.yml/badge.svg)](https://github.com/manz/a816/actions/workflows/build.yml)
[![Binaries](https://github.com/manz/a816/actions/workflows/binaries.yml/badge.svg)](https://github.com/manz/a816/actions/workflows/binaries.yml)
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=manz_a816&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=manz_a816)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=manz_a816&metric=coverage)](https://sonarcloud.io/summary/new_code?id=manz_a816)
[![Maintainability Rating](https://sonarcloud.io/api/project_badges/measure?project=manz_a816&metric=sqale_rating)](https://sonarcloud.io/summary/new_code?id=manz_a816)
[![Reliability Rating](https://sonarcloud.io/api/project_badges/measure?project=manz_a816&metric=reliability_rating)](https://sonarcloud.io/summary/new_code?id=manz_a816)
[![Security Rating](https://sonarcloud.io/api/project_badges/measure?project=manz_a816&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=manz_a816)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Another 65c816 assembler.

Targets Super Famicom / SNES ROM hacking and patching. Ships a CLI assembler,
an object-file linker, an LSP server, and `xdds` (a SNES-aware hex dump /
disassembler).

## Usage

### Command line

The `a816` CLI is subcommand-driven (`ruff` / `cargo` style):

```
$ a816 build   <files> -o <output>    # assemble + link
$ a816 check   <paths>                # lint with fluff (DOC*, E501, N80*, OP001, S00*, ST00*, UP001)
$ a816 format  <paths>                # format .s / .i sources with fluff
$ a816 fix     <paths>                # apply fluff autofixes (--diff / --check / --select / --unsafe-fixes)
$ a816 explain <CODE>                 # rule rationale + good/bad example pair
```

Bare invocation (`a816 file.s -o out.ips`) still routes to `build`
for backwards compatibility — existing scripts keep working.

#### `a816 build` flags

```
-o, --output OUTPUT      Output file (default a.out)
-f FORMAT                Output format (ips, sfc, obj)
-m {low,low2,high}       Default bus when the project declares no map:
                         LoROM (default), LoROM variant 2, HiROM.
--copier-header          Add 0x200 address delta for ips writer.
--dump-symbols           Dump the symbol table.
-c, --compile-only       Compile to object files without linking.
-D KEY=VALUE [KEY=VALUE ...]
                         Define symbols (numeric values use int(., 0)).
                         KEY must be a symbol name (`scope.name` allowed);
                         anything else is rejected.
--no-auto-imports        Disable automatic import resolution.
-I, --module-path PATH   Add a module search path (repeatable).
--obj-dir DIR            Directory for compiled object files (default
                         build/obj).
--include-path PATH      Add directory to include search path for `.include`.
--overlap-mode {error,warn,off}
                         What overlapping writes do (default error).
--experimental FLAG      Enable an experimental feature (repeatable):
                         `track_register_size`.
--no-cache               Compile every module instead of reusing objects.
--verbose                Show every log level, tracebacks included.
```

#### Separate compilation

Compile each module to an object file, then link:

```
$ a816 build --compile-only file1.s file2.s   # produces file1.o, file2.o
$ a816 build file1.o file2.o -o output.ips    # link to IPS
$ a816 build file1.o file2.o -f sfc -o output.sfc
$ a816 build file1.s file2.o -o output.ips    # mix sources and objects
```

#### Lint and format

See [Fluff (lint + format)](fluff.md) for the full rule set, `; noqa`
suppression syntax, and editor integration.

```
$ a816 check src/                       # report lint hits ([*]=fixable safe, [!]=fixable unsafe)
$ a816 format src/                      # rewrite sources in place
$ a816 format --check src/              # exit non-zero if reformatting needed
$ a816 format --diff src/               # print unified diffs without writing
$ a816 fix src/                         # apply safe fixes in place
$ a816 fix --diff src/                  # preview as unified diff
$ a816 fix --select UP001 --unsafe-fixes src/   # opt in to one rule's unsafe fix
$ a816 explain DOC003                   # rationale + good/bad example pair
```

Private symbols (`_`-prefixed labels / macros / scopes) can carry
docstrings without firing DOC002 — naming alone marks them internal.

The legacy `a816-fluff` binary still works but prints a deprecation
notice on stderr — prefer `a816 check` / `a816 format` going forward.

### From Python

```python
from pathlib import Path

from a816.module_builder import build_with_imports

Path("main.s").write_text(".alloc at 0x008000 {\n    rts\n}\n")  # the program to build

result = build_with_imports("main.s", "patch.ips", output_format="ips")
assert result.exit_code == 0, result.diagnostics
```

`build_with_imports` is what `a816 build` runs: it compiles every
`.import`ed module, links, and reads `a816.toml`. See
[Python usage](python_usage.md).

## Syntax

See the [Directives reference](directives.md) for the full set of
assembler directives — `*=`, `@=`, `.scope`, `.macro`, `.struct`,
`.if`, `.for`, `.text` / `.table`, `.incbin`, and friends.

### Mnemonics

```
adc, and, asl, bcc, bcs, beq, bit, bmi, bne, bpl, bra, brk, brl, bvc, bvs, clc, cld, cli, clv, cmp, cop, cpx, cpy, dec, dex, dey, eor, inc, inx, iny, jml, jmp, jsl, jsr, lda, ldx, ldy, lsr, mvn, mvp, nop, ora, pea, pei, per, pha, phb, phd, phk, php, phx, phy, pla, plb, pld, plp, plx, ply, rep, rol, ror, rti, rtl, rts, sbc, sec, sed, sei, sep, sta, stp, stx, sty, stz, tax, tay, tcd, tcs, tdc, trb, tsb, tsc, tsx, txa, txs, txy, tya, tyx, wai, xba, xce
```

## Macros

<!-- example: build -->
```ca65
.macro test(var_1, var_2) {
    lda.w (var_1 << 8) | var_2
}

.alloc at 0x008000 {
    test(0x12, 0x34)    ; expands to lda.w (0x12 << 8) | 0x34: emits AD 34 12
}
```

## Code pointer relocation

```ca65
*=0x008000
    jsr.l _intro
```

## Scopes

```ca65
some_address = 0x54
{
    lda.b some_address
    beq no_action
    ; label only visible inside this scope
    no_action:
}
```

### Named scopes

`.scope name { ... }` exports its labels as `name.label`:

```ca65
*=0x009000
.scope named_scope {
   addr = 0x1234
   youhou_text:
   .text 'youhou'
   .db 0
   yaha_text:
   .text 'yaha'
   .db 0
}

*=0x019A52
    load_system_menu_text_pointer(named_scope.youhou_text)

*=0x019A80
    load_system_menu_text_pointer(named_scope.yaha_text)
```

## Structs

`.struct Name { ... }` declares a layout. Each field is one of `byte`,
`word`, `long` (24-bit), or `dword` (32-bit). Field names export as
`Name.field` constants holding the byte offset from the start of the
struct, plus `Name.__size` for the total length.

```ca65
.struct OAM {
    word x
    byte y
    byte tile
    byte attr
}
```

emits `OAM.x = 0`, `OAM.y = 2`, `OAM.tile = 3`, `OAM.attr = 4`,
`OAM.__size = 5`.

Use the offsets against any base address — a hardware register, a WRAM
pointer, an array stride:

```ca65
.struct PPU {
    byte INIDISP
    byte OBSEL
    word OAMADDR
}

*=0x008000
    lda.w 0x2100 + PPU.OAMADDR  ; assembles as LDA $2102

player = 0x7E0010
    lda.b player + OAM.x
    sta.b player + OAM.tile
```

Structs are layout-only; they don't reserve storage and don't emit
bytes. Pair with `*=` or a memory-map directive to place an instance.

## Modules

`.import "module"` brings symbols from another translation unit; `.extern`
declares cross-module references. See [Modules](modules.md) for the full
workflow, visibility rules, and constants over externs.

```ca65
.import "vwf"
.extern external_func

main:
    jsr.l vwf.init
    jsr.w external_func
    rts
```

## Freespace pools

Declare reusable chunks of free ROM, relocate functions into them,
and let the assembler place everything deterministically. The
`.pool` / `.alloc` / `.relocate` / `.reclaim` directives replace the
manual `*= ADDR` + end-label + overflow guard pattern with a
declarative pool the assembler manages. In object compilation the
linker unions same-named pools across translation units and runs the
allocator over the merged view, so multiple modules can share a pool
name. See [Freespace pools](freespace-pools.md) for syntax,
cross-TU usage, error model, and migration from the manual pattern.

```ca65
.pool bank01_slack {
    range 0x01ff35 0x01ffff
}

.alloc helper in bank01_slack {
    rts
}
```

## Project configuration (`a816.toml`)

Drop an `a816.toml` at the project root. `a816 build` (and the bare
`a816 <file>` form) finds it by walking up from the first input file;
the LSP and fluff read the same file.

```toml
entrypoint    = "src/main.s"
include-paths = ["src/include"]
module-paths  = ["src/modules"]
board         = "SHVC-1A3M-30"  # a real cartridge board (and/or [map.N])
rom_size      = 0x400000

[experimental]
track_register_size = true
```

| Key | Read by | Meaning |
|-----|---------|---------|
| `entrypoint` | LSP | root file the server indexes from |
| `include-paths` | build, LSP, fluff | directories searched by `.include` |
| `module-paths` | build, LSP, fluff | directories searched by `.import` |
| `board` | build | cartridge board from ares' `boards.bml` (`"SHVC-1A3M-30"`) |
| `[map.N]` | build | bus region `N`, in bsnes/`boards.bml` form |
| `rom_size` | build | ROM image size in bytes; required with read-only `[map.N]` regions |
| `[experimental]` | build | opt-in feature flags (`--experimental NAME`) |

`--include-path` / `-I` replace the file's `include-paths` /
`module-paths`; `--experimental` flags add to `[experimental]`.

### Bus map: `board` and `[map.N]`

The cartridge layout belongs to the project, not to each module. The
regions declared here are put on the bus of every translation unit
before its own `.map` lines run, so a module with no local `.map` still
places code in the project's banks. In object mode they are written
into each `.o` like a source `.map`; the linker keeps one copy.

- `board` names a real cartridge board from ares' `boards.bml`
  (vendored, ares revision pinned in `a816.boards.ARES_REVISION`, ISC):
  `"SHVC-1A3M-30"` (LoROM + SRAM), `"SHVC-1J3M-20"` (HiROM + SRAM),
  `"SHVC-LJ3M-01"` (ExHiROM), `"SHVC-1L5B-20"` (SA-1), and every other
  board in the file. It expands to that board's ROM and RAM `map`
  lines (coprocessor MMIO and cartridge slots are left out) plus the
  console's WRAM (`7e-7f:0000-ffff`), which no board lists. Boards
  with ROM need `rom_size`. `[map.N]` tables add to a board.
- `[map.N]` declares region `N` (the identifier a source `.map` uses,
  so `N` must be an integer: `[map.3]`, `[map.0x3]`) the way bsnes and
  ares' `boards.bml` write it, so a board's `map` lines copy over as
  they are. `address` is required; `mask`, `base` (both default 0)
  and `writable` are optional:

  ```toml
  rom_size = 0x400000        # 4 MB image

  [map.1]                    # SHVC-1A3M: LoROM ROM
  address = "00-7d,80-ff:8000-ffff"
  mask    = 0x8000

  [map.2]                    # ... and its SRAM, in the same banks
  address  = "70-7d,f0-ff:0000-7fff"
  mask     = 0x8000
  writable = true
  ```

  `address` is `BANKS:WINDOW` in hex; several bank ranges separate
  with commas and share the window. A region owns only its window, so
  ROM and SRAM can share banks. The file offset of a read-only address
  is computed as in bsnes: the `mask` bits are removed from the full
  24-bit address, `base` is added, and the result folds into
  `rom_size` (so mirrors land on the same bytes). `rom_size` is
  required as soon as one region is read-only. Writable regions have
  no file offset.

- A source `.map` with the same identifier and the same shape as a
  toml region is accepted and does nothing; a different shape fails
  with `E0308` on the source line.
- Declaring any region replaces the `-m` default bus, exactly as a
  source `.map` does, so `board` and `[map.N]` together must cover
  every region the project uses. `-m` (default `low`) only picks the
  bus of a project that declares none.
- `mapper = "lorom"/"hirom"` existed during the 1.1.0 alphas and is
  gone: write `board = "SHVC-1A0N-30"` / `"SHVC-1J0N-20"` and
  `rom_size` instead (`E0504` names the board).
- Changing the bus map rebuilds every cached object.

## LSP

`a816-lsp-server` ships with the package: diagnostics, goto-definition
(including `.import` targets), and hover info. See [LSP](lsp.md) for
editor setup.

## Built-in symbols

- `BUILD_DATE`: the UTC date and time a module is compiled
  (`YYYY-MM-DD HH:MM:SS`). With `SOURCE_DATE_EPOCH` set (seconds since
  1970) it is that time instead, so two builds give the same bytes; a
  malformed value is an error. A module served from the build cache
  keeps the date it was compiled with.

`.text` strings expand `${VAR}` references against defined symbols.

## xdds

SNES-aware hex dump and disassembler.

```
$ xdds --help
$ xdds rom.sfc --low-rom -s 0x008000 -l 256
$ xdds rom.sfc --low-rom -d --m16 --x16 -n 32   # disassemble 32 instrs
$ xdds rom.sfc --ips patch.ips -s '$01:FF40'   # apply IPS, dump from SNES addr
$ xdds rom.sfc --ips patch.ips -d --asm --debug patch.ips.adbg --func main
                                     # walk `main` as a816 source, symbols named
```

Disassembly flags: `-d` disassembles, `--asm` prints a816 syntax,
`--debug FILE.adbg` names jump targets from the build's debug info,
`--sym NAME` starts at a symbol, `--func NAME` walks one function
(following branches, tracking M/X across `rep`/`sep`) and
`--follow-calls` recurses into `jsr`/`jsl`. `--m16`/`--x16` set the
initial register widths.

## xobj

Inspector for the `.o` object-file format the assembler / linker
exchange. Useful when debugging a link failure or auditing what a
module exports.

```
$ xobj file.o                # high-level summary
$ xobj --sections file.o      # section table
$ xobj --symbols file.o      # symbol table sorted by address
$ xobj --relocs file.o       # legacy + expression relocations
$ xobj --lines file.o        # debug line table
$ xobj --bytes 64 file.o     # dump the first 64 bytes of each section
```
