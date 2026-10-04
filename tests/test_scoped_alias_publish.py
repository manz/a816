"""`=` aliases inside a named `.scope` publish as `Scope.name`.

In object mode an alias whose RHS is a label (same module or imported)
defers to the linker. It must still be reachable as `sc.fd`, both from
the declaring module and from importers.
"""

from __future__ import annotations

from pathlib import Path

from a816.module_builder import BuildResult, build_with_imports
from a816.object_file import ObjectFile
from a816.program import Program

_LO_MAP = ".map identifier=1 bank_range=0x00, 0x6f addr_range=0x8000, 0xffff mask=0x8000\n"

_STATE = (
    ".map identifier=3 bank_range=0x70, 0x70 addr_range=0x0000, 0x7fff mask=0x8000 writable=1\n"
    ".struct S {\n    byte a\n    word b\n}\n"
    ".pool st { bss  range 0x707100 0x707102  strategy order }\n"
    ".reserve thing as S in st\n"
)

_LIB = _LO_MAP + "*=0x009000\nlibfn:\n    rts\n"


def _build(root: Path, main: str, modules: dict[str, str] | None = None) -> BuildResult:
    for name, text in (modules or {}).items():
        (root / f"{name}.s").write_text(text, encoding="utf-8")
    (root / "main.s").write_text(main, encoding="utf-8")
    return build_with_imports(
        main_source=root / "main.s",
        output_file=root / "out.ips",
        module_paths=[root],
        output_dir=root / "obj",
    )


def _main_object(root: Path) -> ObjectFile:
    return ObjectFile.from_file(str(root / "obj" / "__main__.o"))


def _ips(root: Path) -> bytes:
    return (root / "out.ips").read_bytes()


def _lda_long(addr: int) -> bytes:
    return b"\xaf" + addr.to_bytes(3, "little")


def test_scoped_alias_to_same_module_label_builds(tmp_path: Path) -> None:
    main = _LO_MAP + "*=0x008000\nhere:\n    nop\n.scope sc {\n    fd = here\n}\n    lda.l sc.fd\n"
    result = _build(tmp_path, main)
    assert result.exit_code == 0, result.diagnostics


def test_scoped_alias_to_same_module_label_links(tmp_path: Path) -> None:
    main = _LO_MAP + "*=0x008000\nhere:\n    nop\n.scope sc {\n    fd = here\n}\n    lda.l sc.fd\n"
    _build(tmp_path, main)
    assert _lda_long(0x008000) in _ips(tmp_path)


def test_scoped_alias_to_imported_label_links(tmp_path: Path) -> None:
    main = _LO_MAP + '.import "lib"\n*=0x008000\n.scope sc {\n    fd = libfn\n}\n    lda.l sc.fd\n'
    _build(tmp_path, main, {"lib": _LIB})
    assert _lda_long(0x009000) in _ips(tmp_path)


def test_scoped_assign_to_extern_links(tmp_path: Path) -> None:
    main = _LO_MAP + '.import "lib"\n.extern libfn\n*=0x008000\n.scope sc {\n    fd := libfn + 2\n}\n    lda.l sc.fd\n'
    _build(tmp_path, main, {"lib": _LIB})
    assert _lda_long(0x009002) in _ips(tmp_path)


def test_scoped_alias_to_imported_label_plus_offset_links(tmp_path: Path) -> None:
    main = _LO_MAP + '.import "lib"\n*=0x008000\n.scope sc {\n    fd = libfn + 1\n}\n    lda.l sc.fd\n'
    _build(tmp_path, main, {"lib": _LIB})
    assert _lda_long(0x009001) in _ips(tmp_path)


def test_scoped_alias_to_imported_reserve_field_links(tmp_path: Path) -> None:
    main = _LO_MAP + '.import "state"\n*=0x008000\n.scope sc {\n    fd = thing.b\n}\n    lda.l sc.fd\n'
    _build(tmp_path, main, {"state": _STATE})
    assert _lda_long(0x707101) in _ips(tmp_path)


def test_nested_named_scope_alias_publishes_full_path(tmp_path: Path) -> None:
    main = (
        _LO_MAP
        + "*=0x008000\nhere:\n    nop\n.scope a {\n    .scope b {\n        x = here\n    }\n}\n    lda.l a.b.x\n"
    )
    _build(tmp_path, main)
    assert _lda_long(0x008000) in _ips(tmp_path)


def test_scoped_alias_object_export_is_dotted(tmp_path: Path) -> None:
    main = _LO_MAP + "*=0x008000\nhere:\n    nop\n.scope sc {\n    fd = here\n}\n"
    _build(tmp_path, main)
    obj = _main_object(tmp_path)
    assert ("sc.fd", "here") in obj.aliases


def test_scoped_alias_reaches_an_importer(tmp_path: Path) -> None:
    owner = _LO_MAP + '.import "lib"\n.scope sc {\n    fd = libfn\n}\n'
    main = _LO_MAP + '.import "owner"\n*=0x008000\n    lda.l sc.fd\n'
    _build(tmp_path, main, {"lib": _LIB, "owner": owner})
    assert _lda_long(0x009000) in _ips(tmp_path)


def test_anonymous_scope_alias_does_not_publish(tmp_path: Path) -> None:
    main = _LO_MAP + "*=0x008000\nhere:\n    nop\n{\n    fd = here\n}\n"
    _build(tmp_path, main)
    obj = _main_object(tmp_path)
    assert all(name != "fd" for name, _ in obj.aliases)


def test_private_scoped_alias_is_not_reachable_outside_its_scope(tmp_path: Path) -> None:
    main = _LO_MAP + "*=0x008000\nhere:\n    nop\n.scope sc {\n    _fd = here\n}\n    lda.l sc._fd\n"
    result = _build(tmp_path, main)
    assert result.exit_code == 1


def test_scoped_alias_to_label_in_direct_mode() -> None:
    program = Program()
    error, nodes = program.parser.parse("*=0x008000\nhere:\n    nop\n.scope sc {\n    fd = here\n}\n    lda.l sc.fd\n")
    assert error is None, error
    program.resolve_labels(nodes)
    assert program.resolver.scopes[0].symbols["sc.fd"] == 0x008000
