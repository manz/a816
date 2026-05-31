"""Label-to-label differences inside a relocatable `.alloc ... in <pool>`.

`lbl - base`, where both labels live in the same pool-allocated body, is a
link-invariant constant (both relocate by the same base). Two regressions hid
it:

1. The relocation/alias renamer mangled `AllocBodyScope` labels to their
   `__sc<idx>__` form, but those labels *export* under their bare name - so
   the relocation referenced a name absent from the linker's symbol map and
   folded to 0 (or raised at link).
2. A single-object build skipped the linker entirely, so the expression
   relocation that carries the delta never ran and the `.dw` shipped its
   placeholder 0.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from a816.linker import Linker
from a816.module_builder import _object_needs_linking, build_with_imports
from a816.object_file import ObjectFile
from a816.program import Program

# Low-ROM pool so no `.map` is needed to translate the body's bus address.
_POOL = """
.pool data { range 0x008000 0x00ffff strategy order }
"""

_INLINE_BODY = """
.alloc blob in data {
base:
    .db 1, 2, 3, 4, 5
lbl:
    .dw ( lbl - base )
    .db 0
}
"""

_ALIAS_BODY = """
OFF = lbl - base
.alloc blob in data {
base:
    .db 1, 2, 3, 4, 5
lbl:
    .dw OFF
    .db 0
}
"""

# base..lbl span five `.db` bytes, so `lbl - base` == 5; the `.dw` lands it
# little-endian (05 00) right after the five data bytes, then a trailing 0x00.
_EXPECTED = b"\x01\x02\x03\x04\x05\x05\x00\x00"


def _compile_and_link(src: str, tmpdir: str) -> ObjectFile:
    asm_file = Path(tmpdir) / "blob.s"
    asm_file.write_text(_POOL + src)
    obj_file = Path(tmpdir) / "blob.o"
    assert Program().assemble_as_object(str(asm_file), obj_file) == 0
    return Linker([ObjectFile.from_file(str(obj_file))]).link(base_address=0x8000)


def test_pool_inline_label_diff_resolves() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        linked = _compile_and_link(_INLINE_BODY, tmpdir)
        assert linked.sections[0].code == _EXPECTED


def test_pool_alias_label_diff_resolves() -> None:
    """`OFF = lbl - base` then `.dw OFF` must yield the same delta as inline."""
    with tempfile.TemporaryDirectory() as tmpdir:
        linked = _compile_and_link(_ALIAS_BODY, tmpdir)
        assert linked.sections[0].code == _EXPECTED


def test_single_aliased_object_needs_linking() -> None:
    """A lone object carrying an alias must not bypass the linker."""
    with tempfile.TemporaryDirectory() as tmpdir:
        asm_file = Path(tmpdir) / "blob.s"
        asm_file.write_text(_POOL + _ALIAS_BODY)
        obj_file = Path(tmpdir) / "blob.o"
        assert Program().assemble_as_object(str(asm_file), obj_file) == 0
        obj = ObjectFile.from_file(str(obj_file))
        assert _object_needs_linking(obj) is True


def test_build_with_imports_links_single_relocatable_module() -> None:
    """End-to-end: the single-module fast path must still resolve relocations."""
    with tempfile.TemporaryDirectory() as tmpdir:
        asm_file = Path(tmpdir) / "blob.s"
        asm_file.write_text(_POOL + _ALIAS_BODY)
        out = Path(tmpdir) / "blob.ips"
        result = build_with_imports(
            main_source=asm_file,
            output_file=out,
            output_format="ips",
            output_dir=Path(tmpdir) / "obj",
        )
        assert result.exit_code == 0
        patch = out.read_bytes()
        # The five data bytes plus the resolved `.dw` delta land verbatim in
        # the IPS payload; a regression ships `...05 00 00` (delta dropped).
        assert _EXPECTED in patch


# A HiROM `.map` whose pool bank ($C1) lands in the default LoROM bus's mirror
# region ($80-$CF, mask $8000). In object mode `.map` is deferred to link, so
# per-module assembly falls back to that default bus - and a *forward* label's
# pass-1 PC walk round-trips through the LoROM 0x8000 bank stride, landing the
# label 0x8000 too high. Backward labels (already bound) escape, which is why
# the inline cases above pass while a forward `.dw (fwd - base)` regresses.
_HIROM_PREAMBLE = (
    ".map identifier=1 bank_range=0xc0, 0xfd addr_range=0x0000, 0xffff "
    "mask=0x10000 mirror_bank_range=0x40, 0x7d\n"
    ".pool hidata { range 0xc10000 0xc1ffff strategy order }\n"
)

# base.. forward .dw (fwd - base) .. fwd. The header byte + the 2-byte .dw put
# fwd at offset 3, so the delta is 3 (03 00 little-endian).
_HIROM_FORWARD_BODY = """
.alloc blob in hidata {
base:
    .db 0xAA
    .dw ( fwd - base )
fwd:
    .db 0x11, 0x22, 0x33
}
"""
_HIROM_EXPECTED = b"\xaa\x03\x00\x11\x22\x33"


def test_pool_forward_label_diff_under_hirom_map() -> None:
    """A forward label diff in an object-mode HiROM pool must resolve linearly.

    The `.map` lives in a *separate imported module*, so per-module object
    assembly defers it to link and falls back to the default LoROM bus - the
    real-world shape. Regression value is `0x8003` (the forward label picks up
    the LoROM mirror bank's bit-15 during pass-1 binding); correct delta is
    `0x0003`.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "pre.s").write_text(_HIROM_PREAMBLE)
        main = tmp / "blob.s"
        main.write_text('.import "pre"\n' + _HIROM_FORWARD_BODY)
        out = tmp / "out.sfc"
        result = build_with_imports(
            main_source=main,
            output_file=out,
            output_format="sfc",
            output_dir=tmp / "obj",
        )
        assert result.exit_code == 0
        rom = out.read_bytes()
        # Pool bank $C1 -> file offset $10000 under HiROM.
        assert rom[0x10000:0x10006] == _HIROM_EXPECTED


