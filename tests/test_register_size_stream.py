"""Register-size state must be the same when labels bind and when bytes emit.

`pc_after` (label binding) and `emit` both size M/X-dependent opcodes from
`resolver.a_size` / `i_size`. Any path where the two walks see a different
state drifts every later label off its bytes without an error.
"""

from __future__ import annotations

from a816.program import Program
from tests import StubWriter


def _assemble(src: str, track: bool = False) -> tuple[bytes, Program]:
    program = Program()
    program.resolver.track_register_size = track
    writer = StubWriter()
    program.assemble_string_with_emitter(src, "stream.s", writer)
    return b"".join(writer.data), program


def _label(program: Program, name: str) -> int:
    return next(scope.labels[name] for scope in program.resolver.scopes if name in scope.labels)


def test_trailing_tracked_rep_does_not_leak_into_emission() -> None:
    # The resolve passes end with A=16; emission must still start at A=8.
    data, program = _assemble("*=0x008000\nlda #0x12\nafter:\nrep #0x20\n", track=True)
    assert (data, _label(program, "after")) == (b"\xa9\x12\xc2\x20", 0x008002)


def test_top_level_size_directive_sizes_labels() -> None:
    # `.a16` widens the emitted immediate; the label must follow it.
    data, program = _assemble("*=0x008000\n.a16\nlda #0x12\nafter:\n")
    assert (data, _label(program, "after")) == (b"\xa9\x12\x00", 0x008003)


_POOL = ".pool client {\n range 0x008000 0x00FFEF\n strategy order\n}\n"


def test_alloc_body_inherits_size_set_before_it() -> None:
    src = _POOL + "*=0x018000\n.a16\nnop\n.alloc r in client {\nlda #0x12\nafter:\nnop\n}\n"
    data, program = _assemble(src)
    assert (data, _label(program, "after")) == (b"\xea\xa9\x12\x00\xea", 0x008003)


def test_code_after_alloc_inherits_size_set_inside_it() -> None:
    src = _POOL + ".alloc r in client {\n.a16\nnop\n}\n*=0x018000\nlda #0x12\nafter:\n"
    data, program = _assemble(src)
    assert (data, _label(program, "after")) == (b"\xea\xa9\x12\x00", 0x018003)
