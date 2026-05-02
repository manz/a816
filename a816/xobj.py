"""xobj - inspect a816 .o object files.

Companion to the IPS/SFC emit trace. xobj shows pre-link intent (what each
.o claims for regions, symbols, relocations, debug info); the emit trace
shows post-link reality (what landed where in the ROM). Diff the two when
hunting subtle drift.
"""

import argparse
import json
import struct
import sys
from pathlib import Path
from typing import Any, TextIO

from a816.object_file import (
    INVALID_FILE_FORMAT,
    ObjectFile,
    Region,
)


def _fmt_addr(addr: int) -> str:
    return f"${addr & 0xFFFFFF:06X}"


def _load_tolerant(path: Path, out: TextIO) -> ObjectFile:
    try:
        return ObjectFile.from_file(str(path))
    except ValueError as exc:
        msg = str(exc)
        if "Unsupported version" not in msg:
            raise
        print(f"warning: {msg}; attempting best-effort read", file=out)
        return _read_any_version(path)


def _read_any_version(path: Path) -> ObjectFile:
    with open(path, "rb") as f:
        header = f.read(7)
        if len(header) < 7:
            raise ValueError(INVALID_FILE_FORMAT)
        magic, _version, flags = struct.unpack("<IHB", header)
        if magic != ObjectFile.MAGIC_NUMBER:
            raise ValueError("Invalid magic number")
        relocatable = bool(flags & 0x01)
        regions = ObjectFile._read_regions(f)
        symbols = ObjectFile._read_symbol_table(f)
        try:
            aliases = ObjectFile._read_alias_table(f)
        except struct.error:
            aliases = []
        try:
            files = ObjectFile._read_file_table(f)
        except struct.error:
            files = []
        return ObjectFile(regions, symbols, aliases=aliases, files=files, relocatable=relocatable)


def _detect_version(path: Path) -> int:
    with open(path, "rb") as f:
        header = f.read(7)
    if len(header) < 7:
        return -1
    _magic, version, _flags = struct.unpack("<IHB", header)
    return int(version)


def print_summary(path: Path, obj: ObjectFile, out: TextIO) -> None:
    version = _detect_version(path)
    total_code = sum(len(r.code) for r in obj.regions)
    total_relocs = sum(len(r.relocations) for r in obj.regions)
    total_expr = sum(len(r.expression_relocations) for r in obj.regions)
    total_lines = sum(len(r.lines) for r in obj.regions)
    print(f"file: {path}", file=out)
    print(f"version: {version}", file=out)
    print(f"relocatable: {obj.relocatable}", file=out)
    print(f"regions: {len(obj.regions)}", file=out)
    print(f"code_bytes: {total_code}", file=out)
    print(f"symbols: {len(obj.symbols)}", file=out)
    print(f"aliases: {len(obj.aliases)}", file=out)
    print(f"files: {len(obj.files)}", file=out)
    print(f"relocations: {total_relocs}", file=out)
    print(f"expression_relocations: {total_expr}", file=out)
    print(f"lines: {total_lines}", file=out)


def _hex_dump(data: bytes, out: TextIO, indent: str = "  ") -> None:
    for i in range(0, len(data), 16):
        chunk = data[i : i + 16]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        hex_part = hex_part.ljust(16 * 3 - 1)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"{indent}{i:04x}: {hex_part}  {ascii_part}", file=out)


def print_regions(obj: ObjectFile, out: TextIO, dump_bytes: int = 0) -> None:
    print(f"# regions ({len(obj.regions)}) — emission order", file=out)
    for idx, region in enumerate(obj.regions):
        print(
            f"[{idx}] base={_fmt_addr(region.base_address)}"
            f" size={len(region.code)}"
            f" relocs={len(region.relocations)}"
            f" expr_relocs={len(region.expression_relocations)}"
            f" lines={len(region.lines)}",
            file=out,
        )
        if dump_bytes > 0 and region.code:
            _hex_dump(region.code[:dump_bytes], out)


def print_symbols(obj: ObjectFile, out: TextIO) -> None:
    print(f"# symbols ({len(obj.symbols)}) — sorted by address", file=out)
    for name, address, sym_type, section in sorted(obj.symbols, key=lambda s: (s[1], s[0])):
        print(
            f"{_fmt_addr(address)}  {sym_type.name:<8} {section.name:<4} {name}",
            file=out,
        )


