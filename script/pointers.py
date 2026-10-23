"""Deprecated: see `katsuji.script.pointers`."""

from katsuji.script import Table
from katsuji.script.pointers import (
    FormulaProtocol,
    Pointer,
    Script,
    read_pointers_from_xml,
    recode_pointer_values,
    write_pointers_addresses_as_binary,
    write_pointers_as_xml,
    write_pointers_value_as_binary,
)

__all__ = [
    "FormulaProtocol",
    "Pointer",
    "Script",
    "Table",
    "read_pointers_from_xml",
    "recode_pointer_values",
    "write_pointers_addresses_as_binary",
    "write_pointers_as_xml",
    "write_pointers_value_as_binary",
]
