"""Section: the unifying primitive for ROM byte placement.

A section is a chunk of emitted bytes plus a placement strategy. Every
emit-bearing node in a module belongs to exactly one section. The
linker walks every module's sections and places them according to the
strategy:

- `PINNED` — base address fixed at parse time (today's `*=`, soon
  `.alloc [NAME] at ADDR[..END] { ... }`).
- `POOLED` — base address chosen by the cross-module pool allocator
  inside a named pool's ranges (today's `.alloc NAME in POOL { ... }`).

No implicit catch-all pool. Top-level emit outside `*=` / `.alloc`
is an error: every byte has an explicit home.

Sections supersede the historical `Region` concept in `object_file.py`.
Region carried no placement metadata — its base address was either
fixed (set by `*=` during compile) or "wherever the linker decides"
(implicit, encoded by the absence of a pool reference). The Section
model surfaces the strategy explicitly so the linker dispatches on
data, not on convention.

See `/Users/manz/.claude/plans/referencing-struct-fields-could-smooth-treehouse.md`
for the broader sections refactor design that this module anchors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from a816.object_file import RelocationType


class Placement(Enum):
    """Where the linker should land a section's bytes."""

    PINNED = 0
    """Base address fixed at parse time.

    Source forms:
      * `*= ADDR` (legacy sugar) — unbounded.
      * `.alloc [NAME] at ADDR[..END] { ... }` (new) — optionally
        bounded by an inclusive `END`. Body overflow past `END` is a
        hard error.

    Cross-section byte overlap between two pinned sections is a
    hard error (no more silent last-write-wins).
    """

    POOLED = 1
    """Base address chosen by the pool allocator at link time.

    Source form: `.alloc NAME in POOL { ... }`.

    The linker's cross-module pool allocator (`_allocate_pools_across_modules`)
    decides where the section's bytes land inside one of POOL's declared
    ranges. NAME binds the chosen address as a global symbol.
    """


@dataclass
class Section:
    """A contiguous span of emitted code with an explicit placement strategy.

    Replaces / generalises `object_file.Region`. Carries everything the
    linker needs to place the bytes: the strategy (pinned or pooled),
    the strategy-specific anchor (literal address vs pool name), and
    the size bound (for pinned) or pool slot size (for pooled).

    Offsets in `relocations`, `expression_relocations`, and `lines`
    are byte offsets into this section's `code`, not into the
    concatenated module.

    Sections are emitted in source order; the linker sorts by
    placement strategy (pinned first, then pooled allocations).
    """

    name: str
    """Stable identifier for diagnostics + adbg.

    Named allocs (`.alloc reset in client { ... }`) carry the source
    label. Anonymous pinned sections (legacy `*=` or `.alloc at ADDR
    { ... }` without a NAME) get an auto-generated identifier
    (`<module>#pin_<n>`) so xobj output + overlap diagnostics still
    have something to point at.
    """

    placement: Placement
    """PINNED vs POOLED — see `Placement` enum docstring."""

    code: bytes
    """Emitted body bytes."""

    base_address: int | None = None
    """Set at parse time for PINNED sections; set at link time for POOLED
    sections (allocator picks the address). `None` until the linker has
    resolved it.
    """

    end_address: int | None = None
    """Inclusive upper bound for PINNED sections declared with `..END`.

    `None` means unbounded (legacy `*=` and `.alloc at ADDR` without
    `..END`). Body overflow past a non-`None` `end_address` raises at
    section-finalize time, pointing at the overflowing byte.

    Always `None` for POOLED sections (the pool's ranges bound them).
    """

    pool_name: str | None = None
    """Pool name for POOLED sections; `None` for PINNED.

    POOLED sections feed the link-time allocator under this name. The
    pool itself must be declared via `.pool NAME { range ... }` (today
    in the same module or via shared preamble; cross-module merging
    happens at link time).
    """

    relocations: list[tuple[int, str, RelocationType]] = field(default_factory=list)
    """Symbol relocations: (offset_into_code, symbol_name, reloc_type)."""

    expression_relocations: list[tuple[int, str, int]] = field(default_factory=list)
    """Expression relocations for deferred cross-module operand evaluation:
    (offset_into_code, expression_blob, size_bytes).
    """

    lines: list[tuple[int, int, int, int, int]] = field(default_factory=list)
    """Source-line provenance for adbg debug info."""

    def __post_init__(self) -> None:
        if self.placement is Placement.PINNED and self.pool_name is not None:
            raise ValueError(
                f"section {self.name!r}: PINNED placement cannot carry a pool_name",
            )
        if self.placement is Placement.POOLED and self.end_address is not None:
            raise ValueError(
                f"section {self.name!r}: POOLED placement is bounded by its pool's ranges, not end_address",
            )
        if self.placement is Placement.POOLED and self.pool_name is None:
            raise ValueError(
                f"section {self.name!r}: POOLED placement requires a pool_name",
            )

    @property
    def size(self) -> int:
        """Body size in bytes. Defined regardless of placement strategy."""
        return len(self.code)

    @property
    def is_bounded(self) -> bool:
        """True iff this section has an explicit upper bound. PINNED with
        `..END` or POOLED (bounded by pool ranges) both count."""
        return self.end_address is not None or self.placement is Placement.POOLED

    def overflows(self) -> bool:
        """True iff a PINNED section's body extends past its declared
        `end_address`. Always False for POOLED (pool allocator handles
        capacity errors via PoolOverflowError) and for unbounded PINNED.
        """
        if self.placement is not Placement.PINNED:
            return False
        if self.end_address is None or self.base_address is None:
            return False
        last_byte = self.base_address + self.size - 1
        return last_byte > self.end_address

    @classmethod
    def anonymous_pinned(
        cls,
        base_address: int,
        code: bytes,
        *,
        relocations: list[tuple[int, str, RelocationType]] | None = None,
        expression_relocations: list[tuple[int, str, int]] | None = None,
        lines: list[tuple[int, int, int, int, int]] | None = None,
        end_address: int | None = None,
    ) -> Section:
        """Convenience for the common case: a PINNED section with an
        auto-generated name derived from `base_address`.

        Used by the wire-format reader (no name metadata yet),
        legacy-shape constructors (`ObjectFile(bytes)`, ad-hoc test
        sections), and the `*=` parse-time desugar pass. Once every
        site can supply a real name, this can shrink.
        """
        return cls(
            name=f"__anon_pin_{base_address:06X}",
            placement=Placement.PINNED,
            code=code,
            base_address=base_address,
            end_address=end_address,
            relocations=list(relocations or []),
            expression_relocations=list(expression_relocations or []),
            lines=list(lines or []),
        )
