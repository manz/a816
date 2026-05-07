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


def test_include_lines_attribute_to_included_file(tmp_path: Path) -> None:
    """`.include`d code should report lines against the included file, not the parent."""
    from a816.program import Program

    inc = tmp_path / "inc.s"
    inc.write_text("; comment line 1\n; comment line 2\nlda.b #0x10\nsta.b 0x20\n", encoding="utf-8")
    main = tmp_path / "main.s"
    main.write_text('*=0x008000\n.include "inc.s"\nlda.b #0x55\n', encoding="utf-8")

    program = Program()
    program.enable_debug_capture()
    program.assemble_as_patch(str(main), tmp_path / "out.ips")
    info = program.build_debug_info(str(main))

    by_addr = {entry.address: entry for entry in info.lines}
    files = info.files

    inc_lda = by_addr[0x008000]
    inc_sta = by_addr[0x008002]
    main_lda = by_addr[0x008004]

    assert Path(files[inc_lda.file_idx]).name == "inc.s"
    assert inc_lda.line == 2  # 0-indexed: third source line
    assert Path(files[inc_sta.file_idx]).name == "inc.s"
    assert inc_sta.line == 3
    assert Path(files[main_lda.file_idx]).name == "main.s"
    assert main_lda.line == 2


def test_multiline_docstring_does_not_skew_following_line_numbers(tmp_path: Path) -> None:
    """A multi-line docstring at the top must not push the next instruction's reported line."""
    from a816.program import Program

    src = tmp_path / "main.s"
    # `"""..."""` spans 3 source lines; opcode sits on line 4 (0-indexed: 3).
    src.write_text('"""\nmodule docstring\n"""\n*=0x008000\nlda.b #0x42\n', encoding="utf-8")

    program = Program()
    program.enable_debug_capture()
    program.assemble_as_patch(str(src), tmp_path / "out.ips")
    info = program.build_debug_info(str(src))

    lda = next(e for e in info.lines if e.address == 0x008000)
    assert lda.line == 4  # editor line 5 → 0-indexed 4
    assert lda.column == 0


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
