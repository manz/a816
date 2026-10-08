"""The LSP's struct lookup parses an imported module with the include paths.

It parsed with none, so a module whose `.include` is found only through an
include path failed to parse, and its struct was not found.
"""

from __future__ import annotations

from pathlib import Path

from a816.lsp.document import A816Document


def test_a_struct_after_an_include_path_include_is_found(tmp_path: Path) -> None:
    (tmp_path / "gen").mkdir()
    (tmp_path / "gen" / "data.i").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "player.s").write_text(
        '.include "data.i"\n.struct Player {\n    byte hp\n}\n', encoding="utf-8"
    )
    main = tmp_path / "src" / "main.s"
    content = '.import "player"\n'
    main.write_text(content, encoding="utf-8")

    document = A816Document(main.as_uri(), content, include_paths=[tmp_path / "src", tmp_path / "gen"])

    assert [node.name for node in document._iter_struct_nodes("Player")] == ["Player"]
