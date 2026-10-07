"""
Bank-1 data: the font tiles and the greeting string, placed by the
`data` pool. Palette colours aren't stored here: the boot path writes
them into the WRAM shadow with `set_palette_color` (`ppu_tools.s`).
"""


.import "preamble"


.table "assets/ff4_charset.tbl"
.alloc font_data in data {
    .incbin "assets/ff4_font_fixed.bin"
}

.alloc hello_string in data {
"""Table-encoded greeting drawn by the boot path, null-terminated."""
    .text "Gyshal Whistle"
    .db 0
}
