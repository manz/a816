"""SPC700 instruction set: operand patterns, opcode table and encoder.

An instruction is a mnemonic plus a tuple of operand patterns. Register
operands (`a`, `x`, `ya`, `(x)+`, ...) carry no bytes; memory operands
carry one (`dp`, `imm`, `rel`) or two (`abs`, `mem.bit`). Two memory
operands are encoded source first (`mov dp, #imm` is `8F imm dp`), except
when one of them is a branch offset (`cbne dp, rel` is `2E dp rel`).
"""

from __future__ import annotations

from dataclasses import dataclass

REGISTERS = frozenset({"a", "x", "y", "ya", "sp", "psw", "c"})

# Patterns a source operand without `!` can still take: the encoder picks
# the direct page form when the value fits a byte.
_ONE_BYTE = frozenset({"imm", "dp", "dp+x", "dp+y", "[dp+x]", "[dp]+y", "dp.bit", "upage", "rel"})
_TWO_BYTES = frozenset({"abs", "abs+x", "abs+y", "[abs+x]", "mem.bit", "/mem.bit"})
_BIT_IN_OPCODE = frozenset({"dp.bit"})


@dataclass(frozen=True)
class Encoding:
    opcode: int
    patterns: tuple[str, ...]

    @property
    def length(self) -> int:
        return 1 + sum(pattern_width(p) for p in self.patterns)

    def byte_order(self) -> tuple[int, ...]:
        """Indices of the byte-carrying operands, in emission order."""
        carrying = tuple(i for i, p in enumerate(self.patterns) if pattern_width(p))
        if "rel" in self.patterns or len(carrying) < 2:
            return carrying
        return tuple(reversed(carrying))


def pattern_width(pattern: str) -> int:
    if pattern in _ONE_BYTE:
        return 1
    if pattern in _TWO_BYTES:
        return 2
    return 0


SPC700_TABLE: dict[str, list[Encoding]] = {}


def _op(mnemonic: str, opcode: int, *patterns: str) -> None:
    SPC700_TABLE.setdefault(mnemonic, []).append(Encoding(opcode, patterns))


def _alu() -> None:
    for mnemonic, base in (("or", 0x00), ("and", 0x20), ("eor", 0x40), ("cmp", 0x60), ("adc", 0x80), ("sbc", 0xA0)):
        _op(mnemonic, base | 0x04, "a", "dp")
        _op(mnemonic, base | 0x05, "a", "abs")
        _op(mnemonic, base | 0x06, "a", "(x)")
        _op(mnemonic, base | 0x07, "a", "[dp+x]")
        _op(mnemonic, base | 0x08, "a", "imm")
        _op(mnemonic, base | 0x09, "dp", "dp")
        _op(mnemonic, base | 0x14, "a", "dp+x")
        _op(mnemonic, base | 0x15, "a", "abs+x")
        _op(mnemonic, base | 0x16, "a", "abs+y")
        _op(mnemonic, base | 0x17, "a", "[dp]+y")
        _op(mnemonic, base | 0x18, "dp", "imm")
        _op(mnemonic, base | 0x19, "(x)", "(y)")
    for opcode, reg, mode in (
        (0xC8, "x", "imm"),
        (0x3E, "x", "dp"),
        (0x1E, "x", "abs"),
        (0xAD, "y", "imm"),
        (0x7E, "y", "dp"),
        (0x5E, "y", "abs"),
    ):
        _op("cmp", opcode, reg, mode)


def _read_modify_write() -> None:
    for mnemonic, base in (("asl", 0x00), ("rol", 0x20), ("lsr", 0x40), ("ror", 0x60), ("dec", 0x80), ("inc", 0xA0)):
        _op(mnemonic, base | 0x0B, "dp")
        _op(mnemonic, base | 0x0C, "abs")
        _op(mnemonic, base | 0x1B, "dp+x")
        _op(mnemonic, base | 0x1C, "a")
    _op("dec", 0x1D, "x")
    _op("dec", 0xDC, "y")
    _op("inc", 0x3D, "x")
    _op("inc", 0xFC, "y")
    _op("decw", 0x1A, "dp")
    _op("incw", 0x3A, "dp")
    _op("cmpw", 0x5A, "ya", "dp")
    _op("addw", 0x7A, "ya", "dp")
    _op("subw", 0x9A, "ya", "dp")
    _op("movw", 0xBA, "ya", "dp")
    _op("movw", 0xDA, "dp", "ya")


