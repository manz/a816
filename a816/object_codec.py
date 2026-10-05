"""Binary encoding of object-file records, derived from their type annotations.

Every table the object format carries (sections, symbols, pools, bus maps) is
a dataclass or a typed tuple. The codec compiles one encoder and one decoder
per annotated type, once, so a new field is one annotated line: nothing to
mirror by hand in a reader and a writer. `schema(tp)` describes a type's whole
wire shape; its digest is the format version, so any change to a field
changes it.

Encoding:
- int: signed 64-bit; bool: one byte; Enum: its value (an int); str, bytes:
  u32 length + bytes; `X | None`: one tag byte, then X.
- dataclass / fixed tuple: its fields in order.
- `list[R]` where R is a record of scalars (int, bool, Enum, str): columnar,
  one packed column per field (`array('q')` for numbers, a NUL-joined blob
  for strings), so 68k symbols decode at C speed. Other lists: u32 count +
  items.
"""

from __future__ import annotations

import dataclasses
import struct
import types
import typing
from array import array
from collections.abc import Callable
from enum import Enum
from functools import cache
from typing import Any, TypeGuard

Encoder = Callable[[Any, bytearray], None]
Decoder = Callable[[memoryview, int], tuple[Any, int]]

_U32 = struct.Struct("<I")
_I64 = struct.Struct("<q")
_SCALARS = (int, bool, str)


def encode(tp: Any, value: Any) -> bytes:
    out = bytearray()
    _codec(tp)[0](value, out)
    return bytes(out)


def decode(tp: Any, data: bytes | memoryview, offset: int = 0) -> tuple[Any, int]:
    """Decode a `tp` at `offset`; return it and the offset just past it."""
    return _codec(tp)[1](memoryview(data), offset)


@cache
def schema(tp: Any) -> str:
    """Canonical description of `tp`'s wire shape (field names and types, in order)."""
    origin, args = typing.get_origin(tp), typing.get_args(tp)
    if _is_optional(tp):
        return f"opt[{schema(_optional_inner(tp))}]"
    if origin is list:
        return f"list[{schema(args[0])}]"
    if origin is tuple:
        return "tuple[" + ",".join(schema(arg) for arg in args) + "]"
    if _is_dataclass_type(tp):
        hints = typing.get_type_hints(tp)
        fields = ",".join(f"{f.name}:{schema(hints[f.name])}" for f in dataclasses.fields(tp))
        return f"{tp.__name__}{{{fields}}}"
    if isinstance(tp, type) and issubclass(tp, Enum):
        return f"enum:{tp.__name__}"
    if tp in (int, bool, str, bytes):
        return str(tp.__name__)
    raise TypeError(f"object codec cannot encode {tp!r}")


@cache
def _codec(tp: Any) -> tuple[Encoder, Decoder]:
    origin, args = typing.get_origin(tp), typing.get_args(tp)
    if _is_optional(tp):
        return _optional_codec(_optional_inner(tp))
    if origin is list:
        return _columnar_codec(args[0]) if _is_flat_record(args[0]) else _list_codec(args[0])
    if origin is tuple:
        return _record_codec([_codec(arg) for arg in args], lambda values: tuple(values), lambda v: v)
    if _is_dataclass_type(tp):
        return _dataclass_codec(tp)
    if isinstance(tp, type) and issubclass(tp, Enum):
        return _enum_codec(tp)
    if tp is bool:
        return _bool_codec()
    if tp is int:
        return _int_codec()
    if tp is str:
        return _str_codec()
    if tp is bytes:
        return _bytes_codec()
    raise TypeError(f"object codec cannot encode {tp!r}")


# --- scalars ---


def _int_codec() -> tuple[Encoder, Decoder]:
    def enc(value: int, out: bytearray) -> None:
        out.extend(_I64.pack(value))

    def dec(data: memoryview, offset: int) -> tuple[int, int]:
        return _I64.unpack_from(data, offset)[0], offset + 8

    return enc, dec


