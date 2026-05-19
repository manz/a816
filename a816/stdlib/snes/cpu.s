"""SNES CPU-side I/O registers ($4200-$421F).

Covers NMI / IRQ / joypad enables, the multiplier and divider math
pair, the joypad auto-read buffers, and the H/V counters. Bind to
its base for typed access:

    .import "@std/snes/cpu"

    cpu_regs := (CPU_REGS_BASE as CPU_REGS)
        lda cpu_regs.NMITIMEN
        sta cpu_regs.WRMPYA

`PAD1L`/`PAD1H` etc. are the auto-read joypad shadows refreshed by
the hardware every vblank; they are stable to read between $4218
and $421F once `NMITIMEN.auto_joypad_read` is enabled.
"""

CPU_REGS_BASE = 0x4200

.struct CPU_REGS {
    byte NMITIMEN
    byte WRIO
    byte WRMPYA
    byte WRMPYB
    byte WRDIVL
    byte WRDIVH
    byte WRDIVB
    byte HTIMEL
    byte HTIMEH
    byte VTIMEL
    byte VTIMEH
    byte MDMAEN
    byte HDMAEN
    byte MEMSEL
    byte UNUSED_420E
    byte UNUSED_420F
    byte RDNMI
    byte TIMEUP
    byte HVBJOY
    byte RDIO
    byte RDDIVL
    byte RDDIVH
    byte RDMPYL
    byte RDMPYH
    byte PAD1L
    byte PAD1H
    byte PAD2L
    byte PAD2H
    byte PAD3L
    byte PAD3H
    byte PAD4L
    byte PAD4H
}
