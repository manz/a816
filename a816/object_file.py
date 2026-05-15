import struct
from dataclasses import dataclass, field
from enum import Enum
from typing import IO

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
    # `.label NAME = ADDR` declarations: the user picked the address, so the
    # linker must NOT shift it by the module's relocation delta. Treated like
    # DATA at the byte level, but flagged so `.adbg` emits LABEL kind and
    # the linker leaves the address absolute.
    ABS_LABEL = 0x03


@dataclass
class Region:
    """A contiguous span of emitted code and its associated relocations.

    A new region is opened on every `*=` directive during compilation. Offsets
    in `relocations`, `expression_relocations`, and `lines` are byte offsets
    into this region's `code`, not into the concatenated module.
    """

    base_address: int
    code: bytes
    relocations: list[tuple[int, str, RelocationType]] = field(default_factory=list)
    expression_relocations: list[tuple[int, str, int]] = field(default_factory=list)
    lines: list[tuple[int, int, int, int, int]] = field(default_factory=list)


@dataclass
class PoolDecl:
    """A `.pool` declaration serialized into a module.

    Linker collects every `PoolDecl` across input modules, unions same-named
    pools (ranges combined, fill/strategy must match), then runs the
    allocator on the merged pool for cross-TU placement.
    """

    name: str
    ranges: list[tuple[int, int]]
    fill: int
    strategy: str


@dataclass
class PoolAlloc:
    """A `.alloc` / `.relocate` request deferred to link time.

    `region_idx` is the body region in the same ObjectFile — the linker
    sets that region's `base_address` to the allocator-chosen address.
    `symbol_name` is the alloc's exported label, also patched.
    """

    pool_name: str
    symbol_name: str
    region_idx: int
    size: int


