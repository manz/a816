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

; -----------------------------------------------------------------------------
; Per-register bit-field structs for the regs whose bits the user
; toggles directly. Bind to the register address; the addressing fields
; expose the containing byte address, the mask/shift constants stay
; absolute on the struct type.
; -----------------------------------------------------------------------------

.struct NMITIMEN {
    u1 joypad_enable
    u3 unused
    u1 h_irq_enable
    u1 v_irq_enable
    u1 unused2
    u1 nmi_enable
}

.struct MEMSEL {
    u1 fastrom
    u7 unused
}

.struct RDNMI {
    u4 cpu_version
    u3 unused
    u1 nmi_flag
}

.struct TIMEUP {
    u7 unused
    u1 irq_flag
}

.struct HVBJOY {
    u1 joypad_busy
    u5 unused
    u1 hblank
    u1 vblank
}

.struct MDMAEN {
    u1 ch0
    u1 ch1
    u1 ch2
    u1 ch3
    u1 ch4
    u1 ch5
    u1 ch6
    u1 ch7
}

.struct HDMAEN {
    u1 ch0
    u1 ch1
    u1 ch2
    u1 ch3
    u1 ch4
    u1 ch5
    u1 ch6
    u1 ch7
}

