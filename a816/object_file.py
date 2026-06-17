import struct
from dataclasses import dataclass
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


from a816.section import Placement as Placement  # noqa: E402  (explicit re-export)
from a816.section import Section as Section  # noqa: E402  (explicit re-export)


def _legacy_pinned_section(
    base_address: int,
    code: bytes,
    relocations: list[tuple[int, str, RelocationType]] | None = None,
    expression_relocations: list[tuple[int, str, int]] | None = None,
    lines: list[tuple[int, int, int, int, int]] | None = None,
    name: str | None = None,
) -> Section:
    """Constructor shim used by ObjectFile + tests written against the
    pre-Section `Region(base_address, code, ...)` signature.

    Wire-format `.o` files don't yet carry section name + placement
    metadata, so anonymous PINNED is the sensible default. Future
    format-version bumps will surface the real placement at read time.
    """
    return Section(
        name=name if name is not None else f"__legacy_pin_{base_address:06X}",
        placement=Placement.PINNED,
        code=code,
        base_address=base_address,
        relocations=list(relocations or []),
        expression_relocations=list(expression_relocations or []),
        lines=list(lines or []),
    )


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
    bss: bool = False
    """Byte-less pool: reservations emit nothing into the image. Must round-trip
    through the object format, else an imported bss pool deserializes as bss=False
    and `generate_pool`'s shape-check rejects the inline (bss=True) re-declaration."""


@dataclass
class BusMapping:
    """A `.map` directive serialized into a module.

    Replays at link time onto the linker's resolver bus so the linked
    program sees the same bank/address mapping the author declared at
    compile time. Without this, custom mappers (SA-1, ExHiROM, any
    non-default cartridge layout) silently fall back to whatever the
    linker's default bus is.
    """

    identifier: str
    bank_range: tuple[int, int]
    addr_range: tuple[int, int]
    mask: int
    writeable: bool = False
    mirror_bank_range: tuple[int, int] | None = None


@dataclass
class PoolAlloc:
    """A `.alloc` / `.relocate` request deferred to link time.

    `section_idx` is the body section in the same ObjectFile — the linker
    sets that section's `base_address` to the allocator-chosen address.
    `symbol_name` is the alloc's exported label, also patched.
    """

    pool_name: str
    symbol_name: str
    section_idx: int
    size: int
    pinned_addr: int = -1
    """Fixed address for a `.reserve NAME SIZE at ADDR in POOL` request; -1
    when the allocator is free to pick. Round-trips so the linker honors the
    pin across modules."""


