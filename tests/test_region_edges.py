"""A block may end exactly where its region's window (or the ROM) ends."""

from pathlib import Path

import pytest

from a816.cpu.mapping import BsnesRegion, Bus, EndAddress
from a816.module_builder import BuildResult, build_with_imports

_HIROM = 'board = "SHVC-1J0N-20"\nrom_size = 0x80000\n'
_VECTORS = ".alloc vecs at 0x00FFE0 size 0x20 {\n    .dw 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0\n}\n"


def _build(tmp_path: Path, source: str, output_format: str = "sfc") -> BuildResult:
    (tmp_path / "a816.toml").write_text(_HIROM, encoding="utf-8")
    (tmp_path / "main.s").write_text(source, encoding="utf-8")
    out = tmp_path / f"out.{output_format}"
    return build_with_imports(tmp_path / "main.s", out, output_format, output_dir=tmp_path / "obj", use_cache=False)


def _rom(tmp_path: Path, source: str) -> bytes:
    result = _build(tmp_path, source)
    assert result.exit_code == 0, result.diagnostics
    return (tmp_path / "out.sfc").read_bytes()


def test_an_alloc_may_end_at_its_window_edge(tmp_path: Path) -> None:
    rom = _rom(tmp_path, _VECTORS.replace(".dw 0, 0", ".dw 0x1234, 0"))
    assert rom[0xFFE0:0xFFE2] == b"\x34\x12"


def test_an_alloc_may_end_at_the_last_rom_byte(tmp_path: Path) -> None:
    rom = _rom(tmp_path, ".alloc rom_pad at 0xC7FFFF size 1 {\n    .db 0xAA\n}\n")
    assert rom[-1] == 0xAA


def test_a_label_after_the_last_byte_runs_on_linearly(tmp_path: Path) -> None:
    source = _VECTORS.replace("}\n", "end_of_vectors:\n}\n") + '.assert end_of_vectors == 0x010000, "runs on"\n'
    assert _rom(tmp_path, source)[0xFFE0:0x10000] == bytes(0x20)


@pytest.mark.parametrize(
    ("pool", "start", "body"),
    [
        ("0x00FFE0 0x00FFFF", "0x00FFE0", ".dw 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0"),
        ("0xC7FFFF 0xC7FFFF", "0xC7FFFF", ".db 0xAA"),
    ],
)
def test_a_cross_bank_alloc_may_end_at_the_edge(tmp_path: Path, pool: str, start: str, body: str) -> None:
    source = f".pool edge {{\n    range {pool}\n}}\n.alloc block at {start} in edge cross_bank {{\n    {body}\n}}\n"
    assert _build(tmp_path, source).exit_code == 0


def test_running_past_the_window_edge_still_fails(tmp_path: Path) -> None:
    source = ".alloc vecs at 0x00FFFE {\n    .dw 0\n    .db 1\n}\n"
    assert _build(tmp_path, source).exit_code != 0


def _end_address() -> EndAddress:
    bus = Bus()
    bus.map_region("rom", BsnesRegion([(0x00, 0x00)], [(0x8000, 0xFFFF)], 0x8000, 0, 0x8000, False))
    end = bus.get_address(0x00FFFF) + 1
    assert isinstance(end, EndAddress)
    return end


def test_end_address_is_one_past_the_last_byte() -> None:
    assert _end_address().physical == 0x8000


def test_end_address_does_not_move_by_zero() -> None:
    end = _end_address()
    assert end + 0 is end


def test_nothing_fits_past_an_end_address() -> None:
    end = _end_address()
    with pytest.raises(ValueError, match=r"physical \$008000 is not reachable"):
        end + 1
