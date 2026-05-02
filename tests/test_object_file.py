from pathlib import Path

from a816.object_file import ObjectFile, Region, RelocationType, SymbolSection, SymbolType


def test_write_empty_object_file(tmp_path: Path) -> None:
    test_filename = tmp_path / "test_object_file.o"
    obj = ObjectFile([], [])
    obj.write(str(test_filename))

    obj2 = ObjectFile.from_file(str(test_filename))
    assert obj2.regions == []
    assert obj2.symbols == []


def test_write_object_file_with_code(tmp_path: Path) -> None:
    test_filename = tmp_path / "test_object_file.o"
    code = b"\x01\x02\x03\x04"
    obj = ObjectFile([Region(base_address=0x008000, code=code)], [])
    obj.write(str(test_filename))

    obj2 = ObjectFile.from_file(str(test_filename))
    assert len(obj2.regions) == 1
    assert obj2.regions[0].base_address == 0x008000
    assert obj2.regions[0].code == code


def test_write_object_file_with_symbols(tmp_path: Path) -> None:
    test_filename = tmp_path / "test_object_file.o"
    symbols: list[tuple[str, int, SymbolType, SymbolSection]] = [
        ("symbol1", 0x1000, SymbolType.LOCAL, SymbolSection.CODE),
        ("symbol2", 0x2000, SymbolType.GLOBAL, SymbolSection.DATA),
    ]
    obj = ObjectFile([], symbols)
    obj.write(str(test_filename))

    obj2 = ObjectFile.from_file(str(test_filename))
    assert obj2.symbols == symbols


def test_write_object_file_with_relocations(tmp_path: Path) -> None:
    test_filename = tmp_path / "test_object_file.o"
    relocations: list[tuple[int, str, RelocationType]] = [
        (0x10, "symbol1", RelocationType.ABSOLUTE_16),
        (0x20, "symbol2", RelocationType.RELATIVE_24),
    ]
    region = Region(base_address=0, code=b"\x00" * 0x40, relocations=relocations)
    obj = ObjectFile([region], [])
    obj.write(str(test_filename))

    obj2 = ObjectFile.from_file(str(test_filename))
    assert obj2.regions[0].relocations == relocations


def test_write_object_file_with_expression_relocations(tmp_path: Path) -> None:
    test_filename = tmp_path / "test_object_file.o"
    expression_relocations: list[tuple[int, str, int]] = [
        (0x10, "symbol1 + 5", 2),
        (0x20, "symbol2 - 1", 3),
    ]
    region = Region(base_address=0, code=b"\x00" * 0x40, expression_relocations=expression_relocations)
    obj = ObjectFile([region], [])
    obj.write(str(test_filename))

    obj2 = ObjectFile.from_file(str(test_filename))
    assert obj2.regions[0].expression_relocations == expression_relocations


def test_read_write(tmp_path: Path) -> None:
    obj = ObjectFile(
        b"\x00\x01\x02\x03",
        [("sym1", 0x10, SymbolType.GLOBAL, SymbolSection.CODE)],
        [(0x02, "sym1", RelocationType.RELATIVE_16)],
    )
    obj.write(str(tmp_path / "test.o"))
    obj2 = ObjectFile.from_file(str(tmp_path / "test.o"))
    assert obj.code == obj2.code
    assert obj.symbols == obj2.symbols
    assert obj.relocations == obj2.relocations


def test_read_write_with_expression_relocations(tmp_path: Path) -> None:
    obj = ObjectFile(
        b"\x00\x01\x02\x03",
        [("sym1", 0x10, SymbolType.GLOBAL, SymbolSection.CODE)],
        [(0x02, "sym1", RelocationType.RELATIVE_16)],
        [(0x00, "sym2 + 10", 2)],
    )
    obj.write(str(tmp_path / "test.o"))
    obj2 = ObjectFile.from_file(str(tmp_path / "test.o"))
    assert obj.code == obj2.code
    assert obj.symbols == obj2.symbols
    assert obj.relocations == obj2.relocations
    assert obj.expression_relocations == obj2.expression_relocations


def test_multi_region_round_trip(tmp_path: Path) -> None:
    regions = [
        Region(base_address=0x008000, code=b"\xea\xea"),
        Region(
            base_address=0x018000,
            code=b"\xa9\x42",
            expression_relocations=[(1, "external + 1", 1)],
        ),
    ]
    obj = ObjectFile(regions, [], relocatable=False)
    obj.write(str(tmp_path / "multi.o"))
    obj2 = ObjectFile.from_file(str(tmp_path / "multi.o"))
    assert len(obj2.regions) == 2
    assert obj2.regions[0].base_address == 0x008000
    assert obj2.regions[0].code == b"\xea\xea"
    assert obj2.regions[1].base_address == 0x018000
    assert obj2.regions[1].code == b"\xa9\x42"
    assert obj2.regions[1].expression_relocations == [(1, "external + 1", 1)]
    assert obj2.relocatable is False


def test_unsupported_version_rejected(tmp_path: Path) -> None:
    """Older .o file versions are rejected — there is no v5 reader."""
    import struct

    path = tmp_path / "old.o"
    with open(path, "wb") as f:
        f.write(struct.pack("<IHB", ObjectFile.MAGIC_NUMBER, 0x0005, 0x00))
    try:
        ObjectFile.from_file(str(path))
    except ValueError as e:
        assert "Unsupported version" in str(e)
    else:
        raise AssertionError("expected ValueError for v5 .o file")


def test_symbol_offsets_in_scoped_blocks(tmp_path: Path) -> None:
    """Labels resolve to absolute logical addresses, including those in nested scopes."""
    from a816.program import Program

    source = """\
first_label:
    lda #0x42
    rts
{
_inner_label:
    lda #0x00
    rts
}
second_label:
    lda #0x01
    rts
"""
    src_file = tmp_path / "test_scoped.s"
    src_file.write_text(source)

    obj_file = tmp_path / "test_scoped.o"
    program = Program()
    result = program.assemble_as_object(str(src_file), obj_file)
    assert result == 0

    obj = ObjectFile.from_file(str(obj_file))
    by_name = {name: address for name, address, _, _ in obj.symbols}
    assert "first_label" in by_name
    assert "_inner_label" in by_name
    assert "second_label" in by_name
    # Labels are 3 bytes apart (lda #imm = 2 + rts = 1).
    assert by_name["_inner_label"] - by_name["first_label"] == 3
    assert by_name["second_label"] - by_name["first_label"] == 6


def test_symbol_offsets_in_nested_scoped_blocks(tmp_path: Path) -> None:
    from a816.program import Program

    source = """\
outer:
    nop
{
_level1:
    nop
    {
    _level2:
        nop
    }
_after_nested:
    nop
}
final:
    nop
"""
    src_file = tmp_path / "test_nested.s"
    src_file.write_text(source)

    obj_file = tmp_path / "test_nested.o"
    program = Program()
    result = program.assemble_as_object(str(src_file), obj_file)
    assert result == 0

    obj = ObjectFile.from_file(str(obj_file))
    by_name = {name: address for name, address, _, _ in obj.symbols}
    base = by_name["outer"]
    # nop = 1 byte; consecutive labels are one byte apart.
    assert by_name["_level1"] - base == 1
    assert by_name["_level2"] - base == 2
    assert by_name["_after_nested"] - base == 3
    assert by_name["final"] - base == 4
