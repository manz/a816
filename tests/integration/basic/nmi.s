"""
Interrupt thunks in bank 0: BRK and the other traps stop the CPU, NMI
long-calls `engine_update_l`.
"""


.import "preamble"
.import "engine"

.alloc brk_handler in client {
"""Stop the CPU on COP/BRK/ABORT/IRQ so kintsuki captures a trace."""
    stp
}

.alloc nmi_handler in client {
"""Vblank entry: save registers, long-call the engine, ack RDNMI, return."""
    .a8
    .i16
    pha
    phx
    phy
    phb
    phd
    jsr.l engine_update_l
    lda.l cpu_regs.RDNMI  ; ack the vblank NMI
    pld
    plb
    ply
    plx
    pla
    rti
}