def _bool_codec() -> tuple[Encoder, Decoder]:
    def enc(value: bool, out: bytearray) -> None:
        out.append(1 if value else 0)

    def dec(data: memoryview, offset: int) -> tuple[bool, int]:
        return bool(data[offset]), offset + 1

    return enc, dec


def _bytes_codec() -> tuple[Encoder, Decoder]:
    def enc(value: bytes, out: bytearray) -> None:
        out.extend(_U32.pack(len(value)))
        out.extend(value)

    def dec(data: memoryview, offset: int) -> tuple[bytes, int]:
        (size,) = _U32.unpack_from(data, offset)
        start = offset + 4
        return bytes(data[start : start + size]), start + size

    return enc, dec


def _str_codec() -> tuple[Encoder, Decoder]:
    enc_bytes, dec_bytes = _bytes_codec()

    def enc(value: str, out: bytearray) -> None:
        enc_bytes(value.encode("utf-8"), out)

    def dec(data: memoryview, offset: int) -> tuple[str, int]:
        raw, offset = dec_bytes(data, offset)
        return raw.decode("utf-8"), offset

    return enc, dec


def _enum_codec(enum: type[Enum]) -> tuple[Encoder, Decoder]:
    enc_int, dec_int = _int_codec()

    def enc(value: Enum, out: bytearray) -> None:
        enc_int(value.value, out)

    def dec(data: memoryview, offset: int) -> tuple[Enum, int]:
        raw, offset = dec_int(data, offset)
        return enum(raw), offset

    return enc, dec


# --- composites ---


def _optional_codec(inner: Any) -> tuple[Encoder, Decoder]:
    enc_inner, dec_inner = _codec(inner)

    def enc(value: Any, out: bytearray) -> None:
        if value is None:
            out.append(0)
        else:
            out.append(1)
            enc_inner(value, out)

    def dec(data: memoryview, offset: int) -> tuple[Any, int]:
        if data[offset] == 0:
            return None, offset + 1
        return dec_inner(data, offset + 1)

    return enc, dec


def _list_codec(item: Any) -> tuple[Encoder, Decoder]:
    enc_item, dec_item = _codec(item)

    def enc(values: list[Any], out: bytearray) -> None:
        out.extend(_U32.pack(len(values)))
        for value in values:
            enc_item(value, out)

    def dec(data: memoryview, offset: int) -> tuple[list[Any], int]:
        (count,) = _U32.unpack_from(data, offset)
        offset += 4
        out = []
        for _ in range(count):
            value, offset = dec_item(data, offset)
            out.append(value)
        return out, offset

    return enc, dec


def _record_codec(
    fields: list[tuple[Encoder, Decoder]], build: Callable[[list[Any]], Any], parts: Callable[[Any], Any]
) -> tuple[Encoder, Decoder]:
    """Fields in order; `parts(value)` yields them, `build(values)` rebuilds the record."""
    encoders = [enc for enc, _ in fields]
    decoders = [dec for _, dec in fields]

    def enc(value: Any, out: bytearray) -> None:
        for encoder, part in zip(encoders, parts(value), strict=True):
            encoder(part, out)

    def dec(data: memoryview, offset: int) -> tuple[Any, int]:
        values = []
        for decoder in decoders:
            part, offset = decoder(data, offset)
            values.append(part)
        return build(values), offset

    return enc, dec


def _dataclass_codec(cls: Any) -> tuple[Encoder, Decoder]:
    hints = typing.get_type_hints(cls)
    names = [f.name for f in dataclasses.fields(cls)]
    return _record_codec(
        [_codec(hints[name]) for name in names],
        lambda values: cls(**dict(zip(names, values, strict=True))),
        lambda value: [getattr(value, name) for name in names],
    )


# --- columnar lists of flat records ---


def _record_fields(tp: Any) -> list[Any] | None:
    """Field types of a tuple or dataclass record, else None."""
    if typing.get_origin(tp) is tuple:
        return list(typing.get_args(tp))
    if _is_dataclass_type(tp):
        hints = typing.get_type_hints(tp)
        return [hints[f.name] for f in dataclasses.fields(tp)]
    return None


