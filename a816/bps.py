"""BPS patches: encode a linear one, apply any, seal, read the header.

BPS (byuu's beat format) records the source and target sizes and the
CRC32 of the source, the target and the patch itself, so a patcher
rejects a wrong or headered ROM instead of producing a broken game.

a816 writes a patch without the base ROM, so the target CRC is not known
at build time: `encode_linear` leaves it zero, and `seal` fills it in
once the ROM is at hand. Such an unsealed patch is not valid BPS until
then. `apply` handles all four actions, so it applies Flips' delta
patches as well as a816's linear ones.
"""

from __future__ import annotations

import re
import struct
import zlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import partial
from xml.sax.saxutils import escape

from a816.error_codes import E_BPS_MALFORMED, E_BPS_SOURCE_MISMATCH, E_BPS_UNDEFINED_VARIABLE, ErrorCode
from a816.exceptions import A816ConfigError, A816Error

MAGIC = b"BPS1"
_TRAILER = 12
_SOURCE_READ, _TARGET_READ, _SOURCE_COPY, _TARGET_COPY = range(4)
COPIER_HEADER = 0x200


class BpsError(A816Error):
    """A malformed patch, or a ROM that is not the one the patch expects."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def format(self) -> str:
        # Late import: intentional to avoid circular dependency with errors module
        from a816.errors import format_error_simple

        return format_error_simple(f"bps error[{self.code}]", self.message)


@dataclass(frozen=True)
class BpsInfo:
    """What a BPS header and trailer record."""

    source_size: int
    target_size: int
    metadata: str
    source_crc32: int
    target_crc32: int
    patch_crc32: int

    @property
    def sealed(self) -> bool:
        """An unsealed patch carries a zero target CRC."""
        return self.target_crc32 != 0


def encode_number(value: int) -> bytes:
    """beat's variable-length number: 7 bits a byte, the last one flagged."""
    out = bytearray()
    while True:
        low = value & 0x7F
        value >>= 7
        if value == 0:
            out.append(0x80 | low)
            return bytes(out)
        out.append(low)
        value -= 1


def _encode_offset(offset: int) -> bytes:
    """A relative copy offset: the magnitude shifted left, the sign in bit 0."""
    return encode_number(abs(offset) << 1 | (offset < 0))


def _action(kind: int, length: int) -> bytes:
    return encode_number((length - 1) << 2 | kind)


def merge_blocks(blocks: Iterable[tuple[int, bytes]], size: int) -> tuple[bytearray, bytearray]:
    """Paint `blocks` in order onto a `size` image: a later write wins.

    Returns the image and a mask holding 1 where a block wrote, which is
    what applying the same blocks as an IPS gives over the source.
    """
    image = bytearray(size)
    mask = bytearray(size)
    for address, data in blocks:
        image[address : address + len(data)] = data
        mask[address : address + len(data)] = b"\x01" * len(data)
    return image, mask


def blocks_end(blocks: Iterable[tuple[int, bytes]]) -> int:
    """One past the last byte any block writes (0 for no block)."""
    return max((address + len(data) for address, data in blocks), default=0)


class _LinearEncoder:
    """Emits the action stream, tracking where TargetCopy reads from."""

    def __init__(self, source_size: int) -> None:
        self.out = bytearray()
        self.source_size = source_size
        self.target_relative = 0

    def gap(self, start: int, end: int) -> None:
        """Bytes no block wrote: the source's own, zeros past it."""
        inside = min(end, self.source_size)
        if start < inside:
            self.out += _action(_SOURCE_READ, inside - start)
            start = inside
        if start < end:
            self._zeros(start, end - start)

    def literal(self, data: bytes | bytearray) -> None:
        self.out += _action(_TARGET_READ, len(data))
        self.out += data

    def _zeros(self, at: int, length: int) -> None:
        """One zero, then TargetCopy re-reading it as it grows: run-length."""
        self.literal(b"\x00")
        if length == 1:
            return
        self.out += _action(_TARGET_COPY, length - 1)
        self.out += _encode_offset(at - self.target_relative)
        self.target_relative = at + length - 1


