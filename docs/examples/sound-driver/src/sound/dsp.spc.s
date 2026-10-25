"""DSP registers through the $F2 / $F3 port pair."""

.alloc dsp at 0x0600 {
dsp_write:
"""Write Y to DSP register A."""
    mov 0xf2, a
    mov 0xf3, y
    ret
}
