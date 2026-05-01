"""`.adbg` debug-info producer and reader.

Format spec: docs/adbg-format.md.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any

MAGIC = b"ADBG"
VERSION = 1
NO_MODULE = 0xFFFFFFFF


class SectionKind(IntEnum):
    FILES = 1
    MODULES = 2
    SYMBOLS = 3
    LINES = 4
    STRINGS = 5


class SymbolScope(IntEnum):
    LOCAL = 0
    GLOBAL = 1
    EXTERNAL = 2


class SymbolKind(IntEnum):
    LABEL = 0
    CONSTANT = 1
    ALIAS = 2


@dataclass
class ModuleEntry:
    name: str
    file_idx: int
    base: int


@dataclass
class SymbolEntry:
    name: str
    address: int
    scope: SymbolScope
    module_idx: int = NO_MODULE
    kind: SymbolKind = SymbolKind.LABEL


@dataclass
class LineEntry:
    address: int
    file_idx: int
    line: int
    column: int
    module_idx: int = NO_MODULE
    flags: int = 0


@dataclass
class DebugInfo:
    files: list[str] = field(default_factory=list)
    modules: list[ModuleEntry] = field(default_factory=list)
    symbols: list[SymbolEntry] = field(default_factory=list)
    lines: list[LineEntry] = field(default_factory=list)

    def add_file(self, path: str) -> int:
        if path in self.files:
            return self.files.index(path)
        self.files.append(path)
        return len(self.files) - 1

    def add_module(self, name: str, file_idx: int, base: int) -> int:
        self.modules.append(ModuleEntry(name, file_idx, base))
        return len(self.modules) - 1


class _StringTable:
    """Null-separated UTF-8 blob; offset 0 maps to the empty string."""

    def __init__(self) -> None:
        self._buf = bytearray(b"\x00")
        self._index: dict[str, int] = {"": 0}

    def add(self, value: str) -> int:
        if value in self._index:
            return self._index[value]
        offset = len(self._buf)
        self._buf.extend(value.encode("utf-8"))
        self._buf.append(0)
        self._index[value] = offset
        return offset

    def serialize(self) -> bytes:
        return bytes(self._buf)


def _pack_files(files: list[str]) -> bytes:
    parts = [struct.pack("<I", len(files))]
    for path in files:
        encoded = path.encode("utf-8")
        parts.append(struct.pack("<H", len(encoded)))
        parts.append(encoded)
    return b"".join(parts)


def _pack_modules(modules: list[ModuleEntry], strings: _StringTable) -> bytes:
    parts = [struct.pack("<I", len(modules))]
    for module in modules:
        parts.append(struct.pack("<III", strings.add(module.name), module.file_idx, module.base))
    return b"".join(parts)


def _pack_symbols(symbols: list[SymbolEntry], strings: _StringTable) -> bytes:
    parts = [struct.pack("<I", len(symbols))]
    for sym in symbols:
        parts.append(
            struct.pack(
                "<IIBIB",
                strings.add(sym.name),
                sym.address & 0xFFFFFFFF,
                int(sym.scope),
                sym.module_idx & 0xFFFFFFFF,
                int(sym.kind),
            )
        )
    return b"".join(parts)


def _pack_lines(lines: list[LineEntry]) -> bytes:
    sorted_lines = sorted(lines, key=lambda entry: entry.address)
    parts = [struct.pack("<I", len(sorted_lines))]
    for entry in sorted_lines:
        parts.append(
            struct.pack(
                "<IIIHIB",
                entry.address & 0xFFFFFFFF,
                entry.file_idx,
                entry.line,
                entry.column & 0xFFFF,
                entry.module_idx & 0xFFFFFFFF,
                entry.flags & 0xFF,
            )
        )
    return b"".join(parts)


def _pack_strings(strings: _StringTable) -> bytes:
    blob = strings.serialize()
    return struct.pack("<I", len(blob)) + blob


def _section(kind: SectionKind, payload: bytes) -> bytes:
    return struct.pack("<II", int(kind), len(payload)) + payload


def serialize(info: DebugInfo) -> bytes:
    """Serialize a DebugInfo instance to bytes."""
    strings = _StringTable()
    sections: list[bytes] = [
        _section(SectionKind.FILES, _pack_files(info.files)),
        _section(SectionKind.MODULES, _pack_modules(info.modules, strings)),
        _section(SectionKind.SYMBOLS, _pack_symbols(info.symbols, strings)),
        _section(SectionKind.LINES, _pack_lines(info.lines)),
    ]
    sections.append(_section(SectionKind.STRINGS, _pack_strings(strings)))
    header = struct.pack("<4sHHI", MAGIC, VERSION, 0, len(sections))
    return header + b"".join(sections)


def write(info: DebugInfo, output_path: Path | str) -> None:
    Path(output_path).write_bytes(serialize(info))


class _Cursor:
    """Lightweight forward cursor over a bytes buffer."""

    __slots__ = ("_buf", "_offset")

    def __init__(self, buf: bytes) -> None:
        self._buf = buf
        self._offset = 0

    def read(self, fmt: str) -> tuple[Any, ...]:
        size = struct.calcsize(fmt)
        chunk = self._buf[self._offset : self._offset + size]
        if len(chunk) < size:
            raise ValueError("Unexpected end of buffer")
        self._offset += size
        return struct.unpack(fmt, chunk)

    def read_bytes(self, length: int) -> bytes:
        chunk = self._buf[self._offset : self._offset + length]
        if len(chunk) < length:
            raise ValueError("Unexpected end of buffer")
        self._offset += length
        return chunk

    def remaining(self) -> int:
        return len(self._buf) - self._offset

    def at_end(self) -> bool:
        return self._offset >= len(self._buf)


def _string_at(blob: bytes, offset: int) -> str:
    end = blob.find(b"\x00", offset)
    if end < 0:
        end = len(blob)
    return blob[offset:end].decode("utf-8")


def _parse_files(payload: bytes) -> list[str]:
    cursor = _Cursor(payload)
    (count,) = cursor.read("<I")
    files: list[str] = []
    for _ in range(count):
        (length,) = cursor.read("<H")
        files.append(cursor.read_bytes(length).decode("utf-8"))
    return files


def _parse_strings(payload: bytes) -> bytes:
    cursor = _Cursor(payload)
    (size,) = cursor.read("<I")
    return cursor.read_bytes(size)


def _parse_modules(payload: bytes, strings: bytes) -> list[ModuleEntry]:
    cursor = _Cursor(payload)
    (count,) = cursor.read("<I")
    modules: list[ModuleEntry] = []
    for _ in range(count):
        name_idx, file_idx, base = cursor.read("<III")
        modules.append(ModuleEntry(_string_at(strings, name_idx), file_idx, base))
    return modules


def _parse_symbols(payload: bytes, strings: bytes) -> list[SymbolEntry]:
    cursor = _Cursor(payload)
    (count,) = cursor.read("<I")
    symbols: list[SymbolEntry] = []
    for _ in range(count):
        name_idx, address, scope, module_idx, kind = cursor.read("<IIBIB")
        symbols.append(
            SymbolEntry(
                name=_string_at(strings, name_idx),
                address=address,
                scope=SymbolScope(scope),
                module_idx=module_idx,
                kind=SymbolKind(kind),
            )
        )
    return symbols


def _parse_lines(payload: bytes) -> list[LineEntry]:
    cursor = _Cursor(payload)
    (count,) = cursor.read("<I")
    lines: list[LineEntry] = []
    for _ in range(count):
        address, file_idx, line, column, module_idx, flags = cursor.read("<IIIHIB")
        lines.append(LineEntry(address, file_idx, line, column, module_idx, flags))
    return lines


def deserialize(buf: bytes) -> DebugInfo:
    """Deserialize a DebugInfo from bytes. Raises ValueError on bad input."""
    cursor = _Cursor(buf)
    magic, version, _flags, section_count = cursor.read("<4sHHI")
    if magic != MAGIC:
        raise ValueError(f"Bad magic: {magic!r}")
    if version != VERSION:
        raise ValueError(f"Unsupported .adbg version: {version}")

    raw_sections: dict[int, bytes] = {}
    for _ in range(section_count):
        kind, length = cursor.read("<II")
        raw_sections[kind] = cursor.read_bytes(length)

    strings_blob = _parse_strings(raw_sections.get(SectionKind.STRINGS, b"\x00\x00\x00\x00"))

    info = DebugInfo()
    info.files = _parse_files(raw_sections.get(SectionKind.FILES, b"\x00\x00\x00\x00"))
    info.modules = _parse_modules(raw_sections.get(SectionKind.MODULES, b"\x00\x00\x00\x00"), strings_blob)
    info.symbols = _parse_symbols(raw_sections.get(SectionKind.SYMBOLS, b"\x00\x00\x00\x00"), strings_blob)
    info.lines = _parse_lines(raw_sections.get(SectionKind.LINES, b"\x00\x00\x00\x00"))
    return info


def read(path: Path | str) -> DebugInfo:
    return deserialize(Path(path).read_bytes())
