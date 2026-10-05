"""Object files are written and read from their type annotations (`object_codec`).

The round trip is checked generically: a sample value is built for every
annotated field of every record type, so a field that doesn't survive
encoding can't slip through the way `bss` and `context` once did through
hand-written readers.
"""

from __future__ import annotations

import dataclasses
import struct
import typing
from enum import Enum
from pathlib import Path
from typing import Any

import pytest

from a816.object_codec import decode, encode, schema
from a816.object_file import (
    SCHEMA_DIGEST,
    BusMapping,
    ObjectFile,
    PoolAlloc,
    PoolDecl,
    Section,
    SymbolSection,
    SymbolType,
    WireObject,
    WireSection,
)


def _sample(tp: Any, seed: int) -> Any:
    """A non-default value of `tp`, different for each `seed`."""
    origin, args = typing.get_origin(tp), typing.get_args(tp)
    if origin in (typing.Union, __import__("types").UnionType):
        inner = next(arg for arg in args if arg is not type(None))
        return None if seed % 2 else _sample(inner, seed)
    if origin is list:
        return [_sample(args[0], seed + i) for i in range(3)]
    if origin is tuple:
        return tuple(_sample(arg, seed + i) for i, arg in enumerate(args))
    if isinstance(tp, type) and dataclasses.is_dataclass(tp):
        hints = typing.get_type_hints(tp)
        return tp(**{f.name: _sample(hints[f.name], seed + i) for i, f in enumerate(dataclasses.fields(tp))})
    if isinstance(tp, type) and issubclass(tp, Enum):
        members = list(tp)
        return members[seed % len(members)]
    if tp is bool:
        return bool(seed % 2)
    if tp is int:
        return -(seed * 977) if seed % 3 == 0 else seed * 104729
    if tp is str:
        return f"s{seed}-é" if seed % 4 else ""
    if tp is bytes:
        return bytes(range(seed % 7))
    raise TypeError(tp)


@pytest.mark.parametrize("record", [PoolDecl, PoolAlloc, BusMapping, WireSection, WireObject])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_every_field_round_trips(record: type, seed: int) -> None:
    value = _sample(record, seed)
    decoded, end = decode(record, encode(record, value))
    assert decoded == value
    assert end == len(encode(record, value))


@pytest.mark.parametrize("count", [0, 1, 5])
def test_columnar_symbol_tables_round_trip(count: int) -> None:
    tp = list[tuple[str, int, SymbolType, SymbolSection]]
    symbols = [(f"sym{i}" if i else "", i * -3, SymbolType.GLOBAL, SymbolSection.CODE) for i in range(count)]
    assert decode(tp, encode(tp, symbols))[0] == symbols


def test_a_nul_in_a_string_column_is_refused() -> None:
    with pytest.raises(ValueError, match="NUL"):
        encode(list[tuple[str, int]], [("a\x00b", 1)])


def test_an_unsupported_type_is_refused() -> None:
    with pytest.raises(TypeError):
        encode(dict[str, int], {})


def test_adding_a_field_changes_the_schema() -> None:
    @dataclasses.dataclass
    class Before:
        name: str

    @dataclasses.dataclass
    class After:
        name: str
        size: int = 0

    assert schema(Before).split("{")[1] != schema(After).split("{")[1]


def test_object_file_round_trips(tmp_path: Path) -> None:
    sample = _sample(WireObject, 1)
    sections = [Section.anonymous_pinned(base_address=0x8000, code=b"\x01\x02")]
    sections[0].bss = True
    obj = ObjectFile(
        sections,
        sample.symbols,
        aliases=sample.aliases,
        files=sample.files,
        relocatable=False,
        pool_decls=sample.pool_decls,
        pool_allocs=sample.pool_allocs,
        bus_mappings=sample.bus_mappings,
    )
    obj.write(str(tmp_path / "m.o"))
    back = ObjectFile.from_file(str(tmp_path / "m.o"))
    assert back.wire() == obj.wire()
    assert back.relocatable is False


def test_header_carries_schema_and_revision(tmp_path: Path) -> None:
    ObjectFile([], []).write(str(tmp_path / "m.o"))
    header = ObjectFile.read_header(str(tmp_path / "m.o"))
    assert header is not None
    assert header.schema == SCHEMA_DIGEST
    assert header.identity == ObjectFile.identity()


def test_an_object_of_another_schema_is_rejected_by_name(tmp_path: Path) -> None:
    path = tmp_path / "m.o"
    ObjectFile([], []).write(str(path))
    data = bytearray(path.read_bytes())
    data[7:23] = bytes(16)  # schema digest
    path.write_bytes(bytes(data))
    with pytest.raises(ValueError, match="rebuild it"):
        ObjectFile.from_file(str(path))


def test_a_short_file_with_the_magic_reports_its_version(tmp_path: Path) -> None:
    path = tmp_path / "old.o"
    path.write_bytes(struct.pack("<IHB", ObjectFile.MAGIC_NUMBER, 5, 0))
    header = ObjectFile.read_header(str(path))
    assert header is not None and header.version == 5


def test_a_file_without_the_magic_has_no_header(tmp_path: Path) -> None:
    path = tmp_path / "junk.o"
    path.write_bytes(b"not an object at all")
    assert ObjectFile.read_header(str(path)) is None
