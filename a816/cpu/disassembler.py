"""
65c816 Disassembler for SNES ROMs.

Provides instruction decoding with support for all addressing modes.
"""

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

    def format_operand(self, m_flag: bool = True, x_flag: bool = True, use_a816_syntax: bool = False) -> str:
        """Format the operand based on addressing mode.

        Args:
            m_flag: True if accumulator is 8-bit
            x_flag: True if index registers are 8-bit
            use_a816_syntax: If True, use 0x prefix instead of $ for a816 compatibility
        """
        val = self.operand_value
        mode = self.mode

        # Helper for hex formatting
        def hex_val(v: int, width: int) -> str:
            if use_a816_syntax:
                return f"0x{v:0{width}X}"
            else:
                return f"${v:0{width}X}"

        if mode == AddrMode.IMPLIED:
            return ""
        elif mode == AddrMode.IMMEDIATE_8:
            return f"#{hex_val(val, 2)}"
        elif mode == AddrMode.IMMEDIATE_16:
            return f"#{hex_val(val, 4)}"
        elif mode == AddrMode.IMMEDIATE_M:
            if m_flag:
                return f"#{hex_val(val, 2)}"
            else:
                return f"#{hex_val(val, 4)}"
        elif mode == AddrMode.IMMEDIATE_X:
            if x_flag:
                return f"#{hex_val(val, 2)}"
            else:
                return f"#{hex_val(val, 4)}"
        elif mode == AddrMode.DIRECT:
            return hex_val(val, 2)
        elif mode == AddrMode.DIRECT_X:
            return f"{hex_val(val, 2)},x"
        elif mode == AddrMode.DIRECT_Y:
            return f"{hex_val(val, 2)},y"
        elif mode == AddrMode.DIRECT_IND:
            return f"({hex_val(val, 2)})"
        elif mode == AddrMode.DIRECT_IND_X:
            return f"({hex_val(val, 2)},x)"
        elif mode == AddrMode.DIRECT_IND_Y:
            return f"({hex_val(val, 2)}),y"
        elif mode == AddrMode.DIRECT_IND_LONG:
            return f"[{hex_val(val, 2)}]"
        elif mode == AddrMode.DIRECT_IND_LONG_Y:
            return f"[{hex_val(val, 2)}],y"
        elif mode == AddrMode.ABSOLUTE:
            return hex_val(val, 4)
        elif mode == AddrMode.ABSOLUTE_X:
            return f"{hex_val(val, 4)},x"
        elif mode == AddrMode.ABSOLUTE_Y:
            return f"{hex_val(val, 4)},y"
        elif mode == AddrMode.ABSOLUTE_LONG:
            return hex_val(val, 6)
        elif mode == AddrMode.ABSOLUTE_LONG_X:
            return f"{hex_val(val, 6)},x"
        elif mode == AddrMode.ABSOLUTE_IND:
            return f"({hex_val(val, 4)})"
        elif mode == AddrMode.ABSOLUTE_IND_X:
            return f"({hex_val(val, 4)},x)"
        elif mode == AddrMode.ABSOLUTE_IND_LONG:
            return f"[{hex_val(val, 4)}]"
        elif mode == AddrMode.STACK_REL:
            return f"{hex_val(val, 2)},s"
        elif mode == AddrMode.STACK_REL_IND_Y:
            return f"({hex_val(val, 2)},s),y"
        elif mode == AddrMode.RELATIVE:
            # Calculate target address
            offset = val if val < 128 else val - 256
            target = self.address + self.length + offset
            return hex_val(target & 0xFFFF, 4)
        elif mode == AddrMode.RELATIVE_LONG:
            offset = val if val < 32768 else val - 65536
            target = self.address + self.length + offset
            return hex_val(target & 0xFFFF, 4)
        elif mode == AddrMode.BLOCK_MOVE:
            # Block move: first byte is dest bank, second is source bank
            dst = val & 0xFF
            src = (val >> 8) & 0xFF
            return f"{hex_val(dst, 2)},{hex_val(src, 2)}"
        else:
            if use_a816_syntax:
                return f"0x{val:X}"
            return f"${val:X}"

    def get_size_hint(self) -> str:
        """Get the size hint suffix for a816 syntax (.b, .w, .l)."""
        mode = self.mode

        # Implied instructions don't need size hints
        if mode == AddrMode.IMPLIED:
            return ""

        # Immediate instructions use the operand size
        if mode in (AddrMode.IMMEDIATE_8, AddrMode.IMMEDIATE_M, AddrMode.IMMEDIATE_X):
            if len(self.operand_bytes) == 1:
                return ".b"
            else:
                return ".w"
        elif mode == AddrMode.IMMEDIATE_16:
            return ".w"

        # Direct page is always byte-addressed
        if mode in (
            AddrMode.DIRECT,
            AddrMode.DIRECT_X,
            AddrMode.DIRECT_Y,
            AddrMode.DIRECT_IND,
            AddrMode.DIRECT_IND_X,
            AddrMode.DIRECT_IND_Y,
            AddrMode.DIRECT_IND_LONG,
            AddrMode.DIRECT_IND_LONG_Y,
            AddrMode.STACK_REL,
            AddrMode.STACK_REL_IND_Y,
        ):
            return ".b"

        # Absolute is word
        if mode in (
            AddrMode.ABSOLUTE,
            AddrMode.ABSOLUTE_X,
            AddrMode.ABSOLUTE_Y,
            AddrMode.ABSOLUTE_IND,
            AddrMode.ABSOLUTE_IND_X,
            AddrMode.ABSOLUTE_IND_LONG,
        ):
            return ".w"

        # Long is 24-bit
        if mode in (AddrMode.ABSOLUTE_LONG, AddrMode.ABSOLUTE_LONG_X):
            return ".l"

        # Relative branches don't need size hints
        if mode in (AddrMode.RELATIVE, AddrMode.RELATIVE_LONG, AddrMode.BLOCK_MOVE):
            return ""

        return ""

    def format_a816(self) -> str:
        """Format instruction in a816-compatible syntax."""
        operand = self.format_operand(use_a816_syntax=True)
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

    def decode_instruction(self, data: bytes, address: int) -> Instruction | None:
        """
        Decode a single instruction from bytes.

        Args:
            data: Bytes to decode (must have at least 1 byte)
            address: SNES logical address of the instruction

        Returns:
            Decoded Instruction, or None if data is empty
        """
        if not data:
            return None

        opcode = data[0]

        if opcode not in OPCODE_TABLE:
            # Unknown opcode - treat as data byte
            return Instruction(
                address=address,
                opcode=opcode,
                mnemonic=".db",
                mode=AddrMode.IMMEDIATE_8,
                operand_bytes=bytes([opcode]),
                operand_value=opcode,
                length=1,
            )

        mnemonic, mode, base_size = OPCODE_TABLE[opcode]
        operand_size = self.get_operand_size(base_size)

        # Check if we have enough data
        total_length = 1 + operand_size
        if len(data) < total_length:
            # Not enough data - return partial
            return Instruction(
                address=address,
                opcode=opcode,
                mnemonic=".db",
                mode=AddrMode.IMMEDIATE_8,
                operand_bytes=data,
                operand_value=opcode,
                length=1,
            )

        # Extract operand
        operand_bytes = data[1 : 1 + operand_size]
        if operand_size == 0:
            operand_value = 0
        elif operand_size == 1:
            operand_value = operand_bytes[0]
        elif operand_size == 2:
            operand_value = operand_bytes[0] | (operand_bytes[1] << 8)
        elif operand_size == 3:
            operand_value = operand_bytes[0] | (operand_bytes[1] << 8) | (operand_bytes[2] << 16)
        else:
            operand_value = 0

        # Track REP/SEP to update processor flags
        if mnemonic == "rep":
            if operand_value & 0x20:
                self.m_flag = False
            if operand_value & 0x10:
                self.x_flag = False
        elif mnemonic == "sep":
            if operand_value & 0x20:
                self.m_flag = True
            if operand_value & 0x10:
                self.x_flag = True

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


