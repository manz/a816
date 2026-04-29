import struct
from pathlib import Path

from a816.object_file import ObjectFile, RelocationType, SymbolSection, SymbolType

# Header format for version 3: <IHIIII (22 bytes)
# magic (4), version (2), code_size (4), symbol_table_size (4),
# relocation_table_size (4), expression_relocation_table_size (4)
HEADER_SIZE = 22
HEADER_FORMAT = "<IHIIII"


def test_write_empty_object_file(tmp_path: Path) -> None:
    test_filename = tmp_path / "test_object_file.o"
    obj = ObjectFile(b"", [], [])
    obj.write(str(test_filename))

    with open(test_filename, "rb") as f:
        header = f.read(HEADER_SIZE)
        magic, version, code_size, symbol_table_size, relocation_table_size, expression_relocation_table_size = (
            struct.unpack(HEADER_FORMAT, header)
        )
        assert magic == ObjectFile.MAGIC_NUMBER
        assert version == ObjectFile.VERSION
        assert code_size == 0
        assert symbol_table_size == 2
        assert relocation_table_size == 2
        assert expression_relocation_table_size == 2

        num_symbols = struct.unpack("<H", f.read(2))[0]
        assert num_symbols == 0

        num_relocations = struct.unpack("<H", f.read(2))[0]
        assert num_relocations == 0


def test_write_object_file_with_code(tmp_path: Path) -> None:
    test_filename = tmp_path / "test_object_file.o"
    code = b"\x01\x02\x03\x04"
    obj = ObjectFile(code, [], [])
    obj.write(str(test_filename))

    with open(test_filename, "rb") as f:
        header = f.read(HEADER_SIZE)
        magic, version, code_size, symbol_table_size, relocation_table_size, expression_relocation_table_size = (
            struct.unpack(HEADER_FORMAT, header)
        )
        assert magic == ObjectFile.MAGIC_NUMBER
        assert version == ObjectFile.VERSION
        assert code_size == len(code)
        assert symbol_table_size == 2
        assert relocation_table_size == 2
        assert expression_relocation_table_size == 2

        read_code = f.read(code_size)
        assert read_code == code

        num_symbols = struct.unpack("<H", f.read(2))[0]
        assert num_symbols == 0

        num_relocations = struct.unpack("<H", f.read(2))[0]
        assert num_relocations == 0


def test_write_object_file_with_symbols(tmp_path: Path) -> None:
    test_filename = tmp_path / "test_object_file.o"
    symbols: list[tuple[str, int, SymbolType, SymbolSection]] = [
        ("symbol1", 0x1000, SymbolType.LOCAL, SymbolSection.CODE),
        ("symbol2", 0x2000, SymbolType.GLOBAL, SymbolSection.DATA),
    ]
    obj = ObjectFile(b"", symbols, [])
    obj.write(str(test_filename))

    with open(test_filename, "rb") as f:
        f.read(HEADER_SIZE)  # Skip header
        f.read(0)  # Skip code section
        num_symbols = struct.unpack("<H", f.read(2))[0]
        assert num_symbols == len(symbols)

        for name, address, symbol_type, section in symbols:
            name_length = struct.unpack("<B", f.read(1))[0]
            read_name = f.read(name_length).decode("utf-8")
            read_address = struct.unpack("<I", f.read(4))[0]
            read_symbol_type = struct.unpack("<B", f.read(1))[0]
            read_section = struct.unpack("<B", f.read(1))[0]

            assert read_name == name
            assert read_address == address
            assert read_symbol_type == symbol_type.value
            assert read_section == section.value

        num_relocations = struct.unpack("<H", f.read(2))[0]
        assert num_relocations == 0


