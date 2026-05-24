"""Bahamut-Lagoon-shape main: single-file with `.include` of
patch sources + top-level `*=` for header / hijacks.

No `.import` modules — the entire program is text-spliced via
`.include`. Sections plan + import-chain-dies doctrine target
this pattern as the canonical "ROM patch" shape that must keep
working without source refactoring."""

.include "header.i"

*=0x008000
boot_hook:
    jsr.l patch_a_entry
    rts

.include "patches_a.s"
.include "patches_b.s"

*=0x00FFC0
    .ascii "BAHAMUT TEST SHAPE   "