_MOV: tuple[tuple[int, str, str], ...] = (
    (0xE8, "a", "imm"),
    (0xE4, "a", "dp"),
    (0xE5, "a", "abs"),
    (0xE6, "a", "(x)"),
    (0xE7, "a", "[dp+x]"),
    (0xF4, "a", "dp+x"),
    (0xF5, "a", "abs+x"),
    (0xF6, "a", "abs+y"),
    (0xF7, "a", "[dp]+y"),
    (0xBF, "a", "(x)+"),
    (0xCD, "x", "imm"),
    (0xF8, "x", "dp"),
    (0xE9, "x", "abs"),
    (0xF9, "x", "dp+y"),
    (0x8D, "y", "imm"),
    (0xEB, "y", "dp"),
    (0xEC, "y", "abs"),
    (0xFB, "y", "dp+x"),
    (0xC4, "dp", "a"),
    (0xC5, "abs", "a"),
    (0xC6, "(x)", "a"),
    (0xC7, "[dp+x]", "a"),
    (0xD4, "dp+x", "a"),
    (0xD5, "abs+x", "a"),
    (0xD6, "abs+y", "a"),
    (0xD7, "[dp]+y", "a"),
    (0xAF, "(x)+", "a"),
    (0xD8, "dp", "x"),
    (0xC9, "abs", "x"),
    (0xD9, "dp+y", "x"),
    (0xCB, "dp", "y"),
    (0xCC, "abs", "y"),
    (0xDB, "dp+x", "y"),
    (0x7D, "a", "x"),
    (0xDD, "a", "y"),
    (0x5D, "x", "a"),
    (0xFD, "y", "a"),
    (0x9D, "x", "sp"),
    (0xBD, "sp", "x"),
    (0xFA, "dp", "dp"),
    (0x8F, "dp", "imm"),
)


def _moves() -> None:
    for opcode, dst, src in _MOV:
        _op("mov", opcode, dst, src)
    for reg, push, pop in (("a", 0x2D, 0xAE), ("x", 0x4D, 0xCE), ("y", 0x6D, 0xEE), ("psw", 0x0D, 0x8E)):
        _op("push", push, reg)
        _op("pop", pop, reg)


def _flow() -> None:
    for mnemonic, opcode in (
        ("bra", 0x2F),
        ("beq", 0xF0),
        ("bne", 0xD0),
        ("bcs", 0xB0),
        ("bcc", 0x90),
        ("bvs", 0x70),
        ("bvc", 0x50),
        ("bmi", 0x30),
        ("bpl", 0x10),
    ):
        _op(mnemonic, opcode, "rel")
    _op("bbs", 0x03, "dp.bit", "rel")
    _op("bbc", 0x13, "dp.bit", "rel")
    _op("cbne", 0x2E, "dp", "rel")
    _op("cbne", 0xDE, "dp+x", "rel")
    _op("dbnz", 0x6E, "dp", "rel")
    _op("dbnz", 0xFE, "y", "rel")
    _op("jmp", 0x5F, "abs")
    _op("jmp", 0x1F, "[abs+x]")
    _op("call", 0x3F, "abs")
    _op("pcall", 0x4F, "upage")
    _op("tcall", 0x01, "vector")


def _bits() -> None:
    _op("set1", 0x02, "dp.bit")
    _op("clr1", 0x12, "dp.bit")
    _op("tset1", 0x0E, "abs")
    _op("tclr1", 0x4E, "abs")
    _op("and1", 0x4A, "c", "mem.bit")
    _op("and1", 0x6A, "c", "/mem.bit")
    _op("or1", 0x0A, "c", "mem.bit")
    _op("or1", 0x2A, "c", "/mem.bit")
    _op("eor1", 0x8A, "c", "mem.bit")
    _op("not1", 0xEA, "mem.bit")
    _op("mov1", 0xAA, "c", "mem.bit")
    _op("mov1", 0xCA, "mem.bit", "c")


