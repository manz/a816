"""PPU upload primitives + runtime palette API.

`palette_buffer` is a full 256-entry CGRAM shadow living in WRAM —
mutate via `set_palette_color` (sets `palette_dirty`), and the NMI
handler flushes the whole thing to CGRAM next vblank. Font upload +
tilemap-shadow clear live here too.

Imports `data` so per-module precompile (object mode) can resolve
`font_data` + the `.incbin` auto-symbol to externs. Main's
direct-mode `.import` inlines both regardless.
"""

.import "data"

.alloc set_palette_color in engine {
    """Write a 16-bit color into the WRAM palette shadow at index X.

    Inputs (16-bit A and X):
      A = color word (BGR555)
      X = palette index (0..255)

    Sets `palette_dirty` so NMI flushes the buffer this frame.
    """
    php
    rep #0x30
    .a16
    .i16
    pha                                 ; preserve color word
    txa
    asl                                 ; A = X * 2 (byte offset)
    tax
    pla
    sta.l palette_buffer, x
    sep #0x20
    .a8
    lda #1
    sta.l palette_dirty
    plp
    rts
}

.alloc upload_font in engine {
    """DMA the fixed-font tile blob into VRAM at word-address $1000."""
    lda.b #FONT_VRAM_WORD & 0xFF
    sta.l screen.VMADDL
    lda.b #(FONT_VRAM_WORD >> 8) & 0xFF
    sta.l screen.VMADDH
    lda #0x80
    sta.l screen.VMAIN

    lda #0x01
    sta.l 0x4300                        ; DMAP0: 2-reg auto-increment
    lda #0x18
    sta.l 0x4301                        ; BBAD0 -> $2118 (VMDATAL)

    lda.b #font_data & 0xFF
    sta.l 0x4302
    lda.b #(font_data >> 8) & 0xFF
    sta.l 0x4303
    lda.b #(font_data >> 16) & 0xFF
    sta.l 0x4304

    lda.b #___assets_ff4_font_fixed_bin__size & 0xFF
    sta.l 0x4305
    lda.b #(___assets_ff4_font_fixed_bin__size >> 8) & 0xFF
    sta.l 0x4306

    lda #0x01
    sta.l cpu_regs.MDMAEN
    rts
}

.alloc clear_tilemap_buffer in engine {
    """Fill the WRAM tilemap shadow with $FF tile indices (attr bytes = 0)."""
    php
    sep #0x20
    .a8
    rep #0x10
    .i16
    lda #0xff
    ldx #0
_clear_tilemap_loop:
    sta.l tilemap_buffer, x
    inx
    inx
    cpx #TILEMAP_BYTES
    bne _clear_tilemap_loop
    rep #0x20
    .a16
    plp
    rts
}
