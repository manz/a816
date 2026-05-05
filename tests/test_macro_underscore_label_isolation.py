"""Regression test: underscore-prefixed labels inside repeated macro invocations
must each resolve to a distinct address. Earlier `_label` skipped per-scope
mangling, so PEA-style operands (`pea.w _return_addr - 1`) collapsed every
invocation onto the first one's address."""

from __future__ import annotations

import tempfile
from pathlib import Path

from a816.object_file import ObjectFile
from a816.program import Program


def _pea_operands(data: bytes) -> list[int]:
    operands: list[int] = []
    idx = 0
    while True:
        i = data.find(b"\xf4", idx)
        if i < 0 or i + 3 > len(data):
            return operands
        operands.append(data[i + 1] | (data[i + 2] << 8))
        idx = i + 1


def test_underscore_label_distinct_per_macro_invocation_in_ips() -> None:
    src = """
.macro callee_with_return(arg) {
    pea.w _return_addr - 1
    pea.w arg
    jmp.w some_target
_return_addr:
    rts
}

*=0x008000
some_target:
    rts

start:
    callee_with_return(0x1111)
    callee_with_return(0x2222)
    callee_with_return(0x3333)
"""
    with tempfile.TemporaryDirectory() as tmp:
        asm = Path(tmp) / "main.s"
        asm.write_text(src)
        ips = Path(tmp) / "out.ips"
        rc = Program().assemble_as_patch(str(asm), ips)
        assert rc == 0
        operands = _pea_operands(ips.read_bytes())
        # Three return-address PEAs and three arg PEAs; return addresses differ.
        return_pea_values = [v for i, v in enumerate(operands) if i % 2 == 0]
        assert len(set(return_pea_values)) == 3, return_pea_values


def test_underscore_label_distinct_per_macro_invocation_in_object_file() -> None:
    """Object-file path must record distinct symbols and per-call relocations."""
    src = """
.macro callee_with_return(arg) {
    pea.w _return_addr - 1
    pea.w arg
    jmp.w some_target
_return_addr:
    rts
}

*=0x008000
some_target:
    rts

start:
    callee_with_return(0x1111)
    callee_with_return(0x2222)
    callee_with_return(0x3333)
"""
    with tempfile.TemporaryDirectory() as tmp:
        asm = Path(tmp) / "main.s"
        asm.write_text(src)
        obj = Path(tmp) / "main.o"
        rc = Program().assemble_as_object(str(asm), obj)
        assert rc == 0
        loaded = ObjectFile.from_file(str(obj))
        return_addrs = [(name, addr) for name, addr, _t, _s in loaded.symbols if name.endswith("_return_addr")]
        assert len(return_addrs) == 3, return_addrs
        # Each invocation should produce its own mangled relocation expression.
        return_relocs = [expr for _offset, expr, _size in loaded.expression_relocations if "_return_addr" in expr]
        assert len(return_relocs) == 3
        assert len(set(return_relocs)) == 3, return_relocs
