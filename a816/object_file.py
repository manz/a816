import struct
from enum import Enum
from typing import BinaryIO

INVALID_FILE_FORMAT = "Invalid file format"


class RelocationType(Enum):
    ABSOLUTE_16 = 0x00
    ABSOLUTE_24 = 0x01
    RELATIVE_16 = 0x02
    RELATIVE_24 = 0x03


class SymbolType(Enum):
    LOCAL = 0x00
    GLOBAL = 0x01
    EXTERNAL = 0x02


class SymbolSection(Enum):
    CODE = 0x00
    DATA = 0x01
    BSS = 0x02


class ObjectFile:
    MAGIC_NUMBER = 0x41383136  # 'A816'
    VERSION = 0x0004  # Version 4: alias table for symbols defined as deferred expressions

    def __init__(
        self,
        code: bytes,
        symbols: list[tuple[str, int, SymbolType, SymbolSection]],
        relocations: list[tuple[int, str, RelocationType]],
        expression_relocations: list[tuple[int, str, int]] | None = None,  # (offset, expression_str, size_bytes)
        aliases: list[tuple[str, str]] | None = None,  # (name, expression_str)
    ) -> None:
        self.code: bytes = code
        self.symbols: list[tuple[str, int, SymbolType, SymbolSection]] = symbols
        self.relocations: list[tuple[int, str, RelocationType]] = relocations
        self.expression_relocations: list[tuple[int, str, int]] = expression_relocations or []
        self.aliases: list[tuple[str, str]] = aliases or []

    def write(self, filename: str) -> None:
        with open(filename, "wb") as f:
            self._write_header(f)
            self._write_code_data_section(f)
            self._write_symbol_table(f)
            self._write_relocation_table(f)
            self._write_expression_relocation_table(f)
            self._write_alias_table(f)

    def _write_header(self, f: BinaryIO) -> None:
        code_size = len(self.code)
        symbol_table_size = self._calculate_symbol_table_size()
        relocation_table_size = self._calculate_relocation_table_size()
        expression_relocation_table_size = self._calculate_expression_relocation_table_size()
        alias_table_size = self._calculate_alias_table_size()

        header = struct.pack(
            "<IHIIIII",
            self.MAGIC_NUMBER,
            self.VERSION,
            code_size,
            symbol_table_size,
            relocation_table_size,
            expression_relocation_table_size,
            alias_table_size,
        )
        f.write(header)

    def _write_code_data_section(self, f: BinaryIO) -> None:
        f.write(self.code)

    def _write_symbol_table(self, f: BinaryIO) -> None:
        num_symbols = len(self.symbols)
        f.write(struct.pack("<H", num_symbols))
        for name, address, symbol_type, section in self.symbols:
            name_bytes = name.encode("utf-8")
            f.write(struct.pack("<B", len(name_bytes)))
            f.write(name_bytes)
            f.write(struct.pack("<I", address))
            f.write(struct.pack("<B", symbol_type.value))
            f.write(struct.pack("<B", section.value))

    def _write_relocation_table(self, f: BinaryIO) -> None:
        num_relocations = len(self.relocations)
        f.write(struct.pack("<H", num_relocations))
        for offset, symbol_name, relocation_type in self.relocations:
            name_bytes = symbol_name.encode("utf-8")
            f.write(struct.pack("<I", offset))
            f.write(struct.pack("<B", len(name_bytes)))
            f.write(name_bytes)
            f.write(struct.pack("<B", relocation_type.value))

    def _calculate_symbol_table_size(self) -> int:
        size = 2  # Number of symbols (2 bytes)
        for name, _, _, _ in self.symbols:
            size += 1  # Name length (1 byte)
            size += len(name)  # Name bytes
            size += 4  # Address (4 bytes)
            size += 1  # Symbol Type (1 byte)
            size += 1  # Symbol Section (1 byte)
        return size

    def _calculate_relocation_table_size(self) -> int:
        size = 2  # Number of relocations (2 bytes)
        for _, name, _ in self.relocations:
            size += 4  # Offset (4 bytes)
            size += 1  # Name length (1 byte)
            size += len(name)  # Name bytes
            size += 1  # Relocation Type (1 byte)
        return size

    def _write_expression_relocation_table(self, f: BinaryIO) -> None:
        num_expr_relocations = len(self.expression_relocations)
        f.write(struct.pack("<H", num_expr_relocations))
        for offset, expression, size_bytes in self.expression_relocations:
            expr_bytes = expression.encode("utf-8")
            f.write(struct.pack("<I", offset))
            f.write(struct.pack("<H", len(expr_bytes)))
            f.write(expr_bytes)
            f.write(struct.pack("<B", size_bytes))

    def _calculate_expression_relocation_table_size(self) -> int:
        size = 2  # Number of expression relocations (2 bytes)
        for _, expression, _ in self.expression_relocations:
            size += 4  # Offset (4 bytes)
            size += 2  # Expression length (2 bytes)
            size += len(expression)  # Expression bytes
            size += 1  # Size in bytes (1 byte)
        return size

    def _write_alias_table(self, f: BinaryIO) -> None:
        f.write(struct.pack("<H", len(self.aliases)))
        for name, expression in self.aliases:
            name_bytes = name.encode("utf-8")
            expr_bytes = expression.encode("utf-8")
            f.write(struct.pack("<B", len(name_bytes)))
            f.write(name_bytes)
            f.write(struct.pack("<H", len(expr_bytes)))
            f.write(expr_bytes)

    def _calculate_alias_table_size(self) -> int:
        size = 2
        for name, expression in self.aliases:
            size += 1 + len(name) + 2 + len(expression)
        return size

    @staticmethod
    def _read_symbols(f: BinaryIO) -> list[tuple[str, int, SymbolType, SymbolSection]]:
        num_symbols = struct.unpack("<H", f.read(2))[0]
        symbols = []
        for _ in range(num_symbols):
            name_length = struct.unpack("<B", f.read(1))[0]
            name = f.read(name_length).decode("utf-8")
            address = struct.unpack("<I", f.read(4))[0]
            symbol_type = SymbolType(struct.unpack("<B", f.read(1))[0])
            section = SymbolSection(struct.unpack("<B", f.read(1))[0])
            symbols.append((name, address, symbol_type, section))
        return symbols

    @staticmethod
    def _read_relocations(f: BinaryIO) -> list[tuple[int, str, RelocationType]]:
        num_relocations = struct.unpack("<H", f.read(2))[0]
        relocations = []
        for _ in range(num_relocations):
            offset = struct.unpack("<I", f.read(4))[0]
            name_length = struct.unpack("<B", f.read(1))[0]
            name = f.read(name_length).decode("utf-8")
            relocation_type = RelocationType(struct.unpack("<B", f.read(1))[0])
            relocations.append((offset, name, relocation_type))
        return relocations

    @staticmethod
    def _read_expression_relocations(f: BinaryIO, table_size: int) -> list[tuple[int, str, int]]:
        if table_size == 0:
            return []
        num_expr_relocations = struct.unpack("<H", f.read(2))[0]
        expression_relocations = []
        for _ in range(num_expr_relocations):
            offset = struct.unpack("<I", f.read(4))[0]
            expr_length = struct.unpack("<H", f.read(2))[0]
            expression = f.read(expr_length).decode("utf-8")
            size_bytes = struct.unpack("<B", f.read(1))[0]
            expression_relocations.append((offset, expression, size_bytes))
        return expression_relocations

    @staticmethod
    def _read_aliases(f: BinaryIO, table_size: int) -> list[tuple[str, str]]:
        if table_size == 0:
            return []
        num_aliases = struct.unpack("<H", f.read(2))[0]
        aliases: list[tuple[str, str]] = []
        for _ in range(num_aliases):
            name_length = struct.unpack("<B", f.read(1))[0]
            name = f.read(name_length).decode("utf-8")
            expr_length = struct.unpack("<H", f.read(2))[0]
            expression = f.read(expr_length).decode("utf-8")
            aliases.append((name, expression))
        return aliases

    @staticmethod
    def read(filename: str) -> "ObjectFile":
        with open(filename, "rb") as f:
            header_data = f.read(26)
            if len(header_data) < 26:
                raise ValueError(INVALID_FILE_FORMAT)
            (
                magic_number,
                version,
                code_size,
                _,
                _,
                expression_relocation_table_size,
                alias_table_size,
            ) = struct.unpack("<IHIIIII", header_data)
            if magic_number != ObjectFile.MAGIC_NUMBER:
                raise ValueError("Invalid magic number")
            if version != ObjectFile.VERSION:
                raise ValueError(f"Unsupported version: {version} (expected {ObjectFile.VERSION})")

            code = f.read(code_size)
            symbols = ObjectFile._read_symbols(f)
            relocations = ObjectFile._read_relocations(f)
            expression_relocations = ObjectFile._read_expression_relocations(f, expression_relocation_table_size)
            aliases = ObjectFile._read_aliases(f, alias_table_size)
            return ObjectFile(code, symbols, relocations, expression_relocations, aliases)
