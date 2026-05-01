; Fixture entry point. Exercises labels, scopes, macros, includes, imports.
.include "constants.s"
.import "vwf"
.import "dma"

.extern target_addr

"""Entry point for the fixture project."""
main:
    sep #0x20
    lda.b #0x42
    sta.w DMA_REG
    vwf_init()
    jsr.l vwf_render
    jmp.l target_addr

.scope helpers {
    """Helper routines for main."""
    reset:
        rep #0x30
        rtl
}
