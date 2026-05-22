"""Tilemap-stamping routine: walk a null-terminated `.text` blob and
write one tilemap entry per glyph into `tilemap_buffer` at offset
$40 (row 2 of the 32-wide BG1 layer).
"""


.alloc draw_string in engine {
    """Stamp one tilemap entry per glyph into `tilemap_buffer + 0x40`.

    Caller sets Y to the string offset within DB (STRINGS_BANK). Each
    entry is a 16-bit word: low byte = tile index, high byte = 0.
    Stops on null.
    Caller convention: A=8 / X=16 / I=16 (DB = STRINGS_BANK).
    """
    .a8
    .i16
    phx
    ldx #0
_draw_string_loop:
    lda.w 0x0000, y                     ; abs,Y read from DB:Y
    beq _draw_string_done
    sta.l tilemap_buffer + 0x40, x
    lda #0
    sta.l tilemap_buffer + 0x40 + 1, x
    inx
    inx
    iny
    bra _draw_string_loop
_draw_string_done:
    plx
    rts
}
