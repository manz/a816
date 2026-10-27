"""Register-size state must be the same when labels bind and when bytes emit.

`pc_after` (label binding) and `emit` both size M/X-dependent opcodes from
`resolver.a_size` / `i_size`. Any path where the two walks see a different
state drifts every later label off its bytes without an error.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from a816.context import AssemblyMode
from a816.module_builder import build_with_imports
from a816.program import Program
from a816.writers import ObjectWriter
from tests import CLIENT_POOL, label_address
from tests.ips_helpers import parse_ips_records


def _assemble(src: str, track: bool = False) -> tuple[bytes, dict[str, int]]:
    """The bytes `src` links to and its linked labels; `track` turns on rep/sep size tracking."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "main.s").write_text(src)
        out = tmp / "out.ips"
        result = build_with_imports(
            tmp / "main.s",
            out,
            output_dir=tmp / "obj",
            use_a816_toml=False,
            use_cache=False,
            experimental=["track_register_size"] if track else None,
        )
        assert result.exit_code == 0
        data = b"".join(block for _, block in parse_ips_records(out.read_bytes()))
    return data, result.symbol_map


def _assemble_object(src: str) -> tuple[bytes, Program]:
    program = Program()
    program.resolver.track_register_size = True
    program.resolver.context.mode = AssemblyMode.OBJECT
    writer = ObjectWriter("stream.o")
    program.resolver.context.object_writer = writer
    error, nodes = program.parser.parse(src, "stream.s")
    assert error is None, error
    program.resolve_labels(nodes)
    program.emit_with_relocations(nodes, writer)
    return b"".join(section.code for section in writer.sections), program


_TRAILING_REP = "*=0x008000\nlda #0x12\nafter:\nrep #0x20\n"


def test_trailing_tracked_rep_does_not_leak_into_emission() -> None:
    # The `lda` runs before the `rep`, at the power-on 8-bit A: the label
    # passes end with A=16 and emission must still start at A=8.
    data, labels = _assemble(_TRAILING_REP, track=True)
    assert (data, labels["after"]) == (b"\xa9\x12\xc2\x20", 0x008002)


def test_trailing_tracked_rep_does_not_leak_into_object_emission() -> None:
    data, program = _assemble_object(_TRAILING_REP)
    assert (data, label_address(program.resolver, "after")) == (b"\xa9\x12\xc2\x20", 0x008002)


def test_trailing_tracked_rep_does_not_leak_into_alloc_label_pass() -> None:
    # Alloc bodies rebind their labels on pass 2, which must not start
    # from the A=16 pass 1 ended with.
    src = CLIENT_POOL + ".alloc r in client {\nlda #0x12\ninside:\nnop\n}\n*=0x018000\nrep #0x20\n"
    data, labels = _assemble(src, track=True)
    assert (data, labels["inside"]) == (b"\xa9\x12\xea\xc2\x20", 0x008002)


def test_reused_program_sizes_pool_slots_from_power_on_state(tmp_path: Path) -> None:
    # The first build leaves A=16 behind; the next build's pass 1, which
    # measures the pool slots, must not inherit it.
    program = Program()
    (tmp_path / "first.s").write_text("*=0x018000\n.a16\nnop\n")
    (tmp_path / "second.s").write_text(
        CLIENT_POOL + ".alloc one in client {\nlda #0x12\n}\n.alloc two in client {\nnop\n}\n"
    )
    assert program.assemble_as_object(str(tmp_path / "first.s"), tmp_path / "first.o") == 0
    assert program.assemble_as_object(str(tmp_path / "second.s"), tmp_path / "second.o") == 0
    assert label_address(program.resolver, "two") == 0x008002


def test_top_level_size_directive_sizes_labels() -> None:
    # `.a16` widens the emitted immediate; the label must follow it.
    data, labels = _assemble("*=0x008000\n.a16\nlda #0x12\nafter:\n")
    assert (data, labels["after"]) == (b"\xa9\x12\x00", 0x008003)


def test_alloc_body_inherits_size_set_before_it() -> None:
    src = CLIENT_POOL + "*=0x018000\n.a16\nnop\n.alloc r in client {\nlda #0x12\nafter:\nnop\n}\n"
    data, labels = _assemble(src)
    assert (data, labels["after"]) == (b"\xea\xa9\x12\x00\xea", 0x008003)


def test_code_after_alloc_inherits_size_set_inside_it() -> None:
    src = CLIENT_POOL + ".alloc r in client {\n.a16\nnop\n}\n*=0x018000\nlda #0x12\nafter:\n"
    data, labels = _assemble(src)
    assert (data, labels["after"]) == (b"\xea\xa9\x12\x00", 0x018003)
