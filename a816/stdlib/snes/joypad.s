"""SNES joypad serial / manual-read registers ($4016-$4017).

These are the raw serial interface; the auto-read shadow registers
($4218-$421F) live in `@std/snes/cpu` as `PAD1L/PAD1H` etc.

    .import "@std/snes/joypad"

    pad := (JOYPAD_BASE as JOYPAD_SERIAL)
        ; manual read sequence
        lda #1
        sta pad.JOYSER0
        stz pad.JOYSER0
        ; shift bits out of JOYSER0/JOYSER1
"""

JOYPAD_BASE = 0x4016

.struct JOYPAD_SERIAL {
    byte JOYSER0
    byte JOYSER1
}
