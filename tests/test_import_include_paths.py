"""An imported module's `.include` searches the build's include paths.

Imported modules were parsed without them, so only the module's own
directory was searched: a file found under an include path from the
entry file was missing from a module (Feda).
"""

from __future__ import annotations

from pathlib import Path

from a816.module_builder import build_with_imports


def test_an_imported_module_includes_from_the_include_paths(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "gen").mkdir()
    (tmp_path / "gen" / "data.s").write_text("VALUE = 0x42\n", encoding="utf-8")
    (tmp_path / "src" / "menu.s").write_text(
        '.include "data.s"\n.alloc menu at 0x008000 {\n    .db VALUE\n}\n', encoding="utf-8"
    )
    main = tmp_path / "src" / "main.s"
    main.write_text('.import "menu"\n', encoding="utf-8")

    result = build_with_imports(
        main,
        tmp_path / "out.ips",
        module_paths=[tmp_path / "src"],
        include_paths=[tmp_path / "gen"],
        output_dir=tmp_path / "obj",
    )

    assert result.exit_code == 0
