"""Reproduce: macro applied inside `.alloc … in pool { ... }` body must
expand the same way in object mode as it does in direct mode. Macros are
compile-time AST expansion; the assembly mode shouldn't matter.

Today it doesn't:

- **direct mode**: macro app expands inside the alloc body, args bind
  to the macro scope, body opcodes evaluate against that scope, emit
  is correct.
- **object mode**: macro app's ScopeNode/PopScopeNode pair is in the
  alloc body's generated node list, but `_object_emit_alloc` walks
  the body without firing those scope-entry nodes in a way that puts
  the macro args in `resolver.current_scope`. The macro body opcode's
  `emit` then runs against the outer scope, where the arg name
  (`source`, `count`, etc.) is undefined → `SymbolNotDefined`.

The xfail is the bug. Flip to a passing test once `_object_emit_alloc`
threads the same scope dispatch as the inline emit path.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from a816.program import Program


_MACRO_DEF = """
.macro store_word(source) {
    lda.l source
    sta 0x00
}
"""


def _write_module(dst: Path) -> None:
    (dst / "macros.i").write_text(_MACRO_DEF, encoding="utf-8")
    (dst / "bank.i").write_text(
        ".pool b { range 0x208000 0x20FFFF\n    strategy order\n}\n",
        encoding="utf-8",
    )
    (dst / "mod.s").write_text(
        '.include "macros.i"\n'
        '.include "bank.i"\n'
        ".extern external_addr\n"
        ".alloc body in b {\n"
        "    store_word(external_addr)\n"
        "}\n",
        encoding="utf-8",
    )


def test_macro_in_alloc_body_direct_mode_resolves() -> None:
    """Sanity: macro args inside an alloc body bind correctly in direct
    mode. The opcode `lda.l external_addr` emits a 4-byte LDA long with
    `external_addr` as the operand symbol. Establishes the direct-mode
    behavior we want object mode to mirror."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        _write_module(tmp)
        main = tmp / "main.s"
        # Provide `external_addr` in main so direct mode has it bound
        # before the alloc body's macro app evaluates.
        main.write_text(
            "external_addr := 0x208100\n"
            '.include "mod.s"\n',
            encoding="utf-8",
        )
        program = Program()
        program.add_include_path(tmp)
        program.add_module_path(tmp)
        ips = tmp / "out.ips"
        assert program.assemble_as_patch(str(main), ips) == 0
        # Direct mode produced an IPS with the resolved opcode bytes.
        assert ips.exists() and ips.stat().st_size > 0


def test_macro_in_alloc_body_object_mode_resolves() -> None:
    """Same macro shape, compiled as a `.o`. Should produce an object
    file whose alloc-body section contains the macro-expanded opcode
    with a relocation referencing `external_addr`."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        _write_module(tmp)
        program = Program()
        program.add_include_path(tmp)
        program.add_module_path(tmp)
        obj = tmp / "mod.o"
        assert program.assemble_as_object(str(tmp / "mod.s"), obj) == 0


_MACRO_WITH_TWO_ARGS = """
.macro store_two(source, count) {
    lda.l source
    sta 0x00
    lda.l count
    sta 0x02
}
"""


def test_macro_two_args_in_alloc_body_object_mode() -> None:
    """Two-arg macro inside an alloc body, both args external. Matches
    the ff4 shape where `dma_transfer_to_vram_call(source, vramptr,
    count, mode)` is applied inside `.alloc small_vwf_init_block in
    bank20_reloc { ... }` with `source`/`count` being externs from a
    `.import`ed module."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "macros.i").write_text(_MACRO_WITH_TWO_ARGS, encoding="utf-8")
        (tmp / "bank.i").write_text(
            ".pool b { range 0x208000 0x20FFFF\n    strategy order\n}\n",
            encoding="utf-8",
        )
        (tmp / "mod.s").write_text(
            '.include "macros.i"\n'
            '.include "bank.i"\n'
            ".extern external_src\n"
            ".extern external_cnt\n"
            ".alloc body in b {\n"
            "    store_two(external_src, external_cnt)\n"
            "}\n",
            encoding="utf-8",
        )
        program = Program()
        program.add_include_path(tmp)
        program.add_module_path(tmp)
        obj = tmp / "mod.o"
        assert program.assemble_as_object(str(tmp / "mod.s"), obj) == 0


