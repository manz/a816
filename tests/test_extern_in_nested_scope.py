"""`.extern` declared inside an alloc body or block, not only at root.

A nested scope handed its lookups to its parent without checking its own
externs, and the root never saw them: `E0200 'helper' is not defined`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.module_builder import ModuleBuilder

LIB = ".alloc lib_code at 0x008000 {\nhelper:\n    rts\n}\n"
EXPECTED = b"\x20\x00\x80\x60"  # jsr $8000 / rts


def _user_code(root: Path, user: str) -> bytes:
    (root / "lib.s").write_text(LIB, encoding="utf-8")
    (root / "user.s").write_text(user, encoding="utf-8")
    main = root / "main.s"
    main.write_text('.import "lib"\n.import "user"\n', encoding="utf-8")
    obj = ModuleBuilder(module_paths=[root], include_paths=[root], output_dir=root / "obj").build(main)
    return next(section.code for section in obj.sections if section.placed_base == 0x009000)


@pytest.mark.parametrize(
    "user",
    [
        pytest.param(".extern helper\n.alloc user_code at 0x009000 {\n    jsr.w helper\n    rts\n}\n", id="root"),
        pytest.param(".alloc user_code at 0x009000 {\n    .extern helper\n    jsr.w helper\n    rts\n}\n", id="alloc"),
        pytest.param(
            ".alloc user_code at 0x009000 {\n    {\n        .extern helper\n        jsr.w helper\n    }\n    rts\n}\n",
            id="block",
        ),
    ],
)
def test_an_extern_resolves_wherever_it_is_declared(tmp_path: Path, user: str) -> None:
    assert _user_code(tmp_path, user) == EXPECTED
