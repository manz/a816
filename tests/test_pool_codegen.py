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
