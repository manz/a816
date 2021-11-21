    php
    pha
    phx
    pea.w return_addr-1
    pea.w source & 0xFFFF
    pea.w  0x00FF & (source >> 16)
    pea.w vramptr
    pea.w count
    pea.w mode
    jmp.l dma_transfer_to_vram
return_addr:
    plx
    pla
    plp
    TAX            ; using math multiplication
    LDA.L vwf_shift_table,X
