"""
SPC700 I/O registers ($F0-$FF) and the S-DSP register map.

The S-DSP is not memory-mapped: the SPC700 writes a register number to
`DSPADDR`, then reads or writes its value through `DSPDATA`. The macros
do both steps.

    .import "@std/spc700/dsp"

    .alloc driver at 0x0400 {
    start:
        dsp_write(DSP_FLG, 0x20)
        dsp_write(DSP_VOICE.SRCN + 2 * DSP_VOICE_STRIDE, 0)
        mov a, #0x04
        dsp_write_a(DSP_KON)
    }

Voice `n`'s registers are at `DSP_VOICE.<field> + n * DSP_VOICE_STRIDE`.
"""

; I/O registers, all direct page.
TEST = 0xF0
CONTROL = 0xF1
DSPADDR = 0xF2
DSPDATA = 0xF3
CPUIO0 = 0xF4
CPUIO1 = 0xF5
CPUIO2 = 0xF6
CPUIO3 = 0xF7
AUXIO4 = 0xF8
AUXIO5 = 0xF9
T0TARGET = 0xFA
T1TARGET = 0xFB
T2TARGET = 0xFC
T0OUT = 0xFD
T1OUT = 0xFE
T2OUT = 0xFF

DSP_VOICE_STRIDE = 0x10

.struct DSP_VOICE {
    """Voice 0's registers; voice n's are `n * DSP_VOICE_STRIDE` further."""
    byte VOL_L
    byte VOL_R
    byte PITCH_L
    byte PITCH_H
    byte SRCN
    byte ADSR1
    byte ADSR2
    byte GAIN
    byte ENVX
    byte OUTX
}

; Global DSP registers.
DSP_MVOL_L = 0x0C
DSP_MVOL_R = 0x1C
DSP_EVOL_L = 0x2C
DSP_EVOL_R = 0x3C
DSP_KON = 0x4C
DSP_KOFF = 0x5C
DSP_FLG = 0x6C
DSP_ENDX = 0x7C
DSP_EFB = 0x0D
DSP_PMON = 0x2D
DSP_NON = 0x3D
DSP_EON = 0x4D
DSP_DIR = 0x5D
DSP_ESA = 0x6D
DSP_EDL = 0x7D
; Echo FIR coefficient n is at `DSP_FIR0 + n * DSP_VOICE_STRIDE`.
DSP_FIR0 = 0x0F

.macro dsp_write(reg, value) {
    """Write `value` to DSP register `reg`. A is untouched."""
    mov DSPADDR, #reg
    mov DSPDATA, #value
}

.macro dsp_write_a(reg) {
    """Write A to DSP register `reg`."""
    mov DSPADDR, #reg
    mov DSPDATA, a
}

.macro dsp_read(reg) {
    """Read DSP register `reg` into A."""
    mov DSPADDR, #reg
    mov a, DSPDATA
}
