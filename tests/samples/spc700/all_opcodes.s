.map identifier=1 bank_range=0x00, 0x00 addr_range=0x0000, 0xffff mask=0x10000
*= 0x0400
    or a, 0x12
    or a, !0x1234
    or a, (x)
    or a, [0x12+x]
    or a, #0x5A
    or 0x12, 0x34
    or a, 0x12+x
    or a, !0x1234+x
    or a, !0x1234+y
    or a, [0x12]+y
    or 0x12, #0x5A
    or (x), (y)
    and a, 0x12
    and a, !0x1234
    and a, (x)
    and a, [0x12+x]
    and a, #0x5A
    and 0x12, 0x34
    and a, 0x12+x
    and a, !0x1234+x
    and a, !0x1234+y
    and a, [0x12]+y
    and 0x12, #0x5A
    and (x), (y)
    eor a, 0x12
    eor a, !0x1234
    eor a, (x)
    eor a, [0x12+x]
    eor a, #0x5A
    eor 0x12, 0x34
    eor a, 0x12+x
    eor a, !0x1234+x
    eor a, !0x1234+y
    eor a, [0x12]+y
    eor 0x12, #0x5A
    eor (x), (y)
    cmp a, 0x12
    cmp a, !0x1234
    cmp a, (x)
    cmp a, [0x12+x]
    cmp a, #0x5A
    cmp 0x12, 0x34
    cmp a, 0x12+x
    cmp a, !0x1234+x
    cmp a, !0x1234+y
    cmp a, [0x12]+y
    cmp 0x12, #0x5A
    cmp (x), (y)
    cmp x, #0x5A
    cmp x, 0x12
    cmp x, !0x1234
    cmp y, #0x5A
    cmp y, 0x12
    cmp y, !0x1234
    adc a, 0x12
    adc a, !0x1234
    adc a, (x)
    adc a, [0x12+x]
    adc a, #0x5A
    adc 0x12, 0x34
    adc a, 0x12+x
    adc a, !0x1234+x
    adc a, !0x1234+y
    adc a, [0x12]+y
    adc 0x12, #0x5A
    adc (x), (y)
    sbc a, 0x12
    sbc a, !0x1234
    sbc a, (x)
    sbc a, [0x12+x]
    sbc a, #0x5A
    sbc 0x12, 0x34
    sbc a, 0x12+x
    sbc a, !0x1234+x
    sbc a, !0x1234+y
    sbc a, [0x12]+y
    sbc 0x12, #0x5A
    sbc (x), (y)
    asl 0x12
    asl !0x1234
    asl 0x12+x
    asl a
    rol 0x12
    rol !0x1234
    rol 0x12+x
    rol a
    lsr 0x12
    lsr !0x1234
    lsr 0x12+x
    lsr a
    ror 0x12
    ror !0x1234
    ror 0x12+x
    ror a
    dec 0x12
    dec !0x1234
    dec 0x12+x
    dec a
    dec x
    dec y
    inc 0x12
    inc !0x1234
    inc 0x12+x
    inc a
    inc x
    inc y
    decw 0x12
    incw 0x12
    cmpw ya, 0x12
    addw ya, 0x12
    subw ya, 0x12
    movw ya, 0x12
    movw 0x12, ya
    mov a, #0x5A
    mov a, 0x12
    mov a, !0x1234
    mov a, (x)
    mov a, [0x12+x]
    mov a, 0x12+x
    mov a, !0x1234+x
    mov a, !0x1234+y
    mov a, [0x12]+y
    mov a, (x)+
    mov x, #0x5A
    mov x, 0x12
    mov x, !0x1234
    mov x, 0x12+y
    mov y, #0x5A
    mov y, 0x12
    mov y, !0x1234
    mov y, 0x12+x
    mov 0x12, a
    mov !0x1234, a
    mov (x), a
    mov [0x12+x], a
    mov 0x12+x, a
    mov !0x1234+x, a
    mov !0x1234+y, a
    mov [0x12]+y, a
    mov (x)+, a
    mov 0x12, x
    mov !0x1234, x
    mov 0x12+y, x
    mov 0x12, y
    mov !0x1234, y
    mov 0x12+x, y
    mov a, x
    mov a, y
    mov x, a
    mov y, a
    mov x, sp
    mov sp, x
    mov 0x12, 0x34
    mov 0x12, #0x5A
    push a
    push x
    push y
    push psw
    pop a
    pop x
    pop y
    pop psw
back1:
    bra back1
back2:
    beq back2
back3:
    bne back3
back4:
    bcs back4
back5:
    bcc back5
back6:
    bvs back6
back7:
    bvc back7
back8:
    bmi back8
back9:
    bpl back9
back10:
    bbs 0x12.0, back10
back11:
    bbs 0x12.1, back11
back12:
    bbs 0x12.2, back12
back13:
    bbs 0x12.3, back13
back14:
    bbs 0x12.4, back14
back15:
    bbs 0x12.5, back15
back16:
    bbs 0x12.6, back16
back17:
    bbs 0x12.7, back17
back18:
    bbc 0x12.0, back18
back19:
    bbc 0x12.1, back19
back20:
    bbc 0x12.2, back20
back21:
    bbc 0x12.3, back21
back22:
    bbc 0x12.4, back22
back23:
    bbc 0x12.5, back23
back24:
    bbc 0x12.6, back24
back25:
    bbc 0x12.7, back25
back26:
    cbne 0x12, back26
back27:
    cbne 0x12+x, back27
back28:
    dbnz 0x12, back28
back29:
    dbnz y, back29
    jmp !0x1234
    jmp [!0x1234+x]
    call !0x1234
    pcall 0x34
    tcall 0
    tcall 1
    tcall 2
    tcall 3
    tcall 4
    tcall 5
    tcall 6
    tcall 7
    tcall 8
    tcall 9
    tcall 10
    tcall 11
    tcall 12
    tcall 13
    tcall 14
    tcall 15
    set1 0x12.0
    set1 0x12.1
    set1 0x12.2
    set1 0x12.3
    set1 0x12.4
    set1 0x12.5
    set1 0x12.6
    set1 0x12.7
    clr1 0x12.0
    clr1 0x12.1
    clr1 0x12.2
    clr1 0x12.3
    clr1 0x12.4
    clr1 0x12.5
    clr1 0x12.6
    clr1 0x12.7
    tset1 !0x1234
    tclr1 !0x1234
    and1 c, 0x1234.5
    and1 c, /0x1234.5
    or1 c, 0x1234.5
    or1 c, /0x1234.5
    eor1 c, 0x1234.5
    not1 0x1234.5
    mov1 c, 0x1234.5
    mov1 0x1234.5, c
    nop
    brk
    ret
    reti
    clrc
    setc
    notc
    clrv
    clrp
    setp
    ei
    di
    sleep
    stop
    daa a
    das a
    xcn a
    mul ya
    div ya, x