def _is_scalar(tp: Any) -> bool:
    return tp in _SCALARS or (isinstance(tp, type) and issubclass(tp, Enum))


def _is_flat_record(tp: Any) -> bool:
    fields = _record_fields(tp)
    return fields is not None and all(_is_scalar(field) for field in fields)


def _columnar_codec(record: Any) -> tuple[Encoder, Decoder]:
    fields = _record_fields(record) or []
    columns = [_column_codec(field) for field in fields]
    if _is_dataclass_type(record):
        names = [f.name for f in dataclasses.fields(record)]

        def split(values: list[Any]) -> list[list[Any]]:
            return [[getattr(value, name) for value in values] for name in names]

        def join(cols: list[list[Any]]) -> list[Any]:
            return [record(*row) for row in zip(*cols, strict=True)]

    else:

        def split(values: list[Any]) -> list[list[Any]]:
            return [list(col) for col in zip(*values, strict=True)] if values else [[] for _ in fields]

        def join(cols: list[list[Any]]) -> list[Any]:
            return list(zip(*cols, strict=True))

    def enc(values: list[Any], out: bytearray) -> None:
        out.extend(_U32.pack(len(values)))
        for (enc_column, _), column in zip(columns, split(values), strict=True):
            enc_column(column, out)

    def dec(data: memoryview, offset: int) -> tuple[list[Any], int]:
        (count,) = _U32.unpack_from(data, offset)
        offset += 4
        cols = []
        for _, dec_column in columns:
            column, offset = dec_column(data, offset, count)
            cols.append(column)
        return join(cols), offset

    return enc, dec


ColumnDecoder = Callable[[memoryview, int, int], tuple[list[Any], int]]


def _column_codec(tp: Any) -> tuple[Callable[[list[Any], bytearray], None], ColumnDecoder]:
    if tp is str:
        return _str_column()
    convert: Callable[[int], Any]
    if tp is bool:
        convert = bool
    elif tp is int:
        convert = int
    else:
        convert = {member.value: member for member in tp}.__getitem__  # an Enum: member by value
    unwrap = (lambda v: v.value) if isinstance(tp, type) and issubclass(tp, Enum) else int

    def enc(column: list[Any], out: bytearray) -> None:
        out.extend(array("q", (unwrap(v) for v in column)).tobytes())

    def dec(data: memoryview, offset: int, count: int) -> tuple[list[Any], int]:
        end = offset + 8 * count
        raw = array("q")
        raw.frombytes(data[offset:end])
        return ([convert(v) for v in raw] if convert is not int else raw.tolist()), end

    return enc, dec


def _str_column() -> tuple[Callable[[list[str], bytearray], None], ColumnDecoder]:
    def enc(column: list[str], out: bytearray) -> None:
        if any("\x00" in value for value in column):
            raise ValueError("object codec: a string column value contains NUL")
        blob = "\x00".join(column).encode("utf-8")
        out.extend(_U32.pack(len(blob)))
        out.extend(blob)

    def dec(data: memoryview, offset: int, count: int) -> tuple[list[str], int]:
        (size,) = _U32.unpack_from(data, offset)
        start = offset + 4
        end = start + size
        if count == 0:
            return [], end
        return bytes(data[start:end]).decode("utf-8").split("\x00"), end

    return enc, dec


# --- typing helpers ---


def _is_dataclass_type(tp: Any) -> TypeGuard[type[Any]]:
    return isinstance(tp, type) and dataclasses.is_dataclass(tp)


def _is_optional(tp: Any) -> bool:
    if typing.get_origin(tp) not in (typing.Union, types.UnionType):
        return False
    args = typing.get_args(tp)
    return len(args) == 2 and type(None) in args


def _optional_inner(tp: Any) -> Any:
    return next(arg for arg in typing.get_args(tp) if arg is not type(None))
