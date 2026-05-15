from __future__ import annotations

import pytest

from a816.parse.codegen import code_gen
from a816.parse.mzparser import MZParser
from a816.pool import Strategy
from a816.symbols import Resolver


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


class TestAllocPlaceholder:
    def test_alloc_into_unknown_pool_errors(self) -> None:
        with pytest.raises(Exception, match="unknown pool"):
            _gen(
                """
                .alloc fn in ghost {
                    rts
                }
                """,
            )

    def test_alloc_not_yet_wired(self) -> None:
        with pytest.raises(Exception, match="not yet wired up"):
            _gen(
                """
                .pool p { range 0x028000 0x0280ff }
                .alloc fn in p {
                    rts
                }
                """,
            )


class TestRelocatePlaceholder:
    def test_relocate_into_unknown_pool_errors(self) -> None:
        with pytest.raises(Exception, match="unknown pool"):
            _gen(
                """
                .relocate fn into ghost {
                    rts
                }
                """,
            )

    def test_relocate_not_yet_wired(self) -> None:
        with pytest.raises(Exception, match="not yet wired up"):
            _gen(
                """
                .pool p { range 0x028000 0x0280ff }
                .relocate fn into p {
                    rts
                }
                """,
            )
