"""
ROM-wide preamble: prepended to every module compilation and to
`main.s` via the `prelude` setting in `a816.toml`.

Declares the bank pools, the WRAM layout, named constants, the SNES
stdlib struct imports, and the typed-register binds every module
shares. No explicit `.import "layout"` at module heads.
"""


.import "@std/snes/ppu"
.import "@std/snes/cpu"
.import "@std/snes/dma"

; --- Typed register binds --------------------------------------------------
; Modules use `screen.<field>` / `cpu_regs.<field>` instead of repeating
; `BASE + Struct.field` everywhere. Names chosen to read at the call
; site without colliding with the underlying stdlib struct identifiers.
screen := (PPU_BASE as PPU)
cpu_regs := (CPU_REGS_BASE as CPU_REGS)

; --- Pools -----------------------------------------------------------------
; Bank-per-role split: client (bank 0) holds reset + NMI thunk + vectors;
; data (bank 1) holds font/strings; engine (bank 2) holds the engine
; code that NMI long-calls into.
.pool client {
    range 0x008000 0x00FFBF
    strategy order
}

.pool data {
    range 0x018000 0x01FFFF
    strategy order
}

.pool engine {
    range 0x028000 0x02FFFF
    strategy order
}

; --- WRAM layout -----------------------------------------------------------
; $7E:0000-$01FF reserved for direct page + stack. Buffers live past that.
tilemap_buffer = 0x7E2000  ; 0x800 bytes (32x32 BG1 entries)
tilemap_dirty = 0x7E2800  ; non-zero => NMI flushes tilemap
palette_buffer = 0x7E2C00  ; 0x200 bytes (256 CGRAM entries)
palette_dirty = 0x7E2E00  ; non-zero => NMI flushes palette

; --- Constants -------------------------------------------------------------
STRINGS_BANK = 0x01  ; bank holding font + strings (DB)
FONT_VRAM_WORD = 0x1000  ; BG1 char base (word address)
TILEMAP_WORD = 0x0000  ; BG1 tilemap base (word address)
TILEMAP_BYTES = 0x0800  ; 32x32 entries * 2 bytes
PALETTE_BYTES = 0x0200  ; 256 entries * 2 bytes
COLOR_BLACK = 0x0000
COLOR_WHITE = 0x7FFF