def _misc() -> None:
    for mnemonic, opcode in (
        ("nop", 0x00),
        ("brk", 0x0F),
        ("ret", 0x6F),
        ("reti", 0x7F),
        ("clrc", 0x60),
        ("setc", 0x80),
        ("notc", 0xED),
        ("clrv", 0xE0),
        ("clrp", 0x20),
        ("setp", 0x40),
        ("ei", 0xA0),
        ("di", 0xC0),
        ("sleep", 0xEF),
        ("stop", 0xFF),
    ):
        _op(mnemonic, opcode)
    _op("daa", 0xDF, "a")
    _op("das", 0xBE, "a")
    _op("xcn", 0x9F, "a")
    _op("mul", 0xCF, "ya")
    _op("div", 0x9E, "ya", "x")


_alu()
_read_modify_write()
_moves()
_flow()
_bits()
_misc()

MNEMONICS = frozenset(SPC700_TABLE)
IMPLIED_ONLY = frozenset(
    name for name, encodings in SPC700_TABLE.items() if encodings == [Encoding(encodings[0].opcode, ())]
)

# What a parsed operand shape may encode as. A plain expression (`mem`)
# is a direct page, absolute, branch target, vector or page number
# depending on the instruction; `!` forces the absolute forms.
SHAPE_PATTERNS: dict[str, frozenset[str]] = {
    "imm": frozenset({"imm"}),
    "mem": frozenset({"dp", "abs", "rel", "upage", "vector"}),
    "mem+x": frozenset({"dp+x", "abs+x"}),
    "mem+y": frozenset({"dp+y", "abs+y"}),
    "abs": frozenset({"abs"}),
    "abs+x": frozenset({"abs+x"}),
    "abs+y": frozenset({"abs+y"}),
    "mem.bit": frozenset({"dp.bit", "mem.bit"}),
    "/mem.bit": frozenset({"/mem.bit"}),
    "(x)": frozenset({"(x)"}),
    "(x)+": frozenset({"(x)+"}),
    "(y)": frozenset({"(y)"}),
    "[dp+x]": frozenset({"[dp+x]"}),
    "[dp]+y": frozenset({"[dp]+y"}),
    "[abs+x]": frozenset({"[abs+x]"}),
}
for _register in REGISTERS:
    SHAPE_PATTERNS[_register] = frozenset({_register})

_DIRECT_PAGE_FORMS = frozenset({"dp", "dp+x", "dp+y", "[dp+x]", "[dp]+y", "dp.bit", "upage"})


def candidates(mnemonic: str, shapes: tuple[str, ...]) -> list[Encoding]:
    """Every encoding of `mnemonic` the operand shapes can take."""
    return [
        encoding
        for encoding in SPC700_TABLE.get(mnemonic, [])
        if len(encoding.patterns) == len(shapes)
        and all(p in SHAPE_PATTERNS.get(s, frozenset()) for p, s in zip(encoding.patterns, shapes, strict=True))
    ]


def choose(found: list[Encoding], values: tuple[int | None, ...]) -> Encoding | None:
    """The direct page form when every ambiguous operand fits a byte, else the absolute one."""
    if len(found) <= 1:
        return found[0] if found else None
    fits = all(v is not None and 0 <= v <= 0xFF for v in values if v is not None)
    preferred = [e for e in found if any(p in _DIRECT_PAGE_FORMS for p in e.patterns) == fits]
    return (preferred or found)[0]


def encode_operand(pattern: str, value: int, bit: int | None, pc_after: int) -> bytes:
    """Bytes one operand contributes, given its resolved value."""
    if pattern == "rel":
        return bytes([(value - pc_after) & 0xFF])
    if pattern in ("mem.bit", "/mem.bit"):
        assert bit is not None
        word = (value & 0x1FFF) | (bit & 7) << 13
        return word.to_bytes(2, "little")
    width = pattern_width(pattern)
    return (value & (1 << 8 * width) - 1).to_bytes(width, "little")


def opcode_byte(encoding: Encoding, bits: tuple[int | None, ...], values: tuple[int | None, ...]) -> int:
    """The opcode, with a `dp.bit` bit number or a `tcall` vector folded in."""
    for pattern, bit, value in zip(encoding.patterns, bits, values, strict=True):
        if pattern in _BIT_IN_OPCODE:
            assert bit is not None
            return encoding.opcode | (bit & 7) << 5
        if pattern == "vector":
            assert value is not None
            return encoding.opcode | (value & 0xF) << 4
    return encoding.opcode