def _print_region_relocs(idx: int, region: Region, out: TextIO) -> None:
    if not region.relocations and not region.expression_relocations:
        return
    print(f"# region [{idx}] base={_fmt_addr(region.base_address)}", file=out)
    for offset, name, reloc_type in region.relocations:
        print(f"  +0x{offset:04x}  {reloc_type.name:<12} {name}", file=out)
    for offset, expression, size_bytes in region.expression_relocations:
        print(f"  +0x{offset:04x}  EXPR(size={size_bytes})  {expression}", file=out)


def print_relocs(obj: ObjectFile, out: TextIO) -> None:
    total = sum(len(r.relocations) + len(r.expression_relocations) for r in obj.regions)
    print(f"# relocations ({total} total)", file=out)
    for idx, region in enumerate(obj.regions):
        _print_region_relocs(idx, region, out)


def print_lines(obj: ObjectFile, out: TextIO) -> None:
    total = sum(len(r.lines) for r in obj.regions)
    print(f"# debug lines ({total} total)", file=out)
    for idx, region in enumerate(obj.regions):
        if not region.lines:
            continue
        print(f"# region [{idx}] base={_fmt_addr(region.base_address)}", file=out)
        for offset, file_idx, line, column, flags in region.lines:
            file_name = obj.files[file_idx] if 0 <= file_idx < len(obj.files) else "<oob>"
            print(
                f"  +0x{offset:04x}  file_idx={file_idx} ({file_name}) line={line} col={column} flags=0x{flags:02x}",
                file=out,
            )


def print_files(obj: ObjectFile, out: TextIO) -> None:
    print(f"# files ({len(obj.files)})", file=out)
    for idx, path in enumerate(obj.files):
        print(f"  [{idx}] {path}", file=out)


def print_aliases(obj: ObjectFile, out: TextIO) -> None:
    print(f"# aliases ({len(obj.aliases)})", file=out)
    for name, expression in obj.aliases:
        print(f"  {name} = {expression}", file=out)


def _enum_or_value(value: Any) -> Any:
    if hasattr(value, "name"):
        return value.name
    return value


def _region_to_dict(region: Region) -> dict[str, Any]:
    return {
        "base_address": region.base_address,
        "size": len(region.code),
        "code": region.code.hex(),
        "relocations": [
            {"offset": off, "name": name, "type": rt.name}
            for off, name, rt in region.relocations
        ],
        "expression_relocations": [
            {"offset": off, "expression": expr, "size_bytes": sz}
            for off, expr, sz in region.expression_relocations
        ],
        "lines": [
            {"offset": off, "file_idx": fi, "line": ln, "column": col, "flags": fl}
            for off, fi, ln, col, fl in region.lines
        ],
    }


def to_json_dict(path: Path, obj: ObjectFile) -> dict[str, Any]:
    return {
        "file": str(path),
        "version": _detect_version(path),
        "relocatable": obj.relocatable,
        "regions": [_region_to_dict(r) for r in obj.regions],
        "symbols": [
            {
                "name": name,
                "address": addr,
                "type": _enum_or_value(st),
                "section": _enum_or_value(sec),
            }
            for name, addr, st, sec in obj.symbols
        ],
        "aliases": [{"name": n, "expression": e} for n, e in obj.aliases],
        "files": list(obj.files),
    }


