"""
PPU upload primitives and the runtime palette API.

`engine_state.palette` is a full 256-entry CGRAM shadow in WRAM: change
it with `set_palette_color`, which sets `palette_dirty`, and the NMI
flushes the whole shadow next vblank. The font upload and the tilemap
shadow clear live here too.
"""


.import "preamble"
.import "engine_state"
.import "data"

.alloc set_palette_color in engine {
"""
Write a 16-bit colour into the palette shadow at index X.

    Inputs (16-bit A and X):
      A = colour word (BGR555)
      X = palette index (0..255)

    Sets `palette_dirty` so NMI flushes the shadow this frame.
"""


    php
    rep #0x30
    .a16
    .i16
    pha  ; preserve the colour word
    txa
    asl  ; A = X * 2 (byte offset)
    tax
    pla
    sta.l engine_state.palette, x
    sep #0x20
    .a8
    lda #1
    sta.l engine_state.palette_dirty
    plp
    rts
}

.alloc upload_font in engine {
"""DMA the fixed-font tiles into VRAM at word address $1000."""
    lda.b #FONT_VRAM_WORD & 0xFF
    sta.l screen.VMADDL
    lda.b #( FONT_VRAM_WORD >> 8 ) & 0xFF
    sta.l screen.VMADDH
    lda #0x80
    sta.l screen.VMAIN

    lda #0x01
    sta.l dma0.DMAP  ; 2-register auto-increment
    lda #0x18
    sta.l dma0.BBAD  ; -> $2118 (VMDATAL)

    lda.b #font_data & 0xFF
    sta.l dma0.A1TL
    lda.b #( font_data >> 8 ) & 0xFF
    sta.l dma0.A1TH
    lda.b #( font_data >> 16 ) & 0xFF
    sta.l dma0.A1B

    lda.b #sizeof(font_data) & 0xFF
    sta.l dma0.DASL
    lda.b #( sizeof(font_data) >> 8 ) & 0xFF
    sta.l dma0.DASH

    lda #0x01
    sta.l cpu_regs.MDMAEN
    rts
}

.alloc clear_tilemap_buffer in engine {
"""Fill the tilemap shadow with tile $FF (attribute bytes 0)."""
    php
    rep #0x30
    .a16
    .i16
    lda #0xff
    ldx #0
_clear_tilemap_loop:
    sta.l engine_state.tilemap, x
    inx
    inx
    cpx #sizeof(Engine.tilemap)
    bne _clear_tilemap_loop
    plp
    rts
}
