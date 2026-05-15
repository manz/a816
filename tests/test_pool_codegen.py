from __future__ import annotations

import pytest

from a816.parse.codegen import code_gen
from a816.parse.mzparser import MZParser
from a816.pool import Strategy
from a816.program import Program
from a816.symbols import Resolver
from tests import StubWriter


def _gen(src: str) -> Resolver:
    resolver = Resolver()
    result = MZParser.parse_as_ast(src, filename="t.s")
    assert result.parse_error is None
    code_gen(result.nodes, resolver)
    return resolver


class TestPoolCodegen:
    def test_pool_registered_on_resolver(self) -> None:
        resolver = _gen(
            """
            .pool bank02_slack {
                range 0x028000 0x028fff
                fill 0xea
                strategy order
            }
            """,
        )
        assert "bank02_slack" in resolver.pools
        pool = resolver.pools["bank02_slack"]
        assert pool.fill == 0xEA
        assert pool.strategy is Strategy.ORDER
        assert pool.capacity == 0x1000

    def test_two_pools(self) -> None:
        resolver = _gen(
            """
            .pool bank02 { range 0x028000 0x028fff }
            .pool bank20 { range 0x208000 0x20ffff }
            """,
        )
        assert set(resolver.pools.keys()) == {"bank02", "bank20"}

    def test_duplicate_pool_decl_errors(self) -> None:
        with pytest.raises(Exception, match="already declared"):
            _gen(
                """
                .pool dup { range 0x028000 0x028fff }
                .pool dup { range 0x02a000 0x02afff }
                """,
            )

    def test_invalid_range_propagates(self) -> None:
        with pytest.raises(Exception, match="crosses bank boundary"):
            _gen(".pool bad { range 0x02ff00 0x030100 }")

    def test_overlapping_ranges_propagate(self) -> None:
        with pytest.raises(Exception, match="overlap"):
            _gen(
                """
                .pool bad {
                    range 0x028000 0x028200
                    range 0x028100 0x028300
                }
                """,
            )


class TestReclaimCodegen:
    def test_reclaim_extends_pool(self) -> None:
        resolver = _gen(
            """
            .pool p { range 0x028000 0x0280ff }
            .reclaim p 0x02a000 0x02a0ff
            """,
        )
        assert resolver.pools["p"].capacity == 0x200

    def test_reclaim_unknown_pool_errors(self) -> None:
        with pytest.raises(Exception, match="unknown pool"):
            _gen(".reclaim ghost 0x02a000 0x02a0ff")

    def test_reclaim_overlapping_propagates(self) -> None:
        with pytest.raises(Exception, match="overlap"):
            _gen(
                """
                .pool p { range 0x028000 0x0280ff }
                .reclaim p 0x028080 0x028180
                """,
            )

    def test_reclaim_adjacent_merges(self) -> None:
        resolver = _gen(
            """
            .pool p { range 0x028000 0x0280ff }
            .reclaim p 0x028100 0x0281ff
            """,
        )
        assert len(resolver.pools["p"].ranges) == 1


class TestAllocCodegen:
    def test_alloc_into_unknown_pool_errors(self) -> None:
        with pytest.raises(Exception, match="unknown pool"):
            _gen(
                """
                .alloc fn in ghost {
                    rts
                }
                """,
            )

    def test_alloc_emits_alloc_node(self) -> None:
        from a816.parse.nodes import AllocNode

        resolver = Resolver()
        result = MZParser.parse_as_ast(
            """
            .pool p { range 0x028000 0x0280ff }
            .alloc fn in p {
                rts
            }
            """,
            filename="t.s",
        )
        assert result.parse_error is None
        nodes = code_gen(result.nodes, resolver)
        alloc_nodes = [n for n in nodes if isinstance(n, AllocNode)]
        assert len(alloc_nodes) == 1
        assert alloc_nodes[0].name == "fn"
        assert alloc_nodes[0].pool_name == "p"


class TestAllocEndToEnd:
    def test_body_lands_at_allocated_addr(self) -> None:
        writer = StubWriter()
        program = Program()
        src = """
        .pool p { range 0x028000 0x0280ff }
        .alloc fn in p {
            rts
        }
        """
        program.assemble_string_with_emitter(src, "test.s", writer)
        # rts opcode = 0x60. Body should land at allocator-chosen 0x028000.
        assert b"\x60" in writer.data
        idx = writer.data.index(b"\x60")
        # The address recorded is physical; first range starts at logical
        # 0x028000 which maps to physical 0x010000 under low_rom.
        assert writer.data_addresses[idx] in (0x028000, 0x010000)

    def test_alloc_binds_symbol(self) -> None:
        program = Program()
        resolver = program.resolver
        src = """
        .pool p { range 0x028000 0x0280ff }
        .alloc fn in p {
            rts
        }
        """
        writer = StubWriter()
        program.assemble_string_with_emitter(src, "test.s", writer)
        # Symbol `fn` should resolve to the allocator-picked address.
        labels = resolver.current_scope.labels
        assert "fn" in labels


