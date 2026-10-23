"""
SPC7110 coprocessor registers ($4800-$4842).

The SPC7110 (Far East of Eden Zero, Momotarou Dentetsu Happy) holds the
game's data ROM behind five units, one struct each:

| Struct          | Base    | Unit                                   |
|-----------------|---------|----------------------------------------|
| `SPC7110_DCU`   | `$4800` | decompression unit                     |
| `SPC7110_DATA`  | `$4810` | data port: direct data ROM reads       |
| `SPC7110_ALU`   | `$4820` | 16x16 multiply, 32/16 divide           |
| `SPC7110_MCU`   | `$4830` | data ROM banks, SRAM enable, ROM sizes |
| `EPSON_RTC`     | `$4840` | RTC-4513 clock (SPC7110-RTC boards)    |

    .import "@std/snes/spc7110"

        spc7110_dcu_start(0x080000, 3, 0)    ; directory at $08:0000, entry 3
        spc7110_dcu_wait()
    dcu := (SPC7110_DCU_BASE as SPC7110_DCU)
        lda dcu.DATA                     ; first decompressed byte

The board maps $D0-$FF linearly over the data ROM, which is what the
power-on banks 0, 1 and 2 in `SPC7110_MCU.BANK_D0`..`BANK_F0` show. A
game that reprograms them (Far East of Eden Zero sets 1, 2, 3 at reset)
describes its banks with `[map.N]` in its a816.toml.

The macros use long stores, so they work whatever DB holds.

Register layout follows ares (`sfc/coprocessor/spc7110`,
`sfc/coprocessor/epsonrtc`).
"""

SPC7110_DCU_BASE = 0x4800
SPC7110_DATA_BASE = 0x4810
SPC7110_ALU_BASE = 0x4820
SPC7110_MCU_BASE = 0x4830
EPSON_RTC_BASE = 0x4840

.struct SPC7110_DCU {
    """
    `DATA` ($4800): read the next decompressed byte; each read also
    decrements `COUNT`.
    `TABLE_L`/`TABLE_H`/`TABLE_B` ($4801-$4803): data ROM offset of the
    directory, four bytes per entry (mode, then big-endian address).
    `INDEX` ($4804): directory entry to load.
    `OFFSET_L`/`OFFSET_H` ($4805-$4806): bytes to skip when `MODE` bit 1
    is set. Writing `OFFSET_H` starts decompression.
    `STRIDE` ($4807): decoder steps per tile row when `MODE` bit 0 is set,
    else 1.
    `COUNT_L`/`COUNT_H` ($4809-$480A): counter decremented by `DATA` reads.
    `MODE` ($480B): bit 0 uses `STRIDE`, bit 1 uses `OFFSET`.
    `STATUS` ($480C): bit 7 set once decompressed data is ready.
    """
    byte DATA
    byte TABLE_L
    byte TABLE_H
    byte TABLE_B
    byte INDEX
    byte OFFSET_L
    byte OFFSET_H
    byte STRIDE
    byte UNUSED_4808
    byte COUNT_L
    byte COUNT_H
    byte MODE
    byte STATUS
}

.struct SPC7110_DATA {
    """
    `DATA` ($4810): read the data ROM byte at `OFFSET` (+ `ADJUST` when
    `MODE` bit 1 is set), then step.
    `OFFSET_L`/`OFFSET_H`/`OFFSET_B` ($4811-$4813): data ROM offset.
    Writing `OFFSET_B` latches the byte at it.
    `ADJUST_L`/`ADJUST_H` ($4814-$4815): second offset added to reads.
    `STRIDE_L`/`STRIDE_H` ($4816-$4817): step per `DATA` read when `MODE`
    bit 0 is set, else 1.
    `MODE` ($4818): bit 0 uses `STRIDE`, bit 1 adds `ADJUST`, bit 2
    signed `STRIDE`, bit 3 signed `ADJUST`, bit 4 steps `ADJUST` instead
    of `OFFSET`; bits 5-6 add `ADJUST` to `OFFSET` on a write to
    `ADJUST_L` (1), to `ADJUST_H` (2) or on a read of `ADJUST_STROBE` (3).
    `ADJUST_STROBE` ($481A): read to add `ADJUST` to `OFFSET` (mode 3).
    """
    byte DATA
    byte OFFSET_L
    byte OFFSET_H
    byte OFFSET_B
    byte ADJUST_L
    byte ADJUST_H
    byte STRIDE_L
    byte STRIDE_H
    byte MODE
    byte UNUSED_4819
    byte ADJUST_STROBE
}

