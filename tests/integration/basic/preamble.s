"""
Shared declarations every module imports: the SNES register binds, the
bank pools, the WRAM pool and the engine state's layout, and the named
constants.

The engine state itself is reserved once, in `engine_state.s`; this
module only declares its type, so importing it emits nothing.
"""


.import "@std/snes/ppu"
.import "@std/snes/cpu"
.import "@std/snes/dma"

; --- Typed register binds --------------------------------------------------
; Modules write `screen.<field>`, `cpu_regs.<field>` and `dma0.<field>`
; instead of repeating `BASE + Struct.field` or raw `$43xx` addresses.
screen := (PPU_BASE as PPU)
cpu_regs := (CPU_REGS_BASE as CPU_REGS)
dma0 := (DMA_BASE as DMAChannel)

; --- Pools -----------------------------------------------------------------
; Bank-per-role split: client (bank 0) holds reset and the interrupt
; thunks, stopping short of the $FFB0 cartridge header and vectors;
; data (bank 1) holds the font and strings; engine (bank 2) holds the
; engine code that NMI long-calls into.
.pool client {
    range 0x008000 0x00FFAF
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

; $7E:0000-$01FF hold the direct page and the stack; WRAM state lives past
; them, laid out by the allocator.
.pool wram {
    bss
    range 0x7E2000 0x7EFFFF
    strategy order
}

; --- Engine state ----------------------------------------------------------
.struct Engine {
    word[32 * 32] tilemap  ; BG1 shadow: one entry per 8x8 cell
    word[256] palette  ; CGRAM shadow
    byte tilemap_dirty  ; non-zero: NMI flushes the tilemap
    byte palette_dirty  ; non-zero: NMI flushes the palette
}

; --- Constants -------------------------------------------------------------
STRINGS_BANK = 0x01  ; bank holding font + strings (DB)
FONT_VRAM_WORD = 0x1000  ; BG1 char base (word address)
TILEMAP_WORD = 0x0000  ; BG1 tilemap base (word address)
COLOR_BLACK = 0x0000
COLOR_WHITE = 0x7FFF
