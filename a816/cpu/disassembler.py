"""
65c816 Disassembler for SNES ROMs.

Provides instruction decoding with support for all addressing modes.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import Enum

from a816.cpu.cpu_65c816 import (
    BlockMoveOpcode,
    Opcode,
    OpcodeWithoutOperand,
    RelativeJumpOpcode,
    RelativeLongJumpOpcode,
    snes_opcode_table,
)
from a816.cpu.types import AddressingMode as _AsmMode


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
#
# DERIVED from the assembler's `snes_opcode_table` (the single source of
# truth); the disassembler stands on the assembler's shoulders. Each
# assembler entry is inverted to byte -> (mnemonic, AddrMode, size) from the
# emitter type, size slot, and index. Encode-only aliases (`jsl`/`jml`) are
# skipped so each byte decodes to exactly one mnemonic (0x22 -> `jsr.l`,
# 0x5C -> `jmp.l`).

# (asm AddressingMode, index) -> per-size-slot (AddrMode, base_size) | None.
_SLOT_MODES: dict[tuple["_AsmMode", str | None], list[tuple[AddrMode, int] | None]] = {
    (_AsmMode.direct, None): [(AddrMode.DIRECT, 1), (AddrMode.ABSOLUTE, 2), (AddrMode.ABSOLUTE_LONG, 3)],
    (_AsmMode.direct_indexed, "x"): [(AddrMode.DIRECT_X, 1), (AddrMode.ABSOLUTE_X, 2), (AddrMode.ABSOLUTE_LONG_X, 3)],
    (_AsmMode.direct_indexed, "y"): [(AddrMode.DIRECT_Y, 1), (AddrMode.ABSOLUTE_Y, 2), None],
    (_AsmMode.direct_indexed, "s"): [(AddrMode.STACK_REL, 1), None, None],
    (_AsmMode.indirect, None): [(AddrMode.DIRECT_IND, 1), (AddrMode.ABSOLUTE_IND, 2), None],
    (_AsmMode.indirect_long, None): [(AddrMode.DIRECT_IND_LONG, 1), (AddrMode.ABSOLUTE_IND_LONG, 2), None],
    (_AsmMode.indirect_indexed, "y"): [(AddrMode.DIRECT_IND_Y, 1), None, None],
    (_AsmMode.indirect_indexed_long, "y"): [(AddrMode.DIRECT_IND_LONG_Y, 1), None, None],
    (_AsmMode.dp_or_sr_indirect_indexed, "x"): [(AddrMode.DIRECT_IND_X, 1), (AddrMode.ABSOLUTE_IND_X, 2), None],
    (_AsmMode.dp_or_sr_indirect_indexed, None): [(AddrMode.DIRECT_IND_X, 1), (AddrMode.ABSOLUTE_IND_X, 2), None],
    (_AsmMode.stack_indexed_indirect_indexed, "y"): [(AddrMode.STACK_REL_IND_Y, 1), None, None],
}


def _immediate_record(op: Opcode) -> tuple[AddrMode, int]:
    if op.is_a:
        return AddrMode.IMMEDIATE_M, -1
    if op.is_x:
        return AddrMode.IMMEDIATE_X, -2
    return AddrMode.IMMEDIATE_8, 1


def _slot_records(
    mnemonic: str, asm_mode: "_AsmMode", index: str | None, op: Opcode
) -> list[tuple[int, AddrMode, int]]:
    """(byte, AddrMode, size) per filled size slot of a memory-mode `Opcode`."""
    records: list[tuple[int, AddrMode, int]] = []
    for i, slot in enumerate(_SLOT_MODES[(asm_mode, index)]):
        if i >= len(op.opcode_def):
            break
        byte = op.opcode_def[i]
        if byte is None:
            continue
        assert slot is not None, f"{mnemonic} {asm_mode} slot {i} has a byte but no mode mapping"
        records.append((byte, slot[0], slot[1]))
    return records


def _opcode_records(
    mnemonic: str, asm_mode: "_AsmMode", index: str | None, op: Opcode
) -> list[tuple[int, AddrMode, int]]:
    """(byte, AddrMode, size) records for an operand-bearing `Opcode`."""
    if op.alias:
        return []  # jsl/jml: encode-only, not decoded
    if asm_mode is _AsmMode.immediate:
        byte = op.opcode_def[0]
        assert byte is not None
        mode, size = _immediate_record(op)
        return [(byte, mode, size)]
    return _slot_records(mnemonic, asm_mode, index, op)


def _emitter_records(
    mnemonic: str, asm_mode: "_AsmMode", index: str | None, em: object
) -> list[tuple[int, AddrMode, int]]:
    """(byte, AddrMode, size) records for one emitter. Empty for encode-only aliases.

    Order matters: the relative/block emitters subclass the plain ones.
    """
    if isinstance(em, BlockMoveOpcode):
        return [(em.opcode, AddrMode.BLOCK_MOVE, 2)]
    if isinstance(em, RelativeLongJumpOpcode):
        return [(em.opcode, AddrMode.RELATIVE_LONG, 2)]
    if isinstance(em, RelativeJumpOpcode):
        return [(em.opcode, AddrMode.RELATIVE, 1)]
    if isinstance(em, OpcodeWithoutOperand):
        return [(em.opcode, AddrMode.IMPLIED, 0)]
    if isinstance(em, Opcode):
        return _opcode_records(mnemonic, asm_mode, index, em)
    return []


def _mode_records(mnemonic: str, asm_mode: "_AsmMode", emitter: object) -> Iterator[tuple[int, AddrMode, int]]:
    """Flatten one mnemonic+mode entry (single emitter or index dict) to records."""
    entries = emitter.items() if isinstance(emitter, dict) else [(None, emitter)]
    for index, em in entries:
        yield from _emitter_records(mnemonic, asm_mode, index, em)


def _derive_opcode_table() -> dict[int, tuple[str, AddrMode, int]]:
    """Invert `snes_opcode_table` into the decoder's byte->instruction map."""
    table: dict[int, tuple[str, AddrMode, int]] = {}
    for mnemonic, modes in snes_opcode_table.items():
        for asm_mode, emitter in modes.items():
            for byte, mode, size in _mode_records(mnemonic, asm_mode, emitter):
                if byte in table:
                    raise ValueError(f"opcode 0x{byte:02x} claimed by {table[byte][0]!r} and {mnemonic!r}")
                table[byte] = (mnemonic, mode, size)
    return table


OPCODE_TABLE: dict[int, tuple[str, AddrMode, int]] = _derive_opcode_table()


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
