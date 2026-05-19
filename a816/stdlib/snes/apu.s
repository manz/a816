"""SNES APU I/O ports ($2140-$2143).

The CPU↔SPC700 communication channels. Each port is full-duplex: the
S-CPU writes a byte and the SPC700 reads it (and vice versa) on the
same address. The struct just lays out the four ports as bytes.

    .import "@std/snes/apu"

    apu := (APU_BASE as APU)
        lda apu.APUIO0          ; read SPC's port-0 reply
        sta apu.APUIO1          ; send byte on port 1
"""

APU_BASE = 0x2140

.struct APU {
    byte APUIO0
    byte APUIO1
    byte APUIO2
    byte APUIO3
}