def encode_linear(
    blocks: Iterable[tuple[int, bytes]],
    *,
    source_size: int,
    source_crc32: int,
    target_size: int,
    metadata: str = "",
) -> bytes:
    """An unsealed linear patch writing `blocks` (address, bytes) over the source.

    Blocks may come in any order and overlap: a later one wins. The target
    is `target_size` long; unwritten bytes come from the source inside it
    and are zero past it. The target CRC is left zero for `seal`.
    """
    blocks = list(blocks)
    if blocks_end(blocks) > target_size:
        raise ValueError(f"a block ends at {blocks_end(blocks):#x}, past the target size {target_size:#x}")
    image, mask = merge_blocks(blocks, target_size)
    encoded_metadata = metadata.encode("utf-8")
    encoder = _LinearEncoder(source_size)
    encoder.out += MAGIC
    encoder.out += encode_number(source_size) + encode_number(target_size)
    encoder.out += encode_number(len(encoded_metadata)) + encoded_metadata
    cursor = 0
    while cursor < target_size:
        written = mask.find(1, cursor)
        if written == -1:
            written = target_size
        encoder.gap(cursor, written)
        if written == target_size:
            break
        cursor = mask.find(0, written)
        if cursor == -1:
            cursor = target_size
        encoder.literal(image[written:cursor])
    encoder.out += struct.pack("<II", source_crc32, 0)
    return _with_patch_crc(encoder.out)


def _with_patch_crc(body: bytes | bytearray) -> bytes:
    return bytes(body) + struct.pack("<I", zlib.crc32(body))


class _Reader:
    def __init__(self, data: bytes, end: int) -> None:
        self.data = data
        self.at = 0
        self.end = end

    def take(self, count: int) -> bytes:
        if self.at + count > self.end:
            raise BpsError(E_BPS_MALFORMED, "the patch is truncated")
        chunk = self.data[self.at : self.at + count]
        self.at += count
        return chunk

    def number(self) -> int:
        value, shift = 0, 1
        while True:
            byte = self.take(1)[0]
            value += (byte & 0x7F) * shift
            if byte & 0x80:
                return value
            shift <<= 7
            value += shift

    def offset(self) -> int:
        raw = self.number()
        return -(raw >> 1) if raw & 1 else raw >> 1


def _header(patch: bytes) -> tuple[_Reader, int, int, str]:
    if len(patch) < len(MAGIC) + _TRAILER or not patch.startswith(MAGIC):
        raise BpsError(E_BPS_MALFORMED, "not a BPS patch (no `BPS1` header)")
    reader = _Reader(patch, len(patch) - _TRAILER)
    reader.take(len(MAGIC))
    source_size = reader.number()
    target_size = reader.number()
    metadata = reader.take(reader.number()).decode("utf-8", errors="replace")
    return reader, source_size, target_size, metadata


def info(patch: bytes) -> BpsInfo:
    """The sizes, CRCs and metadata `patch` records; checks only its shape."""
    _, source_size, target_size, metadata = _header(patch)
    source_crc, target_crc, patch_crc = struct.unpack("<III", patch[-_TRAILER:])
    return BpsInfo(source_size, target_size, metadata, source_crc, target_crc, patch_crc)


def check_source(patch_info: BpsInfo, source: bytes) -> None:
    """Reject a ROM the patch was not made for, naming a copier header."""
    if len(source) != patch_info.source_size:
        hint = ""
        if len(source) == patch_info.source_size + COPIER_HEADER:
            hint = "; it carries a 512-byte copier header, strip it first"
        raise BpsError(
            E_BPS_SOURCE_MISMATCH,
            f"the source ROM is {len(source):#x} bytes, the patch expects {patch_info.source_size:#x}{hint}",
        )
    crc = zlib.crc32(source)
    if crc != patch_info.source_crc32:
        raise BpsError(
            E_BPS_SOURCE_MISMATCH,
            f"the source ROM's CRC32 is {crc:08X}, the patch expects {patch_info.source_crc32:08X}",
        )


