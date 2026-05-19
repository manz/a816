"""SNES PPU registers ($2100-$213F).

Comprehensive layout of the PPU register block exposed as `.struct PPU`.
Bind it to its base address to get typed field access:

    .import "@std/snes/ppu"

    ppu := (PPU_BASE as PPU)
        lda ppu.INIDISP
        sta ppu.OAMADDL

All fields are byte-wide because the SNES PPU register interface is
strictly 8-bit at $21xx; multi-byte registers (e.g. BG offsets) are
written as two consecutive byte stores.
"""

PPU_BASE = 0x2100

.struct PPU {
    byte INIDISP
    byte OBSEL
    byte OAMADDL
    byte OAMADDH
    byte OAMDATA
    byte BGMODE
    byte MOSAIC
    byte BG1SC
    byte BG2SC
    byte BG3SC
    byte BG4SC
    byte BG12NBA
    byte BG34NBA
    byte BG1HOFS
    byte BG1VOFS
    byte BG2HOFS
    byte BG2VOFS
    byte BG3HOFS
    byte BG3VOFS
    byte BG4HOFS
    byte BG4VOFS
    byte VMAIN
    byte VMADDL
    byte VMADDH
    byte VMDATAL
    byte VMDATAH
    byte M7SEL
    byte M7A
    byte M7B
    byte M7C
    byte M7D
    byte M7X
    byte M7Y
    byte CGADD
    byte CGDATA
    byte W12SEL
    byte W34SEL
    byte WOBJSEL
    byte WH0
    byte WH1
    byte WH2
    byte WH3
    byte WBGLOG
    byte WOBJLOG
    byte TM
    byte TS
    byte TMW
    byte TSW
    byte CGWSEL
    byte CGADSUB
    byte COLDATA
    byte SETINI
    byte MPYL
    byte MPYM
    byte MPYH
    byte SLHV
    byte OAMDATAREAD
    byte VMDATALREAD
    byte VMDATAHREAD
    byte CGDATAREAD
    byte OPHCT
    byte OPVCT
    byte STAT77
    byte STAT78
}
