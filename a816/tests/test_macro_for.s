

.macro vwf_char(read_base_address, write_base_address) {
    write_base_address_bank = write_base_address >> 16
    write_base_address_low = write_base_address & 0xFFFF

    phb
    pha
    lda.b #write_base_address_bank
    pha
    plb
    pla

    sep #0x20
    #for k 0 16
    {
        lda read_base_address + k, x
        sta 0x4016
        nop
        nop
        lda 0x4018
        ora.w write_base_address_low + k, y
        sta.w write_base_address_low + k, y
        lsr
        sta.w write_base_address_low + k, y
    }
    lda read_base_address + 16, x
    clc
    adc 0x00
    sta 0x00
    rep #0x20
    plb

}
named.scope {
vwf_char(0xF00000, 0x7FC000)
}
