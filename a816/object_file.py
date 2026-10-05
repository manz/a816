import hashlib
import struct
from dataclasses import dataclass, field
from enum import Enum

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


from a816.object_codec import decode, encode, schema
from a816.section import Placement, Section

# Re-exported for callers that import them from here.
__all__ = ["Placement", "Section"]


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
    context: str | None = None
    """Lifetime of a bss pool (`context NAME`); empty in the object format means none."""


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
    # A BML region (`a816.toml` `[map.N]`): `address` is the bsnes
    # `00-7d,80-ff:8000-ffff` spelling and `mask`/`base`/`rom_size` follow
    # bsnes semantics; the legacy range fields are unused. None for `.map`.
    address: str | None = None
    base: int = 0
    rom_size: int = 0

    @classmethod
    def bml(
        cls, identifier: str, address: str, mask: int = 0, base: int = 0, rom_size: int = 0, writeable: bool = False
    ) -> "BusMapping":
        """A bsnes-semantics region, as written in `boards.bml` / `a816.toml`."""
        return cls(identifier, (0, 0), (0, 0), mask, writeable, None, address, base, rom_size)

    def shape(self) -> tuple[object, ...]:
        """Every field as a plain tuple, for comparing declarations and cache keys."""
        return (
            self.identifier,
            self.bank_range,
            self.addr_range,
            self.mask,
            self.writeable,
            self.mirror_bank_range,
            self.address,
            self.base,
            self.rom_size,
        )


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
    source: str = ""
    """`file:line` of the request, for link-time diagnostics: bss bodies emit no
    bytes, so their section carries no line table to point at."""
    labels: list[str] = field(default_factory=list)
    """Exported names of the symbols bound in this alloc's section. The linker
    rebases them by this section's placement; looking the section up by address
    is ambiguous when pools share memory (contexts)."""
    align: int = 1
    """The block's address must be a multiple of this (a power of two)."""
    cross_bank: bool = False
    """The block may straddle bank edges where the ROM is contiguous."""


CODEGEN_REVISION = 2  # 2: an empty alloc body no longer takes a byte
"""Bumped whenever a816 emits different object bytes for unchanged source (a
codegen fix such as #159's end-marker labels). With the format's schema digest
it forms the object identity the build cache keys on, so a release that changes
codegen rebuilds every cached object and one that doesn't keeps them.
`tests/test_codegen_revision.py` fails when the output changes without a bump,
or the revision moves with no output change."""


@dataclass
class LinkAssert:
    """`.assert EXPR, "message"` carried to the linker, evaluated once every
    symbol has its final address."""

    expression: str
    message: str
    source: str = ""
    """`file:line` of the directive."""


@dataclass
class WireSection:
    """What a section carries in an object file: the reader rebuilds an
    anonymous pinned `Section` from it (placement comes from the pool allocs)."""

    base_address: int
    code: bytes
    bss: bool
    relocations: list[tuple[int, str, RelocationType]]
    expression_relocations: list[tuple[int, str, int]]
    lines: list[tuple[int, int, int, int, int]]


@dataclass
class WireObject:
    """Every table of an object file, in order. Encoded by `object_codec`
    from these annotations: a field added here (or in any record type it
    holds) is written, read and versioned without further code."""

    sections: list[WireSection]
    symbols: list[tuple[str, int, SymbolType, SymbolSection]]
    aliases: list[tuple[str, str]]
    files: list[str]
    pool_decls: list[PoolDecl]
    pool_allocs: list[PoolAlloc]
    bus_mappings: list[BusMapping]
    asserts: list[LinkAssert] = field(default_factory=list)


SCHEMA_DIGEST = hashlib.sha256(schema(WireObject).encode()).digest()[:16]
"""Digest of the wire schema: changes whenever any field of any table does."""

_PREFIX = struct.Struct("<IHB")  # magic, container version, flags: every version starts so
_HEADER = struct.Struct("<IHB16sI")  # ... then schema digest, codegen revision


@dataclass(frozen=True)
class ObjectHeader:
    magic: int
    version: int
    flags: int
    schema: bytes
    revision: int

    @property
    def identity(self) -> str:
        return f"{self.version}:{self.schema.hex()}:{self.revision}"


class ObjectFile:
    MAGIC_NUMBER = 0x41383136  # 'A816'
    VERSION = 0x0010  # Version 16: tables encoded from their annotations (`object_codec`).

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
        asserts: list[LinkAssert] | None = None,
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
        self.asserts: list[LinkAssert] = asserts or []

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
        flags = 0x01 if self.relocatable else 0x00
        with open(filename, "wb") as f:
            f.write(_HEADER.pack(self.MAGIC_NUMBER, self.VERSION, flags, SCHEMA_DIGEST, CODEGEN_REVISION))
            f.write(encode(WireObject, self.wire()))

    def wire(self) -> WireObject:
        """The object as its wire record (what `write` encodes)."""
        sections = [
            WireSection(s.placed_base, s.code, s.bss, s.relocations, s.expression_relocations, s.lines)
            for s in self.sections
        ]
        return WireObject(
            sections,
            self.symbols,
            self.aliases,
            self.files,
            self.pool_decls,
            self.pool_allocs,
            self.bus_mappings,
            self.asserts,
        )

    @staticmethod
    def identity() -> str:
        """What an object's bytes depend on besides its sources: the container
        version, the wire schema and the codegen revision. The build cache
        rebuilds every object built under another identity."""
        return f"{ObjectFile.VERSION}:{SCHEMA_DIGEST.hex()}:{CODEGEN_REVISION}"

    @staticmethod
    def read_header(filename: str) -> ObjectHeader | None:
        """An object's header, or None when the file isn't an a816 object.

        Every container version starts with magic, version and flags; only
        this one carries the schema digest and codegen revision after them,
        so an older object reports its version and an empty schema."""
        with open(filename, "rb") as f:
            raw = f.read(_HEADER.size)
        if len(raw) < _PREFIX.size:
            return None
        magic, version, flags = _PREFIX.unpack_from(raw)
        if magic != ObjectFile.MAGIC_NUMBER:
            return None
        if version != ObjectFile.VERSION or len(raw) < _HEADER.size:
            return ObjectHeader(magic, version, flags, b"", -1)
        return ObjectHeader(*_HEADER.unpack(raw))

    @staticmethod
    def from_file(filename: str) -> "ObjectFile":
        header = ObjectFile.read_header(filename)
        if header is None:
            raise ValueError(INVALID_FILE_FORMAT)
        if header.identity != ObjectFile.identity():
            raise ValueError(
                f"Unsupported version: object built with format {header.identity}, this a816 reads "
                f"{ObjectFile.identity()}; rebuild it"
            )
        with open(filename, "rb") as f:
            data = f.read()[_HEADER.size :]
        wire, _end = decode(WireObject, data)
        sections = []
        for ws in wire.sections:
            section = Section.anonymous_pinned(
                base_address=ws.base_address,
                code=ws.code,
                relocations=ws.relocations,
                expression_relocations=ws.expression_relocations,
                lines=ws.lines,
            )
            section.bss = ws.bss
            sections.append(section)
        return ObjectFile(
            sections,
            wire.symbols,
            aliases=wire.aliases,
            files=wire.files,
            relocatable=bool(header.flags & 0x01),
            pool_decls=wire.pool_decls,
            pool_allocs=wire.pool_allocs,
            bus_mappings=wire.bus_mappings,
            asserts=wire.asserts,
        )
