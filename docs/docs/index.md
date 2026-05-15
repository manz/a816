# a816
Another 65c816 assembler.

Targets Super Famicom / SNES ROM hacking and patching. Ships a CLI assembler,
an object-file linker, an LSP server, and `xdds` (a SNES-aware hex dump /
disassembler).

## Usage

### Command line

The `a816` CLI is subcommand-driven (`ruff` / `cargo` style):

```
$ a816 build  <files> -o <output>     # assemble + link
$ a816 check  <paths>                 # lint with fluff (DOC*, E501, N801, N802)
$ a816 format <paths>                 # format .s / .i sources with fluff
```

Bare invocation (`a816 file.s -o out.ips`) still routes to `build`
for backwards compatibility — existing scripts keep working.

#### `a816 build` flags

```
-o, --output OUTPUT      Output file (default a.out)
-f FORMAT                Output format (ips, sfc, obj)
-m MAPPING               Address mapping (low, low_rom_2, high_rom)
--copier-header          Add 0x200 address delta for ips writer.
--dump-symbols           Dump the symbol table.
-c, --compile-only       Compile to object files without linking.
-D KEY=VALUE [KEY=VALUE ...]
                         Define symbols (numeric values use int(., 0)).
--no-auto-imports        Disable automatic import resolution.
-I, --module-path PATH   Add a module search path (repeatable).
--obj-dir DIR            Directory for compiled object files (default
                         build/obj).
--include-path PATH      Add directory to include search path for `.include`.
--prelude PRELUDE_FILE   Config file prepended to every module compilation.
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
$ a816 check src/                 # report lint hits
$ a816 format src/                # rewrite sources in place
$ a816 format --check src/        # exit non-zero if reformatting needed
$ a816 format --diff src/         # print unified diffs without writing
$ a816 explain DOC003             # rationale + good/bad example pair
```

Private symbols (`_`-prefixed labels / macros / scopes) can carry
docstrings without firing DOC002 — naming alone marks them internal.

The legacy `a816-fluff` binary still works but prints a deprecation
notice on stderr — prefer `a816 check` / `a816 format` going forward.

### From Python

```python
from a816.program import Program

def build_patch(input, output):
    program = Program()
    program.assemble_as_patch(input, output)
    program.resolver.dump_symbol_map()
```

## Syntax

See the [Directives reference](directives.md) for the full set of
assembler directives — `*=`, `@=`, `.scope`, `.macro`, `.struct`,
`.if`, `.for`, `.text` / `.table`, `.incbin`, and friends.

### Mnemonics

```
adc, and, asl, bcc, bcs, beq, bit, bmi, bne, bpl, bra, brk, brl, bvc, bvs, clc, cld, cli, clv, cmp, cop, cpx, cpy, db, dec, dex, dey, eor, inc, inx, iny, jml, jmp, jsl, jsr, lda, ldx, ldy, lsr, mvn, mvp, nop, ora, pea, pei, per, pha, phb, phd, phk, php, phx, phy, pla, plb, pld, plp, plx, ply, rep, rol, ror, rti, rtl, rts, sbc, sec, sed, sei, sep, sta, stp, stx, sty, stz, tax, tay, tcd, tcs, tdc, trb, tsb, tsc, tsx, txa, txs, txy, tya, tyx, wai, xba, xce
```

## Macros

```ca65
.macro test(var_1, var_2) {
    lda.w var_1 << 16 + var_2
}

test(0x10, 0x10)
; expands to: lda.w 0x10 << 16 + 0x10
; emits:      lda.w 0x1010
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

```ca65
*=0x009000
named_scope {
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
allocator core ships today as a Python API (`a816.pool`); the
matching directives (`.pool`, `.relocate`, `.alloc`, `.reclaim`) are
in flight. See [Freespace pools](freespace-pools.md) for the design,
current API, and migration path from the manual `*=` + end-label
pattern.

## Project configuration (`a816.toml`)

Drop an `a816.toml` at the project root to declare the entrypoint and
search paths. Currently consumed by the LSP server; see [LSP](lsp.md).

```toml
entrypoint    = "src/main.s"
include-paths = ["src/include"]
module-paths  = ["src/modules"]
```

## LSP

`a816-lsp-server` ships with the package: diagnostics, goto-definition
(including `.import` targets), and hover info. See [LSP](lsp.md) for
editor setup.

## Built-in symbols

- `BUILD_DATE` — set automatically to the current date.

`.text` strings expand `${VAR}` references against defined symbols.

## xdds

SNES-aware hex dump and disassembler.

```
$ xdds --help
$ xdds rom.sfc --low-rom -s 0x008000 -l 256
$ xdds rom.sfc --low-rom -d --m16 --x16 -n 32   # disassemble 32 instrs
$ xdds rom.sfc --ips patch.ips -s '$01:FF40'   # apply IPS, dump from SNES addr
```

## xobj

Inspector for the `.o` object-file format the assembler / linker
exchange. Useful when debugging a link failure or auditing what a
module exports.

```
$ xobj file.o                # high-level summary
$ xobj --regions file.o      # region table
$ xobj --symbols file.o      # symbol table sorted by address
$ xobj --relocs file.o       # legacy + expression relocations
$ xobj --lines file.o        # debug line table
$ xobj --bytes 64 file.o     # dump the first 64 bytes of each region
```
