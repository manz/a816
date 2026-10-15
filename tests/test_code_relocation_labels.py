"""Labels after `@= ADDR` count from ADDR, forward ones included.

Bahamut Lagoon (1.1.2) assembles room scripts as raw images whose words are
room-relative. With `*= 0x008000` then `@= B`, a forward label took the ROM
address: `@= 0x0000` gave `here` = 0x8004, `@= 0x0001` gave 0x8005. Only a
WRAM base (0x7E / 0x7F, not ROM-mapped) came out right. Advancing the PC
walked the bus, folding a ROM-mapped ADDR through the ROM window.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from a816.module_builder import build_with_imports

_BODY = "start:\n    .dw start\n    .dw here\nhere:\n"


def _words(tmp_path: Path, source: str, count: int) -> tuple[int, ...]:
    (tmp_path / "main.s").write_text(source, encoding="utf-8")
    result = build_with_imports(
        main_source=tmp_path / "main.s",
        output_file=tmp_path / "out.sfc",
        output_format="sfc",
        output_dir=tmp_path / "obj",
    )
    assert result.exit_code == 0, result.diagnostics
    return struct.unpack_from(f"<{count}H", (tmp_path / "out.sfc").read_bytes(), 0)


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        (0x000000, (0x0000, 0x0004)),
        (0x000001, (0x0001, 0x0005)),
        (0x010000, (0x0000, 0x0004)),
        (0x7F0000, (0x0000, 0x0004)),
    ],
)
def test_forward_and_backward_labels_count_from_the_relocation(
    tmp_path: Path, base: int, expected: tuple[int, int]
) -> None:
    assert _words(tmp_path, f"*= 0x008000\n@= {base:#08x}\n{_BODY}", 2) == expected


@pytest.mark.parametrize("base", [0x000100, 0x7F0100])
def test_a_branch_after_the_relocation_keeps_its_offset(tmp_path: Path, base: int) -> None:
    """`bra` over two bytes: offset 2 whether `@=` names ROM or WRAM (which
    has no ROM offset; the linker, blind to `@=`, used the placement)."""
    source = f"*= 0x008000\n@= {base:#08x}\n    bra skip\n    .dw 0\nskip:\n    .dw skip\n"

    assert _words(tmp_path, source, 3) == (0x0280, 0x0000, (base + 4) & 0xFFFF)


_IN_ALLOC = "    @= 0x7F0000\nstart:\n    .dw start\n    .dw here\nhere:\n"


@pytest.mark.parametrize(
    "opening",
    [".alloc at 0x008000 {\n", ".pool code { range 0x008000 0x00ffff }\n.alloc room in code {\n"],
)
def test_code_relocation_inside_an_alloc_body(tmp_path: Path, opening: str) -> None:
    """Bahamut Lagoon: `@=` in an alloc body was E0337, the body sized from
    the `@=` address (`0x7F0002 - 0x8000`, an 8 MB slot)."""
    assert _words(tmp_path, f"{opening}{_IN_ALLOC}}}\n", 2) == (0x0000, 0x0004)


def test_bytes_before_the_relocation_count_in_the_body_size(tmp_path: Path) -> None:
    source = (
        ".pool code { range 0x008000 0x00ffff }\n"
        ".alloc room in code {\n    .dw 0xAAAA\n    @= 0x7F0000\nloop:\n    bra loop\n}\n"
        ".alloc after in code {\n    .dw 0xBBBB\n}\n"
    )

    assert _words(tmp_path, source, 3) == (0xAAAA, 0xFE80, 0xBBBB)
