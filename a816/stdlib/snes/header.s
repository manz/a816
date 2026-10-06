"""
SNES cartridge header ($FFB0-$FFDF) and interrupt vectors ($FFE0-$FFFF).

`SnesHeader` covers the extended header ($FFB0-$FFBF) and the standard
header ($FFC0-$FFDF), `SnesVectors` the native ($FFE0) and emulation
($FFF0) vector tables. Emit them as data with `.istruct`, pinned at the
bank-0 addresses (LoROM and HiROM both mirror them there):

    .import "@std/snes/header"

    .alloc snes_header at SNES_HEADER_BASE size SnesHeader.__size {
        .istruct SnesHeader {
            title = "MY GAME              "  ; 21 bytes, space padded
            map_mode = 0x20                  ; LoROM, SlowROM
            rom_size = 0x08                  ; 2^8 KiB = 256 KiB
            destination = 0x01               ; North America
        }
    }

    .alloc vectors at SNES_VECTORS_BASE size SnesVectors.__size {
        .istruct SnesVectors {
            native = { nmi = nmi_handler, irq = irq_handler }
            emulation = { reset = reset }
        }
    }

SnesHeader fields:

    $FFB0  maker_code[2]          ASCII (extended header)
    $FFB2  game_code[4]           ASCII
    $FFB6  reserved[6]            zero
    $FFBC  expansion_flash_size
    $FFBD  expansion_ram_size
    $FFBE  special_version
    $FFBF  cartridge_subtype
    $FFC0  title[21]              ASCII, space padded
    $FFD5  map_mode               0x20 LoROM, 0x21 HiROM, +0x10 FastROM
    $FFD6  cartridge_type         ROM / RAM / battery / coprocessor
    $FFD7  rom_size               log2(size in KiB)
    $FFD8  sram_size              log2(size in KiB), 0 = none
    $FFD9  destination            region code
    $FFDA  old_maker_code         0x33 = extended header present
    $FFDB  version                mask ROM version
    $FFDC  checksum_complement    checksum ^ 0xFFFF
    $FFDE  checksum

The extended header fields only count when `old_maker_code` is 0x33.
The checksum fields are left for a post-link step to fill in.
"""

SNES_HEADER_BASE = 0x00FFB0
SNES_VECTORS_BASE = 0x00FFE0

.struct SnesHeader {
    byte[2] maker_code
    byte[4] game_code
    byte[6] reserved
    byte expansion_flash_size
    byte expansion_ram_size
    byte special_version
    byte cartridge_subtype
    byte[21] title
    byte map_mode
    byte cartridge_type
    byte rom_size
    byte sram_size
    byte destination
    byte old_maker_code
    byte version
    word checksum_complement
    word checksum
}

"""Native-mode vectors, $FFE0-$FFEF."""
.struct SnesNativeVectors {
    byte[4] reserved
    word coprocessor
    word break
    word abort
    word nmi
    word unused
    word irq
}

"""Emulation-mode vectors, $FFF0-$FFFF; `reset` is the boot entry point."""
.struct SnesEmulationVectors {
    byte[4] reserved
    word coprocessor
    word unused
    word abort
    word nmi
    word reset
    word irq_brk
}

.struct SnesVectors {
    SnesNativeVectors native
    SnesEmulationVectors emulation
}
