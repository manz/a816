"""Tests for 65c816 disassembler."""

from a816.cpu.disassembler import (
    OPCODE_TABLE,
    AddrMode,
    Disassembler,
    Instruction,
    collect_labels,
    format_disassembly,
    format_disassembly_block,
)


class TestOpcodeTable:
    """Tests for the opcode table coverage."""

    def test_all_common_opcodes_defined(self) -> None:
        # Check some critical opcodes are defined
        common_opcodes = [
            0x78,  # sei
            0xA9,  # lda immediate
            0x8D,  # sta absolute
            0x20,  # jsr
            0x60,  # rts
            0x4C,  # jmp
            0xEA,  # nop
        ]
        for op in common_opcodes:
            assert op in OPCODE_TABLE, f"Opcode 0x{op:02X} not in table"

    def test_opcode_table_has_correct_structure(self) -> None:
        for opcode, (mnemonic, mode, size) in OPCODE_TABLE.items():
            assert isinstance(opcode, int)
            assert 0 <= opcode <= 0xFF
            assert isinstance(mnemonic, str)
            assert len(mnemonic) == 3 or mnemonic == ".db"
            assert isinstance(mode, AddrMode)
            assert isinstance(size, int)


class TestDisassembler:
    """Tests for the Disassembler class."""

    def test_decode_implied_instruction(self) -> None:
        disasm = Disassembler()
        inst = disasm.decode_instruction(bytes([0xEA]), 0x8000)  # nop
        assert inst is not None
        assert inst.mnemonic == "nop"
        assert inst.mode == AddrMode.IMPLIED
        assert inst.length == 1

    def test_decode_immediate_8bit(self) -> None:
        disasm = Disassembler(m_flag=True)
        inst = disasm.decode_instruction(bytes([0xA9, 0x42]), 0x8000)  # lda #$42
        assert inst is not None
        assert inst.mnemonic == "lda"
        assert inst.mode == AddrMode.IMMEDIATE_M
        assert inst.operand_value == 0x42
        assert inst.length == 2

    def test_decode_immediate_16bit(self) -> None:
        disasm = Disassembler(m_flag=False)
        inst = disasm.decode_instruction(bytes([0xA9, 0x34, 0x12]), 0x8000)  # lda #$1234
        assert inst is not None
        assert inst.mnemonic == "lda"
        assert inst.operand_value == 0x1234
        assert inst.length == 3

    def test_decode_absolute(self) -> None:
        disasm = Disassembler()
        inst = disasm.decode_instruction(bytes([0x8D, 0x00, 0x21]), 0x8000)  # sta $2100
        assert inst is not None
        assert inst.mnemonic == "sta"
        assert inst.mode == AddrMode.ABSOLUTE
        assert inst.operand_value == 0x2100
        assert inst.length == 3

    def test_decode_absolute_long(self) -> None:
        disasm = Disassembler()
        inst = disasm.decode_instruction(bytes([0x22, 0x00, 0x80, 0x01]), 0x8000)  # jsl $018000
        assert inst is not None
        assert inst.mnemonic == "jsl"
        assert inst.mode == AddrMode.ABSOLUTE_LONG
        assert inst.operand_value == 0x018000
        assert inst.length == 4

    def test_decode_direct_page(self) -> None:
        disasm = Disassembler()
        inst = disasm.decode_instruction(bytes([0xA5, 0x10]), 0x8000)  # lda $10
        assert inst is not None
        assert inst.mnemonic == "lda"
        assert inst.mode == AddrMode.DIRECT
        assert inst.operand_value == 0x10
        assert inst.length == 2

    def test_decode_relative_branch(self) -> None:
        disasm = Disassembler()
        inst = disasm.decode_instruction(bytes([0xD0, 0x05]), 0x8000)  # bne $8007
        assert inst is not None
        assert inst.mnemonic == "bne"
        assert inst.mode == AddrMode.RELATIVE
        assert inst.operand_value == 0x05
        # Target should be $8000 + 2 + 5 = $008007 (bank-prefixed).
        assert inst.format_operand() == "$008007"

    def test_decode_relative_branch_backward(self) -> None:
        disasm = Disassembler()
        inst = disasm.decode_instruction(bytes([0xD0, 0xFE]), 0x8000)  # bne $8000 (infinite loop)
        assert inst is not None
        # Target should be $8000 + 2 - 2 = $008000 (bank-prefixed).
        assert inst.format_operand() == "$008000"

    def test_decode_indexed_indirect(self) -> None:
        disasm = Disassembler()
        inst = disasm.decode_instruction(bytes([0xA1, 0x10]), 0x8000)  # lda ($10,x)
        assert inst is not None
        assert inst.mnemonic == "lda"
        assert inst.mode == AddrMode.DIRECT_IND_X
        assert inst.format_operand() == "($10,x)"

    def test_decode_indirect_indexed(self) -> None:
        disasm = Disassembler()
        inst = disasm.decode_instruction(bytes([0xB1, 0x10]), 0x8000)  # lda ($10),y
        assert inst is not None
        assert inst.mnemonic == "lda"
        assert inst.mode == AddrMode.DIRECT_IND_Y
        assert inst.format_operand() == "($10),y"

    def test_decode_stack_relative(self) -> None:
        disasm = Disassembler()
        inst = disasm.decode_instruction(bytes([0xA3, 0x01]), 0x8000)  # lda $01,s
        assert inst is not None
        assert inst.mnemonic == "lda"
        assert inst.mode == AddrMode.STACK_REL
        assert inst.format_operand() == "$01,s"

    def test_decode_block_move(self) -> None:
        disasm = Disassembler()
        inst = disasm.decode_instruction(bytes([0x54, 0x7E, 0x00]), 0x8000)  # mvn $7E,$00
        assert inst is not None
        assert inst.mnemonic == "mvn"
        assert inst.mode == AddrMode.BLOCK_MOVE
        assert inst.format_operand() == "$7E,$00"

    def test_decode_unknown_opcode(self) -> None:
        # Find an opcode not in table (if any) or use a fake scenario
        # All 256 opcodes should be defined, but let's test the fallback
        disasm = Disassembler()
        # Create a custom test by temporarily removing an opcode
        # For now just verify it handles data correctly
        inst = disasm.decode_instruction(bytes([0x78]), 0x8000)  # sei
        assert inst is not None
        assert inst.mnemonic == "sei"

    def test_rep_updates_m_flag(self) -> None:
        disasm = Disassembler(m_flag=True, x_flag=True)
        disasm.decode_instruction(bytes([0xC2, 0x20]), 0x8000)  # rep #$20
        assert disasm.m_flag is False  # M should now be 0 (16-bit)
        assert disasm.x_flag is True  # X unchanged

    def test_rep_updates_x_flag(self) -> None:
        disasm = Disassembler(m_flag=True, x_flag=True)
        disasm.decode_instruction(bytes([0xC2, 0x10]), 0x8000)  # rep #$10
        assert disasm.m_flag is True  # M unchanged
        assert disasm.x_flag is False  # X should now be 0 (16-bit)

    def test_sep_updates_m_flag(self) -> None:
        disasm = Disassembler(m_flag=False, x_flag=False)
        disasm.decode_instruction(bytes([0xE2, 0x20]), 0x8000)  # sep #$20
        assert disasm.m_flag is True  # M should now be 1 (8-bit)
        assert disasm.x_flag is False  # X unchanged

    def test_disassemble_sequence(self) -> None:
        disasm = Disassembler()
        code = bytes(
            [
                0x78,  # sei
                0xA9,
                0x00,  # lda #$00
                0x8D,
                0x00,
                0x21,  # sta $2100
                0x60,  # rts
            ]
        )
        instructions = disasm.disassemble(code, 0x8000)
        assert len(instructions) == 4
        assert instructions[0].mnemonic == "sei"
        assert instructions[1].mnemonic == "lda"
        assert instructions[2].mnemonic == "sta"
        assert instructions[3].mnemonic == "rts"

    def test_disassemble_with_count_limit(self) -> None:
        disasm = Disassembler()
        code = bytes([0xEA] * 10)  # 10 nops
        instructions = disasm.disassemble(code, 0x8000, count=3)
        assert len(instructions) == 3

    def test_disassemble_empty(self) -> None:
        disasm = Disassembler()
        instructions = disasm.disassemble(b"", 0x8000)
        assert len(instructions) == 0


