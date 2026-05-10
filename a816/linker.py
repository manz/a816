import re
import struct
from re import Match
from typing import cast

from a816.exceptions import (
    DuplicateSymbolError,
    ExpressionEvaluationError,
    RelocationError,
    UnresolvedSymbolError,
)
from a816.object_file import ObjectFile, Region, RelocationType, SymbolSection, SymbolType

SYMBOL_TOKEN_RE = re.compile(r"([A-Za-z_\.][A-Za-z0-9_\.]*)")


class Linker:
    """Links a list of ObjectFiles into one ObjectFile.

    Each input object's regions are placed at their declared (absolute)
    base_address — relocatable modules are shifted by the linker's
    base_address against region 0. Region offsets are then translated to
    final logical addresses, and relocations are patched into per-region
    bytearrays. The linked output preserves the per-region structure so
    downstream IPS/SFC writers emit one block per region rather than one
    flat span.
    """

    def __init__(self, object_files: list[ObjectFile], base_address: int = 0) -> None:
        self.object_files = object_files
        self.base_address = base_address
        # Linked regions, keyed by their final logical base_address.
        self.linked_regions: list[Region] = []
        self.linked_symbols: list[tuple[str, int, SymbolType, SymbolSection]] = []
        # (final_address, region_idx, symbol_name, RelocationType)
        self._linked_relocations: list[tuple[int, int, str, RelocationType]] = []
        # (final_address, region_idx, expression, size_bytes)
        self._linked_expression_relocations: list[tuple[int, int, str, int]] = []
        self.linked_aliases: list[tuple[str, str]] = []
        self.linked_files: list[str] = []
        self._file_index: dict[str, int] = {}
        self.symbol_map: dict[str, int] = {}
        self._region_buffers: dict[int, bytearray] = {}

    @property
    def linked_code(self) -> bytes:
        """Concatenated bytes of all linked regions, kept for legacy callers."""
        return b"".join(region.code for region in self.linked_regions)

    def link(self, base_address: int | None = None) -> ObjectFile:
        if base_address is not None:
            self.base_address = base_address
        self._resolve_symbols()
        self._resolve_aliases()
        self._check_unresolved()
        self._apply_relocations()
        self._apply_expression_relocations()
        return ObjectFile(
            self.linked_regions,
            self.linked_symbols,
            aliases=[],
            files=self.linked_files,
            relocatable=False,
        )

    def _delta_for(self, obj_file: ObjectFile, running_offset: int) -> int:
        """How much to shift this module's logical addresses by.

        Relocatable modules anchor region 0 at the linker's base_address
        plus the running byte offset of prior relocatable modules. Pinned
        modules keep their declared *= addresses unchanged.
        """
        if obj_file.relocatable and obj_file.regions:
            return self.base_address + running_offset - obj_file.regions[0].base_address
        return 0

    def _ingest_object(self, obj_file: ObjectFile, running_offset: int) -> None:
        delta = self._delta_for(obj_file, running_offset)
        local_to_linked_file = self._merge_file_table(obj_file)

        for region in obj_file.regions:
            final_base = region.base_address + delta
            region_idx = len(self.linked_regions)
            new_region = Region(
                base_address=final_base,
                code=bytes(region.code),
                relocations=list(region.relocations),
                expression_relocations=list(region.expression_relocations),
                lines=[
                    (offset, local_to_linked_file.get(file_idx, 0), line, column, flags)
                    for offset, file_idx, line, column, flags in region.lines
                ],
            )
            self.linked_regions.append(new_region)

            for offset, name, reloc_type in region.relocations:
                self._linked_relocations.append((final_base + offset, region_idx, name, reloc_type))
            for offset, expression, size_bytes in region.expression_relocations:
                self._linked_expression_relocations.append((final_base + offset, region_idx, expression, size_bytes))

        for sym in obj_file.symbols:
            self._ingest_symbol(sym, delta)

        self.linked_aliases.extend(obj_file.aliases)

    def _ingest_symbol(self, sym: tuple[str, int, SymbolType, SymbolSection], delta: int) -> None:
        name, address, symbol_type, section = sym
        if symbol_type == SymbolType.EXTERNAL:
            self._external_symbols_needed.add(name)
            return
        # CODE symbols ride the module's delta; DATA/BSS/ABS_LABEL are absolute.
        # ABS_LABEL is a `.label`-declared address binding — the user picked
        # the value, so it must NOT shift with the module placement.
        final_address = address + delta if section == SymbolSection.CODE else address
        if symbol_type == SymbolType.GLOBAL:
            if name in self.symbol_map:
                raise DuplicateSymbolError(name)
            self.symbol_map[name] = final_address
            self.linked_symbols.append((name, final_address, symbol_type, section))
            return
        if symbol_type == SymbolType.LOCAL:
            self.linked_symbols.append((name, final_address, symbol_type, section))
            return
        raise ValueError(f"Unknown symbol type: {symbol_type}")

    def _merge_file_table(self, obj_file: ObjectFile) -> dict[int, int]:
        local_to_linked: dict[int, int] = {}
        for local_idx, path in enumerate(obj_file.files):
            if path in self._file_index:
                local_to_linked[local_idx] = self._file_index[path]
            else:
                new_idx = len(self.linked_files)
                self.linked_files.append(path)
                self._file_index[path] = new_idx
                local_to_linked[local_idx] = new_idx
        return local_to_linked

    def _resolve_symbols(self) -> None:
        self._external_symbols_needed: set[str] = set()
        running_offset = 0
        for obj_file in self.object_files:
            self._ingest_object(obj_file, running_offset)
            if obj_file.relocatable:
                running_offset += sum(len(r.code) for r in obj_file.regions)

    def _check_unresolved(self) -> None:
        unresolved_symbols = self._external_symbols_needed - set(self.symbol_map.keys())
        if unresolved_symbols:
            raise UnresolvedSymbolError(unresolved_symbols)

    def _resolve_aliases(self) -> None:
        if not self.linked_aliases:
            return
        remaining = list(self.linked_aliases)
        progress = True
        while remaining and progress:
            progress = False
            still_pending: list[tuple[str, str]] = []
            for name, expression in remaining:
                try:
                    value = self._evaluate_expression(expression)
                except ExpressionEvaluationError:
                    still_pending.append((name, expression))
                    continue
                self.symbol_map[name] = value
                self.linked_symbols.append((name, value, SymbolType.GLOBAL, SymbolSection.DATA))
                progress = True
            remaining = still_pending
        if remaining:
            raise UnresolvedSymbolError({name for name, _ in remaining})

    def _region_view(self, region_idx: int) -> tuple[Region, bytearray]:
        region = self.linked_regions[region_idx]
        buf = self._region_buffers.get(region_idx)
        if buf is None:
            buf = bytearray(region.code)
            self._region_buffers[region_idx] = buf
        return region, buf

    def _flush_region_buffers(self) -> None:
        for region_idx, buf in self._region_buffers.items():
            self.linked_regions[region_idx].code = bytes(buf)
        self._region_buffers.clear()

    def _apply_relocations(self) -> None:
        self._region_buffers = {}
        for final_address, region_idx, symbol_name, relocation_type in self._linked_relocations:
            if symbol_name not in self.symbol_map:
                raise UnresolvedSymbolError({symbol_name})
            symbol_address = self.symbol_map[symbol_name]
            region, code = self._region_view(region_idx)
            offset = final_address - region.base_address

            match relocation_type:
                case RelocationType.ABSOLUTE_16:
                    if not 0 <= symbol_address <= 0xFFFF:
                        raise RelocationError(
                            symbol_name, "16-bit absolute", symbol_address, "is out of range (must be 0x0000-0xFFFF)"
                        )
                    struct.pack_into("<H", code, offset, symbol_address)
                case RelocationType.ABSOLUTE_24:
                    if not 0 <= symbol_address <= 0xFFFFFF:
                        raise RelocationError(
                            symbol_name,
                            "24-bit absolute",
                            symbol_address,
                            "is out of range (must be 0x000000-0xFFFFFF)",
                        )
                    self._write_le24(code, offset, symbol_address)
                case RelocationType.RELATIVE_16:
                    target_address = symbol_address - (final_address + 2)
                    if not -0x8000 <= target_address <= 0x7FFF:
                        raise RelocationError(
                            symbol_name,
                            "16-bit relative",
                            target_address,
                            "is out of range (must be -0x8000 to 0x7FFF)",
                        )
                    struct.pack_into("<h", code, offset, target_address)
                case RelocationType.RELATIVE_24:
                    target_address = symbol_address - (final_address + 3)
                    if not -0x800000 <= target_address <= 0x7FFFFF:
                        raise RelocationError(
                            symbol_name,
                            "24-bit relative",
                            target_address,
                            "is out of range (must be -0x800000 to 0x7FFFFF)",
                        )
                    self._write_le24(code, offset, target_address & 0xFFFFFF)
                case _:
                    raise ValueError(f"Unknown relocation type: {relocation_type}")

        self._flush_region_buffers()

    def _apply_expression_relocations(self) -> None:
        self._region_buffers = {}
        for final_address, region_idx, expression, size_bytes in self._linked_expression_relocations:
            evaluated_value = self._evaluate_expression(expression)
            region, code = self._region_view(region_idx)
            offset = final_address - region.base_address
            if size_bytes == 1:
                struct.pack_into("<B", code, offset, evaluated_value & 0xFF)
            elif size_bytes == 2:
                struct.pack_into("<H", code, offset, evaluated_value & 0xFFFF)
            elif size_bytes == 3:
                if not -0x800000 <= evaluated_value <= 0xFFFFFF:
                    raise ExpressionEvaluationError(expression, f"result {evaluated_value:#x} is out of 24-bit range")
                self._write_le24(code, offset, evaluated_value & 0xFFFFFF)
            else:
                raise ExpressionEvaluationError(expression, f"unsupported operand size: {size_bytes} bytes")

        self._flush_region_buffers()

    def _evaluate_expression(self, expression: str) -> int:
        expr_to_eval = self._substitute_symbols(expression)
        try:
            return cast(int, eval(expr_to_eval, {"__builtins__": {}}, {}))
        except (SyntaxError, NameError, TypeError, ValueError) as e:
            raise ExpressionEvaluationError(expression, str(e)) from e

    def _substitute_symbols(self, expression: str) -> str:
        def replace(match: Match[str]) -> str:
            token = match.group(0)
            if token in self.symbol_map:
                return str(self.symbol_map[token])
            return token

        return SYMBOL_TOKEN_RE.sub(replace, expression)

    def _write_le24(self, code: bytearray, offset: int, value: int) -> None:
        code[offset : offset + 3] = bytes(
            (
                value & 0xFF,
                (value >> 8) & 0xFF,
                (value >> 16) & 0xFF,
            )
        )
