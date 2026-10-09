"""Reclaim corpus for the codegen-revision guard: `.reclaim` and `.relocate`.

Both give a range back to a pool. The object carries it as its own pool
record, so a change to how reclaimed space reaches the linker shows up in
the guard's digest.
"""

.map identifier=1 bank_range=0xc0, 0xfd addr_range=0x0000, 0xffff mask=0x10000 mirror_bank_range=0x40, 0x7d

.pool slack { range 0xc18000 0xc18003  strategy order }
.reclaim slack 0xc18100 0xc1811f

.relocate moved 0xc18200 0xc1821f into slack {
    rts
}

.alloc big in slack {
    .db 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
}