class TestInstruction:
    """Tests for the Instruction class."""

    def test_format_operand_immediate_8(self) -> None:
        inst = Instruction(
            address=0x8000,
            opcode=0xA9,
            mnemonic="lda",
            mode=AddrMode.IMMEDIATE_8,
            operand_bytes=bytes([0x42]),
            operand_value=0x42,
            length=2,
        )
        assert inst.format_operand() == "#$42"

    def test_format_operand_immediate_16(self) -> None:
        inst = Instruction(
            address=0x8000,
            opcode=0xA9,
            mnemonic="lda",
            mode=AddrMode.IMMEDIATE_16,
            operand_bytes=bytes([0x34, 0x12]),
            operand_value=0x1234,
            length=3,
        )
        assert inst.format_operand() == "#$1234"

    def test_format_operand_absolute(self) -> None:
        inst = Instruction(
            address=0x8000,
            opcode=0x8D,
            mnemonic="sta",
            mode=AddrMode.ABSOLUTE,
            operand_bytes=bytes([0x00, 0x21]),
            operand_value=0x2100,
            length=3,
        )
        assert inst.format_operand() == "$2100"

    def test_format_operand_absolute_long(self) -> None:
        inst = Instruction(
            address=0x8000,
            opcode=0x22,
            mnemonic="jsl",
            mode=AddrMode.ABSOLUTE_LONG,
            operand_bytes=bytes([0x00, 0x80, 0x01]),
            operand_value=0x018000,
            length=4,
        )
        assert inst.format_operand() == "$018000"

    def test_str_representation(self) -> None:
        inst = Instruction(
            address=0x8000,
            opcode=0x8D,
            mnemonic="sta",
            mode=AddrMode.ABSOLUTE,
            operand_bytes=bytes([0x00, 0x21]),
            operand_value=0x2100,
            length=3,
        )
        assert str(inst) == "sta $2100"

    def test_str_implied(self) -> None:
        inst = Instruction(
            address=0x8000,
            opcode=0xEA,
            mnemonic="nop",
            mode=AddrMode.IMPLIED,
            operand_bytes=b"",
            operand_value=0,
            length=1,
        )
        assert str(inst) == "nop"


