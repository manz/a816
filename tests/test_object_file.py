import struct
from pathlib import Path

from a816.object_file import ObjectFile, RelocationType, SymbolSection, SymbolType


def test_write_empty_object_file(tmp_path: Path) -> None:
    test_filename = tmp_path / "test_object_file.o"
    obj = ObjectFile(b"", [], [])
    obj.write(str(test_filename))

    with open(test_filename, "rb") as f:
        header = f.read(16)
        magic, version, code_size, symbol_table_size, relocation_table_size, expression_relocation_table_size = (
            struct.unpack("<IHHHHI", header)
        )
        assert magic == ObjectFile.MAGIC_NUMBER
        assert version == ObjectFile.VERSION
        assert code_size == 0
        assert symbol_table_size == 2
        assert relocation_table_size == 2
        assert expression_relocation_table_size == 2
        # assert reserved == 0

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
        header = f.read(16)
        magic, version, code_size, symbol_table_size, relocation_table_size, expression_relocation_table_size = (
            struct.unpack("<IHHHHI", header)
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
        f.read(16)  # Skip header
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
        f.read(16)  # Skip header
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
        header = f.read(16)
        magic, version, code_size, symbol_table_size, relocation_table_size, expression_relocation_table_size = (
            struct.unpack("<IHHHHI", header)
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
