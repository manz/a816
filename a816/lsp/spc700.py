"""SPC700 in the language server: a `*.spc.s` document gets its mnemonics and registers."""

from __future__ import annotations

from a816.cpu.arch import arch_for_path
from a816.cpu.spc700 import REGISTERS, SPC700_TABLE


def is_spc700_uri(uri: str) -> bool:
    return arch_for_path(uri) == "spc700"


def spc700_forms(mnemonic: str) -> list[str]:
    """Every operand form of `mnemonic`, as written: `mov a, dp`, `mov1 c, mem.bit`."""
    return [
        f"{mnemonic} {', '.join(encoding.patterns)}".rstrip() for encoding in SPC700_TABLE.get(mnemonic.lower(), [])
    ]


SPC700_REGISTER_NAMES = frozenset(REGISTERS)
