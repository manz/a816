"""SNES PPU registers ($2100-$213F).

Comprehensive layout of the PPU register block exposed as `.struct PPU`.
Bind it to its base address to get typed field access:

    .import "@std/snes/ppu"

    ppu := (PPU_BASE as PPU)
        lda ppu.INIDISP
        sta ppu.OAMADDL

All fields are byte-wide because the SNES PPU register interface is
strictly 8-bit at $21xx; multi-byte registers (e.g. BG offsets) are
written as two consecutive byte stores.
"""

PPU_BASE = 0x2100

.struct PPU {
    byte INIDISP
    byte OBSEL
    byte OAMADDL
    byte OAMADDH
    byte OAMDATA
    byte BGMODE
    byte MOSAIC
    byte BG1SC
    byte BG2SC
    byte BG3SC
    byte BG4SC
    byte BG12NBA
    byte BG34NBA
    byte BG1HOFS
    byte BG1VOFS
    byte BG2HOFS
    byte BG2VOFS
    byte BG3HOFS
    byte BG3VOFS
    byte BG4HOFS
    byte BG4VOFS
    byte VMAIN
    byte VMADDL
    byte VMADDH
    byte VMDATAL
    byte VMDATAH
    byte M7SEL
    byte M7A
    byte M7B
    byte M7C
    byte M7D
    byte M7X
    byte M7Y
    byte CGADD
    byte CGDATA
    byte W12SEL
    byte W34SEL
    byte WOBJSEL
    byte WH0
    byte WH1
    byte WH2
    byte WH3
    byte WBGLOG
    byte WOBJLOG
    byte TM
    byte TS
    byte TMW
    byte TSW
    byte CGWSEL
    byte CGADSUB
    byte COLDATA
    byte SETINI
    byte MPYL
    byte MPYM
    byte MPYH
    byte SLHV
    byte OAMDATAREAD
    byte VMDATALREAD
    byte VMDATAHREAD
    byte CGDATAREAD
    byte OPHCT
    byte OPVCT
    byte STAT77
    byte STAT78
}

; -----------------------------------------------------------------------------
; Per-register bit-field structs. Bind each to its register address for
; typed bit access:
;
;     inidisp := (PPU_BASE + PPU.INIDISP as INIDISP)
;         lda inidisp.brightness         ; addresses $2100
;         ora #INIDISP.force_blank.mask  ; immediate mask = 0x80
;
; Coexists with the monolithic `PPU` struct above — both APIs work; pick
; whichever reads best at the call site.
; -----------------------------------------------------------------------------

.struct INIDISP {
    u4 brightness
    u3 unused
    u1 force_blank
}

.struct OBSEL {
    u3 name_select
    u2 name_base
    u3 size
}

.struct BGMODE {
    u3 mode
    u1 bg3_priority
    u1 bg1_tile_size
    u1 bg2_tile_size
    u1 bg3_tile_size
    u1 bg4_tile_size
}

.struct MOSAIC {
    u1 bg1_enable
    u1 bg2_enable
    u1 bg3_enable
    u1 bg4_enable
    u4 size
}

.struct BGSC {
    u2 screen_size
    u6 name_base
}

.struct BGNBA {
    u4 low_bg_base
    u4 high_bg_base
}

.struct VMAIN {
    u2 increment_amount
    u2 translation
    u3 unused
    u1 increment_mode
}

.struct M7SEL {
    u1 h_flip
    u1 v_flip
    u4 unused
    u2 empty_fill
}

.struct TM {
    u1 bg1
    u1 bg2
    u1 bg3
    u1 bg4
    u1 obj
    u3 unused
}

.struct CGWSEL {
    u2 clip
    u2 prevent
    u2 unused
    u1 sub_screen_enable
    u1 direct_color
}

.struct CGADSUB {
    u1 bg1
    u1 bg2
    u1 bg3
    u1 bg4
    u1 obj
    u1 backdrop
    u1 half
    u1 subtract
}

.struct COLDATA {
    u5 channel_value
    u1 red
    u1 green
    u1 blue
}

.struct SETINI {
    u1 interlace
    u1 sprite_interlace
    u1 overscan
    u1 hires
    u2 unused
    u1 extbg
    u1 external_sync
}

.struct STAT77 {
    u4 ppu1_version
    u1 mode7_empty
    u1 unused
    u1 range_over
    u1 time_over
}

.struct STAT78 {
    u4 ppu2_version
    u1 mode
    u1 external_latch
    u1 ntsc_pal
    u1 interlace_field
}

