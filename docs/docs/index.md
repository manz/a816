# a816
Another 65c816 assembler.

Targets Super Famicom / SNES ROM hacking and patching. Ships a CLI assembler,
an object-file linker, an LSP server, and `xdds` (a SNES-aware hex dump /
disassembler).

## Usage

### Command line

```
$ a816 --help
usage: x816 [-h] [--verbose] [-o OUTPUT_FILE] [-f FORMAT] [-m MAPPING]
            [--copier-header] [--dump-symbols] [-c]
            [-D KEY=VALUE [KEY=VALUE ...]] [--no-auto-imports] [-I PATH]
            [--obj-dir OBJ_DIR] [--include-path PATH] [--prelude PRELUDE_FILE]
            input_files [input_files ...]

positional arguments:
  input_files           Input files (asm files or object files for linking)

options:
  -o, --output OUTPUT_FILE
                        Output file
  -f FORMAT             Output format (ips, sfc, obj)
  -m MAPPING            Address mapping (low_rom, low_rom_2, high_rom)
  --copier-header       Adds 0x200 address delta for ips writer.
  --dump-symbols        Dumps symbol table
  -c, --compile-only    Compile to object files without linking.
  -D, --defines KEY=VALUE [KEY=VALUE ...]
                        Defines symbols.
  --no-auto-imports     Disable automatic import resolution.
  -I, --module-path PATH
                        Add directory to module search path.
  --obj-dir OBJ_DIR     Directory for compiled object files (default:
                        build/obj).
  --include-path PATH   Add directory to include search path for .include.
  --prelude PRELUDE_FILE
                        Config file prepended to every module compilation.
```

### Separate compilation

Compile each module to an object file, then link:

```
$ a816 --compile-only file1.s file2.s   # produces file1.o, file2.o
$ a816 file1.o file2.o -o output.ips    # link to IPS
$ a816 file1.o file2.o -f sfc -o output.sfc
$ a816 file1.s file2.o -o output.ips    # mix sources and objects
```

### From Python

```python
from a816.program import Program

def build_patch(input, output):
    program = Program()
    program.assemble_as_patch(input, output)
    program.resolver.dump_symbol_map()
```

## Supported Syntax

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
