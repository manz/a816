dma_transfer_to_vram:
{
    ; on the stack:
    ; return address
    ; source offset
    ; source bank
    ; vram pointer
    ; count
    ; mode
    arg_count = 5
    stack_ptr       = arg_count * 2 - 1

    source_offset   = stack_ptr
    source_bank     = stack_ptr - 2
    vram_pointer    = stack_ptr - 4
    count           = stack_ptr - 6
    dma_mode        = stack_ptr - 8
    channel         = 4

    rep #0x20
    sep #0x10
    ldx #0x80
    stx 0x2115

    lda.b source_offset, s
    sta.w 0x4302 +(channel << 4)

    sep #0x10
    lda.b source_bank, s
    sta.w 0x4304 + (channel << 4)
    rep #0x20

    lda.b vram_pointer, s
    sta.w 0x2116

    lda.b count, s
    sta.w 0x4305 + (channel << 4)

    lda.b dma_mode, s
    sta.w 0x4300 + (channel << 4)

    ldx.b #1 << channel
    stx 0x420B
    nop
    nop
    pla
    pla
    pla
    pla
    pla
    rts
}