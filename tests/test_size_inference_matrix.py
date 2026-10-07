"""Gate `OpcodeNode.pc_after` against the bytes `emit` writes, for every opcode.

Labels bind from `pc_after` (the size prediction); the ROM receives what
`emit` produces. When the two disagree every label after the instruction
drifts off its bytes, silently (the ff4 boot break). Cases are generated
from `snes_opcode_table`: every mnemonic x addressing mode x index x suffix,
under each A/X width, with rep/sep tracking on and off.

Two layers:

* node level: one parsed `OpcodeNode` per (entry, suffix, operand), measured
  and emitted under each register state. Exhaustive and cheap; also checks
  that combinations the table has no slot for are rejected, not emitted.
* end to end: real assemblies per addressing-mode family through top-level
  `*=`, `.alloc at`, a pooled `.alloc` (measured before placement), a pooled
  `.alloc` entered with register state set outside it, and object mode. Each
  instruction is followed by a label that must land right after its bytes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from itertools import product

import pytest

from a816.context import AssemblyMode
from a816.cpu.cpu_65c816 import (
    BlockMoveOpcode,
    LongOpcode,
    Opcode,
    OpcodeWithoutOperand,
    RelativeJumpOpcode,
    TransferOpcode,
    snes_opcode_table,
)
from a816.cpu.mapping import Address
from a816.cpu.types import AddressingMode
from a816.parse.nodes import NodeError
from a816.parse.nodes.alloc import AllocNode
from a816.parse.nodes.opcode import OpcodeNode
from a816.program import Program
from a816.protocols import NodeProtocol, OpcodeProtocol
from a816.symbols import Resolver
from a816.writers import ObjectWriter
from tests import CLIENT_POOL, StubWriter, label_address

_ORIGIN = 0x008000
_WIDTH_BYTES: dict[str, int] = {"b": 1, "w": 2, "l": 3}
_SLOT: dict[str, int] = {"b": 0, "w": 1, "l": 2}

# Operand syntax per addressing mode; `{v}` is the value, `{i}` the index.
_TEMPLATE: dict[AddressingMode, str] = {
    AddressingMode.none: "",
    AddressingMode.immediate: " #{v}",
    AddressingMode.direct: " {v}",
    AddressingMode.direct_indexed: " {v},{i}",
    AddressingMode.indirect: " ({v})",
    AddressingMode.indirect_indexed: " ({v}),{i}",
    AddressingMode.indirect_long: " [{v}]",
    AddressingMode.indirect_indexed_long: " [{v}],{i}",
    AddressingMode.dp_or_sr_indirect_indexed: " ({v},x)",
    AddressingMode.stack_indexed_indirect_indexed: " ({v},s),y",
    AddressingMode.block_move: " {v}, {v}",
}


@dataclass(frozen=True)
class Entry:
    """One encodable `(mnemonic, addressing mode, index)` slot of the table."""

    mnemonic: str
    mode: AddressingMode
    index: str | None
    emitter: OpcodeProtocol

    @property
    def ident(self) -> str:
        return f"{self.mnemonic}-{self.mode.name}" + (f"-{self.index}" if self.index else "")


@dataclass(frozen=True)
class Operand:
    """Operand text plus what size inference sees in it.

    `width` is the value-driven width (`None`: a forward reference, unknown
    on the first pass). `fits_byte` mirrors the `.b` immediate overflow check.
    `{k}` in `text` is replaced by the case's own trailing label.
    """

    text: str
    width: str | None
    fits_byte: bool


@dataclass(frozen=True)
class RegState:
    a16: bool
    i16: bool
    track: bool

    @property
    def ident(self) -> str:
        return f"a{16 if self.a16 else 8}i{16 if self.i16 else 8}{'-track' if self.track else ''}"


def _entries() -> list[Entry]:
    out: list[Entry] = []
    for mnemonic, modes in snes_opcode_table.items():
        for mode, emitter in modes.items():
            if isinstance(emitter, dict):
                out.extend(Entry(mnemonic, mode, index, sub) for index, sub in emitter.items())
            else:
                out.append(Entry(mnemonic, mode, None, emitter))
    return out


_ENTRIES = _entries()
_STATES = [RegState(a16, i16, track) for a16, i16, track in product((False, True), repeat=3)]
_NUMBERS = (Operand("0x12", "b", True), Operand("0x1234", "w", False), Operand("0x123456", "l", False))
# Relative operands target the instruction's own trailing label in end-to-end
# programs (displacement 0); node-level cases aim a few bytes ahead.
_NODE_BRANCH_TARGET = Operand("0x008010", "w", False)
_SELF_LABEL = Operand("L{k}", None, False)
_BACKWARD_LABEL = Operand("start", "w", False)  # bound at 0x008000
# Object mode: an extern evaluates to 0 until link, so its magnitude sizes
# nothing (`_width`); unsized, only a register-sized immediate or a
# single-form opcode is decidable, the rest stops with E0313.
_EXTERN = Operand("ext", None, True)


def _is_relative(entry: Entry) -> bool:
    return isinstance(entry.emitter, RelativeJumpOpcode)


def _suffixes(entry: Entry) -> tuple[str | None, ...]:
    """Size suffixes worth generating for `entry`.

    Only size-indexed emitters (`Opcode`, incl. `LongOpcode`) read a suffix.
    `OpcodeWithoutOperand`, `RelativeJumpOpcode` and `BlockMoveOpcode` encode
    a fixed width, so a suffix there is not part of the table.
    """
    if isinstance(entry.emitter, Opcode):
        return (None, "b", "w", "l")
    return (None,)


def _line(entry: Entry, suffix: str | None, operand: Operand, k: int = 0) -> str:
    value = operand.text.format(k=k)
    head = entry.mnemonic + (f".{suffix}" if suffix else "")
    return head + _TEMPLATE[entry.mode].format(v=value, i=entry.index)


def _width(entry: Entry, suffix: str | None, operand: Operand, state: RegState) -> str | None:
    """Operand width the table encodes; `None` when nothing determines it."""
    emitter = entry.emitter
    assert isinstance(emitter, Opcode)
    if isinstance(emitter, LongOpcode):
        return "l"
    if suffix:
        return suffix
    if (emitter.is_a and state.a16) or (emitter.is_x and state.i16):
        return "w"
    forms = emitter.encodable_sizes()
    if operand is _EXTERN:
        if emitter.is_a or emitter.is_x:
            return "b"
        return forms[0] if len(forms) == 1 else None
    # A single form widens a narrower unsized operand (`pea 0x0000`,
    # `lda 0x12,y` -> abs,y) and never narrows a wider one.
    if len(forms) == 1 and operand.width is not None and _SLOT[operand.width] < _SLOT[forms[0]]:
        return forms[0]
    return operand.width


def _sized_length(entry: Entry, width: str, operand: Operand) -> int | None:
    emitter = entry.emitter
    assert isinstance(emitter, Opcode)
    slot = _SLOT[width]
    if slot >= len(emitter.opcode_def) or emitter.opcode_def[slot] is None:
        return None  # no opcode byte for this width: emit must reject
    if entry.mode is AddressingMode.immediate and width == "b" and not operand.fits_byte:
        return None  # `.b` immediate overflow: emit must reject
    return 1 + _WIDTH_BYTES[width]


def _expected_length(entry: Entry, suffix: str | None, operand: Operand, state: RegState) -> int | None:
    """Encoded length per the opcode table, `None` when the table rejects it."""
    emitter = entry.emitter
    if isinstance(emitter, RelativeJumpOpcode):
        return 1 + emitter.OFFSET_BYTES
    if isinstance(emitter, BlockMoveOpcode):
        return 3
    if isinstance(emitter, OpcodeWithoutOperand):
        return 1
    width = _width(entry, suffix, operand, state)
    assert width is not None
    if isinstance(emitter, TransferOpcode) and suffix is None and width == "l":
        # A bare jsr/jmp takes the absolute form in its own bank and is
        # rejected (E0346) into another one.
        if int(operand.text, 16) >> 16 != _ORIGIN >> 16:
            return None
        width = "w"
    return _sized_length(entry, width, operand)


def _resolvable(entry: Entry, suffix: str | None, operand: Operand, state: RegState) -> bool:
    """False for an unsized forward reference or link-time extern.

    The first pass cannot size either: assembly stops with E0200 or E0313
    (loud, never silent drift).
    """
    if operand.width is not None or not isinstance(entry.emitter, Opcode):
        return True
    return _width(entry, suffix, operand, state) is not None


# --------------------------------------------------------------------------
# Node level
# --------------------------------------------------------------------------


def _node_operands(entry: Entry) -> tuple[Operand, ...]:
    if entry.mode is AddressingMode.none:
        return (Operand("", None, True),)
    if _is_relative(entry):
        return (_NODE_BRANCH_TARGET,)
    if entry.mode is AddressingMode.block_move:
        return (_NUMBERS[0],)
    return _NUMBERS


def _enter_state(resolver: Resolver, state: RegState) -> Address:
    resolver.track_register_size = state.track
    resolver.a_size = 16 if state.a16 else 8
    resolver.i_size = 16 if state.i16 else 8
    resolver.set_position(_ORIGIN)
    return resolver.reloc_address


def _predicted(node: OpcodeNode, state: RegState) -> int:
    start = _enter_state(node.resolver, state)
    return node.pc_after(start).logical_value - start.logical_value


def _emitted(node: OpcodeNode, state: RegState) -> int | None:
    """Emitted length, `None` when emit rejects the combination."""
    start = _enter_state(node.resolver, state)
    try:
        return len(node.emit(start))
    except NodeError:
        return None


def _parse_opcode(program: Program, line: str) -> OpcodeNode:
    error, nodes = program.parser.parse(line, "matrix.s")
    assert error is None, f"{line!r}: {error}"
    node = nodes[-1]
    assert isinstance(node, OpcodeNode), line
    return node


def _node_case_failures(node: OpcodeNode, line: str, expected: int | None, state: RegState) -> Iterator[str]:
    emitted = _emitted(node, state)
    where = f"{line!r} [{state.ident}]"
    if expected is None:
        if emitted is not None:
            yield f"{where}: table has no encoding, emit produced {emitted} bytes"
        return
    predicted = _predicted(node, state)
    if predicted != emitted:
        yield f"{where}: pc_after advanced {predicted}, emit wrote {emitted}"
    elif emitted != expected:
        yield f"{where}: emitted {emitted} bytes, table encodes {expected}"


def _node_failures(entry: Entry) -> Iterator[str]:
    program = Program()
    for suffix, operand in product(_suffixes(entry), _node_operands(entry)):
        line = _line(entry, suffix, operand)
        node = _parse_opcode(program, line)
        for state in _STATES:
            expected = _expected_length(entry, suffix, operand, state)
            yield from _node_case_failures(node, line, expected, state)


@pytest.mark.parametrize("entry", _ENTRIES, ids=[e.ident for e in _ENTRIES])
def test_pc_after_matches_emit_length(entry: Entry) -> None:
    assert list(_node_failures(entry)) == []


# --------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Setup:
    """Source that puts the assembler in `state` before the measured body."""

    lines: str
    state: RegState

    @property
    def ident(self) -> str:
        lines = self.lines.replace("\n", ";")
        return f"{lines}-{self.state.ident}"


def _directives(a16: bool, i16: bool, track: bool) -> Setup:
    lines = f".a{16 if a16 else 8}\n.i{16 if i16 else 8}"
    return Setup(lines, RegState(a16, i16, track))


# Every A/X width via directives; tracking adds nothing to directives beyond
# the two extremes, its own surface is `rep`/`sep` (here and in the body of
# the immediate family). The node layer covers the full state product.
_SETUPS: list[Setup] = [
    *(_directives(a16, i16, False) for a16, i16 in product((False, True), repeat=2)),
    _directives(False, False, True),
    _directives(True, True, True),
    Setup("rep #0x30", RegState(True, True, True)),
    Setup("rep #0x20", RegState(True, False, True)),
    Setup("rep #0x10", RegState(False, True, True)),
    # Untracked rep changes the CPU, not the assembler's sizes.
    Setup("rep #0x30", RegState(False, False, False)),
]


@dataclass(frozen=True)
class Context:
    name: str
    render: Callable[[str, str], str]
    object_mode: bool = False
    # The setup follows the body: the body runs at power-on sizes and the
    # trailing state change must not reach back into it.
    trailing: bool = False

    def body_state(self, setup: Setup) -> RegState:
        return RegState(False, False, setup.state.track) if self.trailing else setup.state


def _top(setup: str, body: str) -> str:
    return f"*=0x008000\nstart:\n{setup}\n{body}\n"


def _alloc_at(setup: str, body: str) -> str:
    return f".alloc at 0x008000 {{\nstart:\n{setup}\n{body}\n}}\n"


def _alloc_pool(setup: str, body: str) -> str:
    return CLIENT_POOL + f".alloc routine in client {{\nstart:\n{setup}\n{body}\n}}\n"


def _alloc_entered(setup: str, body: str) -> str:
    """State set at top level, then a pooled alloc measured before placement."""
    return CLIENT_POOL + f"*=0x018000\n{setup}\nnop\n.alloc routine in client {{\nstart:\n{body}\n}}\n"


def _alloc_chained(setup: str, body: str) -> str:
    """State set by a previous alloc body, carried into the next one."""
    return (
        CLIENT_POOL + f".alloc entry in client {{\n{setup}\nnop\n}}\n.alloc routine in client {{\nstart:\n{body}\n}}\n"
    )


def _alloc_exited(setup: str, body: str) -> str:
    """State set inside an alloc body, carried into the top-level code after it."""
    return CLIENT_POOL + f".alloc entry in client {{\n{setup}\nnop\n}}\n*=0x00c000\nstart:\n{body}\n"


def _top_trailing(setup: str, body: str) -> str:
    return f"*=0x008000\nstart:\n{body}\n{setup}\nnop\n"


def _object(setup: str, body: str) -> str:
    return ".extern ext\n" + _alloc_pool(setup, body)


_CONTEXTS = [
    Context("top", _top),
    Context("alloc-at", _alloc_at),
    Context("alloc-pool", _alloc_pool),
    Context("alloc-entered", _alloc_entered),
    Context("alloc-chained", _alloc_chained),
    Context("alloc-exited", _alloc_exited),
    Context("top-trailing", _top_trailing, trailing=True),
    Context("object", _object, object_mode=True),
]

# Families: one end-to-end program per addressing mode (per context/setup).
_FAMILIES = sorted({e.mode for e in _ENTRIES}, key=lambda m: m.value)


def _e2e_operands(entry: Entry, object_mode: bool) -> tuple[Operand, ...]:
    if entry.mode is AddressingMode.none:
        return (Operand("", None, True),)
    if _is_relative(entry):
        return (_SELF_LABEL,)
    if entry.mode is AddressingMode.block_move:
        return (_NUMBERS[0],)
    labels = (_BACKWARD_LABEL, _SELF_LABEL) + ((_EXTERN,) if object_mode else ())
    return _NUMBERS + labels


@dataclass(frozen=True)
class Case:
    line: str
    expected: int


def _family_cases(mode: AddressingMode, entry_state: RegState, object_mode: bool) -> list[Case]:
    """Body lines for one family; a tracked `rep`/`sep` line resizes the lines after it."""
    cases: list[Case] = []
    running = entry_state
    for entry in (e for e in _ENTRIES if e.mode is mode):
        for suffix, operand in product(_suffixes(entry), _e2e_operands(entry, object_mode)):
            if not _resolvable(entry, suffix, operand, running):
                continue
            expected = _expected_length(entry, suffix, operand, running)
            if expected is not None:
                cases.append(Case(_line(entry, suffix, operand, len(cases)), expected))
                running = _state_after(entry, operand, running)
    return cases


def _state_after(entry: Entry, operand: Operand, state: RegState) -> RegState:
    """Register sizes after a body line: a tracked constant `rep`/`sep` flips them."""
    if not state.track or entry.mnemonic not in ("rep", "sep") or not operand.text.startswith("0x"):
        return state
    flags = int(operand.text, 16)
    wide = entry.mnemonic == "rep"
    a16 = wide if flags & 0x20 else state.a16
    i16 = wide if flags & 0x10 else state.i16
    return RegState(a16, i16, state.track)


def _opcodes(nodes: list[NodeProtocol]) -> Iterator[OpcodeNode]:
    for node in nodes:
        if isinstance(node, OpcodeNode):
            yield node
        elif isinstance(node, AllocNode):
            yield from _opcodes(node.body)


class _EmitLog:
    """Maps `id(node)` to the `(address, length)` of its `OpcodeNode.emit`."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.records: dict[int, tuple[int, int]] = {}
        original = OpcodeNode.emit

        def spy(node: OpcodeNode, current_pc: Address) -> bytes:
            out = original(node, current_pc)
            self.records[id(node)] = (current_pc.logical_value, len(out))
            return out

        monkeypatch.setattr(OpcodeNode, "emit", spy)


