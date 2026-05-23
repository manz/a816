"""
Bank-1 data blobs: font tiles + greeting string.

Allocated into the shared `bank1` pool declared in `layout.s`. Palette
init values are not stored here — they're written into the WRAM shadow
at runtime via `set_palette_color` (see `ppu_tools.s`).
"""


.import "preamble"


.table "assets/ff4_charset.tbl"
.alloc font_data in data {
    .incbin "assets/ff4_font_fixed.bin"
}

.alloc hello_string in data {
"""Table-encoded greeting drawn by the boot path  ; null-terminated."""
    .text "Gyshal Whistle"
    .db 0
}
