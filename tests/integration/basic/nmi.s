.import "@std/snes/ppu"
.import "@std/snes/cpu"
.import "@std/snes/dma"
.import "preamble"
"""Interrupt thunks in bank 0: BRK→STP, NMI→engine_update_l.

Imports `engine` so per-module precompile (object mode) can resolve
`engine_update_l` to an extern. Main's direct-mode import inlines
both anyway.
"""

.import "engine"

.alloc brk_handler in client {
    """Stop the CPU on COP/BRK/ABORT/IRQ so kintsuki captures a trace."""
    stp
}

.alloc nmi_handler in client {
    """Vblank entry. Save regs, long-call engine, ack RDNMI, return."""
    .a8
    .i16
    pha
    phx
    phy
    phb
    phd
    jsr.l engine_update_l
    lda.l cpu_regs.RDNMI                    ; ack vblank-NMI
    pld
    plb
    ply
    plx
    pla
    rti
}
