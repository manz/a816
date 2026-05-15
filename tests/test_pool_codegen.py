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
