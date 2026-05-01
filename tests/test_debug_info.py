"""Round-trip tests for the .adbg debug-info container."""

from __future__ import annotations

from pathlib import Path

from a816.debug_info import (
    MAGIC,
    DebugInfo,
    LineEntry,
    ModuleEntry,
    SymbolEntry,
    SymbolKind,
    SymbolScope,
    deserialize,
    read,
    serialize,
    write,
)


def _sample_info() -> DebugInfo:
    info = DebugInfo()
    info.files = ["src/main.s", "modules/vwf.s", "modules/dma.s"]
    info.modules = [
        ModuleEntry(name="__main__", file_idx=0, base=0x008000),
        ModuleEntry(name="vwf", file_idx=1, base=0x028000),
        ModuleEntry(name="dma", file_idx=2, base=0x038000),
    ]
    info.symbols = [
        SymbolEntry(name="main", address=0x008000, scope=SymbolScope.GLOBAL, module_idx=0),
        SymbolEntry(
            name="vwf_render",
            address=0x028010,
            scope=SymbolScope.GLOBAL,
            module_idx=1,
            kind=SymbolKind.LABEL,
        ),
        SymbolEntry(
            name="DMA_REG",
            address=0x4300,
            scope=SymbolScope.GLOBAL,
            module_idx=0,
            kind=SymbolKind.CONSTANT,
        ),
    ]
    info.lines = [
        LineEntry(address=0x008005, file_idx=0, line=12, column=4, module_idx=0),
        LineEntry(address=0x008000, file_idx=0, line=10, column=0, module_idx=0),
        LineEntry(address=0x028010, file_idx=1, line=7, column=0, module_idx=1, flags=1),
    ]
    return info


def test_round_trip_in_memory() -> None:
    info = _sample_info()
    blob = serialize(info)
    assert blob.startswith(MAGIC)
    decoded = deserialize(blob)

    assert decoded.files == info.files
    assert decoded.modules == info.modules
    assert decoded.symbols == info.symbols
    # Lines come back sorted by address.
    assert [entry.address for entry in decoded.lines] == [0x008000, 0x008005, 0x028010]


def test_round_trip_via_disk(tmp_path: Path) -> None:
    info = _sample_info()
    path = tmp_path / "build.adbg"
    write(info, path)
    decoded = read(path)
    assert decoded.files == info.files
    assert decoded.modules == info.modules


def test_deserialize_rejects_bad_magic() -> None:
    bogus = b"NOPE" + b"\x00" * 12
    try:
        deserialize(bogus)
    except ValueError as exc:
        assert "magic" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError on bad magic")


def test_string_table_dedupes() -> None:
    info = DebugInfo()
    info.modules = [
        ModuleEntry(name="vwf", file_idx=0, base=0),
        ModuleEntry(name="vwf", file_idx=0, base=1),
    ]
    info.symbols = [
        SymbolEntry(name="vwf", address=0, scope=SymbolScope.GLOBAL),
    ]
    blob = serialize(info)
    # Only one "vwf" string in the table (length 4 = "vwf\0" + leading null).
    assert blob.count(b"vwf\x00") == 1