class ObjectFile:
    MAGIC_NUMBER = 0x41383136  # 'A816'
    VERSION = 0x0008  # Version 8: pool decls + alloc requests for cross-TU pool merging.

    def __init__(
        self,
        regions_or_code: list[Region] | bytes,
        symbols: list[tuple[str, int, SymbolType, SymbolSection]],
        relocations: list[tuple[int, str, RelocationType]] | None = None,
        expression_relocations: list[tuple[int, str, int]] | None = None,
        aliases: list[tuple[str, str]] | None = None,
        files: list[str] | None = None,
        lines: list[tuple[int, int, int, int, int]] | None = None,
        relocatable: bool = True,
        pool_decls: list[PoolDecl] | None = None,
        pool_allocs: list[PoolAlloc] | None = None,
    ) -> None:
        # `relocatable` is True iff the source contained no `*=` directive,
        # so the importer is free to place region 0 at the import site PC
        # and shift CODE symbols accordingly. Once `*=` is present, every
        # region is pinned to its compile-time base_address.
        if isinstance(regions_or_code, bytes):
            # Legacy single-region constructor used by tests.
            self.regions: list[Region] = [
                Region(
                    base_address=0,
                    code=regions_or_code,
                    relocations=list(relocations) if relocations else [],
                    expression_relocations=list(expression_relocations) if expression_relocations else [],
                    lines=list(lines) if lines else [],
                )
            ]
        else:
            self.regions = regions_or_code
        self.symbols: list[tuple[str, int, SymbolType, SymbolSection]] = symbols
        self.aliases: list[tuple[str, str]] = aliases or []
        self.files: list[str] = files or []
        self.relocatable: bool = relocatable
        self.pool_decls: list[PoolDecl] = pool_decls or []
        self.pool_allocs: list[PoolAlloc] = pool_allocs or []

    # ----- legacy single-region accessors (tests / older callers) -----
    def _ensure_first_region(self) -> Region:
        if not self.regions:
            self.regions.append(Region(base_address=0, code=b""))
        return self.regions[0]

    @property
    def code(self) -> bytes:
        return self.regions[0].code if self.regions else b""

    @code.setter
    def code(self, value: bytes) -> None:
        self._ensure_first_region().code = value

    @property
    def relocations(self) -> list[tuple[int, str, RelocationType]]:
        return self.regions[0].relocations if self.regions else []

    @relocations.setter
    def relocations(self, value: list[tuple[int, str, RelocationType]]) -> None:
        self._ensure_first_region().relocations = list(value)

    @property
    def expression_relocations(self) -> list[tuple[int, str, int]]:
        return self.regions[0].expression_relocations if self.regions else []

    @expression_relocations.setter
    def expression_relocations(self, value: list[tuple[int, str, int]]) -> None:
        self._ensure_first_region().expression_relocations = list(value)

    @property
    def lines(self) -> list[tuple[int, int, int, int, int]]:
        out: list[tuple[int, int, int, int, int]] = []
        for region in self.regions:
            out.extend(region.lines)
        return out

    def write(self, filename: str) -> None:
        with open(filename, "wb") as f:
            self._write_header(f)
            self._write_regions(f)
            self._write_symbol_table(f)
            self._write_alias_table(f)
            self._write_file_table(f)
            self._write_pool_decls(f)
            self._write_pool_allocs(f)

    def _write_pool_decls(self, f: IO[bytes]) -> None:
        f.write(struct.pack("<H", len(self.pool_decls)))
        for decl in self.pool_decls:
            name_bytes = decl.name.encode("utf-8")
            strategy_bytes = decl.strategy.encode("utf-8")
            f.write(struct.pack("<B", len(name_bytes)))
            f.write(name_bytes)
            f.write(struct.pack("<B", len(strategy_bytes)))
            f.write(strategy_bytes)
            f.write(struct.pack("<BH", decl.fill, len(decl.ranges)))
            for start, end in decl.ranges:
                f.write(struct.pack("<II", start, end))

    def _write_pool_allocs(self, f: IO[bytes]) -> None:
        f.write(struct.pack("<H", len(self.pool_allocs)))
        for alloc in self.pool_allocs:
            pool_bytes = alloc.pool_name.encode("utf-8")
            sym_bytes = alloc.symbol_name.encode("utf-8")
            f.write(struct.pack("<B", len(pool_bytes)))
            f.write(pool_bytes)
            f.write(struct.pack("<B", len(sym_bytes)))
            f.write(sym_bytes)
            f.write(struct.pack("<II", alloc.region_idx, alloc.size))

    def _write_header(self, f: IO[bytes]) -> None:
        flags = 0x01 if self.relocatable else 0x00
        header = struct.pack(
            "<IHB",
            self.MAGIC_NUMBER,
            self.VERSION,
            flags,
        )
        f.write(header)

    def _write_regions(self, f: IO[bytes]) -> None:
        f.write(struct.pack("<H", len(self.regions)))
        for region in self.regions:
            f.write(struct.pack("<II", region.base_address, len(region.code)))
            f.write(struct.pack("<HHI", len(region.relocations), len(region.expression_relocations), len(region.lines)))
            f.write(region.code)
            for offset, name, reloc_type in region.relocations:
                name_bytes = name.encode("utf-8")
                f.write(struct.pack("<IB", offset, len(name_bytes)))
                f.write(name_bytes)
                f.write(struct.pack("<B", reloc_type.value))
            for offset, expression, size_bytes in region.expression_relocations:
                expr_bytes = expression.encode("utf-8")
                f.write(struct.pack("<IH", offset, len(expr_bytes)))
                f.write(expr_bytes)
                f.write(struct.pack("<B", size_bytes))
            for offset, file_idx, line, column, flags in region.lines:
                f.write(struct.pack("<IIIHB", offset, file_idx, line, column & 0xFFFF, flags & 0xFF))

    def _write_symbol_table(self, f: IO[bytes]) -> None:
        f.write(struct.pack("<H", len(self.symbols)))
        for name, address, symbol_type, section in self.symbols:
            name_bytes = name.encode("utf-8")
            f.write(struct.pack("<B", len(name_bytes)))
            f.write(name_bytes)
            f.write(struct.pack("<IBB", address, symbol_type.value, section.value))

    def _write_alias_table(self, f: IO[bytes]) -> None:
        f.write(struct.pack("<H", len(self.aliases)))
        for name, expression in self.aliases:
            name_bytes = name.encode("utf-8")
            expr_bytes = expression.encode("utf-8")
            f.write(struct.pack("<B", len(name_bytes)))
            f.write(name_bytes)
            f.write(struct.pack("<H", len(expr_bytes)))
            f.write(expr_bytes)

    def _write_file_table(self, f: IO[bytes]) -> None:
        f.write(struct.pack("<H", len(self.files)))
        for path in self.files:
            encoded = path.encode("utf-8")
            f.write(struct.pack("<H", len(encoded)))
            f.write(encoded)

    @staticmethod
    def _read_regions(f: IO[bytes]) -> list[Region]:
        (count,) = struct.unpack("<H", f.read(2))
        regions: list[Region] = []
        for _ in range(count):
            base_address, code_size = struct.unpack("<II", f.read(8))
            num_relocs, num_expr_relocs, num_lines = struct.unpack("<HHI", f.read(8))
            code = f.read(code_size)
            relocs: list[tuple[int, str, RelocationType]] = []
            for _ in range(num_relocs):
                offset, name_len = struct.unpack("<IB", f.read(5))
                name = f.read(name_len).decode("utf-8")
                (rt_value,) = struct.unpack("<B", f.read(1))
                relocs.append((offset, name, RelocationType(rt_value)))
            expr_relocs: list[tuple[int, str, int]] = []
            for _ in range(num_expr_relocs):
                offset, expr_len = struct.unpack("<IH", f.read(6))
                expression = f.read(expr_len).decode("utf-8")
                (size_bytes,) = struct.unpack("<B", f.read(1))
                expr_relocs.append((offset, expression, size_bytes))
            lines: list[tuple[int, int, int, int, int]] = []
            for _ in range(num_lines):
                offset, file_idx, line, column, flags = struct.unpack("<IIIHB", f.read(15))
                lines.append((offset, file_idx, line, column, flags))
            regions.append(
                Region(
                    base_address=base_address,
                    code=code,
                    relocations=relocs,
                    expression_relocations=expr_relocs,
                    lines=lines,
                )
            )
        return regions

    @staticmethod
    def _read_symbol_table(f: IO[bytes]) -> list[tuple[str, int, SymbolType, SymbolSection]]:
        (count,) = struct.unpack("<H", f.read(2))
        out: list[tuple[str, int, SymbolType, SymbolSection]] = []
        for _ in range(count):
            (name_len,) = struct.unpack("<B", f.read(1))
            name = f.read(name_len).decode("utf-8")
            address, sym_type, section = struct.unpack("<IBB", f.read(6))
            out.append((name, address, SymbolType(sym_type), SymbolSection(section)))
        return out

    @staticmethod
    def _read_alias_table(f: IO[bytes]) -> list[tuple[str, str]]:
        (count,) = struct.unpack("<H", f.read(2))
        out: list[tuple[str, str]] = []
        for _ in range(count):
            (name_len,) = struct.unpack("<B", f.read(1))
            name = f.read(name_len).decode("utf-8")
            (expr_len,) = struct.unpack("<H", f.read(2))
            expression = f.read(expr_len).decode("utf-8")
            out.append((name, expression))
        return out

    @staticmethod
    def _read_file_table(f: IO[bytes]) -> list[str]:
        (count,) = struct.unpack("<H", f.read(2))
        out: list[str] = []
        for _ in range(count):
            (path_len,) = struct.unpack("<H", f.read(2))
            out.append(f.read(path_len).decode("utf-8"))
        return out

    @staticmethod
    def _read_pool_decls(f: IO[bytes]) -> list[PoolDecl]:
        (count,) = struct.unpack("<H", f.read(2))
        out: list[PoolDecl] = []
        for _ in range(count):
            (name_len,) = struct.unpack("<B", f.read(1))
            name = f.read(name_len).decode("utf-8")
            (strategy_len,) = struct.unpack("<B", f.read(1))
            strategy = f.read(strategy_len).decode("utf-8")
            fill, range_count = struct.unpack("<BH", f.read(3))
            ranges: list[tuple[int, int]] = []
            for _ in range(range_count):
                start, end = struct.unpack("<II", f.read(8))
                ranges.append((start, end))
            out.append(PoolDecl(name=name, ranges=ranges, fill=fill, strategy=strategy))
        return out

    @staticmethod
    def _read_pool_allocs(f: IO[bytes]) -> list[PoolAlloc]:
        (count,) = struct.unpack("<H", f.read(2))
        out: list[PoolAlloc] = []
        for _ in range(count):
            (pool_len,) = struct.unpack("<B", f.read(1))
            pool_name = f.read(pool_len).decode("utf-8")
            (sym_len,) = struct.unpack("<B", f.read(1))
            sym_name = f.read(sym_len).decode("utf-8")
            region_idx, size = struct.unpack("<II", f.read(8))
            out.append(PoolAlloc(pool_name=pool_name, symbol_name=sym_name, region_idx=region_idx, size=size))
        return out

    @staticmethod
    def from_file(filename: str) -> "ObjectFile":
        with open(filename, "rb") as f:
            header = f.read(7)
            if len(header) < 7:
                raise ValueError(INVALID_FILE_FORMAT)
            magic, version, flags = struct.unpack("<IHB", header)
            if magic != ObjectFile.MAGIC_NUMBER:
                raise ValueError("Invalid magic number")
            if version != ObjectFile.VERSION:
                raise ValueError(f"Unsupported version: {version} (expected {ObjectFile.VERSION})")
            relocatable = bool(flags & 0x01)
            regions = ObjectFile._read_regions(f)
            symbols = ObjectFile._read_symbol_table(f)
            aliases = ObjectFile._read_alias_table(f)
            files = ObjectFile._read_file_table(f)
            pool_decls = ObjectFile._read_pool_decls(f)
            pool_allocs = ObjectFile._read_pool_allocs(f)
            return ObjectFile(
                regions,
                symbols,
                aliases=aliases,
                files=files,
                relocatable=relocatable,
                pool_decls=pool_decls,
                pool_allocs=pool_allocs,
            )