def format_disassembly(inst: Instruction, show_bytes: bool = True, a816_syntax: bool = False) -> str:
    """
    Format a single instruction for display.

    Args:
        inst: Decoded instruction
        show_bytes: Whether to show raw bytes
        a816_syntax: If True, output a816-compatible assembly syntax

    Returns:
        Formatted string
    """
    # Address
    bank = (inst.address >> 16) & 0xFF
    addr = inst.address & 0xFFFF

    if a816_syntax:
        # a816-compatible format: label and instruction
        addr_str = f"L_{bank:02X}{addr:04X}:"
        asm_str = inst.format_a816()

        if show_bytes:
            all_bytes = bytes([inst.opcode]) + inst.operand_bytes
            bytes_str = " ".join(f"{b:02X}" for b in all_bytes)
            comment = f"; {bytes_str}"
            return f"{addr_str:14} {asm_str:24} {comment}"
        else:
            return f"{addr_str:14} {asm_str}"
    else:
        addr_str = f"${bank:02X}:{addr:04X}"

        if show_bytes:
            # Raw bytes (up to 4)
            all_bytes = bytes([inst.opcode]) + inst.operand_bytes
            bytes_str = " ".join(f"{b:02X}" for b in all_bytes)
            bytes_str = bytes_str.ljust(11)  # 4 bytes max = "XX XX XX XX"
        else:
            bytes_str = ""

        # Mnemonic and operand
        operand = inst.format_operand()
        if operand:
            asm_str = f"{inst.mnemonic:4} {operand}"
        else:
            asm_str = inst.mnemonic

        if show_bytes:
            return f"{addr_str}  {bytes_str}  {asm_str}"
        else:
            return f"{addr_str}  {asm_str}"