def _check_patch_crc(patch: bytes, patch_info: BpsInfo) -> None:
    crc = zlib.crc32(patch[:-4])
    if crc != patch_info.patch_crc32:
        raise BpsError(
            E_BPS_MALFORMED, f"the patch's CRC32 is {crc:08X}, its trailer says {patch_info.patch_crc32:08X}"
        )


def _from_source(source: bytes, start: int, length: int, action: str) -> bytes:
    if start < 0 or start + length > len(source):
        raise BpsError(E_BPS_MALFORMED, f"a {action} reads outside the source ROM")
    return source[start : start + length]


def _target_copy(target: bytearray, start: int, length: int) -> None:
    """Copy from the target itself; an overlapping run repeats its period."""
    if not 0 <= start < len(target):
        raise BpsError(E_BPS_MALFORMED, "a TargetCopy reads outside the bytes written so far")
    period = len(target) - start
    if period >= length:
        target += target[start : start + length]
    else:
        target += (target[start:] * (length // period + 1))[:length]


def _run(reader: _Reader, source: bytes, target_size: int) -> bytearray:
    target = bytearray()
    source_relative = target_relative = 0
    while reader.at < reader.end:
        data = reader.number()
        kind, length = data & 3, (data >> 2) + 1
        if kind == _SOURCE_READ:
            target += _from_source(source, len(target), length, "SourceRead")
        elif kind == _TARGET_READ:
            target += reader.take(length)
        elif kind == _SOURCE_COPY:
            source_relative += reader.offset()
            target += _from_source(source, source_relative, length, "SourceCopy")
            source_relative += length
        else:
            target_relative += reader.offset()
            _target_copy(target, target_relative, length)
            target_relative += length
        if len(target) > target_size:
            raise BpsError(E_BPS_MALFORMED, "the patch writes past its target size")
    if len(target) != target_size:
        raise BpsError(E_BPS_MALFORMED, f"the patch writes {len(target):#x} bytes, its target size is {target_size:#x}")
    return target


def apply(patch: bytes, source: bytes, *, unsealed: bool = False) -> bytes:
    """The target `patch` makes from `source`, every CRC checked.

    `unsealed` skips the target CRC, which an unsealed patch leaves zero.
    """
    patch_info = info(patch)
    _check_patch_crc(patch, patch_info)
    check_source(patch_info, source)
    reader, _, target_size, _ = _header(patch)
    target = bytes(_run(reader, source, target_size))
    if not unsealed:
        crc = zlib.crc32(target)
        if crc != patch_info.target_crc32:
            raise BpsError(
                E_BPS_MALFORMED,
                f"the patched ROM's CRC32 is {crc:08X}, the patch expects {patch_info.target_crc32:08X}",
            )
    return target


def seal(patch: bytes, source: bytes) -> bytes:
    """`patch` with the target CRC it gives on `source`, the patch CRC redone."""
    target = apply(patch, source, unsealed=True)
    body = patch[:-8] + struct.pack("<I", zlib.crc32(target))
    return _with_patch_crc(body)


_VARIABLE = re.compile(r"\$\{([^}]*)\}")
_QUOTE = {'"': "&quot;"}


def metadata_xml(attributes: Mapping[str, str], defines: Mapping[str, str]) -> str:
    """`[bps.metadata]` as one `<patch …/>` element, `${NAME}` filled from `defines`.

    `defines` maps each name to its value as spelled (`-D VERSION=0x10`
    gives `0x10`). An empty table gives no metadata at all.
    """
    if not attributes:
        return ""

    def fill(key: str, match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in defines:
            raise A816ConfigError(
                E_BPS_UNDEFINED_VARIABLE,
                f"`${{{name}}}` in [bps.metadata] {key} names no define; pass -D {name}=... or declare it under [defines]",
            )
        return defines[name]

    pairs = [f'{key}="{escape(_VARIABLE.sub(partial(fill, key), value), _QUOTE)}"' for key, value in attributes.items()]
    return f"<patch {' '.join(pairs)}/>"
