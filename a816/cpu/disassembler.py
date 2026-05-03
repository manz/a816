"""
65c816 Disassembler for SNES ROMs.

Provides instruction decoding with support for all addressing modes.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class AddrMode(Enum):
    """Addressing modes for 65c816."""

    IMPLIED = "imp"  # No operand
    IMMEDIATE_8 = "imm8"  # #$XX
    IMMEDIATE_16 = "imm16"  # #$XXXX
    IMMEDIATE_M = "immM"  # #$XX or #$XXXX depending on M flag
    IMMEDIATE_X = "immX"  # #$XX or #$XXXX depending on X flag
    DIRECT = "dp"  # $XX
    DIRECT_X = "dp,x"  # $XX,X
    DIRECT_Y = "dp,y"  # $XX,Y
    DIRECT_IND = "(dp)"  # ($XX)
    DIRECT_IND_X = "(dp,x)"  # ($XX,X)
    DIRECT_IND_Y = "(dp),y"  # ($XX),Y
    DIRECT_IND_LONG = "[dp]"  # [$XX]
    DIRECT_IND_LONG_Y = "[dp],y"  # [$XX],Y
    ABSOLUTE = "abs"  # $XXXX
    ABSOLUTE_X = "abs,x"  # $XXXX,X
    ABSOLUTE_Y = "abs,y"  # $XXXX,Y
    ABSOLUTE_LONG = "long"  # $XXXXXX
    ABSOLUTE_LONG_X = "long,x"  # $XXXXXX,X
    ABSOLUTE_IND = "(abs)"  # ($XXXX)
    ABSOLUTE_IND_X = "(abs,x)"  # ($XXXX,X)
    ABSOLUTE_IND_LONG = "[abs]"  # [$XXXX]
    STACK_REL = "sr,s"  # $XX,S
    STACK_REL_IND_Y = "(sr,s),y"  # ($XX,S),Y
    RELATIVE = "rel"  # Relative branch (8-bit)
    RELATIVE_LONG = "rell"  # Relative branch (16-bit)
    BLOCK_MOVE = "blk"  # Block move (2 bytes)


@dataclass
class Instruction:
    """Decoded instruction."""

    address: int  # SNES logical address
    opcode: int  # Opcode byte
    mnemonic: str  # Instruction mnemonic
    mode: AddrMode  # Addressing mode
    operand_bytes: bytes  # Raw operand bytes
    operand_value: int  # Decoded operand value
    length: int  # Total instruction length

    def relative_target(self) -> int:
        """Compute the 24-bit absolute target for a relative branch.

        Preserves the current bank because RELATIVE/RELATIVE_LONG cannot
        cross banks; the offset wraps inside the bank-local 16-bit space.
        """
        val = self.operand_value
        if self.mode == AddrMode.RELATIVE:
            offset = val if val < 0x80 else val - 0x100
        else:
            offset = val if val < 0x8000 else val - 0x10000
        bank = self.address & 0xFF0000
        target = (self.address + self.length + offset) & 0xFFFF
        return bank | target

    def format_operand(
        self,
        m_flag: bool = True,
        x_flag: bool = True,
        use_a816_syntax: bool = False,
        label_map: dict[int, str] | None = None,
    ) -> str:
        """Format operand for the addressing mode.

        m_flag/x_flag select 8- vs 16-bit width for IMMEDIATE_M / IMMEDIATE_X.
        use_a816_syntax: 0x prefix instead of $.
        label_map: address -> label substitution for branch / jump targets.
        """
        val = self.operand_value

        def hex_val(v: int, width: int) -> str:
            return (f"0x{v:0{width}X}") if use_a816_syntax else (f"${v:0{width}X}")

        # Static templates: (width, format_string).
        templates: dict[AddrMode, tuple[int, str]] = {
            AddrMode.IMMEDIATE_8: (2, "#{}"),
            AddrMode.IMMEDIATE_16: (4, "#{}"),
            AddrMode.DIRECT: (2, "{}"),
            AddrMode.DIRECT_X: (2, "{},x"),
            AddrMode.DIRECT_Y: (2, "{},y"),
            AddrMode.DIRECT_IND: (2, "({})"),
            AddrMode.DIRECT_IND_X: (2, "({},x)"),
            AddrMode.DIRECT_IND_Y: (2, "({}),y"),
            AddrMode.DIRECT_IND_LONG: (2, "[{}]"),
            AddrMode.DIRECT_IND_LONG_Y: (2, "[{}],y"),
            AddrMode.ABSOLUTE: (4, "{}"),
            AddrMode.ABSOLUTE_X: (4, "{},x"),
            AddrMode.ABSOLUTE_Y: (4, "{},y"),
            AddrMode.ABSOLUTE_LONG: (6, "{}"),
            AddrMode.ABSOLUTE_LONG_X: (6, "{},x"),
            AddrMode.ABSOLUTE_IND: (4, "({})"),
            AddrMode.ABSOLUTE_IND_X: (4, "({},x)"),
            AddrMode.ABSOLUTE_IND_LONG: (4, "[{}]"),
            AddrMode.STACK_REL: (2, "{},s"),
            AddrMode.STACK_REL_IND_Y: (2, "({},s),y"),
        }

        return self._dispatch_operand_format(val, templates, hex_val, m_flag, x_flag, use_a816_syntax, label_map)

    def _format_relative_target(self, hex_val: Callable[[int, int], str], label_map: dict[int, str] | None) -> str:
        target = self.relative_target()
        if label_map is not None and target in label_map:
            return label_map[target]
        return hex_val(target, 6)

    def _format_jump_target(
        self, val: int, width: int, hex_val: Callable[[int, int], str], label_map: dict[int, str] | None
    ) -> str:
        """For absolute jumps/calls, swap in a label when the target matches."""
        if label_map is None:
            return hex_val(val, width)
        if width >= 6:
            target = val & 0xFFFFFF
        else:
            target = (self.address & 0xFF0000) | (val & 0xFFFF)
        if target in label_map:
            return label_map[target]
        return hex_val(val, width)

    def _dispatch_operand_format(
        self,
        val: int,
        templates: dict[AddrMode, tuple[int, str]],
        hex_val: Callable[[int, int], str],
        m_flag: bool,
        x_flag: bool,
        use_a816_syntax: bool,
        label_map: dict[int, str] | None = None,
    ) -> str:
        if self.mode == AddrMode.IMPLIED:
            return ""
        # Absolute jumps / calls — use label substitution when known.
        if self.mnemonic in ("jmp", "jsr", "jsl") and self.mode in (
            AddrMode.ABSOLUTE,
            AddrMode.ABSOLUTE_LONG,
        ):
            width = 6 if self.mode == AddrMode.ABSOLUTE_LONG else 4
            return self._format_jump_target(val, width, hex_val, label_map)
        if self.mode in templates:
            width, fmt = templates[self.mode]
            return fmt.format(hex_val(val, width))
        if self.mode == AddrMode.IMMEDIATE_M:
            return f"#{hex_val(val, 2 if m_flag else 4)}"
        if self.mode == AddrMode.IMMEDIATE_X:
            return f"#{hex_val(val, 2 if x_flag else 4)}"
        if self.mode in (AddrMode.RELATIVE, AddrMode.RELATIVE_LONG):
            return self._format_relative_target(hex_val, label_map)
        if self.mode == AddrMode.BLOCK_MOVE:
            return f"{hex_val(val & 0xFF, 2)},{hex_val((val >> 8) & 0xFF, 2)}"
        return (f"0x{val:X}") if use_a816_syntax else (f"${val:X}")

    def get_size_hint(self) -> str:
        """Get the size hint suffix for a816 syntax (.b, .w, .l).

        Minimal-suffix policy aimed at matching idiomatic a816 source:
        - Bare `lda #imm` for IMMEDIATE_M/IMMEDIATE_X 8-bit, IMMEDIATE_8.
        - `.w` only when the operand is a 16-bit immediate (forces width).
        - Bare absolute / direct (no `.w` / `.b` clutter).
        - `.l` retained for ABSOLUTE_LONG family (a816 needs it to pick jsl).
        - Block-move / relative / implied: no suffix.
        """
        mode = self.mode

        # 16-bit immediate forces `.w` so a816 picks the right opcode width.
        if mode in (AddrMode.IMMEDIATE_M, AddrMode.IMMEDIATE_X):
            return ".w" if len(self.operand_bytes) == 2 else ""
        if mode == AddrMode.IMMEDIATE_16:
            return ".w"

        if mode in (AddrMode.ABSOLUTE_LONG, AddrMode.ABSOLUTE_LONG_X):
            return ".l"

        return ""

    def format_a816(self, label_map: dict[int, str] | None = None) -> str:
        """Format instruction in a816-compatible syntax.

        label_map: optional address -> label dict. When provided, branch
        and jump targets that match are rendered as labels.
        """
        operand = self.format_operand(use_a816_syntax=True, label_map=label_map)
        size_hint = self.get_size_hint()

        if operand:
            return f"{self.mnemonic}{size_hint} {operand}"
        return self.mnemonic

    def __str__(self) -> str:
        operand = self.format_operand()
        if operand:
            return f"{self.mnemonic} {operand}"
        return self.mnemonic


# Opcode table: opcode -> (mnemonic, addressing_mode, operand_size)
# operand_size: 0=none, 1=byte, 2=word, 3=long, -1=M-dependent, -2=X-dependent
OPCODE_TABLE: dict[int, tuple[str, AddrMode, int]] = {
    # ADC
    0x69: ("adc", AddrMode.IMMEDIATE_M, -1),
    0x65: ("adc", AddrMode.DIRECT, 1),
    0x75: ("adc", AddrMode.DIRECT_X, 1),
    0x72: ("adc", AddrMode.DIRECT_IND, 1),
    0x61: ("adc", AddrMode.DIRECT_IND_X, 1),
    0x71: ("adc", AddrMode.DIRECT_IND_Y, 1),
    0x67: ("adc", AddrMode.DIRECT_IND_LONG, 1),
    0x77: ("adc", AddrMode.DIRECT_IND_LONG_Y, 1),
    0x6D: ("adc", AddrMode.ABSOLUTE, 2),
    0x7D: ("adc", AddrMode.ABSOLUTE_X, 2),
    0x79: ("adc", AddrMode.ABSOLUTE_Y, 2),
    0x6F: ("adc", AddrMode.ABSOLUTE_LONG, 3),
    0x7F: ("adc", AddrMode.ABSOLUTE_LONG_X, 3),
    0x63: ("adc", AddrMode.STACK_REL, 1),
    0x73: ("adc", AddrMode.STACK_REL_IND_Y, 1),
    # AND
    0x29: ("and", AddrMode.IMMEDIATE_M, -1),
    0x25: ("and", AddrMode.DIRECT, 1),
    0x35: ("and", AddrMode.DIRECT_X, 1),
    0x32: ("and", AddrMode.DIRECT_IND, 1),
    0x21: ("and", AddrMode.DIRECT_IND_X, 1),
    0x31: ("and", AddrMode.DIRECT_IND_Y, 1),
    0x27: ("and", AddrMode.DIRECT_IND_LONG, 1),
    0x37: ("and", AddrMode.DIRECT_IND_LONG_Y, 1),
    0x2D: ("and", AddrMode.ABSOLUTE, 2),
    0x3D: ("and", AddrMode.ABSOLUTE_X, 2),
    0x39: ("and", AddrMode.ABSOLUTE_Y, 2),
    0x2F: ("and", AddrMode.ABSOLUTE_LONG, 3),
    0x3F: ("and", AddrMode.ABSOLUTE_LONG_X, 3),
    0x23: ("and", AddrMode.STACK_REL, 1),
    0x33: ("and", AddrMode.STACK_REL_IND_Y, 1),
    # ASL
    0x0A: ("asl", AddrMode.IMPLIED, 0),
    0x06: ("asl", AddrMode.DIRECT, 1),
    0x16: ("asl", AddrMode.DIRECT_X, 1),
    0x0E: ("asl", AddrMode.ABSOLUTE, 2),
    0x1E: ("asl", AddrMode.ABSOLUTE_X, 2),
    # Branch instructions
    0x90: ("bcc", AddrMode.RELATIVE, 1),
    0xB0: ("bcs", AddrMode.RELATIVE, 1),
    0xF0: ("beq", AddrMode.RELATIVE, 1),
    0x30: ("bmi", AddrMode.RELATIVE, 1),
    0xD0: ("bne", AddrMode.RELATIVE, 1),
    0x10: ("bpl", AddrMode.RELATIVE, 1),
    0x80: ("bra", AddrMode.RELATIVE, 1),
    0x82: ("brl", AddrMode.RELATIVE_LONG, 2),
    0x50: ("bvc", AddrMode.RELATIVE, 1),
    0x70: ("bvs", AddrMode.RELATIVE, 1),
    # BIT
    0x89: ("bit", AddrMode.IMMEDIATE_M, -1),
    0x24: ("bit", AddrMode.DIRECT, 1),
    0x34: ("bit", AddrMode.DIRECT_X, 1),
    0x2C: ("bit", AddrMode.ABSOLUTE, 2),
    0x3C: ("bit", AddrMode.ABSOLUTE_X, 2),
    # BRK, COP
    0x00: ("brk", AddrMode.IMMEDIATE_8, 1),
    0x02: ("cop", AddrMode.IMMEDIATE_8, 1),
    # Clear/Set flags
    0x18: ("clc", AddrMode.IMPLIED, 0),
    0xD8: ("cld", AddrMode.IMPLIED, 0),
    0x58: ("cli", AddrMode.IMPLIED, 0),
    0xB8: ("clv", AddrMode.IMPLIED, 0),
    0x38: ("sec", AddrMode.IMPLIED, 0),
    0xF8: ("sed", AddrMode.IMPLIED, 0),
    0x78: ("sei", AddrMode.IMPLIED, 0),
    # CMP
    0xC9: ("cmp", AddrMode.IMMEDIATE_M, -1),
    0xC5: ("cmp", AddrMode.DIRECT, 1),
    0xD5: ("cmp", AddrMode.DIRECT_X, 1),
    0xD2: ("cmp", AddrMode.DIRECT_IND, 1),
    0xC1: ("cmp", AddrMode.DIRECT_IND_X, 1),
    0xD1: ("cmp", AddrMode.DIRECT_IND_Y, 1),
    0xC7: ("cmp", AddrMode.DIRECT_IND_LONG, 1),
    0xD7: ("cmp", AddrMode.DIRECT_IND_LONG_Y, 1),
    0xCD: ("cmp", AddrMode.ABSOLUTE, 2),
    0xDD: ("cmp", AddrMode.ABSOLUTE_X, 2),
    0xD9: ("cmp", AddrMode.ABSOLUTE_Y, 2),
    0xCF: ("cmp", AddrMode.ABSOLUTE_LONG, 3),
    0xDF: ("cmp", AddrMode.ABSOLUTE_LONG_X, 3),
    0xC3: ("cmp", AddrMode.STACK_REL, 1),
    0xD3: ("cmp", AddrMode.STACK_REL_IND_Y, 1),
    # CPX
    0xE0: ("cpx", AddrMode.IMMEDIATE_X, -2),
    0xE4: ("cpx", AddrMode.DIRECT, 1),
    0xEC: ("cpx", AddrMode.ABSOLUTE, 2),
    # CPY
    0xC0: ("cpy", AddrMode.IMMEDIATE_X, -2),
    0xC4: ("cpy", AddrMode.DIRECT, 1),
    0xCC: ("cpy", AddrMode.ABSOLUTE, 2),
    # DEC
    0x3A: ("dec", AddrMode.IMPLIED, 0),
    0xC6: ("dec", AddrMode.DIRECT, 1),
    0xD6: ("dec", AddrMode.DIRECT_X, 1),
    0xCE: ("dec", AddrMode.ABSOLUTE, 2),
    0xDE: ("dec", AddrMode.ABSOLUTE_X, 2),
    # DEX, DEY
    0xCA: ("dex", AddrMode.IMPLIED, 0),
    0x88: ("dey", AddrMode.IMPLIED, 0),
    # EOR
    0x49: ("eor", AddrMode.IMMEDIATE_M, -1),
    0x45: ("eor", AddrMode.DIRECT, 1),
    0x55: ("eor", AddrMode.DIRECT_X, 1),
    0x52: ("eor", AddrMode.DIRECT_IND, 1),
    0x41: ("eor", AddrMode.DIRECT_IND_X, 1),
    0x51: ("eor", AddrMode.DIRECT_IND_Y, 1),
    0x47: ("eor", AddrMode.DIRECT_IND_LONG, 1),
    0x57: ("eor", AddrMode.DIRECT_IND_LONG_Y, 1),
    0x4D: ("eor", AddrMode.ABSOLUTE, 2),
    0x5D: ("eor", AddrMode.ABSOLUTE_X, 2),
    0x59: ("eor", AddrMode.ABSOLUTE_Y, 2),
    0x4F: ("eor", AddrMode.ABSOLUTE_LONG, 3),
    0x5F: ("eor", AddrMode.ABSOLUTE_LONG_X, 3),
    0x43: ("eor", AddrMode.STACK_REL, 1),
    0x53: ("eor", AddrMode.STACK_REL_IND_Y, 1),
    # INC
    0x1A: ("inc", AddrMode.IMPLIED, 0),
    0xE6: ("inc", AddrMode.DIRECT, 1),
    0xF6: ("inc", AddrMode.DIRECT_X, 1),
    0xEE: ("inc", AddrMode.ABSOLUTE, 2),
    0xFE: ("inc", AddrMode.ABSOLUTE_X, 2),
    # INX, INY
    0xE8: ("inx", AddrMode.IMPLIED, 0),
    0xC8: ("iny", AddrMode.IMPLIED, 0),
    # JMP
    0x4C: ("jmp", AddrMode.ABSOLUTE, 2),
    0x5C: ("jmp", AddrMode.ABSOLUTE_LONG, 3),
    0x6C: ("jmp", AddrMode.ABSOLUTE_IND, 2),
    0x7C: ("jmp", AddrMode.ABSOLUTE_IND_X, 2),
    0xDC: ("jmp", AddrMode.ABSOLUTE_IND_LONG, 2),
    # JSR, JSL
    0x20: ("jsr", AddrMode.ABSOLUTE, 2),
    0x22: ("jsl", AddrMode.ABSOLUTE_LONG, 3),
    0xFC: ("jsr", AddrMode.ABSOLUTE_IND_X, 2),
    # LDA
    0xA9: ("lda", AddrMode.IMMEDIATE_M, -1),
    0xA5: ("lda", AddrMode.DIRECT, 1),
    0xB5: ("lda", AddrMode.DIRECT_X, 1),
    0xB2: ("lda", AddrMode.DIRECT_IND, 1),
    0xA1: ("lda", AddrMode.DIRECT_IND_X, 1),
    0xB1: ("lda", AddrMode.DIRECT_IND_Y, 1),
    0xA7: ("lda", AddrMode.DIRECT_IND_LONG, 1),
    0xB7: ("lda", AddrMode.DIRECT_IND_LONG_Y, 1),
    0xAD: ("lda", AddrMode.ABSOLUTE, 2),
    0xBD: ("lda", AddrMode.ABSOLUTE_X, 2),
    0xB9: ("lda", AddrMode.ABSOLUTE_Y, 2),
    0xAF: ("lda", AddrMode.ABSOLUTE_LONG, 3),
    0xBF: ("lda", AddrMode.ABSOLUTE_LONG_X, 3),
    0xA3: ("lda", AddrMode.STACK_REL, 1),
    0xB3: ("lda", AddrMode.STACK_REL_IND_Y, 1),
    # LDX
    0xA2: ("ldx", AddrMode.IMMEDIATE_X, -2),
    0xA6: ("ldx", AddrMode.DIRECT, 1),
    0xB6: ("ldx", AddrMode.DIRECT_Y, 1),
    0xAE: ("ldx", AddrMode.ABSOLUTE, 2),
    0xBE: ("ldx", AddrMode.ABSOLUTE_Y, 2),
    # LDY
    0xA0: ("ldy", AddrMode.IMMEDIATE_X, -2),
    0xA4: ("ldy", AddrMode.DIRECT, 1),
    0xB4: ("ldy", AddrMode.DIRECT_X, 1),
    0xAC: ("ldy", AddrMode.ABSOLUTE, 2),
    0xBC: ("ldy", AddrMode.ABSOLUTE_X, 2),
    # LSR
    0x4A: ("lsr", AddrMode.IMPLIED, 0),
    0x46: ("lsr", AddrMode.DIRECT, 1),
    0x56: ("lsr", AddrMode.DIRECT_X, 1),
    0x4E: ("lsr", AddrMode.ABSOLUTE, 2),
    0x5E: ("lsr", AddrMode.ABSOLUTE_X, 2),
    # Block move
    0x54: ("mvn", AddrMode.BLOCK_MOVE, 2),
    0x44: ("mvp", AddrMode.BLOCK_MOVE, 2),
    # NOP
    0xEA: ("nop", AddrMode.IMPLIED, 0),
    # ORA
    0x09: ("ora", AddrMode.IMMEDIATE_M, -1),
    0x05: ("ora", AddrMode.DIRECT, 1),
    0x15: ("ora", AddrMode.DIRECT_X, 1),
    0x12: ("ora", AddrMode.DIRECT_IND, 1),
    0x01: ("ora", AddrMode.DIRECT_IND_X, 1),
    0x11: ("ora", AddrMode.DIRECT_IND_Y, 1),
    0x07: ("ora", AddrMode.DIRECT_IND_LONG, 1),
    0x17: ("ora", AddrMode.DIRECT_IND_LONG_Y, 1),
    0x0D: ("ora", AddrMode.ABSOLUTE, 2),
    0x1D: ("ora", AddrMode.ABSOLUTE_X, 2),
    0x19: ("ora", AddrMode.ABSOLUTE_Y, 2),
    0x0F: ("ora", AddrMode.ABSOLUTE_LONG, 3),
    0x1F: ("ora", AddrMode.ABSOLUTE_LONG_X, 3),
    0x03: ("ora", AddrMode.STACK_REL, 1),
    0x13: ("ora", AddrMode.STACK_REL_IND_Y, 1),
    # PEA, PEI, PER
    0xF4: ("pea", AddrMode.ABSOLUTE, 2),
    0xD4: ("pei", AddrMode.DIRECT_IND, 1),
    0x62: ("per", AddrMode.RELATIVE_LONG, 2),
    # Push/Pull
    0x48: ("pha", AddrMode.IMPLIED, 0),
    0x8B: ("phb", AddrMode.IMPLIED, 0),
    0x0B: ("phd", AddrMode.IMPLIED, 0),
    0x4B: ("phk", AddrMode.IMPLIED, 0),
    0x08: ("php", AddrMode.IMPLIED, 0),
    0xDA: ("phx", AddrMode.IMPLIED, 0),
    0x5A: ("phy", AddrMode.IMPLIED, 0),
    0x68: ("pla", AddrMode.IMPLIED, 0),
    0xAB: ("plb", AddrMode.IMPLIED, 0),
    0x2B: ("pld", AddrMode.IMPLIED, 0),
    0x28: ("plp", AddrMode.IMPLIED, 0),
    0xFA: ("plx", AddrMode.IMPLIED, 0),
    0x7A: ("ply", AddrMode.IMPLIED, 0),
    # REP, SEP
    0xC2: ("rep", AddrMode.IMMEDIATE_8, 1),
    0xE2: ("sep", AddrMode.IMMEDIATE_8, 1),
    # ROL
    0x2A: ("rol", AddrMode.IMPLIED, 0),
    0x26: ("rol", AddrMode.DIRECT, 1),
    0x36: ("rol", AddrMode.DIRECT_X, 1),
    0x2E: ("rol", AddrMode.ABSOLUTE, 2),
    0x3E: ("rol", AddrMode.ABSOLUTE_X, 2),
    # ROR
    0x6A: ("ror", AddrMode.IMPLIED, 0),
    0x66: ("ror", AddrMode.DIRECT, 1),
    0x76: ("ror", AddrMode.DIRECT_X, 1),
    0x6E: ("ror", AddrMode.ABSOLUTE, 2),
    0x7E: ("ror", AddrMode.ABSOLUTE_X, 2),
    # RTI, RTL, RTS
    0x40: ("rti", AddrMode.IMPLIED, 0),
    0x6B: ("rtl", AddrMode.IMPLIED, 0),
    0x60: ("rts", AddrMode.IMPLIED, 0),
    # SBC
    0xE9: ("sbc", AddrMode.IMMEDIATE_M, -1),
    0xE5: ("sbc", AddrMode.DIRECT, 1),
    0xF5: ("sbc", AddrMode.DIRECT_X, 1),
    0xF2: ("sbc", AddrMode.DIRECT_IND, 1),
    0xE1: ("sbc", AddrMode.DIRECT_IND_X, 1),
    0xF1: ("sbc", AddrMode.DIRECT_IND_Y, 1),
    0xE7: ("sbc", AddrMode.DIRECT_IND_LONG, 1),
    0xF7: ("sbc", AddrMode.DIRECT_IND_LONG_Y, 1),
    0xED: ("sbc", AddrMode.ABSOLUTE, 2),
    0xFD: ("sbc", AddrMode.ABSOLUTE_X, 2),
    0xF9: ("sbc", AddrMode.ABSOLUTE_Y, 2),
    0xEF: ("sbc", AddrMode.ABSOLUTE_LONG, 3),
    0xFF: ("sbc", AddrMode.ABSOLUTE_LONG_X, 3),
    0xE3: ("sbc", AddrMode.STACK_REL, 1),
    0xF3: ("sbc", AddrMode.STACK_REL_IND_Y, 1),
    # STA
    0x85: ("sta", AddrMode.DIRECT, 1),
    0x95: ("sta", AddrMode.DIRECT_X, 1),
    0x92: ("sta", AddrMode.DIRECT_IND, 1),
    0x81: ("sta", AddrMode.DIRECT_IND_X, 1),
    0x91: ("sta", AddrMode.DIRECT_IND_Y, 1),
    0x87: ("sta", AddrMode.DIRECT_IND_LONG, 1),
    0x97: ("sta", AddrMode.DIRECT_IND_LONG_Y, 1),
    0x8D: ("sta", AddrMode.ABSOLUTE, 2),
    0x9D: ("sta", AddrMode.ABSOLUTE_X, 2),
    0x99: ("sta", AddrMode.ABSOLUTE_Y, 2),
    0x8F: ("sta", AddrMode.ABSOLUTE_LONG, 3),
    0x9F: ("sta", AddrMode.ABSOLUTE_LONG_X, 3),
    0x83: ("sta", AddrMode.STACK_REL, 1),
    0x93: ("sta", AddrMode.STACK_REL_IND_Y, 1),
    # STP, WAI
    0xDB: ("stp", AddrMode.IMPLIED, 0),
    0xCB: ("wai", AddrMode.IMPLIED, 0),
    # STX
    0x86: ("stx", AddrMode.DIRECT, 1),
    0x96: ("stx", AddrMode.DIRECT_Y, 1),
    0x8E: ("stx", AddrMode.ABSOLUTE, 2),
    # STY
    0x84: ("sty", AddrMode.DIRECT, 1),
    0x94: ("sty", AddrMode.DIRECT_X, 1),
    0x8C: ("sty", AddrMode.ABSOLUTE, 2),
    # STZ
    0x64: ("stz", AddrMode.DIRECT, 1),
    0x74: ("stz", AddrMode.DIRECT_X, 1),
    0x9C: ("stz", AddrMode.ABSOLUTE, 2),
    0x9E: ("stz", AddrMode.ABSOLUTE_X, 2),
    # Transfers
    0xAA: ("tax", AddrMode.IMPLIED, 0),
    0xA8: ("tay", AddrMode.IMPLIED, 0),
    0x5B: ("tcd", AddrMode.IMPLIED, 0),
    0x1B: ("tcs", AddrMode.IMPLIED, 0),
    0x7B: ("tdc", AddrMode.IMPLIED, 0),
    0x3B: ("tsc", AddrMode.IMPLIED, 0),
    0xBA: ("tsx", AddrMode.IMPLIED, 0),
    0x8A: ("txa", AddrMode.IMPLIED, 0),
    0x9A: ("txs", AddrMode.IMPLIED, 0),
    0x9B: ("txy", AddrMode.IMPLIED, 0),
    0x98: ("tya", AddrMode.IMPLIED, 0),
    0xBB: ("tyx", AddrMode.IMPLIED, 0),
    # TRB, TSB
    0x14: ("trb", AddrMode.DIRECT, 1),
    0x1C: ("trb", AddrMode.ABSOLUTE, 2),
    0x04: ("tsb", AddrMode.DIRECT, 1),
    0x0C: ("tsb", AddrMode.ABSOLUTE, 2),
    # WDM (reserved)
    0x42: ("wdm", AddrMode.IMMEDIATE_8, 1),
    # XBA, XCE
    0xEB: ("xba", AddrMode.IMPLIED, 0),
    0xFB: ("xce", AddrMode.IMPLIED, 0),
}


class Disassembler:
    """65c816 disassembler."""

    def __init__(self, m_flag: bool = True, x_flag: bool = True):
        """
        Initialize disassembler.

        Args:
            m_flag: True if accumulator is 8-bit (M=1), False if 16-bit (M=0)
            x_flag: True if index registers are 8-bit (X=1), False if 16-bit (X=0)
        """
        self.m_flag = m_flag
        self.x_flag = x_flag

    def get_operand_size(self, base_size: int) -> int:
        """Get actual operand size based on processor flags."""
        if base_size == -1:  # M-dependent
            return 1 if self.m_flag else 2
        elif base_size == -2:  # X-dependent
            return 1 if self.x_flag else 2
        return base_size

    @staticmethod
    def _decode_operand(operand_bytes: bytes) -> int:
        return sum(b << (8 * i) for i, b in enumerate(operand_bytes))

    @staticmethod
    def _data_byte(address: int, opcode: int, raw: bytes) -> Instruction:
        return Instruction(
            address=address,
            opcode=opcode,
            mnemonic=".db",
            mode=AddrMode.IMMEDIATE_8,
            operand_bytes=raw,
            operand_value=opcode,
            length=1,
        )

    def _track_register_flags(self, mnemonic: str, operand_value: int) -> None:
        if mnemonic not in ("rep", "sep"):
            return
        new_state = mnemonic == "sep"
        if operand_value & 0x20:
            self.m_flag = new_state
        if operand_value & 0x10:
            self.x_flag = new_state

    def decode_instruction(self, data: bytes, address: int) -> Instruction | None:
        """Decode a single instruction. Returns None on empty input."""
        if not data:
            return None

        opcode = data[0]
        if opcode not in OPCODE_TABLE:
            return self._data_byte(address, opcode, bytes([opcode]))

        mnemonic, mode, base_size = OPCODE_TABLE[opcode]
        operand_size = self.get_operand_size(base_size)
        total_length = 1 + operand_size
        if len(data) < total_length:
            return self._data_byte(address, opcode, data)

        operand_bytes = data[1:total_length]
        operand_value = self._decode_operand(operand_bytes)
        self._track_register_flags(mnemonic, operand_value)

        return Instruction(
            address=address,
            opcode=opcode,
            mnemonic=mnemonic,
            mode=mode,
            operand_bytes=operand_bytes,
            operand_value=operand_value,
            length=total_length,
        )

    def disassemble(self, data: bytes, start_address: int, count: int | None = None) -> list[Instruction]:
        """
        Disassemble a sequence of bytes.

        Args:
            data: Bytes to disassemble
            start_address: SNES logical address of the first byte
            count: Maximum number of instructions to decode (None = all)

        Returns:
            List of decoded Instructions
        """
        instructions: list[Instruction] = []
        offset = 0
        address = start_address

        while offset < len(data):
            if count is not None and len(instructions) >= count:
                break

            remaining = data[offset:]
            inst = self.decode_instruction(remaining, address)

            if inst is None:
                break

            instructions.append(inst)
            offset += inst.length
            address += inst.length

        return instructions


_CONDITIONAL_BRANCHES = frozenset({"bcc", "bcs", "beq", "bmi", "bne", "bpl", "bvc", "bvs"})
_UNCONDITIONAL_BRANCHES = frozenset({"bra", "brl"})
_RETURNS = frozenset({"rts", "rtl", "rti"})
_UNCONDITIONAL_JUMPS = frozenset({"jmp", "jml"})


def _absolute_jump_target(inst: Instruction) -> int | None:
    """Static target of an absolute jmp / jsr / jsl. None if not statically resolvable."""
    if inst.mode == AddrMode.ABSOLUTE_LONG:
        return inst.operand_value & 0xFFFFFF
    if inst.mode == AddrMode.ABSOLUTE:
        return (inst.address & 0xFF0000) | (inst.operand_value & 0xFFFF)
    return None


def _enqueue_successors(
    inst: Instruction, m: bool, x: bool, follow_calls: bool, work: list[tuple[int, bool, bool]]
) -> bool:
    """Push CFG successors of `inst` onto `work`. Return True if the path
    terminates here (return / unconditional jump / unconditional branch).
    """
    if inst.mnemonic in _RETURNS:
        return True
    if inst.mnemonic in _UNCONDITIONAL_JUMPS:
        target = _absolute_jump_target(inst)
        if target is not None:
            work.append((target, m, x))
        return True
    if inst.mnemonic in _UNCONDITIONAL_BRANCHES:
        work.append((inst.relative_target(), m, x))
        return True
    if inst.mnemonic in _CONDITIONAL_BRANCHES:
        work.append((inst.relative_target(), m, x))
    if follow_calls and inst.mnemonic in ("jsr", "jsl"):
        target = _absolute_jump_target(inst)
        if target is not None:
            work.append((target, m, x))
    return False


def disassemble_function(
    entry: int,
    data_provider: Callable[[int, int], bytes],
    m_flag: bool = True,
    x_flag: bool = True,
    max_instructions: int = 4096,
    follow_calls: bool = False,
) -> list[Instruction]:
    """Walk a 65c816 function CFG starting at `entry`, returning every
    decoded instruction in sorted address order.

    Tracks M / X through `sep` / `rep`, enqueues both branches at
    conditional jumps, follows unconditional branches, stops a path at
    `rts` / `rtl` / `rti` or an unconditional jump. `data_provider(addr,
    length)` must return up to `length` bytes from SNES logical address
    `addr`. `follow_calls=True` enqueues `jsr` / `jsl` targets too.
    `max_instructions` caps runaway decodes on garbage past the body.
    """
    seen: set[tuple[int, bool, bool]] = set()
    output: dict[int, Instruction] = {}
    work: list[tuple[int, bool, bool]] = [(entry, m_flag, x_flag)]
    decoded = 0

    while work and decoded < max_instructions:
        addr, m, x = work.pop()
        if (addr, m, x) in seen:
            continue
        seen.add((addr, m, x))
        decoded += _walk_path(addr, m, x, data_provider, output, work, follow_calls, max_instructions - decoded)

    return [output[addr] for addr in sorted(output)]


def _walk_path(
    addr: int,
    m: bool,
    x: bool,
    data_provider: Callable[[int, int], bytes],
    output: dict[int, Instruction],
    work: list[tuple[int, bool, bool]],
    follow_calls: bool,
    budget: int,
) -> int:
    """Decode straight-line from `addr` until a terminator or the budget
    runs out. Returns the number of instructions decoded.
    """
    local = Disassembler(m_flag=m, x_flag=x)
    cur = addr
    decoded = 0
    while decoded < budget:
        chunk = data_provider(cur, 4)
        if not chunk:
            break
        inst = local.decode_instruction(chunk, cur)
        if inst is None:
            break
        output.setdefault(cur, inst)
        decoded += 1
        if _enqueue_successors(inst, local.m_flag, local.x_flag, follow_calls, work):
            break
        cur += inst.length
    return decoded


def _label_for(address: int) -> str:
    return f"_{(address >> 16) & 0xFF:02X}{address & 0xFFFF:04X}"


def collect_labels(instructions: list[Instruction]) -> dict[int, str]:
    """Build address -> label map for branch and jump targets.

    Only addresses targeted by branches or absolute/long jumps/calls get
    a label entry, so disassembly substitutes labels in operands and the
    block formatter emits label lines at in-range targets. Out-of-range
    targets still substitute in operands; the user supplies the label
    definition elsewhere when reassembling.
    """
    targets: set[int] = set()
    for inst in instructions:
        if inst.mode in (AddrMode.RELATIVE, AddrMode.RELATIVE_LONG):
            targets.add(inst.relative_target())
            continue
        if inst.mnemonic in ("jmp", "jsr", "jsl"):
            if inst.mode == AddrMode.ABSOLUTE_LONG:
                targets.add(inst.operand_value & 0xFFFFFF)
            elif inst.mode == AddrMode.ABSOLUTE:
                targets.add((inst.address & 0xFF0000) | (inst.operand_value & 0xFFFF))
    return {target: _label_for(target) for target in targets}


def format_disassembly(
    inst: Instruction,
    show_bytes: bool = True,
    a816_syntax: bool = False,
    label_map: dict[int, str] | None = None,
) -> str:
    """Format a single instruction for display.

    Args:
        inst: Decoded instruction.
        show_bytes: Whether to show raw bytes.
        a816_syntax: If True, output a816-compatible assembly syntax.
        label_map: Optional address -> label dict for branch / jump targets.
            When provided in a816 mode, no per-line synthetic label is
            emitted; callers should print the label on its own line where
            applicable (see format_disassembly_block).

    Returns:
        Formatted string.
    """
    bank = (inst.address >> 16) & 0xFF
    addr = inst.address & 0xFFFF

    if a816_syntax:
        asm_str = inst.format_a816(label_map=label_map)
        if label_map is None:
            addr_str = f"{_label_for(inst.address)}:"
            if show_bytes:
                all_bytes = bytes([inst.opcode]) + inst.operand_bytes
                bytes_str = " ".join(f"{b:02X}" for b in all_bytes)
                return f"{addr_str:14} {asm_str:24} ; {bytes_str}"
            return f"{addr_str:14} {asm_str}"
        # label_map mode: emit instruction flush-left; labels printed by block formatter.
        if show_bytes:
            all_bytes = bytes([inst.opcode]) + inst.operand_bytes
            bytes_str = " ".join(f"{b:02X}" for b in all_bytes)
            return f"{asm_str:32} ; ${inst.address & 0xFFFFFF:06X}: {bytes_str}"
        return asm_str

    addr_str = f"${bank:02X}:{addr:04X}"
    if show_bytes:
        all_bytes = bytes([inst.opcode]) + inst.operand_bytes
        bytes_str = " ".join(f"{b:02X}" for b in all_bytes).ljust(11)
    else:
        bytes_str = ""

    operand = inst.format_operand()
    asm_str = f"{inst.mnemonic:4} {operand}" if operand else inst.mnemonic

    if show_bytes:
        return f"{addr_str}  {bytes_str}  {asm_str}"
    return f"{addr_str}  {asm_str}"


def format_disassembly_block(
    instructions: list[Instruction],
    show_bytes: bool = True,
    a816_syntax: bool = False,
    symbol_map: dict[int, str] | None = None,
) -> list[str]:
    """Format a contiguous run of instructions, emitting label lines only
    at addresses referenced by branches or jumps within the block.

    Returns one string per output line (mix of label lines and instruction
    lines). When `a816_syntax` is False this is equivalent to mapping
    `format_disassembly` over the instruction list.

    symbol_map: optional address -> human-readable symbol name dict that
    takes precedence over synthesized `_BBHHHH` labels. When a target's
    address has an entry, the symbol name is used in operands and as the
    on-line label.
    """
    if not a816_syntax:
        return [format_disassembly(inst, show_bytes=show_bytes, a816_syntax=False) for inst in instructions]
    label_map = collect_labels(instructions)
    if symbol_map:
        label_map = {**label_map, **symbol_map}
    label_emit_addresses: set[int] = set()
    for inst in instructions:
        if inst.address in label_map:
            label_emit_addresses.add(inst.address)
    lines: list[str] = []
    for inst in instructions:
        if inst.address in label_emit_addresses:
            lines.append(f"{label_map[inst.address]}:")
        lines.append(format_disassembly(inst, show_bytes=show_bytes, a816_syntax=True, label_map=label_map))
    return lines
