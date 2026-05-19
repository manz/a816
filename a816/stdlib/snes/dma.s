"""SNES DMA channels ($4300-$437F).

Each of the eight DMA channels has a 16-byte register slice; the first
12 bytes are documented (the remaining four are reserved / unmapped).
The `DMAChannel` struct lays out one channel; the eight channels are
addressable by computing the base + `channel * 0x10` or by binding one
typed instance per channel that needs configuration.

    .import "@std/snes/dma"
    .import "@std/snes/cpu"

    ; Channel 0 → VRAM upload
    ch0 := (DMA_BASE as DMAChannel)
        lda #0x01
        sta ch0.DMAP
        lda #0x18                    ; VMDATAL
        sta ch0.BBAD
        ; ... configure source + size, then enable on MDMAEN

Channel base addresses for direct constant use:

    DMA_CH0 = DMA_BASE + 0 * DMAChannel.__size
    DMA_CH1 = DMA_BASE + 1 * DMAChannel.__size
    ...

The 16-byte stride is the bus mapping width even though only 12 are
register-mapped; the trailing four are reserved on real hardware.
"""

DMA_BASE = 0x4300

.struct DMAChannel {
    byte DMAP
    byte BBAD
    byte A1TL
    byte A1TH
    byte A1B
    byte DASL
    byte DASH
    byte DASB
    byte A2AL
    byte A2AH
    byte NTRL
    byte UNUSED_B
    byte UNUSED_C
    byte UNUSED_D
    byte UNUSED_E
    byte UNUSED_F
}
