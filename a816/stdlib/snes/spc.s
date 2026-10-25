"""
SPC700 boot upload, S-CPU side.

After reset the SPC700 runs its 64-byte IPL ROM, which waits on the
APU ports for a program to copy into ARAM and run. `spc_ipl_upload`
is that handshake. With the driver built by the same project
(`.embed`), its arguments are the driver's own symbols:

    .import "@std/snes/spc"

    .alloc sound_image in rom {
    driver_image:
        .embed "sound/driver"
    }

    .alloc sound_init in rom {
        sep #0x20
        rep #0x10
        spc_ipl_upload(driver_image, driver.__size, driver.__base, driver.start)
        rts
    }

IPL protocol:
  1. Wait for the ready signature: APUIO0 == $AA, APUIO1 == $BB.
  2. Open a block: APUIO2/3 = ARAM address, APUIO1 = nonzero, APUIO0 = $CC.
  3. Per byte: APUIO1 = data, APUIO0 = index; wait for the index echo.
  4. Run: APUIO2/3 = entry, APUIO1 = 0, APUIO0 = last index + 2 (never 0).
"""

.import "@std/snes/apu"

.macro spc_ipl_upload(src, size, aram, entry) {
    """
    Copy `size` bytes (1 to 65535) from `src`, a long address, to ARAM
    `aram` through the IPL ROM, then run them at `entry`. Returns once
    the SPC700 has jumped. Only right after reset: the `$CC` kick opens
    the IPL's first block. M8 / X16; A and X are clobbered. Long
    accesses, so any DB works.
    """
_wait_ready:
    lda.l APU_BASE + APU.APUIO0
    cmp.b #0xAA
    bne _wait_ready
    lda.l APU_BASE + APU.APUIO1
    cmp.b #0xBB
    bne _wait_ready
    lda.b #<aram
    sta.l APU_BASE + APU.APUIO2
    lda.b #>aram
    sta.l APU_BASE + APU.APUIO3
    lda.b #0x01
    sta.l APU_BASE + APU.APUIO1
    lda.b #0xCC
    sta.l APU_BASE + APU.APUIO0
_wait_open:
    cmp.l APU_BASE + APU.APUIO0
    bne _wait_open
    ldx.w #0
_next_byte:
    lda.l src, x
    sta.l APU_BASE + APU.APUIO1
    txa
    sta.l APU_BASE + APU.APUIO0
_wait_byte:
    cmp.l APU_BASE + APU.APUIO0
    bne _wait_byte
    inx
    cpx.w #size
    bne _next_byte
    lda.b #<entry
    sta.l APU_BASE + APU.APUIO2
    lda.b #>entry
    sta.l APU_BASE + APU.APUIO3
    lda.b #0
    sta.l APU_BASE + APU.APUIO1
    txa
    inc
    bne _run
    inc
_run:
    sta.l APU_BASE + APU.APUIO0
_wait_run:
    cmp.l APU_BASE + APU.APUIO0
    bne _wait_run
}