def test_write_object_file_with_relocations(tmp_path: Path) -> None:
    test_filename = tmp_path / "test_object_file.o"
    relocations: list[tuple[int, str, RelocationType]] = [
        (0x10, "symbol1", RelocationType.ABSOLUTE_16),
        (0x20, "symbol2", RelocationType.RELATIVE_24),
    ]
    obj = ObjectFile(b"", [], relocations)
    obj.write(str(test_filename))

    with open(test_filename, "rb") as f:
        f.read(HEADER_SIZE)  # Skip header
        f.read(0)  # Skip code section
        f.read(2)  # Skip symbol table size
        num_relocations = struct.unpack("<H", f.read(2))[0]
        assert num_relocations == len(relocations)

        for offset, symbol_name, relocation_type in relocations:
            read_offset = struct.unpack("<I", f.read(4))[0]
            name_length = struct.unpack("<B", f.read(1))[0]
            read_name = f.read(name_length).decode("utf-8")
            read_relocation_type = struct.unpack("<B", f.read(1))[0]

            assert read_offset == offset
            assert read_name == symbol_name
            assert read_relocation_type == relocation_type.value


def test_calculate_symbol_table_size() -> None:
    symbols: list[tuple[str, int, SymbolType, SymbolSection]] = [
        ("symbol1", 0x1000, SymbolType.LOCAL, SymbolSection.CODE),
        ("symbol2", 0x2000, SymbolType.GLOBAL, SymbolSection.DATA),
        ("a", 0x3000, SymbolType.EXTERNAL, SymbolSection.BSS),
    ]
    obj = ObjectFile(b"", symbols, [])
    expected_size = 2 + (1 + len("symbol1") + 4 + 1 + 1) + (1 + len("symbol2") + 4 + 1 + 1) + (1 + len("a") + 4 + 1 + 1)
    assert obj._calculate_symbol_table_size() == expected_size


def test_calculate_relocation_table_size() -> None:
    relocations: list[tuple[int, str, RelocationType]] = [
        (0x10, "symbol1", RelocationType.ABSOLUTE_16),
        (0x20, "symbol2", RelocationType.RELATIVE_24),
        (0x30, "a", RelocationType.RELATIVE_16),
    ]
    obj = ObjectFile(b"", [], relocations)
    expected_size = 2 + (4 + 1 + len("symbol1") + 1) + (4 + 1 + len("symbol2") + 1) + (4 + 1 + len("a") + 1)
    assert obj._calculate_relocation_table_size() == expected_size


def test_write_object_file_with_expression_relocations(tmp_path: Path) -> None:
    test_filename = tmp_path / "test_object_file.o"
    expression_relocations: list[tuple[int, str, int]] = [
        (0x10, "symbol1 + 5", 2),
        (0x20, "symbol2 - 1", 3),
    ]
    obj = ObjectFile(b"", [], [], expression_relocations)
    obj.write(str(test_filename))

    with open(test_filename, "rb") as f:
        header = f.read(HEADER_SIZE)
        magic, version, code_size, symbol_table_size, relocation_table_size, expression_relocation_table_size = (
            struct.unpack(HEADER_FORMAT, header)
        )
        assert magic == ObjectFile.MAGIC_NUMBER
        assert version == ObjectFile.VERSION
        assert code_size == 0
        assert symbol_table_size == 2
        assert relocation_table_size == 2
        expected_expr_size = 2 + (4 + 2 + len("symbol1 + 5") + 1) + (4 + 2 + len("symbol2 - 1") + 1)
        assert expression_relocation_table_size == expected_expr_size

        f.read(0)  # Skip code section
        f.read(2)  # Skip symbol table size
        f.read(2)  # Skip relocation table size
        num_expr_relocations = struct.unpack("<H", f.read(2))[0]
        assert num_expr_relocations == len(expression_relocations)

        for offset, expression, size_bytes in expression_relocations:
            read_offset = struct.unpack("<I", f.read(4))[0]
            expr_length = struct.unpack("<H", f.read(2))[0]
            read_expression = f.read(expr_length).decode("utf-8")
            read_size_bytes = struct.unpack("<B", f.read(1))[0]

            assert read_offset == offset
            assert read_expression == expression
            assert read_size_bytes == size_bytes


