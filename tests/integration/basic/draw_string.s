"""
Tilemap-stamping routine: walk a null-terminated `.text` blob and
write one tilemap entry per glyph into `tilemap_buffer` at a
caller-supplied byte offset (X).
"""

.import "@std/snes/ppu"
.import "@std/snes/cpu"
.import "@std/snes/dma"
.import "preamble"


.alloc draw_string in engine {
"""
Stamp one tilemap entry per glyph into `tilemap_buffer + X`.

    Caller sets:
      * X — byte offset into `tilemap_buffer` (start of destination
        cell; advances by 2 per glyph). E.g. `0x40` lands at row 2
        of the 32-wide BG1 layer.
      * Y — string offset within DB (`STRINGS_BANK`).

    Each entry is a 16-bit word: low byte = tile index, high byte = 0.
    Stops on null.
    Caller convention: A=8 / X=16 / I=16 (DB = STRINGS_BANK).
    X is preserved across the call (pushed on entry, popped on exit).
"""


    .a8
    .i16
    phx
_draw_string_loop:
    lda.w 0x0000, y  ; abs,Y read from DB:Y
    beq _draw_string_done
    sta.l tilemap_buffer, x
    lda #0
    sta.l tilemap_buffer + 1, x
    inx
    inx
    iny
    bra _draw_string_loop
_draw_string_done:
    plx
    rts
}