def test_macro_in_alloc_body_with_if_wrapper_object_mode() -> None:
    """Macro app inside `.alloc body in pool { .if FLAG { macro(...) }}`.
    Adds the conditional layer the ff4 build hits — flag is supplied
    via prelude/include and the `.if` body's CompoundAstNode pushes
    its own scope on top of the alloc body's. Validates that the
    macro's ScopeNode/PopScopeNode pair still fires correctly when
    nested inside another conditional scope during object emit."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "macros.i").write_text(_MACRO_WITH_TWO_ARGS, encoding="utf-8")
        (tmp / "bank.i").write_text(
            ".pool b { range 0x208000 0x20FFFF\n    strategy order\n}\n",
            encoding="utf-8",
        )
        (tmp / "mod.s").write_text(
            "FEATURE := 1\n"
            '.include "macros.i"\n'
            '.include "bank.i"\n'
            ".extern external_src\n"
            ".extern external_cnt\n"
            ".alloc body in b {\n"
            "    .if FEATURE {\n"
            "        store_two(external_src, external_cnt)\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        program = Program()
        program.add_include_path(tmp)
        program.add_module_path(tmp)
        obj = tmp / "mod.o"
        assert program.assemble_as_object(str(tmp / "mod.s"), obj) == 0


def test_macro_arg_from_imported_module_alloc_body_object_mode() -> None:
    """Most ff4-like shape: macro arg is a symbol exported by a
    separately-compiled `.import`ed module (alloc-body label), not a
    plain `.extern`. The .o for the importer needs:
      1. an alias for the macro arg whose RHS canonicalises to the
         imported symbol's exported name;
      2. a relocation in the alloc-body section pointing at that alias
         (which the linker resolves to the imported module's final
         placement).
    Today this raises `SymbolNotDefined` during the importer's emit."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "macros.i").write_text(_MACRO_WITH_TWO_ARGS, encoding="utf-8")
        (tmp / "bank.i").write_text(
            ".pool b { range 0x208000 0x20FFFF\n    strategy order\n}\n",
            encoding="utf-8",
        )
        # Provider: exports `payload` as a GLOBAL alloc-body label.
        (tmp / "provider.s").write_text(
            '.include "bank.i"\n'
            ".alloc payload in b {\n"
            "    .db 0xAA, 0xBB, 0xCC, 0xDD\n"
            "}\n",
            encoding="utf-8",
        )
        # Pre-build provider.o so the consumer's import resolves.
        prog0 = Program()
        prog0.add_include_path(tmp)
        prog0.add_module_path(tmp)
        assert prog0.assemble_as_object(str(tmp / "provider.s"), tmp / "provider.o") == 0
        # Consumer: imports provider, applies macro with `payload` as arg.
        (tmp / "consumer.s").write_text(
            '.include "macros.i"\n'
            '.include "bank.i"\n'
            '.import "provider"\n'
            ".alloc body in b {\n"
            "    store_two(payload, payload)\n"
            "}\n",
            encoding="utf-8",
        )
        program = Program()
        program.add_include_path(tmp)
        program.add_module_path(tmp)
        obj = tmp / "consumer.o"
        assert program.assemble_as_object(str(tmp / "consumer.s"), obj) == 0