def test_read_write(tmp_path: Path) -> None:
    obj = ObjectFile(
        b"\x00\x01\x02\x03",
        [("sym1", 0x10, SymbolType.GLOBAL, SymbolSection.CODE)],
        [(0x02, "sym1", RelocationType.RELATIVE_16)],
    )
    obj.write(str(tmp_path / "test.o"))
    obj2 = ObjectFile.read(str(tmp_path / "test.o"))
    assert obj.code == obj2.code
    assert obj.symbols == obj2.symbols
    assert obj.relocations == obj2.relocations


def test_read_write_with_expression_relocations(tmp_path: Path) -> None:
    obj = ObjectFile(
        b"\x00\x01\x02\x03",
        [("sym1", 0x10, SymbolType.GLOBAL, SymbolSection.CODE)],
        [(0x02, "sym1", RelocationType.RELATIVE_16)],
        [(0x04, "sym2 + 10", 2)],
    )
    obj.write(str(tmp_path / "test.o"))
    obj2 = ObjectFile.read(str(tmp_path / "test.o"))
    assert obj.code == obj2.code
    assert obj.symbols == obj2.symbols
    assert obj.relocations == obj2.relocations
    assert obj.expression_relocations == obj2.expression_relocations


def test_symbol_offsets_in_scoped_blocks(tmp_path: Path) -> None:
    """Test that labels inside { } scoped blocks get correct relative offsets.

    This tests a bug where labels inside anonymous scoped blocks were getting
    logical addresses (0x8000+offset) instead of relative offsets (0+offset).
    """
    from a816.program import Program

    # Create test source with labels inside and outside scoped blocks
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
    assert result == 0, "Assembly should succeed"

    # Read the object file and check symbol offsets
    obj = ObjectFile.read(str(obj_file))

    # Build a map of symbol name to offset
    symbol_offsets = {name: offset for name, offset, _, _ in obj.symbols}

    # first_label should be at offset 0
    assert "first_label" in symbol_offsets
    assert symbol_offsets["first_label"] == 0, f"first_label should be at 0, got {symbol_offsets['first_label']}"

    # _inner_label should be at offset 3 (lda #imm = 2 bytes, rts = 1 byte)
    assert "_inner_label" in symbol_offsets
    inner_offset = symbol_offsets["_inner_label"]
    # The offset should be a small positive number, NOT 0x8000+
    assert inner_offset < 0x100, f"_inner_label should have small offset, got 0x{inner_offset:04x}"
    assert inner_offset == 3, f"_inner_label should be at 3, got {inner_offset}"

    # second_label should be at offset 6 (first_label code + inner_label code)
    assert "second_label" in symbol_offsets
    second_offset = symbol_offsets["second_label"]
    assert second_offset < 0x100, f"second_label should have small offset, got 0x{second_offset:04x}"
    assert second_offset == 6, f"second_label should be at 6, got {second_offset}"


def test_symbol_offsets_in_nested_scoped_blocks(tmp_path: Path) -> None:
    """Test labels in nested scoped blocks get correct offsets."""
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
    assert result == 0, "Assembly should succeed"

    obj = ObjectFile.read(str(obj_file))
    symbol_offsets = {name: offset for name, offset, _, _ in obj.symbols}

    # All offsets should be small (relative to start of code, not logical addresses)
    for name, offset in symbol_offsets.items():
        assert offset < 0x100, f"{name} should have small offset, got 0x{offset:04x}"

    # nop = 1 byte each
    assert symbol_offsets.get("outer") == 0
    assert symbol_offsets.get("_level1") == 1
    assert symbol_offsets.get("_level2") == 2
    assert symbol_offsets.get("_after_nested") == 3
    assert symbol_offsets.get("final") == 4