def _compile(path: Path) -> ObjectFile:
    obj = path.with_suffix(".o")
    assert Program().assemble_as_object(str(path), obj) == 0
    return ObjectFile.from_file(str(obj))


def test_jmpw_to_local_label_resolves_per_module() -> None:
    """`jmp.w <local>` must target the emitting module's label, not a same-named
    local in another module.

    Underscore-private labels export as bare LOCAL names that collide in the
    linker's flat symbol map. A module-local relocation must resolve against
    its own object, otherwise `jmp.w _loop` in the second-placed module silently
    jumps into the first module's `_loop` (a layout-dependent wild branch).
    """
    pool = ".pool engine { range 0xc10000 0xc1ffff strategy order }\n"
    # First-placed module owns `_loop`/`_end` at the pool start.
    a_src = pool + ".alloc a_routine in engine {\n_loop:\n    .db 1,2,3,4,5,6,7,8\n_end:\n    rts\n}\n"
    # Second module reuses the same local names and jumps to its OWN copies.
    b_src = pool + ".alloc b_routine in engine {\n_loop:\n    nop\n    jmp.w _end\n    jmp.w _loop\n_end:\n    rts\n}\n"
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "a.s").write_text(a_src)
        (tmp / "b.s").write_text(b_src)
        linked = Linker([_compile(tmp / "a.s"), _compile(tmp / "b.s")]).link(base_address=0x8000)

        # b_routine is placed right after a_routine's 9 bytes: base $C10009.
        b = next(s for s in linked.sections if s.code.startswith(b"\xea\x4c"))
        assert b.placed_base == 0xC10009
        # nop, jmp _end ($C10010 -> $0010), jmp _loop ($C10009 -> $0009), rts.
        assert b.code == b"\xea\x4c\x10\x00\x4c\x09\x00\x60"


def test_jmpw_to_local_label_resolves_per_alloc_in_one_module() -> None:
    """`jmp.w <local>` must target ITS OWN alloc's label, not a same-named
    private label in a sibling alloc of the SAME module.

    Eight allocs each declaring `_done`/`_scan` collided in one flat symbol
    table, so `jmp.w _scan` bound to whichever duplicate the linker placed
    last (the reported wild branch). Underscore-private alloc-body labels now
    export per-alloc-mangled (`__sc<idx>__`), keeping each distinct.
    """
    src = (
        ".pool engine { range 0xc10000 0xc1ffff strategy order }\n"
        ".alloc a in engine {\n_loop:\n    nop\n    jmp.w _loop\n_done:\n    rts\n}\n"
        ".alloc b in engine {\n_loop:\n    nop\n    nop\n    jmp.w _loop\n_done:\n    rts\n}\n"
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "m.s").write_text(src)
        linked = Linker([_compile(tmp / "m.s")]).link(base_address=0x8000)
        a = next(s for s in linked.sections if s.code == b"\xea\x4c\x00\x00\x60")
        b = next(s for s in linked.sections if s.code.startswith(b"\xea\xea\x4c"))
        # a placed at $C10000: jmp _loop -> $0000. b at $C10005: jmp _loop -> $0005.
        assert a.placed_base == 0xC10000
        assert b.placed_base == 0xC10005
        assert b.code == b"\xea\xea\x4c\x05\x00\x60"
