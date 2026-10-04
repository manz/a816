"""Super Famicom cartridge boards, from ares' `boards.bml`.

`boards.bml` is vendored from ares (ISC, see `LICENSE.ares`) at
`ARES_REVISION`. Only the parts a816 needs are read: the ROM and RAM
`map` lines of each board, in bsnes form, which `a816.toml`'s
`board = "SHVC-1A3M-30"` expands into `[map.N]`-style regions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files

ARES_REVISION = "4af59c63f1e0d2e9fdf89770f3fd726d463770e2"


@dataclass(frozen=True)
class BoardRegion:
    """One `map` line of a board's ROM or RAM, in bsnes form."""

    address: str
    mask: int = 0
    base: int = 0
    writable: bool = False


@dataclass
class _Node:
    name: str
    value: str
    attrs: dict[str, str]
    children: list[_Node] = field(default_factory=list)


def _parse_line(text: str) -> _Node:
    """`board: NAME`, `memory type=ROM content=Program`, `map address=00-3f:8000-ffff mask=0x8000`."""
    head, _, rest = text.partition(" ")
    if head.endswith(":"):
        return _Node(head[:-1], rest.strip(), {})
    attrs = dict(token.split("=", 1) for token in rest.split() if "=" in token)
    return _Node(head, "", attrs)


def _parse_bml(text: str) -> list[_Node]:
    """Indentation-structured BML: two spaces per level, `//` comments."""
    roots: list[_Node] = []
    stack: list[tuple[int, _Node]] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("//"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        node = _parse_line(stripped)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        (stack[-1][1].children if stack else roots).append(node)
        stack.append((indent, node))
    return roots


def _expand_names(name: str) -> list[str]:
    """`SHVC-1A3M-(10,20)` -> `SHVC-1A3M-10`, `SHVC-1A3M-20`; other names as they are."""
    match = re.fullmatch(r"(.*)\(([^)]*)\)(.*)", name)
    if match is None:
        return [name]
    prefix, variants, suffix = match.groups()
    return [f"{prefix}{variant.strip()}{suffix}" for variant in variants.split(",")]


def _map_regions(node: _Node, writable: bool) -> list[BoardRegion]:
    return [
        BoardRegion(
            address=child.attrs["address"],
            mask=int(child.attrs.get("mask", "0"), 0),
            base=int(child.attrs.get("base", "0"), 0),
            writable=writable,
        )
        for child in node.children
        if child.name == "map"
    ]


def _is_program_rom(node: _Node) -> bool:
    return node.name == "memory" and node.attrs.get("type") == "ROM"


def _regions(node: _Node) -> list[BoardRegion]:
    """ROM and RAM regions under ``node``, in file order. Slots and MMIO maps are not memory."""
    out: list[BoardRegion] = []
    for child in node.children:
        if child.name == "slot":
            continue
        if child.name == "memory":
            out += _map_regions(child, writable=child.attrs.get("type") != "ROM")
        elif child.name == "mcu" and any(_is_program_rom(grand) for grand in child.children):
            # A coprocessor's mcu window is how the CPU sees program ROM (SA-1, SDD-1, ...).
            out += _map_regions(child, writable=False)
            out += _regions(child)
        else:
            out += _regions(child)
    return out


@cache
def boards() -> dict[str, tuple[BoardRegion, ...]]:
    """Every board name (variants expanded) to its memory regions."""
    text = files(__package__).joinpath("boards.bml").read_text(encoding="utf-8")
    out: dict[str, tuple[BoardRegion, ...]] = {}
    for node in _parse_bml(text):
        if node.name != "board":
            continue
        regions = tuple(_regions(node))
        for name in _expand_names(node.value):
            out[name] = regions
    return out