def _assemble(context: Context, setup: Setup, source: str) -> tuple[Program, list[NodeProtocol]]:
    program = Program()
    program.resolver.track_register_size = setup.state.track
    if context.object_mode:
        program.resolver.context.mode = AssemblyMode.OBJECT
        program.resolver.context.object_writer = ObjectWriter("matrix.o")
    error, nodes = program.parser.parse(source, "matrix.s")
    assert error is None, error
    program.resolve_labels(nodes)
    if context.object_mode:
        writer = program.resolver.context.object_writer
        assert writer is not None
        program.emit_with_relocations(nodes, writer)
    else:
        program.emit(nodes, StubWriter())
    return program, nodes


def _body_opcodes(nodes: list[NodeProtocol], count: int, trailing: bool) -> list[OpcodeNode]:
    """The measured body's opcodes: setup opcodes sit before it, or after it when trailing."""
    opcodes = list(_opcodes(nodes))
    return opcodes[:count] if trailing else opcodes[len(opcodes) - count :]


def _e2e_failures(program: Program, body: list[OpcodeNode], cases: list[Case], log: _EmitLog) -> Iterator[str]:
    for k, (case, node) in enumerate(zip(cases, body, strict=True)):
        address, length = log.records[id(node)]
        label = label_address(program.resolver, f"L{k}")
        if label != address + length:
            yield f"{case.line!r}: emitted {length} bytes at {address:#x}, next label bound at {label:#x}"
            return  # every later label inherits the drift
        if length != case.expected:
            yield f"{case.line!r}: emitted {length} bytes, table encodes {case.expected}"


@pytest.mark.parametrize("context", _CONTEXTS, ids=[c.name for c in _CONTEXTS])
@pytest.mark.parametrize("setup", _SETUPS, ids=[s.ident for s in _SETUPS])
@pytest.mark.parametrize("mode", _FAMILIES, ids=[m.name for m in _FAMILIES])
def test_labels_follow_emitted_bytes(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    context: Context,
    setup: Setup,
    mode: AddressingMode,
) -> None:
    # Suffixes that disagree with M/X warn by design; formatting thousands
    # of those diagnostics dominates the runtime and proves nothing here.
    caplog.set_level(logging.ERROR, logger="a816")
    cases = _family_cases(mode, context.body_state(setup), context.object_mode)
    body = "\n".join(f"{case.line}\nL{k}:" for k, case in enumerate(cases))
    log = _EmitLog(monkeypatch)
    program, nodes = _assemble(context, setup, context.render(setup.lines, body))
    measured = _body_opcodes(nodes, len(cases), context.trailing)
    assert list(_e2e_failures(program, measured, cases, log)) == []