class TestRelocateCodegen:
    def test_relocate_into_unknown_pool_errors(self) -> None:
        with pytest.raises(Exception, match="unknown pool"):
            _gen(
                """
                .relocate fn 0x02c000 0x02c17f into ghost {
                    rts
                }
                """,
            )

    def test_relocate_emits_relocate_node(self) -> None:
        from a816.parse.nodes import RelocateNode

        resolver = Resolver()
        result = MZParser.parse_as_ast(
            """
            .pool p { range 0x028000 0x0280ff }
            .relocate fn 0x02c000 0x02c17f into p {
                rts
            }
            """,
            filename="t.s",
        )
        assert result.parse_error is None
        nodes = code_gen(result.nodes, resolver)
        relocs = [n for n in nodes if isinstance(n, RelocateNode)]
        assert len(relocs) == 1
        assert relocs[0].name == "fn"
        assert relocs[0].old_start == 0x02C000
        assert relocs[0].old_end == 0x02C17F


class TestAllocPass1ForwardRefs:
    """Regression: ff4-modules dogfood surfaced AllocNode crashing on
    forward refs because `SymbolNode.pc_after` evaluates RHS eagerly.
    `Program.resolve_labels` skips SymbolNode in pass-1 for the same
    reason; AllocNode now mirrors that.
    """

    def test_alloc_body_with_forward_label_ref(self) -> None:
        """Mirrors ff4-modules pattern: body references a label declared
        later in another scope. Pre-fix this raised SymbolNotDefined
        during AllocNode's pass-1 size measurement."""
        program = Program()
        writer = StubWriter()
        src = """
        .pool p { range 0x028000 0x0280ff }
        .alloc fn in p {
            jsr.l later.target
            rts
        }

        *=0x008000
        .scope later {
        target:
            rts
        }
        """
        program.assemble_string_with_emitter(src, "test.s", writer)

        labels = program.resolver.current_scope.labels
        assert "fn" in labels, "alloc label must bind"
        # fn binds at logical 0x028000 (only chunk start). Translate to
        # physical via the bus so we can find the matching writer record.
        fn_logical = labels["fn"]
        fn_phys = program.resolver.get_bus().get_address(fn_logical).physical
        # Body bytes: 22 00 80 00 (jsr.l later.target) + 60 (rts) = 5 bytes.
        alloc_block = next(
            (b for a, b in zip(writer.data_addresses, writer.data, strict=False) if a == fn_phys),
            None,
        )
        assert alloc_block == b"\x22\x00\x80\x00\x60", (
            f"alloc body bytes wrong at phys=0x{fn_phys:06x}: {alloc_block!r}"
        )


