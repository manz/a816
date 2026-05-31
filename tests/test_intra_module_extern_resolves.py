"""Underscore-prefixed labels are PRIVATE by convention (per
`_classify_object_symbol`: leading `_` → LOCAL). Cross-module
references to them are unsupported — the only legitimate `.extern`
target is a public (non-underscore) name. This pins both directions:

1. Non-underscore label inside an alloc body resolves an `.extern`
   in the same compile unit (provider/consumer in two `.include`d
   fragments under one main).
2. Underscore label CAN'T be `.extern`ed from outside its defining
   module — the convention is enforced by the export classifier, so
   the linker raises `unresolved symbol '_foo'`. Source code that
   relies on private symbols cross-module needs to either drop the
   underscore or use a re-export shim.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from a816.object_file import ObjectFile, SymbolType
from a816.program import Program

_POOL = ".pool p { range 0x208000 0x20FFFF\n    strategy order\n}\n"


def test_public_label_in_alloc_body_visible_to_extern_in_same_module() -> None:
    """Non-underscore label inside `.alloc helpers in p { foo: ... }`
    exports as CODE; sibling `.extern foo` resolves at link time."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "provider.s").write_text(
            _POOL + ".alloc helpers in p {\nfoo:\n    rts\n}\n",
            encoding="utf-8",
        )
        (tmp / "consumer.s").write_text(
            ".extern foo\n.alloc client in p {\n    jsr.l foo\n    rts\n}\n",
            encoding="utf-8",
        )
        (tmp / "main.s").write_text(
            '.include "provider.s"\n.include "consumer.s"\n',
            encoding="utf-8",
        )
        program = Program()
        program.add_include_path(tmp)
        program.add_module_path(tmp)
        obj_path = tmp / "main.o"
        assert program.assemble_as_object(str(tmp / "main.s"), obj_path) == 0
        obj = ObjectFile.from_file(str(obj_path))
        globals_ = [n for n, _, st, _ in obj.symbols if st == SymbolType.GLOBAL]
        assert "foo" in globals_, f"`foo` should export as GLOBAL, got {globals_}"


def test_underscore_label_export_classification_stays_local() -> None:
    """Underscore-prefixed labels classify as LOCAL on export — that's
    the privacy convention's actual teeth. They also export under a
    per-alloc-mangled name (`__sc<idx>___foo`) so two sibling allocs both
    declaring `_foo` stay distinct; the bare private name never leaks as a
    GLOBAL. Modules wanting durable cross-module references must drop the
    underscore."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "mod.s").write_text(
            _POOL + ".alloc helpers in p {\n_foo:\n    rts\n}\n",
            encoding="utf-8",
        )
        program = Program()
        program.add_include_path(tmp)
        program.add_module_path(tmp)
        obj_path = tmp / "mod.o"
        assert program.assemble_as_object(str(tmp / "mod.s"), obj_path) == 0
        obj = ObjectFile.from_file(str(obj_path))
        kinds = {n: st for n, _, st, _ in obj.symbols}
        # Exported per-alloc-mangled and LOCAL; no bare `_foo` GLOBAL leaks.
        private = {n: st for n, st in kinds.items() if n.endswith("_foo")}
        assert private == {"__sc1___foo": SymbolType.LOCAL}, kinds
