"""
Engine entry points.

Engine code lives in its own bank (`engine` pool). Callers in other
banks use the `_l` long entries; engine-internal callers use the bare
names with `jsr.w` / `rts`.
"""


.import "preamble"
.import "engine_state"
.import "ppu_tools"
.import "draw_string"

.alloc engine_update in engine {
"""
Per-frame entry: DMA each WRAM shadow whose dirty flag is set into the
    PPU, then clear the flag.

    Caller convention: the NMI prologue already saved A/X/Y/B/D and set
    M = 8 / X = 16. No further register save here.
"""


    .a8
    .i16

    lda.l engine_state.tilemap_dirty
    beq _engine_skip_tilemap

    lda.b #TILEMAP_WORD & 0xFF
    sta.l screen.VMADDL
    lda.b #( TILEMAP_WORD >> 8 ) & 0xFF
    sta.l screen.VMADDH
    lda #0x80
    sta.l screen.VMAIN

    lda #0x01
    sta.l dma0.DMAP  ; 2-register auto-increment
    lda #0x18
    sta.l dma0.BBAD  ; -> $2118 (VMDATAL)

    lda.b #engine_state.tilemap & 0xFF
    sta.l dma0.A1TL
    lda.b #( engine_state.tilemap >> 8 ) & 0xFF
    sta.l dma0.A1TH
    lda.b #( engine_state.tilemap >> 16 ) & 0xFF
    sta.l dma0.A1B

    lda.b #sizeof(Engine.tilemap) & 0xFF
    sta.l dma0.DASL
    lda.b #( sizeof(Engine.tilemap) >> 8 ) & 0xFF
    sta.l dma0.DASH

    lda #0x01
    sta.l cpu_regs.MDMAEN

    lda #0
    sta.l engine_state.tilemap_dirty

_engine_skip_tilemap:
    lda.l engine_state.palette_dirty
    beq _engine_done

    lda #0x00
    sta.l screen.CGADD  ; CGRAM address 0

    lda #0x00
    sta.l dma0.DMAP  ; 1-register auto-increment
    lda #0x22
    sta.l dma0.BBAD  ; -> $2122 (CGDATA)

    lda.b #engine_state.palette & 0xFF
    sta.l dma0.A1TL
    lda.b #( engine_state.palette >> 8 ) & 0xFF
    sta.l dma0.A1TH
    lda.b #( engine_state.palette >> 16 ) & 0xFF
    sta.l dma0.A1B

    lda.b #sizeof(Engine.palette) & 0xFF
    sta.l dma0.DASL
    lda.b #( sizeof(Engine.palette) >> 8 ) & 0xFF
    sta.l dma0.DASH

    lda #0x01
    sta.l cpu_regs.MDMAEN

    lda #0
    sta.l engine_state.palette_dirty

_engine_done:
    rts
}

.alloc engine_update_l in engine {
"""Long entry to `engine_update`, for the NMI thunk in bank 0."""
    jsr.w engine_update
    rtl
}

.alloc upload_font_l in engine {
"""Long entry to `upload_font`, for client banks."""
    jsr.w upload_font
    rtl
}

.alloc clear_tilemap_buffer_l in engine {
"""Long entry to `clear_tilemap_buffer`, for client banks."""
    jsr.w clear_tilemap_buffer
    rtl
}

.alloc set_palette_color_l in engine {
"""Long entry to `set_palette_color`, for client banks."""
    jsr.w set_palette_color
    rtl
}

.alloc draw_string_l in engine {
"""Long entry to `draw_string`, for client banks."""
    jsr.w draw_string
    rtl
}
