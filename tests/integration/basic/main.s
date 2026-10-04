"""
Top-level entry: imports the split modules, declares the reset
routine, and pins the SNES cartridge header (`$00:FFB0`) and vector
table (`$00:FFE0`).

Layout (one concern per module, all share `layout.s`):
  * `layout.s`     — pools, WRAM map, named constants.
  * `data.s`       — font tiles, greeting string (bank 1).
  * `ppu_tools.s`  — `set_palette_color`, `upload_font`,
                     `clear_tilemap_buffer`.
  * `draw_string.s`— tilemap stamper.
  * `nmi.s`        — `nmi_handler` (flushes both shadows on dirty
                     flags), `brk_handler` (STP).

Boot path: reset configures PPU, primes WRAM shadows (palette +
tilemap + glyphs), enables NMI + screen, idles. NMI flushes whichever
shadow the dirty flag advertises.
"""


.import "@std/snes/ppu"
.import "@std/snes/cpu"
.import "@std/snes/dma"
.import "@std/snes/header"
.import "preamble"
.import "data"
.import "ppu_tools"
.import "draw_string"
.import "nmi"
.import "engine"

.alloc reset in client {
"""
Cold-boot reset: native mode, kill NMI/DMA, init PPU, prime
    shadows, enable NMI + screen, idle. NMI handles DMA flushes.
"""


    sei
    clc
    xce  ; -> native mode (M=1, X=1)

    rep #0x30
    .a16
    .i16
    ldx #0x01FF
    txs
    lda #0x0000
    tcd  ; DP = 0

    sep #0x20
    .a8

    lda #STRINGS_BANK
    pha
    plb  ; DB = STRINGS_BANK

    lda #0x80
    sta.l screen.INIDISP  ; force-blank during setup

    lda #0x00
    sta.l cpu_regs.NMITIMEN
    sta.l cpu_regs.MDMAEN
    sta.l cpu_regs.HDMAEN

    sta.l screen.BGMODE  ; mode 0, 8x8 tiles
    sta.l screen.BG1SC  ; tilemap base = $0000 (word)
    lda #0x01
    sta.l screen.BG12NBA  ; BG1 char base = $1000 (word)

; Seed palette: index 3 = white (text fg). Index 0, 1, 2 black.
    rep #0x30
    .a16
    .i16
    ldx #0x0000
    lda #COLOR_BLACK
    jsr.l set_palette_color_l
    ldx #0x0001
    lda #COLOR_BLACK
    jsr.l set_palette_color_l
    ldx #0x0002
    lda #COLOR_BLACK
    jsr.l set_palette_color_l
    ldx #0x0003
    lda #COLOR_WHITE
    jsr.l set_palette_color_l
    sep #0x20
    .a8

    jsr.l upload_font_l
    jsr.l clear_tilemap_buffer_l

    rep #0x10
    .i16
    ldy.w #hello_string
    ldx.w #( 1 * 2 ) + ( 13 * 0x40 )
    jsr.l draw_string_l

    lda #1
    sta.l tilemap_dirty  ; NMI flushes this frame

    lda #0x01
    sta.l screen.TM  ; enable BG1 on main screen
    lda #0x80
    sta.l cpu_regs.NMITIMEN
    lda #0x0F
    sta.l screen.INIDISP  ; screen on, brightness 15

_idle:
    wai
    bra _idle
}

; --- Cartridge header (LoROM, $00:FFB0..$00:FFDF) ------------------------
; Checksum fields stay 0: no post-link checksum step yet.
.alloc snes_header at SNES_HEADER_BASE size SnesHeader.__size {
    .istruct SnesHeader {
        title = "A816 BASIC ROM       "
        map_mode = 0x20  ; LoROM, SlowROM
        rom_size = 0x08  ; 256 KiB
        destination = 0x01  ; North America
    }
}

; --- Vectors (LoROM, $00:FFE0..$00:FFFF) ----------------------------------
; Pinned at the hardware-mandated SNES vector address. `size` bounds the
; body so a stray field past the table fails the build instead of
; trampling the rest of the bank. Unset vectors stay 0.
.alloc vector_table at SNES_VECTORS_BASE size SnesVectors.__size {
    .istruct SnesVectors {
        native = {
            coprocessor = brk_handler
            break = brk_handler  ; -> STP
            abort = brk_handler
            nmi = nmi_handler
            irq = brk_handler
        }
        emulation = {
            coprocessor = brk_handler
            abort = brk_handler
            reset = reset
            irq_brk = brk_handler
        }
    }
}

; --- Pad ROM to 256KB (kintsuki refuses sub-power-of-two LoROM) -----------
.alloc rom_pad at 0x07FFFF size 0x01 {
    .db 0
}
