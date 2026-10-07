"""A module's `name := <expression over a runtime name>` stays its own.

`font_ptr := target + 0x40` (`target` an `.extern`) built alone but failed in
every importer: the import inliner evaluated the `:=` there, where `target`
is not placed yet (E0210). The importer now takes `font_ptr` as an extern
and the owner's object supplies it, as typed views already did.
"""

from __future__ import annotations

from pathlib import Path

from a816.module_builder import ModuleBuilder

FONT = ".extern target\nfont_ptr := target + 0x40\n"
MAIN = (
    ".map identifier=1 bank_range=0xc0, 0xfd addr_range=0x0000, 0xffff mask=0x10000 mirror_bank_range=0x40, 0x7d\n"
    ".pool code { range 0xc10000 0xc1ffff  strategy order }\n"
    '.import "font"\n'
    ".alloc target at 0xc12000 in code {\n    .db 1, 2\n}\n"
    ".alloc user at 0xc10000 in code {\n    lda.l font_ptr\n}\n"
)


def test_an_importer_reads_the_owners_bind(tmp_path: Path) -> None:
    (tmp_path / "font.s").write_text(FONT, encoding="utf-8")
    main = tmp_path / "main.s"
    main.write_text(MAIN, encoding="utf-8")

    obj = ModuleBuilder(module_paths=[tmp_path], include_paths=[tmp_path], output_dir=tmp_path / "obj").build(main)
    user = next(section.code for section in obj.sections if section.placed_base == 0xC10000)

    assert user == b"\xaf\x40\x20\xc1"  # lda.l target + 0x40
