import struct
from enum import Enum
from typing import BinaryIO


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
    VERSION = 0x0003  # Version 3: 32-bit sizes to support large files

    def __init__(
        self,
        code: bytes,
        symbols: list[tuple[str, int, SymbolType, SymbolSection]],
        relocations: list[tuple[int, str, RelocationType]],
        expression_relocations: list[tuple[int, str, int]] | None = None,  # (offset, expression_str, size_bytes)
    ) -> None:
        self.code: bytes = code
        self.symbols: list[tuple[str, int, SymbolType, SymbolSection]] = symbols
        self.relocations: list[tuple[int, str, RelocationType]] = relocations
        self.expression_relocations: list[tuple[int, str, int]] = expression_relocations or []

    def write(self, filename: str) -> None:
        with open(filename, "wb") as f:
            self._write_header(f)
            self._write_code_data_section(f)
            self._write_symbol_table(f)
            self._write_relocation_table(f)
            self._write_expression_relocation_table(f)

    def _write_header(self, f: BinaryIO) -> None:
        code_size = len(self.code)
        symbol_table_size = self._calculate_symbol_table_size()
        relocation_table_size = self._calculate_relocation_table_size()
        expression_relocation_table_size = self._calculate_expression_relocation_table_size()

        header = struct.pack(
            "<IHIIII",
            self.MAGIC_NUMBER,
            self.VERSION,
            code_size,
            symbol_table_size,
            relocation_table_size,
            expression_relocation_table_size,
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

    @staticmethod
    def read(filename: str) -> "ObjectFile":
        with open(filename, "rb") as f:
            # Read enough bytes for version 3 header (22 bytes)
            header_data = f.read(22)
            if len(header_data) < 6:  # Minimum: magic + version
                raise ValueError("Invalid file format")

            # First read magic and version to determine format
            magic_number, version = struct.unpack("<IH", header_data[:6])

            if magic_number != ObjectFile.MAGIC_NUMBER:
                raise ValueError("Invalid magic number")

            # Parse header based on version
            if version == 0x0001:
                # Version 1: <IHHHI (14 bytes)
                if len(header_data) < 14:
                    raise ValueError("Invalid file format")
                _, _, code_size, symbol_table_size, relocation_table_size = struct.unpack("<IHHHI", header_data[:14])
                expression_relocation_table_size = 0
                # Seek back if we read too much
                f.seek(14)
            elif version == 0x0002:
                # Version 2: <IHHHHI (16 bytes)
                if len(header_data) < 16:
                    raise ValueError("Invalid file format")
                (
                    _,
                    _,
                    code_size,
                    symbol_table_size,
                    relocation_table_size,
                    expression_relocation_table_size,
                ) = struct.unpack("<IHHHHI", header_data[:16])
                # Seek back if we read too much
                f.seek(16)
            elif version == 0x0003:
                # Version 3: <IHIIII (22 bytes) - 32-bit sizes
                if len(header_data) < 22:
                    raise ValueError("Invalid file format")
                (
                    _,
                    _,
                    code_size,
                    symbol_table_size,
                    relocation_table_size,
                    expression_relocation_table_size,
                ) = struct.unpack("<IHIIII", header_data)
            else:
                raise ValueError(f"Unsupported version: {version}")

            code = f.read(code_size)

            num_symbols = struct.unpack("<H", f.read(2))[0]
            symbols = []
            for _ in range(num_symbols):
                name_length = struct.unpack("<B", f.read(1))[0]
                name = f.read(name_length).decode("utf-8")
                address = struct.unpack("<I", f.read(4))[0]
                symbol_type = SymbolType(struct.unpack("<B", f.read(1))[0])
                section = SymbolSection(struct.unpack("<B", f.read(1))[0])
                symbols.append((name, address, symbol_type, section))

            num_relocations = struct.unpack("<H", f.read(2))[0]
            relocations = []
            for _ in range(num_relocations):
                offset = struct.unpack("<I", f.read(4))[0]
                name_length = struct.unpack("<B", f.read(1))[0]
                name = f.read(name_length).decode("utf-8")
                relocation_type = RelocationType(struct.unpack("<B", f.read(1))[0])
                relocations.append((offset, name, relocation_type))

            # Read expression relocations if present (version 2+)
            expression_relocations = []
            if version >= 0x0002 and expression_relocation_table_size > 0:
                num_expr_relocations = struct.unpack("<H", f.read(2))[0]
                for _ in range(num_expr_relocations):
                    offset = struct.unpack("<I", f.read(4))[0]
                    expr_length = struct.unpack("<H", f.read(2))[0]
                    expression = f.read(expr_length).decode("utf-8")
                    size_bytes = struct.unpack("<B", f.read(1))[0]
                    expression_relocations.append((offset, expression, size_bytes))

            return ObjectFile(code, symbols, relocations, expression_relocations)
