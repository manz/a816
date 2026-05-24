"""Bank-0 patches, included from main.s. Two `*=` sites pinning
small instruction sequences."""

.include "header.i"

*=0x008100
patch_a_entry:
    lda #SHARED_TILE
    sta 0x00
    rts

*=0x008200
patch_a_alt:
    lda #SHARED_TILE
    sta 0x01
    rts
