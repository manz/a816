.macro dma_transfer_to_vram_call(source, vramptr, count, mode) {
    php
    pha
    phx
    pea.w return_addr-1
    pea.w source & 0xFFFF
    pea.w source >> 16
    pea.w vramptr
    pea.w count
    pea.w mode
    jmp.w dma_transfer_to_vram
    return_addr:
    plx
    pla
    plp
}