def test_macro_arg_is_anon_block_label_link_resolves() -> None:
    """The actual ff4 failure shape — link-time alias resolution.

    Macro arg `jump_table` is bound to `battle_flags_jump_table`, a
    label declared inside a BARE `{ ... }` block inside the alloc body.
    Bare blocks push an ANONYMOUS scope, so the label exports as
    `__sc<N>__battle_flags_jump_table` LOCAL (or doesn't export at all
    as bare GLOBAL).

    `_resolve_aliases` in the linker tries to evaluate the alias
    expression `battle_flags_jump_table` against `symbol_map`. The
    mangled form is in the map; the bare form isn't → `UnresolvedSymbolError`.

    Fix path: when an alias's RHS canonicalisation runs at the
    importer's emit, references to local labels must use the labels'
    EXPORTED names (`__sc<N>__...` or the dotted scope-prefixed form),
    not the source-level bare names.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "macros.i").write_text(
            ".macro use_table(jump_table) {\n"
            "    lda.l jump_table\n"
            "    sta 0x00\n"
            "}\n",
            encoding="utf-8",
        )
        (tmp / "bank.i").write_text(
            ".pool b { range 0x208000 0x20FFFF\n    strategy order\n}\n",
            encoding="utf-8",
        )
        (tmp / "mod.s").write_text(
            '.include "macros.i"\n'
            '.include "bank.i"\n'
            ".alloc body in b {\n"
            "    {\n"
            "        use_table(jt_label)\n"
            "    jt_label:\n"
            "        .dw 0x1234\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        (tmp / "main.s").write_text(
            '.import "mod"\n',
            encoding="utf-8",
        )
        from a816.module_builder import build_with_imports

        result = build_with_imports(
            main_source=tmp / "main.s",
            output_file=tmp / "out.ips",
            output_format="ips",
            module_paths=[tmp],
            output_dir=tmp,
            include_paths=[tmp],
        )
        assert result.exit_code == 0, f"build failed: {result.diagnostics}"


def test_macro_arg_from_imported_extern_inside_if_object_mode() -> None:
    """ff4 intro.s shape: `.import`ed module exports a label; consumer
    `.alloc body in pool { .if FLAG { macro_call(imported_label, ...) }}`.
    Macro arg `source` should bind to `imported_label` as an alias.

    Reduced from the actual ff4 intro module which fails with
    `source is not defined` during the importer's own object compile."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "macros.i").write_text(
            ".macro store_low(source) {\n"
            "    pea.w source & 0xFFFF\n"
            "    pea.w 0x00FF & ( source >> 16 )\n"
            "}\n",
            encoding="utf-8",
        )
        (tmp / "bank.i").write_text(
            ".pool b { range 0x208000 0x20FFFF\n    strategy order\n}\n",
            encoding="utf-8",
        )
        (tmp / "assets.s").write_text(
            '.include "bank.i"\n'
            ".alloc payload in b {\n"
            "    .db 0xAA, 0xBB\n"
            "}\n",
            encoding="utf-8",
        )
        # Pre-build assets.o
        prog0 = Program()
        prog0.add_include_path(tmp)
        prog0.add_module_path(tmp)
        assert prog0.assemble_as_object(str(tmp / "assets.s"), tmp / "assets.o") == 0
        (tmp / "intro.s").write_text(
            "FEATURE := 1\n"
            '.include "macros.i"\n'
            '.include "bank.i"\n'
            '.import "assets"\n'
            ".alloc body in b {\n"
            "    .if FEATURE {\n"
            "        store_low(payload)\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        program = Program()
        program.add_include_path(tmp)
        program.add_module_path(tmp)
        obj = tmp / "intro.o"
        assert program.assemble_as_object(str(tmp / "intro.s"), obj) == 0


def test_macro_called_twice_in_alloc_body_object_mode() -> None:
    """Two macro applications in the same alloc body — second app pushes
    a fresh scope, so the resolver's scope list grows past idx 1. If
    `_object_emit_alloc` walks the body without firing each ScopeNode,
    only one of the two macro expansions would resolve."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "macros.i").write_text(_MACRO_WITH_TWO_ARGS, encoding="utf-8")
        (tmp / "bank.i").write_text(
            ".pool b { range 0x208000 0x20FFFF\n    strategy order\n}\n",
            encoding="utf-8",
        )
        (tmp / "mod.s").write_text(
            '.include "macros.i"\n'
            '.include "bank.i"\n'
            ".extern src_a\n"
            ".extern cnt_a\n"
            ".extern src_b\n"
            ".extern cnt_b\n"
            ".alloc body in b {\n"
            "    store_two(src_a, cnt_a)\n"
            "    store_two(src_b, cnt_b)\n"
            "}\n",
            encoding="utf-8",
        )
        program = Program()
        program.add_include_path(tmp)
        program.add_module_path(tmp)
        obj = tmp / "mod.o"
        assert program.assemble_as_object(str(tmp / "mod.s"), obj) == 0
