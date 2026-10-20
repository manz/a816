"""Reading, applying and extending IPS patches in memory (the writer is `writers.IPSWriter`)."""

from __future__ import annotations

from dataclasses import dataclass

from a816.error_codes import E_IPS_MALFORMED, ErrorCode
from a816.errors import format_error_simple

MAGIC = b"PATCH"
END = b"EOF"


class IpsError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def format(self) -> str:
        return format_error_simple(f"ips error[{self.code}]", self.message)


@dataclass(frozen=True)
class Record:
    offset: int
    data: bytes
    start: int  # where the record begins in the patch


@dataclass(frozen=True)
class Patch:
    records: list[Record]
    end: int  # where `EOF` begins in the patch


def parse(patch: bytes) -> Patch:
    """Every record, RLE ones expanded. A truncation size after `EOF` is kept, not read."""
    if not patch.startswith(MAGIC):
        raise IpsError(E_IPS_MALFORMED, "does not start with PATCH")
    out: list[Record] = []
    at = len(MAGIC)
    while patch[at : at + 3] != END:
        if at + 5 > len(patch):
            raise IpsError(E_IPS_MALFORMED, f"the record at byte {at} runs past the end, with no EOF")
        start = at
        offset = int.from_bytes(patch[at : at + 3], "big")
        size = int.from_bytes(patch[at + 3 : at + 5], "big")
        at += 5
        if size == 0:
            count = int.from_bytes(patch[at : at + 2], "big")
            out.append(Record(offset, patch[at + 2 : at + 3] * count, start))
            at += 3
        else:
            out.append(Record(offset, patch[at : at + size], start))
            at += size
    return Patch(out, at)


def apply(patch: bytes, base: bytes) -> bytes:
    """`base` with the records written, grown as far as the last one reaches."""
    rom = bytearray(base)
    for record in parse(patch).records:
        end = record.offset + len(record.data)
        if end > len(rom):
            rom += bytes(end - len(rom))
        rom[record.offset : end] = record.data
    return bytes(rom)


def record_bytes(offset: int, data: bytes) -> bytes:
    return offset.to_bytes(3, "big") + len(data).to_bytes(2, "big") + data


def append_record(patch: bytes, offset: int, data: bytes) -> bytes:
    end = parse(patch).end
    return patch[:end] + record_bytes(offset, data) + patch[end:]


def set_last_record(patch: bytes, offset: int, data: bytes) -> bytes:
    """`patch` ending with a record writing `data` at `offset`.

    A last record already at `offset` with as many bytes is replaced, so
    sealing twice leaves one record.
    """
    parsed = parse(patch)
    cut = parsed.end
    if parsed.records:
        last = parsed.records[-1]
        if last.offset == offset and len(last.data) == len(data):
            cut = last.start
    return patch[:cut] + record_bytes(offset, data) + patch[parsed.end :]