class ObjectFile:
    MAGIC_NUMBER = 0x41383136  # 'A816'
    VERSION = 0x000C  # Version 12: PoolAlloc carries pinned_addr (fixed-address reserves).

    def __init__(
        self,
        sections_or_code: list[Section] | bytes,
        symbols: list[tuple[str, int, SymbolType, SymbolSection]],
        relocations: list[tuple[int, str, RelocationType]] | None = None,
        expression_relocations: list[tuple[int, str, int]] | None = None,
        aliases: list[tuple[str, str]] | None = None,
        files: list[str] | None = None,
        lines: list[tuple[int, int, int, int, int]] | None = None,
        relocatable: bool = True,
        pool_decls: list[PoolDecl] | None = None,
        pool_allocs: list[PoolAlloc] | None = None,
        bus_mappings: list[BusMapping] | None = None,
    ) -> None:
        # `relocatable` is True iff the source contained no `*=` directive,
        # so the importer is free to place section 0 at the import site PC
        # and shift CODE symbols accordingly. Once `*=` is present, every
        # section is pinned to its compile-time base_address.
        if isinstance(sections_or_code, bytes):
            # Legacy single-section constructor used by tests.
            self.sections: list[Section] = [
                _legacy_pinned_section(
                    base_address=0,
                    code=sections_or_code,
                    relocations=relocations,
                    expression_relocations=expression_relocations,
                    lines=lines,
                )
            ]
        else:
            self.sections = sections_or_code
        self.symbols: list[tuple[str, int, SymbolType, SymbolSection]] = symbols
        self.aliases: list[tuple[str, str]] = aliases or []
        self.files: list[str] = files or []
        self.relocatable: bool = relocatable
        self.pool_decls: list[PoolDecl] = pool_decls or []
        self.pool_allocs: list[PoolAlloc] = pool_allocs or []
        self.bus_mappings: list[BusMapping] = bus_mappings or []

    # ----- legacy single-section accessors (tests / older callers) -----
    def _ensure_first_section(self) -> Section:
        if not self.sections:
            self.sections.append(_legacy_pinned_section(base_address=0, code=b""))
        return self.sections[0]

    @property
    def code(self) -> bytes:
        return self.sections[0].code if self.sections else b""

    @code.setter
    def code(self, value: bytes) -> None:
        self._ensure_first_section().code = value

    @property
    def relocations(self) -> list[tuple[int, str, RelocationType]]:
        return self.sections[0].relocations if self.sections else []

    @relocations.setter
    def relocations(self, value: list[tuple[int, str, RelocationType]]) -> None:
        self._ensure_first_section().relocations = list(value)

    @property
    def expression_relocations(self) -> list[tuple[int, str, int]]:
        return self.sections[0].expression_relocations if self.sections else []

    @expression_relocations.setter
    def expression_relocations(self, value: list[tuple[int, str, int]]) -> None:
        self._ensure_first_section().expression_relocations = list(value)

    @property
    def lines(self) -> list[tuple[int, int, int, int, int]]:
        out: list[tuple[int, int, int, int, int]] = []
        for section in self.sections:
            out.extend(section.lines)
        return out

    def write(self, filename: str) -> None:
        with open(filename, "wb") as f:
            self._write_header(f)
            self._write_sections(f)
            self._write_symbol_table(f)
            self._write_alias_table(f)
            self._write_file_table(f)
            self._write_pool_decls(f)
            self._write_pool_allocs(f)
            self._write_bus_mappings(f)

    def _write_pool_decls(self, f: IO[bytes]) -> None:
        f.write(struct.pack("<H", len(self.pool_decls)))
        for decl in self.pool_decls:
            name_bytes = decl.name.encode("utf-8")
            strategy_bytes = decl.strategy.encode("utf-8")
            f.write(struct.pack("<B", len(name_bytes)))
            f.write(name_bytes)
            f.write(struct.pack("<B", len(strategy_bytes)))
            f.write(strategy_bytes)
            f.write(struct.pack("<B", 1 if decl.bss else 0))
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
            f.write(struct.pack("<IIi", alloc.section_idx, alloc.size, alloc.pinned_addr))

    def _write_bus_mappings(self, f: IO[bytes]) -> None:
        f.write(struct.pack("<H", len(self.bus_mappings)))
        for mapping in self.bus_mappings:
            ident_bytes = mapping.identifier.encode("utf-8")
            f.write(struct.pack("<B", len(ident_bytes)))
            f.write(ident_bytes)
            f.write(
                struct.pack(
                    "<HHIIIB",
                    mapping.bank_range[0],
                    mapping.bank_range[1],
                    mapping.addr_range[0],
                    mapping.addr_range[1],
                    mapping.mask,
                    1 if mapping.writeable else 0,
                )
            )
            if mapping.mirror_bank_range is None:
                f.write(struct.pack("<B", 0))
            else:
                f.write(struct.pack("<BHH", 1, mapping.mirror_bank_range[0], mapping.mirror_bank_range[1]))

    def _write_header(self, f: IO[bytes]) -> None:
        flags = 0x01 if self.relocatable else 0x00
        header = struct.pack(
            "<IHB",
            self.MAGIC_NUMBER,
            self.VERSION,
            flags,
        )
        f.write(header)

    def _write_sections(self, f: IO[bytes]) -> None:
        f.write(struct.pack("<H", len(self.sections)))
        for section in self.sections:
            f.write(struct.pack("<II", section.base_address, len(section.code)))
            section_flags = 0x01 if section.bss else 0x00
            f.write(struct.pack("<B", section_flags))
            f.write(
                struct.pack("<HHI", len(section.relocations), len(section.expression_relocations), len(section.lines))
            )
            f.write(section.code)
            for offset, name, reloc_type in section.relocations:
                name_bytes = name.encode("utf-8")
                f.write(struct.pack("<IB", offset, len(name_bytes)))
                f.write(name_bytes)
                f.write(struct.pack("<B", reloc_type.value))
            for offset, expression, size_bytes in section.expression_relocations:
                expr_bytes = expression.encode("utf-8")
                f.write(struct.pack("<IH", offset, len(expr_bytes)))
                f.write(expr_bytes)
                f.write(struct.pack("<B", size_bytes))
            for offset, file_idx, line, column, flags in section.lines:
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
    def _read_sections(f: IO[bytes]) -> list[Section]:
        (count,) = struct.unpack("<H", f.read(2))
        sections: list[Section] = []
        for _ in range(count):
            base_address, code_size = struct.unpack("<II", f.read(8))
            (section_flags,) = struct.unpack("<B", f.read(1))
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
            section = Section.anonymous_pinned(
                base_address=base_address,
                code=code,
                relocations=relocs,
                expression_relocations=expr_relocs,
                lines=lines,
            )
            section.bss = bool(section_flags & 0x01)
            sections.append(section)
        return sections

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
            (bss_flag,) = struct.unpack("<B", f.read(1))
            fill, range_count = struct.unpack("<BH", f.read(3))
            ranges: list[tuple[int, int]] = []
            for _ in range(range_count):
                start, end = struct.unpack("<II", f.read(8))
                ranges.append((start, end))
            out.append(PoolDecl(name=name, ranges=ranges, fill=fill, strategy=strategy, bss=bool(bss_flag)))
        return out

    @staticmethod
    def _read_bus_mappings(f: IO[bytes]) -> list[BusMapping]:
        (count,) = struct.unpack("<H", f.read(2))
        out: list[BusMapping] = []
        for _ in range(count):
            (ident_len,) = struct.unpack("<B", f.read(1))
            identifier = f.read(ident_len).decode("utf-8")
            bank_lo, bank_hi, addr_lo, addr_hi, mask, writeable_byte = struct.unpack("<HHIIIB", f.read(17))
            (has_mirror,) = struct.unpack("<B", f.read(1))
            mirror: tuple[int, int] | None = None
            if has_mirror:
                m_lo, m_hi = struct.unpack("<HH", f.read(4))
                mirror = (m_lo, m_hi)
            out.append(
                BusMapping(
                    identifier=identifier,
                    bank_range=(bank_lo, bank_hi),
                    addr_range=(addr_lo, addr_hi),
                    mask=mask,
                    writeable=bool(writeable_byte),
                    mirror_bank_range=mirror,
                )
            )
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
            section_idx, size, pinned_addr = struct.unpack("<IIi", f.read(12))
            out.append(
                PoolAlloc(
                    pool_name=pool_name,
                    symbol_name=sym_name,
                    section_idx=section_idx,
                    size=size,
                    pinned_addr=pinned_addr,
                )
            )
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
            sections = ObjectFile._read_sections(f)
            symbols = ObjectFile._read_symbol_table(f)
            aliases = ObjectFile._read_alias_table(f)
            files = ObjectFile._read_file_table(f)
            pool_decls = ObjectFile._read_pool_decls(f)
            pool_allocs = ObjectFile._read_pool_allocs(f)
            bus_mappings = ObjectFile._read_bus_mappings(f)
            return ObjectFile(
                sections,
                symbols,
                aliases=aliases,
                files=files,
                relocatable=relocatable,
                pool_decls=pool_decls,
                pool_allocs=pool_allocs,
                bus_mappings=bus_mappings,
            )