class TestLspPoolFeatures:
    """Pool-aware LSP: hover, goto-def, diagnostic, completion-in-context."""

    @staticmethod
    def _doc(src: str):  # type: ignore[no-untyped-def]
        from a816.lsp.server import A816Document

        return A816Document(uri="file:///t.s", content=src)

    def test_pool_details_tracked(self) -> None:
        doc = self._doc(
            ".pool slack { range 0x028000 0x0280ff fill 0xea strategy order }\n.alloc fn in slack {\n    rts\n}\n"
        )
        assert "slack" in doc.pools
        detail = doc.pool_details["slack"]
        assert "range" in detail
        assert "fill" in detail
        assert "order" in detail

    def test_alloc_target_pool_tracked(self) -> None:
        doc = self._doc(
            ".pool p { range 0x028000 0x0280ff }\n"
            ".alloc helper in p {\n    rts\n}\n"
            ".relocate moved 0x02c000 0x02c17f into p {\n    rts\n}\n"
        )
        assert doc.alloc_target_pool["helper"] == "p"
        assert doc.alloc_target_pool["moved"] == "p"

    def test_pool_consumers_tracked(self) -> None:
        doc = self._doc(
            ".pool p { range 0x028000 0x0280ff }\n.alloc a in p {\n    rts\n}\n.reclaim p 0x02c000 0x02c17f\n"
        )
        consumers = doc.pool_consumers["p"]
        kinds = [k for _, k in consumers]
        assert "alloc" in kinds
        assert "reclaim" in kinds

    def test_undeclared_pool_diagnostic(self) -> None:
        doc = self._doc(
            ".alloc helper in ghost {\n    rts\n}\n",
        )
        msgs = [d.message for d in doc.diagnostics]
        assert any("undeclared pool 'ghost'" in m for m in msgs)

    def test_hover_on_pool_name_returns_summary(self) -> None:
        from a816.lsp.server import A816LanguageServer

        server = A816LanguageServer()
        doc = self._doc(".pool slack { range 0x028000 0x0280ff fill 0xea strategy order }\n")
        hover = server._hover_for_pool_directive(doc, "slack")
        assert hover is not None
        content = hover.contents.value  # type: ignore[union-attr]
        assert ".pool slack" in content
        assert "range" in content

    def test_hover_on_alloc_name_returns_target_pool(self) -> None:
        from a816.lsp.server import A816LanguageServer

        server = A816LanguageServer()
        doc = self._doc(".pool p { range 0x028000 0x0280ff }\n.alloc helper in p {\n    rts\n}\n")
        hover = server._hover_for_pool_directive(doc, "helper")
        assert hover is not None
        assert "pool `p`" in hover.contents.value  # type: ignore[union-attr]

    def test_hover_on_unrelated_word_returns_none(self) -> None:
        from a816.lsp.server import A816LanguageServer

        server = A816LanguageServer()
        doc = self._doc(".pool p { range 0x028000 0x0280ff }\n")
        assert server._hover_for_pool_directive(doc, "not_a_pool_name") is None

    def test_pool_name_completions_in_context_after_in(self) -> None:
        from a816.lsp.server import A816LanguageServer

        server = A816LanguageServer()
        doc = self._doc(".pool slack { range 0x028000 0x0280ff }\n.pool bank20 { range 0x208000 0x20ffff }\n")
        # Cursor after `.alloc fn in ` — returns pool-name list.
        items = server._pool_name_completions_in_context(".alloc fn in ", len(".alloc fn in "), doc)
        assert items is not None
        labels = {item.label for item in items}
        assert "slack" in labels
        assert "bank20" in labels

    def test_pool_name_completions_in_context_after_into(self) -> None:
        from a816.lsp.server import A816LanguageServer

        server = A816LanguageServer()
        doc = self._doc(".pool p { range 0x028000 0x0280ff }\n")
        items = server._pool_name_completions_in_context(
            ".relocate fn 0x02c000 0x02c17f into ",
            len(".relocate fn 0x02c000 0x02c17f into "),
            doc,
        )
        assert items is not None
        assert any(item.label == "p" for item in items)

    def test_pool_name_completions_in_context_after_reclaim(self) -> None:
        from a816.lsp.server import A816LanguageServer

        server = A816LanguageServer()
        doc = self._doc(".pool p { range 0x028000 0x0280ff }\n")
        items = server._pool_name_completions_in_context(".reclaim ", len(".reclaim "), doc)
        assert items is not None
        assert any(item.label == "p" for item in items)

    def test_pool_name_completions_in_context_returns_none_outside_pool_context(self) -> None:
        from a816.lsp.server import A816LanguageServer

        server = A816LanguageServer()
        doc = self._doc(".pool p { range 0x028000 0x0280ff }\n")
        # Regular instruction line — not a pool-context completion.
        assert server._pool_name_completions_in_context("    lda ", len("    lda "), doc) is None
        # Empty line.
        assert server._pool_name_completions_in_context("", 0, doc) is None


class TestLspDocumentSymbols:
    """`.pool` and `.alloc` / `.relocate` names surface in the LSP
    document outline so editors can show them in the navigation panel."""

    def test_pool_and_alloc_indexed(self) -> None:
        from a816.lsp.server import A816Document

        doc = A816Document(
            uri="file:///t.s",
            content=(
                ".pool bank01_slack { range 0x01ff35 0x01ffff }\n"
                ".alloc helper in bank01_slack {\n"
                "    rts\n"
                "}\n"
                ".relocate fn 0x02c000 0x02c17f into bank01_slack {\n"
                "    rts\n"
                "}\n"
            ),
        )
        assert "bank01_slack" in doc.pools
        assert "helper" in doc.allocs
        assert "fn" in doc.allocs


