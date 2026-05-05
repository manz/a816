; Variable-width font module fixture.

"""Variable-width font helpers used by the menu and dialogue engines."""

"""Initialise VWF state. Call once on boot."""
.macro vwf_init() {
    sep #0x20
    lda.b #0x00
    sta.b _vwf_cursor
}

"""Render one glyph from the queued buffer."""
vwf_render:
    rep #0x30
    rts

_vwf_cursor = 0x7E0000