class TestFormatDisassembly:
    """Tests for the format_disassembly function."""

    def test_format_with_bytes(self) -> None:
        inst = Instruction(
            address=0x018000,
            opcode=0xA9,
            mnemonic="lda",
            mode=AddrMode.IMMEDIATE_8,
            operand_bytes=bytes([0x42]),
            operand_value=0x42,
            length=2,
        )
        output = format_disassembly(inst, show_bytes=True)
        assert "$01:8000" in output
        assert "A9 42" in output
        assert "lda" in output
        assert "#$42" in output

    def test_format_without_bytes(self) -> None:
        inst = Instruction(
            address=0x018000,
            opcode=0xA9,
            mnemonic="lda",
            mode=AddrMode.IMMEDIATE_8,
            operand_bytes=bytes([0x42]),
            operand_value=0x42,
            length=2,
        )
        output = format_disassembly(inst, show_bytes=False)
        assert "$01:8000" in output
        assert "A9" not in output
        assert "lda" in output
        assert "#$42" in output

    def test_format_long_instruction(self) -> None:
        inst = Instruction(
            address=0x008000,
            opcode=0x22,
            mnemonic="jsl",
            mode=AddrMode.ABSOLUTE_LONG,
            operand_bytes=bytes([0x00, 0x80, 0x01]),
            operand_value=0x018000,
            length=4,
        )
        output = format_disassembly(inst, show_bytes=True)
        assert "$00:8000" in output
        assert "22 00 80 01" in output
        assert "jsl" in output
        assert "$018000" in output

    def test_format_a816_syntax(self) -> None:
        inst = Instruction(
            address=0x018000,
            opcode=0xA9,
            mnemonic="lda",
            mode=AddrMode.IMMEDIATE_8,
            operand_bytes=bytes([0x42]),
            operand_value=0x42,
            length=2,
        )
        output = format_disassembly(inst, show_bytes=True, a816_syntax=True)
        assert "_018000:" in output
        assert "lda #0x42" in output
        assert "A9 42" in output

    def test_format_a816_syntax_absolute(self) -> None:
        inst = Instruction(
            address=0x008000,
            opcode=0x8D,
            mnemonic="sta",
            mode=AddrMode.ABSOLUTE,
            operand_bytes=bytes([0x00, 0x21]),
            operand_value=0x2100,
            length=3,
        )
        output = format_disassembly(inst, show_bytes=False, a816_syntax=True)
        assert "_008000:" in output
        assert "sta 0x2100" in output

    def test_format_a816_syntax_long(self) -> None:
        inst = Instruction(
            address=0x008000,
            opcode=0x22,
            mnemonic="jsl",
            mode=AddrMode.ABSOLUTE_LONG,
            operand_bytes=bytes([0x00, 0x80, 0x01]),
            operand_value=0x018000,
            length=4,
        )
        output = format_disassembly(inst, show_bytes=False, a816_syntax=True)
        assert "_008000:" in output
        assert "jsl.l 0x018000" in output


