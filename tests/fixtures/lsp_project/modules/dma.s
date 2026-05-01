; DMA helper module. Declares an extern resolved by the linker.

.extern target_addr

"""Trigger a DMA transfer to VRAM."""
.macro dma_to_vram(src) {
    lda.l src
    sta.w 0x4300
}
