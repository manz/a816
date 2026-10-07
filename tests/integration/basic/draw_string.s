"""
Tilemap stamper: walk a null-terminated `.text` string and write one
tilemap entry per glyph into the tilemap shadow at a caller-supplied
byte offset.
"""


.import "preamble"
.import "engine_state"

.alloc draw_string in engine {
"""
Stamp one tilemap entry per glyph into `engine_state.tilemap + X`.

    Caller sets:
      * X: byte offset into the tilemap shadow (2 bytes per cell, so
        `0x40` is row 1 of the 32-wide BG1 layer);
      * Y: string offset within DB (`STRINGS_BANK`).

    Each entry is a word: low byte the tile index, high byte 0. Stops on
    the null. Caller convention: A = 8 / X = 16 (DB = STRINGS_BANK).
    X is preserved across the call.
"""


    .a8
    .i16
    phx
_draw_string_loop:
    lda.w 0x0000, y  ; read DB:Y
    beq _draw_string_done
    sta.l engine_state.tilemap, x
    lda #0
    sta.l engine_state.tilemap + 1, x
    inx
    inx
    iny
    bra _draw_string_loop
_draw_string_done:
    plx
    rts
}
