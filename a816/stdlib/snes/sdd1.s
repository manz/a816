"""
S-DD1 decompression coprocessor registers ($4800-$4807).

The S-DD1 (Star Ocean, Street Fighter Alpha 2) decompresses on the fly
while a DMA channel reads its source from banks $C0-$FF, and pages the
ROM into those banks one megabyte at a time.

    .import "@std/snes/sdd1"

    sdd1 := (SDD1_BASE as SDD1)
        lda #0x01
        sta sdd1.DMA_ENABLE          ; channel 0 decompresses ...
        sta sdd1.DMA_TRIGGER         ; ... on its next transfer
        sta 0x420B                   ; MDMAEN: run it
        stz sdd1.DMA_ENABLE

The example's absolute stores need DB in $00-$3F or $80-$BF; the macros
use long stores and work whatever DB holds.

Register layout follows ares (`sfc/coprocessor/sdd1`). $4802-$4803 and
$4808-$480F are not registers: reads fall through to ROM.
"""

SDD1_BASE = 0x4800

.struct SDD1 {
    """
    `DMA_ENABLE` ($4800): bit n set lets DMA channel n decompress.
    `DMA_TRIGGER` ($4801): bit n set decompresses channel n's next
    transfer; the chip clears the bit once the transfer's byte count
    runs out.
    `BANK_C0`..`BANK_F0` ($4804-$4807): bits 0-3 pick the 1 MiB ROM page
    seen at $C0-$CF, $D0-$DF, $E0-$EF and $F0-$FF. Power-on pages are
    0, 1, 2 and 3. Bit 7 of `BANK_D0` makes $20-$3F:8000-FFFF read as
    $00-$1F; bit 7 of `BANK_F0` does the same for $A0-$BF.
    """
    byte DMA_ENABLE
    byte DMA_TRIGGER
    byte UNUSED_4802
    byte UNUSED_4803
    byte BANK_C0
    byte BANK_D0
    byte BANK_E0
    byte BANK_F0
}

.macro sdd1_decompress(channels) {
    """
    Decompress through the DMA `channels` (bit mask) and start them.
    Configure the channels first; the source is the compressed data's
    address in $C0-$FF. Clears `DMA_ENABLE` once the transfer is done,
    as the games do. Long stores, so any DB works. A is 8-bit and
    clobbered.
    """
    lda.b #channels
    sta.l SDD1_BASE + SDD1.DMA_ENABLE
    sta.l SDD1_BASE + SDD1.DMA_TRIGGER
    sta.l 0x00420B  ; MDMAEN
    lda.b #0
    sta.l SDD1_BASE + SDD1.DMA_ENABLE
}

.macro sdd1_banks(c0, d0, e0, f0) {
    """Page ROM megabytes into $C0, $D0, $E0 and $F0. Long stores, so any DB works. A is 8-bit and clobbered."""
    lda.b #c0
    sta.l SDD1_BASE + SDD1.BANK_C0
    lda.b #d0
    sta.l SDD1_BASE + SDD1.BANK_D0
    lda.b #e0
    sta.l SDD1_BASE + SDD1.BANK_E0
    lda.b #f0
    sta.l SDD1_BASE + SDD1.BANK_F0
}
