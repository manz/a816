.scope Mymodule {
    v_idx = 0

__init__:
    self = 0x00
    phx

    ldx v_idx

    rts
}

JSR.w Mymodule.__init__
