"""A module recompiled on a warm build names its includes as a cold build does.

ff4 (rc1): `.include "src/rolling_state.i"` (found through the `.` include
path) in three modules, holding a `.reserve`. Cold, each module's compile
reused its discovery parse, made with the include paths as given: the header
was `src/rolling_state.i` everywhere, and the identical reserves merged. Warm,
a module recompiled because an import changed was never discovery-parsed and
parsed itself with the resolved, absolute paths: one object said
`/abs/.../src/rolling_state.i`, and the link failed E0400 on the reserve.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.module_builder import build_with_imports

HEADER = ".pool ram { bss  range 0x7e2000 0x7e2fff }\n.reserve scratch 2 in ram\n"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "state.i").write_text(HEADER, encoding="utf-8")
    (src / "leaf.s").write_text(".alloc leaf at 0x008000 {\n    rts\n}\n", encoding="utf-8")
    (src / "a.s").write_text('.include "src/state.i"\n', encoding="utf-8")
    (src / "b.s").write_text('.import "leaf"\n.include "src/state.i"\n', encoding="utf-8")
    (src / "main.s").write_text('.import "a"\n.import "b"\n', encoding="utf-8")
    return tmp_path


def _build(obj: str) -> int:
    return build_with_imports(
        Path("src/main.s"),
        Path("out.ips"),
        module_paths=[Path("src")],
        include_paths=[Path("src"), Path(".")],
        output_dir=Path(obj),
    ).exit_code


def _touch_leaf(project: Path) -> None:
    with (project / "src" / "leaf.s").open("a", encoding="utf-8") as leaf:
        leaf.write("; edited\n")


def test_a_warm_rebuild_after_an_import_changes_links(project: Path) -> None:
    _build("obj")
    _touch_leaf(project)

    assert _build("obj") == 0


def test_a_recompiled_object_matches_a_cold_one(project: Path) -> None:
    """`b` is recompiled because `leaf` changed; its object must be the cold one."""
    _build("obj")
    _touch_leaf(project)
    _build("obj")
    _build("cold")

    assert (project / "obj" / "b.o").read_bytes() == (project / "cold" / "b.o").read_bytes()
