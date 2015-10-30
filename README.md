# a816
Another 65c816 assembler

## Usage

### Command line
```
$ ./x816 --help
usage: x816.py [-h] [--verbose] [-o OUTPUT_FILE] input_file

a816 Arguments parser

positional arguments:
  input_file            The asm file to assemble.

optional arguments:
  -h, --help            show this help message and exit
  --verbose             Displays all log levels.
  -o OUTPUT_FILE, --output OUTPUT_FILE
                        Output file
```

or directly from python code:

### From python code

```python
from a816.program import Program

def build_patch(input, output):
    program = Program()
    program.assemble_as_patch(input, output)
    program.resolver.dump_symbol_map()
```

## Supported Syntax

### Memonics

```
adc, and, asl, bcc, bcs, beq, bit, bmi, bne, bpl, bra, brk, brl, bvc, bvs, clc, cld, cli, clv, cmp, cop, cpx, cpy, db, dec, dex, dey, eor, inc, inx, iny, jml, jmp, jsl, jsr, lda, ldx, ldy, lsr, mvn, mvp, nop, ora, pea, pei, per, pha, phb, phd, phk, php, phx, phy, pla, plb, pld, plp, plx, ply, rep, rol, ror, rti, rtl, rts, sbc, sec, sed, sei, sep, sta, stp, stx, sty, stz, tax, tay, tcd, tcs, tdc, trb, tsb, tsc, tsx, txa, txs, txy, tya, tyx, wai, xba, xce
```

## Macros

```ca65
; define a macro
.macro test(var_1, var_2) {
    lda.w var_1 << 16 + var_2
}

; use that macro
test(0x10, 0x10)

; should expand that way
lda.w 0x10 << 16 + 0x10

; code generated
lda.w 0x1010
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
    ; this label is only visible inside this scope
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