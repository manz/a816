"""Placement corpus for the codegen-revision guard: pools, allocs and bss.

Covers the constructs whose emitted bytes or placement a816 has changed
before (empty bodies, end markers, bss reservations), so a change to how
they assemble shows up in the guard's digest.
"""

.map identifier=1 bank_range=0xc0, 0xfd addr_range=0x0000, 0xffff mask=0x10000 mirror_bank_range=0x40, 0x7d
.map identifier=3 bank_range=0x7e, 0x7f addr_range=0x0000, 0xffff mask=0x10000 writable=1

.pool code { range 0xc10000 0xc1ffff  strategy order }
.pool wram { bss  range 0x7e2000 0x7e2fff  strategy order }

.alloc empty in code {
}

.alloc table in code {
    .db 1, 2, 3
table_end:
}

.alloc routine in code {
    lda.l table
    ldx.w #table_end - table
    rts
}

.reserve state 0x10 in wram
.reserve pinned_state 4 at 0x7e2800 in wram
