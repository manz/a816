.import "@std/snes/ppu"
.import "@std/snes/cpu"
.import "@std/snes/dma"
.import "preamble"
"""Engine entry points + BRK trap.

Engine code lives in its own bank (`engine` pool). Cross-bank
callers use the `_l` long wrappers; engine-internal callers use the
bare names with plain `jsr`/`rts`.

Imports `ppu_tools` + `draw_string` so per-module precompile can
resolve the routines wrapped here as externs.
"""

.import "ppu_tools"
.import "draw_string"

.alloc engine_update in engine {
    """Per-frame entry. Walks dirty flags and DMAs the WRAM shadows
    that advertise themselves into PPU (`tilemap_dirty`,
    `palette_dirty`).

    Caller convention: NMI prologue already saved A/X/Y/B/D and
    set M = 8 / X = 16. No further reg save here.
    """
    .a8
    .i16

    lda.l tilemap_dirty
    beq _engine_skip_tilemap

    lda.b #TILEMAP_WORD & 0xFF
    sta.l screen.VMADDL
    lda.b #(TILEMAP_WORD >> 8) & 0xFF
    sta.l screen.VMADDH
    lda #0x80
    sta.l screen.VMAIN

    lda #0x01
    sta.l 0x4300                            ; DMAP0: 2-reg auto-increment
    lda #0x18
    sta.l 0x4301                            ; BBAD0 -> $2118 (VMDATAL)

    lda.b #tilemap_buffer & 0xFF
    sta.l 0x4302
    lda.b #(tilemap_buffer >> 8) & 0xFF
    sta.l 0x4303
    lda.b #(tilemap_buffer >> 16) & 0xFF
    sta.l 0x4304

    lda.b #TILEMAP_BYTES & 0xFF
    sta.l 0x4305
    lda.b #(TILEMAP_BYTES >> 8) & 0xFF
    sta.l 0x4306

    lda #0x01
    sta.l cpu_regs.MDMAEN

    lda #0
    sta.l tilemap_dirty

_engine_skip_tilemap:
    lda.l palette_dirty
    beq _engine_done

    lda #0x00
    sta.l screen.CGADD                      ; CGRAM addr = 0

    lda #0x00
    sta.l 0x4300                            ; DMAP0: 1-reg auto-increment
    lda #0x22
    sta.l 0x4301                            ; BBAD0 -> $2122 (CGDATA)

    lda.b #palette_buffer & 0xFF
    sta.l 0x4302
    lda.b #(palette_buffer >> 8) & 0xFF
    sta.l 0x4303
    lda.b #(palette_buffer >> 16) & 0xFF
    sta.l 0x4304

    lda.b #PALETTE_BYTES & 0xFF
    sta.l 0x4305
    lda.b #(PALETTE_BYTES >> 8) & 0xFF
    sta.l 0x4306

    lda #0x01
    sta.l cpu_regs.MDMAEN

    lda #0
    sta.l palette_dirty

_engine_done:
    rts
}

.alloc engine_update_l in engine {
    """Long entry: callers outside the engine bank use `jsr.l
    engine_update_l`. Trampolines into the bare `engine_update`
    impl and returns with `rtl`."""
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
