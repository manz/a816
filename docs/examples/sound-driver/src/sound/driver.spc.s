"""Sound driver: key on the voices the S-CPU writes to port 0."""

.import "sound/dsp"

PORT0 = 0xf4
KON = 0x4c

.alloc driver at 0x0400 {
start:
"""Entry: the IPL jumps here once the upload is done."""
    mov a, #0
    mov PORT0, a
wait:
    mov a, PORT0
    beq wait
    mov y, a
    mov a, #KON
    call !dsp_write
    mov PORT0, y
    bra wait
}