class TestCrossTuPoolMerging:
    """Linker unions same-named `.pool` decls across modules.

    Two modules contributing complementary ranges to the same pool name
    merge into one larger pool on the output. fill / strategy must agree.
    Full deferred allocation (alloc placement decided at link time across
    all modules) is a follow-up; this slice serializes decls and validates
    conflicts so the foundation is in place.
    """

    def test_pool_decls_serialize_to_object_file(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from a816.object_file import ObjectFile

        asm = tmp_path / "mod.s"
        asm.write_text(
            """
            .pool slack {
                range 0x028000 0x0280ff
                fill 0xea
                strategy order
            }
            .alloc fn in slack {
                rts
            }
            """
        )
        obj = tmp_path / "mod.o"
        assert Program().assemble_as_object(str(asm), obj) == 0
        loaded = ObjectFile.from_file(str(obj))
        assert len(loaded.pool_decls) == 1
        decl = loaded.pool_decls[0]
        assert decl.name == "slack"
        assert decl.ranges == [(0x028000, 0x0280FF)]
        assert decl.fill == 0xEA
        assert decl.strategy == "order"

    def test_linker_unions_pool_ranges(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from a816.linker import Linker
        from a816.object_file import ObjectFile

        mod_a = tmp_path / "a.s"
        mod_a.write_text(
            """
            .pool slack {
                range 0x028000 0x0280ff
            }
            .alloc fn_a in slack {
                rts
            }
            """
        )
        mod_b = tmp_path / "b.s"
        mod_b.write_text(
            """
            .pool slack {
                range 0x02a000 0x02a0ff
            }
            .alloc fn_b in slack {
                rts
            }
            """
        )
        obj_a = tmp_path / "a.o"
        obj_b = tmp_path / "b.o"
        assert Program().assemble_as_object(str(mod_a), obj_a) == 0
        assert Program().assemble_as_object(str(mod_b), obj_b) == 0
        linker = Linker([ObjectFile.from_file(str(obj_a)), ObjectFile.from_file(str(obj_b))])
        linked = linker.link()
        # Linker exposes merged pool decl on output.
        merged = next(p for p in linked.pool_decls if p.name == "slack")
        # Both modules' ranges combine.
        assert (0x028000, 0x0280FF) in merged.ranges
        assert (0x02A000, 0x02A0FF) in merged.ranges

    def test_link_time_allocator_places_allocs_across_modules(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Two modules share `.pool slack`, each with its own range.

        The linker unions the ranges and runs the allocator across all
        deferred alloc requests so the two modules' allocs land at
        non-overlapping addresses within the combined pool.
        """
        from a816.linker import Linker
        from a816.object_file import ObjectFile

        # Each module contributes a 1-byte chunk so each alloc is forced
        # into a different chunk (each chunk holds exactly one rts).
        mod_a = tmp_path / "a.s"
        mod_a.write_text(
            """
            .pool slack {
                range 0x028000 0x028000
                strategy order
            }
            .alloc fn_a in slack {
                rts
            }
            """
        )
        mod_b = tmp_path / "b.s"
        mod_b.write_text(
            """
            .pool slack {
                range 0x02a000 0x02a000
                strategy order
            }
            .alloc fn_b in slack {
                rts
            }
            """
        )
        obj_a = tmp_path / "a.o"
        obj_b = tmp_path / "b.o"
        assert Program().assemble_as_object(str(mod_a), obj_a) == 0
        assert Program().assemble_as_object(str(mod_b), obj_b) == 0
        linker = Linker([ObjectFile.from_file(str(obj_a)), ObjectFile.from_file(str(obj_b))])
        linker.link()
        # Both allocs land in the merged pool; addresses are distinct and
        # come from different chunks (request 1 fills chunk a, request 2
        # falls into chunk b under strategy=order).
        addr_a = linker.symbol_map["fn_a"]
        addr_b = linker.symbol_map["fn_b"]
        assert addr_a == 0x028000
        assert addr_b == 0x02A000

    def test_link_time_pool_overflow_errors(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Linker errors loud when cross-TU allocs exceed merged pool capacity."""
        from a816.linker import Linker
        from a816.object_file import ObjectFile

        # Pool has two 1-byte chunks (no single chunk fits a 2-byte alloc).
        # First module declares the pool + a 2-byte alloc → won't fit.
        mod_a = tmp_path / "a.s"
        mod_a.write_text(
            """
            .pool tiny {
                range 0x028000 0x028000
            }
            .alloc fn_a in tiny {
                rts
                rts
            }
            """
        )
        mod_b = tmp_path / "b.s"
        mod_b.write_text(
            """
            .pool tiny {
                range 0x02a000 0x02a000
            }
            .alloc fn_b in tiny {
                rts
            }
            """
        )
        for name, src in (("a", mod_a), ("b", mod_b)):
            obj = tmp_path / f"{name}.o"
            assert Program().assemble_as_object(str(src), obj) == 0
        objs = [
            ObjectFile.from_file(str(tmp_path / "a.o")),
            ObjectFile.from_file(str(tmp_path / "b.o")),
        ]
        with pytest.raises(Exception, match="does not fit"):
            Linker(objs).link()

    def test_linker_pool_fill_mismatch_errors(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from a816.linker import Linker
        from a816.object_file import ObjectFile

        for name, fill in (("a", "0xea"), ("b", "0xff")):
            asm = tmp_path / f"{name}.s"
            asm.write_text(
                f"""
                .pool slack {{
                    range 0x02{name}000 0x02{name}0ff
                    fill {fill}
                }}
                .alloc fn_{name} in slack {{
                    rts
                }}
                """
            )
            obj = tmp_path / f"{name}.o"
            assert Program().assemble_as_object(str(asm), obj) == 0

        objs = [
            ObjectFile.from_file(str(tmp_path / "a.o")),
            ObjectFile.from_file(str(tmp_path / "b.o")),
        ]
        with pytest.raises(ValueError, match="conflicting fill"):
            Linker(objs).link()


class TestObjectMode:
    """`.pool` + `.alloc` work when compiling to .o.

    The module becomes pinned (allocator picks final addresses, so no
    further relocation makes sense). Each .alloc emits its body as a
    pinned region at the allocator-chosen address; alloc's label binds
    there and ships in the symbol table for cross-module callers.
    """

    def test_alloc_object_mode_writes_region_at_allocated_addr(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from a816.object_file import ObjectFile

        src = """
        .pool p { range 0x028000 0x0280ff }
        .alloc fn in p {
            rts
        }
        """
        asm_file = tmp_path / "mod.s"
        asm_file.write_text(src)
        obj_file = tmp_path / "mod.o"
        program = Program()
        rc = program.assemble_as_object(str(asm_file), obj_file)
        assert rc == 0
        obj = ObjectFile.from_file(str(obj_file))
        # One pinned region landing at the allocator-picked addr (0x028000).
        alloc_regions = [r for r in obj.regions if r.base_address == 0x028000]
        assert len(alloc_regions) == 1
        assert alloc_regions[0].code == b"\x60"  # rts
        sym_names = [name for name, _, _, _ in obj.symbols]
        assert "fn" in sym_names

    def test_alloc_object_mode_link_resolves_caller(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from a816.linker import Linker
        from a816.object_file import ObjectFile

        # Provider: defines fn via .alloc.
        provider_src = """
        .pool p { range 0x028000 0x0280ff }
        .alloc fn in p {
            rts
        }
        """
        # Consumer: external ref to fn.
        consumer_src = """
        .extern fn
        *=0x008000
        main:
            jsr.l fn
            rts
        """
        prov_asm = tmp_path / "prov.s"
        prov_asm.write_text(provider_src)
        cons_asm = tmp_path / "cons.s"
        cons_asm.write_text(consumer_src)
        prov_o = tmp_path / "prov.o"
        cons_o = tmp_path / "cons.o"
        assert Program().assemble_as_object(str(prov_asm), prov_o) == 0
        assert Program().assemble_as_object(str(cons_asm), cons_o) == 0
        linker = Linker([ObjectFile.from_file(str(prov_o)), ObjectFile.from_file(str(cons_o))])
        linked = linker.link()
        # Linker resolves fn to 0x028000 (provider's pinned alloc addr).
        assert linker.symbol_map["fn"] == 0x028000
        # Consumer's jsr.l fn (0x22) operand patched to the alloc addr.
        cons_region = next(r for r in linked.regions if r.base_address == 0x008000)
        # main: jsr.l fn (4 bytes: 0x22 LO MID HI) + rts (0x60) = 5 bytes
        assert cons_region.code[:5] == b"\x22\x00\x80\x02\x60"


class TestAllocImportDedupe:
    """ff4 Q#13: `.import` of the same .o module via both `.include`'d patch
    file AND inside `.alloc` body double-emitted because
    `_mark_import_winners` walked only top-level program nodes, missing
    LinkedModuleNode children inside AllocNode.body."""

    def test_o_import_inside_alloc_dedupes_against_outer_include(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        # Build dialog.s -> dialog.o so import resolves through LinkedModuleNode
        dialog_s = tmp_path / "dialog.s"
        dialog_s.write_text("get_bank1:\n    rep #0x20\n    lda.l 0x218000\n    sta 0x20\n    rts\n")
        Program().assemble_as_object(str(dialog_s), tmp_path / "dialog.o")

        included = tmp_path / "patches.i"
        included.write_text('*=0x018798\n    jsr.w 0x9876\n.import "dialog"\n')

        main = tmp_path / "main.s"
        main.write_text(
            ".pool slack { range 0x208000 0x20ffff strategy order }\n"
            '.include "patches.i"\n'
            '.alloc bank20_main in slack {\n    .import "dialog"\n}\n'
        )
        program = Program()
        program.add_module_path(str(tmp_path))
        program.add_include_path(str(tmp_path))
        program.assemble_as_patch(str(main), tmp_path / "out.ips")

        # Parse IPS records: expect only the patch (3B) + alloc body (single
        # copy of dialog) — no duplicate at the patch's surrounding org.
        d = (tmp_path / "out.ips").read_bytes()
        pos = 5
        records: list[tuple[int, bytes]] = []
        while True:
            if d[pos : pos + 3] == b"EOF":
                break
            addr = int.from_bytes(d[pos : pos + 3], "big")
            pos += 3
            sz = int.from_bytes(d[pos : pos + 2], "big")
            pos += 2
            if sz == 0:
                run = int.from_bytes(d[pos : pos + 2], "big")
                pos += 2
                byte = d[pos : pos + 1]
                pos += 1
                records.append((addr, byte * run))
            else:
                records.append((addr, d[pos : pos + sz]))
                pos += sz
        # Patch at $01:8798 stays 3B (the jsr.w), dialog body lands ONLY in
        # the alloc region. Pre-fix: patch record swelled by 18B (dialog bytes).
        patch_record = next(b for a, b in records if a == 0x008798)
        assert len(patch_record) == 3, (
            f"patch record should be 3 bytes (jsr.w only); got {len(patch_record)} — duplicate import emission"
        )


class TestPoolExhaustion:
    """Pool overflow errors are surfaced loudly in both modes."""

    def test_direct_mode_overflow_raises_with_alloc_name(self) -> None:
        """One .alloc bigger than the only chunk → loud failure at codegen."""
        program = Program()
        writer = StubWriter()
        src = """
        .pool tiny { range 0x028000 0x028000 }
        .alloc oversized in tiny {
            rts
            rts
            rts
        }
        """
        with pytest.raises(Exception, match="oversized"):
            program.assemble_string_with_emitter(src, "test.s", writer)

    def test_direct_mode_two_allocs_overflow_raises(self) -> None:
        """First .alloc fills pool, second has nowhere to go."""
        program = Program()
        writer = StubWriter()
        src = """
        .pool tiny { range 0x028000 0x028001 strategy order }
        .alloc filler in tiny {
            rts
            rts
        }
        .alloc spill in tiny {
            rts
        }
        """
        with pytest.raises(Exception, match="spill|does not fit"):
            program.assemble_string_with_emitter(src, "test.s", writer)


class TestPoolStatsSymbols:
    """`.pool` decl binds `<name>.capacity`, `.fragments`, `.largest_chunk`
    as scope symbols so users can write compile-time guards like:
        .if mypool.capacity < 0x200 { .debug 'pool too small' }
    """

    def test_capacity_bound(self) -> None:
        resolver = _gen(
            """
            .pool p {
                range 0x028000 0x0280ff
                range 0x02a000 0x02a0ff
            }
            """,
        )
        assert resolver.current_scope.value_for("p.capacity") == 0x200

    def test_fragments_bound(self) -> None:
        resolver = _gen(
            """
            .pool p {
                range 0x028000 0x0280ff
                range 0x02a000 0x02a0ff
            }
            """,
        )
        assert resolver.current_scope.value_for("p.fragments") == 2

    def test_largest_chunk_bound(self) -> None:
        resolver = _gen(
            """
            .pool p {
                range 0x028000 0x0280ff
                range 0x02a000 0x02a1ff
            }
            """,
        )
        assert resolver.current_scope.value_for("p.largest_chunk") == 0x200

    def test_capacity_usable_in_if_guard(self) -> None:
        # Compile-time .if reading <pool>.capacity must not crash.
        program = Program()
        writer = StubWriter()
        src = """
        .pool p {
            range 0x028000 0x0280ff
        }
        .if p.capacity < 0x10000 {
            .debug 'pool small (expected)'
        }
        .alloc fn in p {
            rts
        }
        """
        program.assemble_string_with_emitter(src, "test.s", writer)


class TestPoolExpressions:
    """`.pool` / `.reclaim` / `.relocate` accept constant expressions
    for range bounds, fill byte, and addresses — not just numeric literals.
    Limitation: forward refs to user-declared constants aren't resolved
    yet at code-generation time (they live in SymbolNode.pc_after which
    runs in pass-2 of resolve_labels). Arithmetic on literals works.
    """

    def test_pool_range_uses_constant_symbol_forward_ref(self) -> None:
        """Constant declared before .pool is bound eagerly so the pool
        literal can read it at codegen time."""
        program = Program()
        writer = StubWriter()
        src = """
        BANK02_BASE = 0x028000
        BANK02_TOP  = 0x028fff
        .pool p {
            range BANK02_BASE BANK02_TOP
            fill 0xea
        }
        .alloc fn in p {
            rts
        }
        """
        program.assemble_string_with_emitter(src, "test.s", writer)
        pool = program.resolver.pools["p"]
        assert pool.ranges[0].start == 0x028000
        assert pool.ranges[0].end == 0x028FFF
        assert pool.fill == 0xEA

    def test_pool_fill_arithmetic_expression(self) -> None:
        program = Program()
        writer = StubWriter()
        src = """
        .pool p {
            range 0x028000 0x028000 + 0xff
            fill 0xea + 1
        }
        .alloc fn in p {
            rts
        }
        """
        program.assemble_string_with_emitter(src, "test.s", writer)
        pool = program.resolver.pools["p"]
        assert pool.ranges[0].start == 0x028000
        assert pool.ranges[0].end == 0x0280FF
        assert pool.fill == 0xEB

    def test_pool_unknown_symbol_in_literal_raises(self) -> None:
        from a816.parse.codegen import code_gen
        from a816.symbols import Resolver

        resolver = Resolver()
        result = MZParser.parse_as_ast(
            """
            .pool p {
                range undefined_symbol 0x0280ff
            }
            """,
            filename="t.s",
        )
        assert result.parse_error is None
        with pytest.raises(Exception, match="undefined symbol"):
            code_gen(result.nodes, resolver)

    def test_reclaim_uses_arithmetic_expression(self) -> None:
        program = Program()
        writer = StubWriter()
        src = """
        .pool p { range 0x028000 0x0280ff }
        .reclaim p 0x02c000 0x02c000 + 0x17f
        """
        program.assemble_string_with_emitter(src, "test.s", writer)
        pool = program.resolver.pools["p"]
        assert pool.capacity == 0x100 + 0x180

    def test_relocate_uses_arithmetic_expression(self) -> None:
        program = Program()
        writer = StubWriter()
        src = """
        .pool p { range 0x028000 0x0280ff }
        .relocate fn 0x02c000 0x02c000 + 0x17f into p {
            rts
        }
        """
        program.assemble_string_with_emitter(src, "test.s", writer)
        # Original range reclaimed alongside the body's allocation.
        pool = program.resolver.pools["p"]
        assert pool.capacity == 0x100 + 0x180


class TestMultiAlloc:
    def test_two_allocs_order_strategy_contiguous(self) -> None:
        """Multi-alloc dogfood: two .allocs in order strategy lay out
        contiguously, byte-identical to a single .alloc of combined size."""
        program = Program()
        writer = StubWriter()
        src = """
        .pool p {
            range 0x028000 0x0280ff
            strategy order
        }
        .alloc a in p {
            nop
            nop
            rts
        }
        .alloc b in p {
            rts
        }
        """
        program.assemble_string_with_emitter(src, "test.s", writer)
        labels = program.resolver.current_scope.labels
        a_logical = labels["a"]
        b_logical = labels["b"]
        # a is 3 bytes (nop, nop, rts), b should follow at a+3.
        assert b_logical - a_logical == 3, f"order strategy not contiguous: a=0x{a_logical:06x} b=0x{b_logical:06x}"
        # a placed at chunk start = 0x028000.
        assert a_logical == 0x028000


class TestAllocNodeInternals:
    """Cover NodeProtocol surface bits (emit, __str__, empty paths)."""

    @staticmethod
    def _make_alloc(src: str):  # type: ignore[no-untyped-def]
        from a816.parse.nodes import AllocNode

        resolver = Resolver()
        result = MZParser.parse_as_ast(src, filename="t.s")
        assert result.parse_error is None
        nodes = code_gen(result.nodes, resolver)
        allocs = [n for n in nodes if isinstance(n, AllocNode)]
        assert allocs
        return allocs[0]

    _SRC = """
        .pool p { range 0x028000 0x0280ff }
        .alloc fn in p {
            rts
        }
        """

    def test_alloc_str_repr(self) -> None:
        node = self._make_alloc(self._SRC)
        assert "fn" in str(node)
        assert "p" in str(node)

    def test_alloc_emit_returns_empty_bytes(self) -> None:
        node = self._make_alloc(self._SRC)
        dummy = Resolver().get_bus().get_address(0)
        # emit() is documented to be a no-op; bytes flow through emit_blocks.
        assert node.emit(dummy) == b""

    def test_emit_blocks_empty_before_allocation(self) -> None:
        node = self._make_alloc(self._SRC)
        dummy = Resolver().get_bus().get_address(0)
        # Before pc_after runs and allocator places the slot, emit_blocks
        # returns [].
        assert node.emit_blocks(dummy) == []

    def test_pc_after_unknown_pool_raises(self) -> None:
        """Defense in depth: codegen validates the pool exists before
        constructing AllocNode, but the runtime guard also raises if a
        node is built directly without going through generate_alloc."""
        from a816.parse.nodes import AllocNode, NodeError
        from a816.parse.tokens import Token, TokenType

        resolver = Resolver()
        fake_tok = Token(TokenType.IDENTIFIER, "ghost")
        node = AllocNode("fn", "ghost", [], resolver, fake_tok)
        dummy = resolver.get_bus().get_address(0)
        with pytest.raises(NodeError, match="unknown pool"):
            node.pc_after(dummy)

    def test_alloc_skips_symbol_node_in_pass1(self) -> None:
        """`continue` branch in _measure_body when body has a SymbolNode."""
        program = Program()
        writer = StubWriter()
        # `local_const = 0x42` produces a SymbolNode that pass-1 must skip
        # to keep measurement deterministic (regardless of RHS resolvability).
        src = """
        .pool p { range 0x028000 0x0280ff }
        .alloc fn in p {
            local_const = 0x42
            rts
        }
        """
        program.assemble_string_with_emitter(src, "test.s", writer)
        labels = program.resolver.current_scope.labels
        assert "fn" in labels


class TestRelocateNodeInternals:
    def test_relocate_str_repr(self) -> None:
        from a816.parse.nodes import RelocateNode

        resolver = Resolver()
        result = MZParser.parse_as_ast(
            """
            .pool p { range 0x028000 0x0280ff }
            .relocate fn 0x02c000 0x02c17f into p {
                rts
            }
            """,
            filename="t.s",
        )
        assert result.parse_error is None
        nodes = code_gen(result.nodes, resolver)
        relocs = [n for n in nodes if isinstance(n, RelocateNode)]
        assert relocs
        s = str(relocs[0])
        assert "fn" in s
        assert "0x02c000" in s
        assert "0x02c17f" in s
        assert "p" in s


class TestRelocateEndToEnd:
    def test_relocate_reclaims_old_range_into_pool(self) -> None:
        program = Program()
        writer = StubWriter()
        src = """
        .pool p { range 0x028000 0x0280ff }
        .relocate fn 0x02c000 0x02c17f into p {
            rts
        }
        """
        program.assemble_string_with_emitter(src, "test.s", writer)
        pool = program.resolver.pools["p"]
        # Pool now contains the original 0x100-byte range plus the
        # reclaimed 0x180-byte range, minus the 1 byte the body used.
        assert pool.capacity == 0x100 + 0x180
        assert pool.used == 1

    def test_relocate_binds_symbol_to_new_addr(self) -> None:
        program = Program()
        writer = StubWriter()
        src = """
        .pool p { range 0x028000 0x0280ff }
        .relocate fn 0x02c000 0x02c17f into p {
            rts
        }
        """
        program.assemble_string_with_emitter(src, "test.s", writer)
        labels = program.resolver.current_scope.labels
        assert "fn" in labels

    def test_relocate_reclaim_overlapping_pool_range_errors(self) -> None:
        program = Program()
        writer = StubWriter()
        src = """
        .pool p { range 0x02c100 0x02c200 }
        .relocate fn 0x02c150 0x02c1ff into p {
            rts
        }
        """
        with pytest.raises(Exception, match="overlap"):
            program.assemble_string_with_emitter(src, "test.s", writer)