.struct SPC7110_ALU {
    """
    `A0`..`A3` ($4820-$4823): 32-bit dividend; `A0`/`A1` are also the
    16-bit multiplicand.
    `MUL_L`/`MUL_H` ($4824-$4825): multiplier. Writing `MUL_H` multiplies.
    `DIV_L`/`DIV_H` ($4826-$4827): divisor. Writing `DIV_H` divides.
    `RESULT_0`..`RESULT_3` ($4828-$482B): product or quotient.
    `REMAINDER_L`/`REMAINDER_H` ($482C-$482D): remainder.
    `SIGNED` ($482E): bit 0 makes both operations signed.
    `STATUS` ($482F): bit 7 set while an operation runs.
    """
    byte A0
    byte A1
    byte A2
    byte A3
    byte MUL_L
    byte MUL_H
    byte DIV_L
    byte DIV_H
    byte RESULT_0
    byte RESULT_1
    byte RESULT_2
    byte RESULT_3
    byte REMAINDER_L
    byte REMAINDER_H
    byte SIGNED
    byte STATUS
}

.struct SPC7110_MCU {
    """
    `CONTROL` ($4830): bit 7 enables SRAM at $00-$3F,$80-$BF:6000-7FFF;
    bits 0-2 page the data ROM into $C0-$CF on boards without a program
    ROM there.
    `BANK_D0`/`BANK_E0`/`BANK_F0` ($4831-$4833): 1 MiB data ROM page seen
    at $D0-$DF, $E0-$EF and $F0-$FF. Power-on pages are 0, 1 and 2.
    `ROM_SIZE` ($4834): bits 0-1 give the data ROM size (1, 2, 4 or
    8 MiB); bit 2 maps the second program ROM megabyte at $D0-$DF.
    Below 3, a data port offset with bit 22 set reads 0: games probe the
    size that way, so an expanded data ROM must patch every write here
    (Far East of Eden Zero writes it three times).
    """
    byte CONTROL
    byte BANK_D0
    byte BANK_E0
    byte BANK_F0
    byte ROM_SIZE
}

.struct EPSON_RTC {
    """
    `CHIP_SELECT` ($4840): write 1 to select the clock; any other value
    resets its command state.
    `DATA` ($4841): 4-bit port. After selecting, write the command (0x03
    write, 0x0C read), then the register index, then read or write one
    nibble per access.
    `STATUS` ($4842): bit 7 set when `DATA` is ready for the next access.
    """
    byte CHIP_SELECT
    byte DATA
    byte STATUS
}

.macro spc7110_dcu_start(table, index, offset) {
    """
    Load directory entry `index` from the directory at data ROM offset
    `table` and start decompressing. `offset` is skipped only when
    `SPC7110_DCU.MODE` bit 1 is set. Long stores, so any DB works. A is 8-bit
    and clobbered.
    """
    lda.b #table & 0xFF
    sta.l SPC7110_DCU_BASE + SPC7110_DCU.TABLE_L
    lda.b #table >> 8 & 0xFF
    sta.l SPC7110_DCU_BASE + SPC7110_DCU.TABLE_H
    lda.b #table >> 16 & 0xFF
    sta.l SPC7110_DCU_BASE + SPC7110_DCU.TABLE_B
    lda.b #index
    sta.l SPC7110_DCU_BASE + SPC7110_DCU.INDEX
    lda.b #offset & 0xFF
    sta.l SPC7110_DCU_BASE + SPC7110_DCU.OFFSET_L
    lda.b #offset >> 8 & 0xFF
    sta.l SPC7110_DCU_BASE + SPC7110_DCU.OFFSET_H
}

.macro spc7110_dcu_wait() {
    """Spin until the decompression unit has data ready. A is 8-bit and clobbered."""
    {
_poll:
        lda.l SPC7110_DCU_BASE + SPC7110_DCU.STATUS
        bpl _poll
    }
}

.macro spc7110_data_seek(offset) {
    """Point the data port at data ROM `offset`. A is 8-bit and clobbered."""
    lda.b #offset & 0xFF
    sta.l SPC7110_DATA_BASE + SPC7110_DATA.OFFSET_L
    lda.b #offset >> 8 & 0xFF
    sta.l SPC7110_DATA_BASE + SPC7110_DATA.OFFSET_H
    lda.b #offset >> 16 & 0xFF
    sta.l SPC7110_DATA_BASE + SPC7110_DATA.OFFSET_B
}

.macro spc7110_banks(d0, e0, f0) {
    """Page data ROM megabytes into $D0, $E0 and $F0. A is 8-bit and clobbered."""
    lda.b #d0
    sta.l SPC7110_MCU_BASE + SPC7110_MCU.BANK_D0
    lda.b #e0
    sta.l SPC7110_MCU_BASE + SPC7110_MCU.BANK_E0
    lda.b #f0
    sta.l SPC7110_MCU_BASE + SPC7110_MCU.BANK_F0
}
