# Standard library

a816 ships a small standard library of `.struct` declarations for SNES
hardware registers. The modules live inside the wheel and are reached
via the `@std/` virtual prefix: they never collide with user modules,
and there is no path to configure.

```ca65
.import "@std/snes/ppu"

*= 0x008000
init:
    ppu := (PPU_BASE as PPU)
        lda ppu.INIDISP
        sta ppu.OAMDATA
    rts
```

## What's included

| Module               | Covers                                            |
|----------------------|---------------------------------------------------|
| `@std/snes/ppu`      | `$2100`–`$213F` PPU control / VRAM / CGRAM / OAM  |
| `@std/snes/cpu`      | `$4200`–`$421F` NMI / IRQ / math / joypad shadows |
| `@std/snes/dma`      | `$4300`–`$437F` DMA channels (16-byte stride)     |
| `@std/snes/apu`      | `$2140`–`$2143` APU communication ports           |
| `@std/snes/joypad`   | `$4016`–`$4017` serial joypad ports               |
| `@std/snes/wram`     | `$2180`–`$2183` WRAM streaming port               |
| `@std/snes/header`   | `$FFB0`–`$FFFF` cartridge header + vectors        |
| `@std/snes/sdd1`     | `$4800`–`$4807` S-DD1 decompression + ROM banks   |
| `@std/snes/spc7110`  | `$4800`–`$4842` SPC7110 units + Epson RTC         |

Each module exports a single struct named after the block (`PPU`,
`CPU_REGS`, `DMAChannel`, …) plus a base-address constant
(`PPU_BASE`, `CPU_REGS_BASE`, `DMA_BASE`, …). Pair the two via a
typed bind to get auto-sized opcode emission on every field access:

```ca65
.import "@std/snes/cpu"

cpu := (CPU_REGS_BASE as CPU_REGS)
    lda cpu.RDNMI                 ; → LDA $4210
```

## Cartridge header and vectors

`@std/snes/header` is data rather than registers: `SnesHeader` lays out
the extended (`$FFB0`) and standard (`$FFC0`) cartridge header,
`SnesVectors` the native (`$FFE0`) and emulation (`$FFF0`) interrupt
vectors. Emit both with [`.istruct`](directives.md) at
`SNES_HEADER_BASE` / `SNES_VECTORS_BASE`; unset fields are 0. A ROM
hack that changes only some fields uses [`.patch`](directives.md)
instead, which leaves the rest as the ROM has them.

```ca65
.import "@std/snes/header"

.alloc snes_header at SNES_HEADER_BASE {
    .istruct SnesHeader {
        title = "MY GAME              "  ; 21 bytes, space padded
        map_mode = 0x20  ; LoROM, SlowROM
        rom_size = 0x08  ; 256 KiB
        destination = 0x01  ; North America
    }
}

.alloc vectors at SNES_VECTORS_BASE {
    .istruct SnesVectors {
        native = { nmi = nmi_handler, irq = irq_handler }
        emulation = { reset = reset }
    }
}
```

| Address | `SnesHeader` field | Notes |
|---------|--------------------|-------|
| `$FFB0` | `maker_code[2]` | ASCII, extended header |
| `$FFB2` | `game_code[4]` | ASCII |
| `$FFB6` | `reserved[6]` | zero |
| `$FFBC` | `expansion_flash_size` | |
| `$FFBD` | `expansion_ram_size` | |
| `$FFBE` | `special_version` | |
| `$FFBF` | `cartridge_subtype` | |
| `$FFC0` | `title[21]` | ASCII, space padded |
| `$FFD5` | `map_mode` | `0x20` LoROM, `0x21` HiROM, `+0x10` FastROM |
| `$FFD6` | `cartridge_type` | ROM / RAM / battery / coprocessor |
| `$FFD7` | `rom_size` | log2 of the size in KiB |
| `$FFD8` | `sram_size` | log2 of the size in KiB, 0 for none |
| `$FFD9` | `destination` | region code |
| `$FFDA` | `old_maker_code` | `0x33` marks the extended header as present |
| `$FFDB` | `version` | |
| `$FFDC` | `checksum_complement` | word |
| `$FFDE` | `checksum` | word |

`SnesNativeVectors` fields: `reserved[4]`, `coprocessor`, `break`,
`abort`, `nmi`, `unused`, `irq`. `SnesEmulationVectors`: `reserved[4]`,
`coprocessor`, `unused`, `abort`, `nmi`, `reset`, `irq_brk`.
(`cop` / `brk` would scan as opcodes, hence the long names.)

The checksum is out of scope: both checksum fields stay 0 until a
post-link step fills them in.

## Coprocessors

`@std/snes/sdd1` and `@std/snes/spc7110` name the registers of the two
decompression chips, after ares. a816 has no compressor or
decompressor: that is tooling, in katsuji or the game's repo.

| Module    | Structs                                                          | Macros |
|-----------|------------------------------------------------------------------|--------|
| `sdd1`    | `SDD1`                                                           | `sdd1_decompress(channels)`, `sdd1_banks(c0, d0, e0, f0)` |
| `spc7110` | `SPC7110_DCU`, `SPC7110_DATA`, `SPC7110_ALU`, `SPC7110_MCU`, `EPSON_RTC` | `spc7110_dcu_start(table, index, offset)`, `spc7110_dcu_wait()`, `spc7110_data_seek(offset)`, `spc7110_banks(d0, e0, f0)` |

The macros expect an 8-bit accumulator and clobber it.

```ca65
.import "@std/snes/spc7110"

    spc7110_dcu_start(0x080000, 3, 0)  ; directory at data ROM $08:0000, entry 3
    spc7110_dcu_wait()
    lda SPC7110_DCU_BASE + SPC7110_DCU.DATA
```

The SPC7110 board maps `$D0`-`$FF` linearly over the data ROM, which
is what the power-on banks 0, 1 and 2 at `$4831`-`$4833` show. A game
that reprograms them (Far East of Eden Zero sets 1, 2, 3 at reset)
describes its banks with `[map.N]` in its `a816.toml`.

## How resolution works

When the parser sees `.import "@std/snes/ppu"` it strips the `@std/`
prefix and looks for `<wheel>/a816/stdlib/snes/ppu.s` (or `.o`). If
the file is missing the import fails with the usual
`Module not found:` error: there is no implicit fallback to user
search paths once `@std/` is on the front.

To browse the bundled source from a Python REPL:

```python
from importlib.resources import files
print((files("a816.stdlib") / "snes" / "ppu.s").read_text())
```

## Extending

To add another block of registers, drop a `.s` file under
`a816/stdlib/<arch>/<name>.s` in this repository and ship it in the
next release. The wheel build picks the file up automatically; the
resolver has no per-file allowlist.

For project-private "stdlib" modules (game-specific RAM layouts,
shared macro bundles), keep using regular `module_paths` configured
in `a816.toml`: `@std/` is reserved for assembler-bundled content.
