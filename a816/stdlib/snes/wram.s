"""SNES WRAM access registers ($2180-$2183).

The serial WRAM port — useful when DMA isn't an option (e.g. inside
NMI / IRQ when the DMA controller is committed elsewhere). Set
`WMADDL/WMADDH/WMADDB` once, then read or write `WMDATA` in a tight
loop with the WRAM address auto-incrementing.

    .import "@std/snes/wram"

    wram := (WRAM_BASE as WRAM)
        lda #0x00
        sta wram.WMADDL
        sta wram.WMADDH
        sta wram.WMADDB
        ; subsequent loads from wram.WMDATA stream WRAM bytes
"""

WRAM_BASE = 0x2180

.struct WRAM {
    byte WMDATA
    byte WMADDL
    byte WMADDH
    byte WMADDB
}
