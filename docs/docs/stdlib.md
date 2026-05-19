# Standard library

a816 ships a small standard library of `.struct` declarations for SNES
hardware registers. The modules live inside the wheel and are reached
via the `@std/` virtual prefix — they never collide with user modules,
and there is no path to configure.

```ca65
.import "@std/snes/ppu"

*=0x008000
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

Each module exports a single struct named after the block (`PPU`,
`CPU_REGS`, `DMAChannel`, …) plus a base-address constant
(`PPU_BASE`, `CPU_REGS_BASE`, `DMA_BASE`, …). Pair the two via a
typed bind to get auto-sized opcode emission on every field access:

```ca65
.import "@std/snes/cpu"

cpu := (CPU_REGS_BASE as CPU_REGS)
    lda cpu.RDNMI                 ; → LDA $4210
```

## How resolution works

When the parser sees `.import "@std/snes/ppu"` it strips the `@std/`
prefix and looks for `<wheel>/a816/stdlib/snes/ppu.s` (or `.o`). If
the file is missing the import fails with the usual
`Module not found:` error — there is no implicit fallback to user
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
in `a816.toml` — `@std/` is reserved for assembler-bundled content.