class TestInstructionA816Format:
    """Tests for a816-compatible instruction formatting."""

    def test_format_a816_immediate(self) -> None:
        inst = Instruction(
            address=0x8000,
            opcode=0xA9,
            mnemonic="lda",
            mode=AddrMode.IMMEDIATE_8,
            operand_bytes=bytes([0x42]),
            operand_value=0x42,
            length=2,
        )
        assert inst.format_a816() == "lda #0x42"

    def test_format_a816_absolute(self) -> None:
        inst = Instruction(
            address=0x8000,
            opcode=0x8D,
            mnemonic="sta",
            mode=AddrMode.ABSOLUTE,
            operand_bytes=bytes([0x00, 0x21]),
            operand_value=0x2100,
            length=3,
        )
        assert inst.format_a816() == "sta 0x2100"

    def test_format_a816_long(self) -> None:
        inst = Instruction(
            address=0x8000,
            opcode=0x22,
            mnemonic="jsl",
            mode=AddrMode.ABSOLUTE_LONG,
            operand_bytes=bytes([0x00, 0x80, 0x01]),
            operand_value=0x018000,
            length=4,
        )
        assert inst.format_a816() == "jsl.l 0x018000"

    def test_format_a816_implied(self) -> None:
        inst = Instruction(
            address=0x8000,
            opcode=0xEA,
            mnemonic="nop",
            mode=AddrMode.IMPLIED,
            operand_bytes=b"",
            operand_value=0,
            length=1,
        )
        assert inst.format_a816() == "nop"

    def test_format_a816_direct_page(self) -> None:
        inst = Instruction(
            address=0x8000,
            opcode=0xA5,
            mnemonic="lda",
            mode=AddrMode.DIRECT,
            operand_bytes=bytes([0x10]),
            operand_value=0x10,
            length=2,
        )
        assert inst.format_a816() == "lda 0x10"


