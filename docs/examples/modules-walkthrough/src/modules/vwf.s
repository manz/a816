"""VWF helpers shared across the hack."""

.alloc vwf_code at 0x018000 {
    .scope vwf {
init:
"""Set the VRAM increment mode used by the VWF renderer."""
    lda.b #0x80
    sta.w 0x2115
    rtl

; private, only callable from inside this module
_zero_pad:
    rep #0x20
    lda.w #0x0000
    rts
    }
}
