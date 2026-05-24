"""Bank-1 patches in a separate include. References a label
defined in patches_a.s (cross-include label resolution)."""

.include "header.i"

*=0x018000
patch_b_entry:
    jsr.l patch_a_entry
    rts
