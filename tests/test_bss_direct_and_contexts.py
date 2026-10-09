"""A reservation made directly in a pool lives in every one of its contexts.

The allocator placed a direct reservation and a context's reservation at the
same address (each at the range start), then the linker's overlap check
rejected its own layout with E0406: a pool could hold direct reservations or
contexts, never both. Found by the bss torture test (rc2).
"""

from __future__ import annotations

from pathlib import Path

from a816.module_builder import build_with_imports

POOL = ".pool ram { bss  range 0x7e2000 0x7e2fff  contexts field, battle }\n"


def _build(tmp_path: Path, files: dict[str, str]) -> tuple[int, dict[str, int]]:
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    result = build_with_imports(
        tmp_path / "main.s",
        tmp_path / "out.ips",
        module_paths=[tmp_path],
        include_paths=[tmp_path],
        output_dir=tmp_path / "obj",
    )
    return result.exit_code, result.symbol_map


def _spans(symbols: dict[str, int], sizes: dict[str, int]) -> dict[str, range]:
    return {name: range(symbols[name], symbols[name] + size) for name, size in sizes.items()}


SOURCE = POOL + (
    ".reserve shared 0x20 in ram\n.reserve field_buf 0x40 in ram.field\n.reserve battle_buf 0x40 in ram.battle\n"
)


def test_a_direct_reservation_and_contexts_build(tmp_path: Path) -> None:
    code, _ = _build(tmp_path, {"main.s": SOURCE})

    assert code == 0


def test_the_direct_reservation_overlaps_no_context(tmp_path: Path) -> None:
    _, symbols = _build(tmp_path, {"main.s": SOURCE})
    spans = _spans(symbols, {"shared": 0x20, "field_buf": 0x40, "battle_buf": 0x40})

    assert not set(spans["shared"]) & (set(spans["field_buf"]) | set(spans["battle_buf"]))


def test_contexts_still_share_with_each_other(tmp_path: Path) -> None:
    _, symbols = _build(tmp_path, {"main.s": SOURCE})

    assert symbols["field_buf"] == symbols["battle_buf"]


def test_across_modules(tmp_path: Path) -> None:
    files = {
        "ram.i": POOL,
        "a.s": '.include "ram.i"\n.reserve shared 0x20 in ram\n',
        "b.s": '.include "ram.i"\n.reserve field_buf 0x40 in ram.field\n',
        "main.s": '.import "a"\n.import "b"\n',
    }
    code, symbols = _build(tmp_path, files)
    spans = _spans(symbols, {"shared": 0x20, "field_buf": 0x40})

    assert (code, bool(set(spans["shared"]) & set(spans["field_buf"]))) == (0, False)
