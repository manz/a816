"""Read IPS patches back in tests."""

from __future__ import annotations


def parse_ips_records(content: bytes) -> list[tuple[int, bytes]]:
    """Walk an IPS file and return [(physical_offset, data), ...]."""
    out: list[tuple[int, bytes]] = []
    i = 5  # skip "PATCH"
    while i < len(content) - 3:
        if content[i : i + 3] == b"EOF":
            break
        offset = int.from_bytes(content[i : i + 3], "big")
        i += 3
        size = int.from_bytes(content[i : i + 2], "big")
        i += 2
        if size == 0:
            # a816's IPS writer never emits RLE records; skip them.
            i += 3
            continue
        out.append((offset, content[i : i + size]))
        i += size
    return out
