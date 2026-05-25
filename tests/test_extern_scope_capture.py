"""`.extern Foo` captures the whole `Foo.*` namespace.

A `.scope Foo { bar: ... }` provider exports its members as `Foo.bar`
GLOBAL (per `Resolver._export_name`). Consumers that need many members
shouldn't have to spell out `.extern Foo.bar`, `.extern Foo.baz`, ... —
one `.extern Foo` lets every dotted reference under that prefix flow
through the relocation pipeline and resolve at link time against the
provider's dotted exports.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from a816.module_builder import build_with_imports
from a816.program import Program

_PROVIDER = """
.alloc payload_block at 0x208000 {
    .scope payload {
        first:
            .db 0xAA
        second:
            .db 0xBB
    }
}
"""


def test_extern_scope_resolves_all_dotted_members() -> None:
    """Provider exports `payload.first` and `payload.second` GLOBAL.
    Consumer with `.extern payload` references both as `payload.first`
    and `payload.second`; both should resolve at link time."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "provider.s").write_text(_PROVIDER, encoding="utf-8")
        (tmp / "consumer.s").write_text(
            ".extern payload\n"
            ".alloc consumer_block at 0x208100 {\n"
            "    lda.l payload.first\n"
            "    sta 0x00\n"
            "    lda.l payload.second\n"
            "    sta 0x02\n"
            "}\n",
            encoding="utf-8",
        )
        # Pre-build provider.
        prog0 = Program()
        prog0.add_include_path(tmp)
        prog0.add_module_path(tmp)
        assert prog0.assemble_as_object(str(tmp / "provider.s"), tmp / "provider.o") == 0
        # Consumer object compile + link via main.
        (tmp / "main.s").write_text(
            '.import "provider"\n.import "consumer"\n',
            encoding="utf-8",
        )
        result = build_with_imports(
            main_source=tmp / "main.s",
            output_file=tmp / "out.ips",
            output_format="ips",
            module_paths=[tmp],
            output_dir=tmp,
            include_paths=[tmp],
        )
        assert result.exit_code == 0, f"build failed: {result.diagnostics}"
