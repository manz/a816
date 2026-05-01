; DMA helper module. Defines target_addr used by main.s.

target_addr:
    rtl

"""Trigger a DMA transfer to VRAM."""
.macro dma_to_vram(src) {
    lda.l src
    sta.w 0x4300
}