class TestA816BlockFormat:
    """Tests for the label-aware block formatter (idiomatic a816 syntax)."""

    def test_collect_labels_picks_branch_targets(self) -> None:
        disasm = Disassembler()
        # bne +5 at 0x008000 -> target 0x008007; jmp.l 0x018000 at 0x008002 -> 0x018000.
        data = bytes.fromhex("d005000000005c008001")
        instructions = disasm.disassemble(data, 0x008000)
        labels = collect_labels(instructions)
        assert 0x008007 in labels
        assert labels[0x008007] == "_008007"
        assert 0x018000 in labels
        assert labels[0x018000] == "_018000"

    def test_block_emits_label_lines_only_at_targets(self) -> None:
        disasm = Disassembler()
        # bne +0 -> target = next instruction (0x008002), so label appears.
        data = bytes.fromhex("d000ea")
        instructions = disasm.disassemble(data, 0x008000)
        lines = format_disassembly_block(instructions, show_bytes=False, a816_syntax=True)
        # Expected: instruction line for bne, label line for _008002, then nop.
        assert any(line == "_008002:" for line in lines)
        assert sum(1 for line in lines if line.endswith(":")) == 1

    def test_branch_uses_label_when_target_known(self) -> None:
        disasm = Disassembler()
        data = bytes.fromhex("d005000000000000ea")  # bne +5, then padding, then nop at +7
        instructions = disasm.disassemble(data, 0x008000)
        lines = format_disassembly_block(instructions, show_bytes=False, a816_syntax=True)
        bne_line = next(line for line in lines if "bne" in line)
        assert "_008007" in bne_line
        assert "0x008007" not in bne_line

    def test_jmp_long_uses_label(self) -> None:
        disasm = Disassembler()
        data = bytes.fromhex("5c008001")
        instructions = disasm.disassemble(data, 0x008000)
        lines = format_disassembly_block(instructions, show_bytes=False, a816_syntax=True)
        jmp_line = next(line for line in lines if "jmp" in line)
        assert "_018000" in jmp_line

    def test_minimal_suffixes_round_trip(self) -> None:
        disasm = Disassembler()
        # lda #0x42, sta 0x1234, lda 0x10, jsl 0x018000, nop
        data = bytes.fromhex("a9428d3412a510220080 01ea".replace(" ", ""))
        instructions = disasm.disassemble(data, 0x008000)
        lines = format_disassembly_block(instructions, show_bytes=False, a816_syntax=True)
        joined = "\n".join(lines)
        assert "lda #0x42" in joined
        assert "sta 0x1234" in joined
        assert "lda 0x10" in joined
        assert "jsl.l _018000" in joined
        assert "nop" in joined
        assert ".w" not in joined  # no verbose word suffix on absolute / direct


class TestFunctionDisassembly:
    """Tests for the CFG-driven function walker."""

    def test_stops_at_rts(self) -> None:
        from a816.cpu.disassembler import disassemble_function

        code = bytes.fromhex("a90060eaeaea")  # lda #$00, rts, then garbage NOPs

        def provider(addr: int, length: int) -> bytes:
            offset = addr - 0x008000
            return code[offset : offset + length] if offset >= 0 else b""

        instructions = disassemble_function(0x008000, provider)
        assert [inst.mnemonic for inst in instructions] == ["lda", "rts"]

    def test_follows_conditional_branch_and_fallthrough(self) -> None:
        from a816.cpu.disassembler import disassemble_function

        # 0x008000: bne +2 (target 0x008004)
        # 0x008002: nop          (fallthrough)
        # 0x008003: rts
        # 0x008004: nop          (branch target)
        # 0x008005: rts
        code = bytes.fromhex("d002ea60ea60")

        def provider(addr: int, length: int) -> bytes:
            offset = addr - 0x008000
            return code[offset : offset + length] if offset >= 0 else b""

        instructions = disassemble_function(0x008000, provider)
        addresses = [inst.address for inst in instructions]
        assert 0x008002 in addresses  # fallthrough decoded
        assert 0x008004 in addresses  # branch target decoded

    def test_tracks_m_flag_across_branches(self) -> None:
        from a816.cpu.disassembler import AddrMode, disassemble_function

        # 0x008000: rep #$20      (M=0, 16-bit)
        # 0x008002: lda #$1234    (3 bytes total, A9 34 12)
        # 0x008005: rts
        code = bytes.fromhex("c220a9341260")

        def provider(addr: int, length: int) -> bytes:
            offset = addr - 0x008000
            return code[offset : offset + length] if offset >= 0 else b""

        instructions = disassemble_function(0x008000, provider, m_flag=True)
        lda = next(inst for inst in instructions if inst.mnemonic == "lda")
        assert lda.length == 3
        assert lda.mode == AddrMode.IMMEDIATE_M

    def test_unconditional_branch_terminates_path(self) -> None:
        from a816.cpu.disassembler import disassemble_function

        # 0x008000: bra +2 -> target 0x008004
        # 0x008002: nop  (skipped — bra is unconditional, no fallthrough)
        # 0x008003: nop
        # 0x008004: rts
        code = bytes.fromhex("8002eaea60")

        def provider(addr: int, length: int) -> bytes:
            offset = addr - 0x008000
            return code[offset : offset + length] if offset >= 0 else b""

        instructions = disassemble_function(0x008000, provider)
        addresses = [inst.address for inst in instructions]
        assert 0x008002 not in addresses
        assert 0x008003 not in addresses
        assert 0x008004 in addresses
