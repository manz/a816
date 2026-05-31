"""Underscore-private labels stay local to their `.alloc` body.

Pre-fix: two sibling `.alloc` bodies in the same module both
declaring `_skip:` collided in the module's flat label namespace
and `bne _skip` in the first block resolved to the last block's
`_skip`, silently miscoding the branch (`d0 07` instead of `d0 01`).
Same shape for `jmp.w _end`.

Fix opens an `AllocBodyScope` around each body so scope-chain
lookup finds the local label first; non-underscore body labels
bubble back to the parent on PopScope so cross-alloc public
references still resolve.
"""

from __future__ import annotations

from a816.program import Program
from tests import StubWriter


def _assemble_bytes(src: str) -> bytes:
    program = Program()
    writer = StubWriter()
    program.assemble_string_with_emitter(src, "t.s", writer)
    return b"".join(writer.data)


_POOL = """
.pool client {
    range 0x008000 0x00FFEF
    strategy order
}
"""


def test_underscore_label_branches_resolve_within_own_alloc() -> None:
    src = (
        _POOL
        + """
        .alloc routine_a in client {
            lda.b #0x00
            bne _skip
            rts
        _skip:
            rts
        }
        .alloc routine_b in client {
            lda.b #0x00
            bne _skip
            rts
        _skip:
            nop
            nop
            rts
        }
        """
    )
    data = _assemble_bytes(src)
    # routine_a: lda #0; bne +1; rts; rts
    assert data[:6] == b"\xa9\x00\xd0\x01\x60\x60", f"routine_a bytes wrong: {data[:6].hex()}"
    # routine_b: lda #0; bne +1 (same shape, own `_skip` one byte past
    # the rts); rts; nop; nop; rts. The branch offset is `+1` again
    # because both bodies have the same instruction layout — what the
    # bug was hiding is which `_skip` the relative offset *resolved
    # against*, not the offset value itself.
    assert data[6:13] == b"\xa9\x00\xd0\x01\x60\xea\xea", f"routine_b bytes wrong: {data[6:13].hex()}"


def test_underscore_label_absolute_jump_resolves_locally() -> None:
    src = (
        _POOL
        + """
        .alloc demo in client {
            nop
            jmp.w _end
            nop
            nop
        _end:
            rts
        }
        .alloc later in client {
            nop
            nop
        _end:
            rts
        }
        """
    )
    data = _assemble_bytes(src)
    # demo at $8000: nop (1) + jmp.w _end (3) + nop + nop (2) + _end:rts (1) = 7 bytes,
    # _end at $8000 + 6 = $8006.
    assert data[1] == 0x4C, f"expected jmp.w opcode at byte 1, got 0x{data[1]:02x}"
    target = data[2] | (data[3] << 8)
    assert target == 0x8006, f"jmp.w should target demo's `_end` at $8006, got ${target:04x}"


def test_public_label_in_alloc_still_callable_from_sibling() -> None:
    # Non-underscore labels must bubble out so cross-alloc references
    # keep working (the privacy convention is underscore = private).
    src = (
        _POOL
        + """
        .alloc helper in client {
        do_thing:
            rts
        }
        .alloc caller in client {
            jsr.w do_thing
            rts
        }
        """
    )
    # No assertion on bytes — just verify the build doesn't error on the
    # cross-alloc public reference.
    _assemble_bytes(src)