def print_diff(path_a: Path, path_b: Path, obj_a: ObjectFile, obj_b: ObjectFile, out: TextIO) -> None:
    print(f"--- {path_a}", file=out)
    print(f"+++ {path_b}", file=out)
    counts = [
        ("regions", len(obj_a.regions), len(obj_b.regions)),
        ("symbols", len(obj_a.symbols), len(obj_b.symbols)),
        (
            "relocations",
            sum(len(r.relocations) for r in obj_a.regions),
            sum(len(r.relocations) for r in obj_b.regions),
        ),
        (
            "expression_relocations",
            sum(len(r.expression_relocations) for r in obj_a.regions),
            sum(len(r.expression_relocations) for r in obj_b.regions),
        ),
        ("aliases", len(obj_a.aliases), len(obj_b.aliases)),
        ("files", len(obj_a.files), len(obj_b.files)),
    ]
    for name, av, bv in counts:
        marker = " " if av == bv else "!"
        print(f"{marker} {name}: {av} -> {bv}", file=out)

    max_regions = max(len(obj_a.regions), len(obj_b.regions))
    for idx in range(max_regions):
        if idx >= len(obj_a.regions):
            rb = obj_b.regions[idx]
            print(f"+ region[{idx}] only in B: base={_fmt_addr(rb.base_address)} size={len(rb.code)}", file=out)
            continue
        if idx >= len(obj_b.regions):
            ra = obj_a.regions[idx]
            print(f"- region[{idx}] only in A: base={_fmt_addr(ra.base_address)} size={len(ra.code)}", file=out)
            continue
        ra = obj_a.regions[idx]
        rb = obj_b.regions[idx]
        if ra.base_address != rb.base_address or len(ra.code) != len(rb.code):
            print(
                f"! region[{idx}]: base {_fmt_addr(ra.base_address)} -> {_fmt_addr(rb.base_address)},"
                f" size {len(ra.code)} -> {len(rb.code)}",
                file=out,
            )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="xobj",
        description="Inspect a816 .o object files.",
    )
    p.add_argument("files", nargs="+", type=Path, help="object file(s)")
    p.add_argument("--summary", action="store_true", help="high-level counts (default)")
    p.add_argument("--regions", action="store_true", help="region table")
    p.add_argument("--bytes", type=int, default=0, metavar="N", help="dump first N bytes of each region")
    p.add_argument("--symbols", action="store_true", help="symbol table sorted by address")
    p.add_argument("--relocs", action="store_true", help="relocations (legacy + expression)")
    p.add_argument("--lines", action="store_true", help="debug line table")
    p.add_argument("--files", action="store_true", help="debug file table")
    p.add_argument("--aliases", action="store_true", help="alias table")
    p.add_argument("--imports", action="store_true", help="import list (if format supports)")
    p.add_argument("--exports", action="store_true", help="export list (if format supports)")
    p.add_argument("--all", action="store_true", help="every section in declared order")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p.add_argument("--diff", action="store_true", help="diff two object files (requires exactly two paths)")
    return p


def _emit(path: Path, obj: ObjectFile, args: argparse.Namespace, out: TextIO) -> None:
    if args.json:
        json.dump(to_json_dict(path, obj), out, indent=2, sort_keys=False)
        out.write("\n")
        return

    sections_requested = any(
        getattr(args, attr)
        for attr in ("regions", "symbols", "relocs", "lines", "files", "aliases", "imports", "exports")
    )
    show_all = args.all or (not args.summary and not sections_requested)

    if args.summary or show_all:
        print_summary(path, obj, out)
    if args.regions or show_all:
        if args.summary or show_all:
            out.write("\n")
        print_regions(obj, out, dump_bytes=args.bytes)
    if args.symbols or show_all:
        out.write("\n")
        print_symbols(obj, out)
    if args.relocs or show_all:
        out.write("\n")
        print_relocs(obj, out)
    if args.lines or show_all:
        out.write("\n")
        print_lines(obj, out)
    if args.files or show_all:
        out.write("\n")
        print_files(obj, out)
    if args.aliases or show_all:
        out.write("\n")
        print_aliases(obj, out)
    if args.imports:
        imports = getattr(obj, "imports", None)
        if imports is not None:
            out.write("\n")
            print(f"# imports ({len(imports)})", file=out)
            for entry in imports:
                print(f"  {entry}", file=out)
    if args.exports:
        exports = getattr(obj, "exports", None)
        if exports is not None:
            out.write("\n")
            print(f"# exports ({len(exports)})", file=out)
            for entry in exports:
                print(f"  {entry}", file=out)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    out = sys.stdout

    for path in args.files:
        if not path.exists():
            print(f"xobj: file not found: {path}", file=sys.stderr)
            return 2

    if args.diff:
        if len(args.files) != 2:
            print("xobj: --diff requires exactly two files", file=sys.stderr)
            return 2
        try:
            obj_a = _load_tolerant(args.files[0], sys.stderr)
            obj_b = _load_tolerant(args.files[1], sys.stderr)
        except ValueError as exc:
            print(f"xobj: {exc}", file=sys.stderr)
            return 2
        print_diff(args.files[0], args.files[1], obj_a, obj_b, out)
        return 0

    for i, path in enumerate(args.files):
        try:
            obj = _load_tolerant(path, sys.stderr)
        except ValueError as exc:
            print(f"xobj: {path}: {exc}", file=sys.stderr)
            return 2
        if i > 0:
            out.write("\n")
        _emit(path, obj, args, out)

    return 0


if __name__ == "__main__":
    sys.exit(main())
