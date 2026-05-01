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
from a816.object_file import ObjectFile, RelocationType, SymbolSection, SymbolType

SYMBOL_TOKEN_RE = re.compile(r"([A-Za-z_\.][A-Za-z0-9_\.]*)")


class Linker:
    def __init__(self, object_files: list[ObjectFile], base_address: int = 0) -> None:
        self.object_files = object_files
        self.base_address = base_address
        self.linked_code: bytearray = bytearray()
        self.linked_symbols: list[tuple[str, int, SymbolType, SymbolSection]] = []
        self.linked_relocations: list[tuple[int, str, RelocationType]] = []
        self.linked_expression_relocations: list[tuple[int, str, int]] = []  # (offset, expression, size_bytes)
        self.linked_aliases: list[tuple[str, str]] = []  # (name, expression)
        self.linked_files: list[str] = []
        self.linked_lines: list[tuple[int, int, int, int, int]] = []
        self._file_index: dict[str, int] = {}
        self.symbol_map: dict[str, int] = {}

    def link(self, base_address: int | None = None) -> ObjectFile:
        """Link object files into a single object file.

        Args:
            base_address: Optional override for the base ROM address. Defaults to value
                         passed at construction time (0 if not specified).

        Returns:
            A linked ObjectFile containing combined code, resolved symbols, and remaining relocations.
        """
        if base_address is not None:
            self.base_address = base_address
        self._resolve_symbols()
        self._resolve_aliases()
        self._check_unresolved()
        self._apply_relocations()
        self._apply_expression_relocations()
        return ObjectFile(
            bytes(self.linked_code),
            self.linked_symbols,
            self.linked_relocations,
            files=self.linked_files,
            lines=self.linked_lines,
        )

    def _resolved_address(self, address: int, section: SymbolSection, code_offset: int) -> int:
        # CODE = base + module offset + symbol offset; DATA = absolute.
        return address if section == SymbolSection.DATA else self.base_address + code_offset + address

    def _ingest_symbol(self, sym: tuple[str, int, SymbolType, SymbolSection], code_offset: int) -> None:
        name, address, symbol_type, section = sym
        if symbol_type == SymbolType.EXTERNAL:
            self._external_symbols_needed.add(name)
            return
        if symbol_type == SymbolType.GLOBAL:
            if name in self.symbol_map:
                raise DuplicateSymbolError(name)
            final_address = self._resolved_address(address, section, code_offset)
            self.symbol_map[name] = final_address
            self.linked_symbols.append((name, final_address, symbol_type, section))
            return
        if symbol_type == SymbolType.LOCAL:
            local_address = self._resolved_address(address, section, code_offset)
            self.linked_symbols.append((name, local_address, symbol_type, section))
            return
        raise ValueError(f"Unknown symbol type: {symbol_type}")

    def _ingest_lines(self, obj_file: ObjectFile, code_offset: int) -> None:
        if not obj_file.lines:
            return
        # Map per-object file indices into the linked file table.
        local_to_linked: dict[int, int] = {}
        for local_idx, path in enumerate(obj_file.files):
            if path in self._file_index:
                local_to_linked[local_idx] = self._file_index[path]
            else:
                new_idx = len(self.linked_files)
                self.linked_files.append(path)
                self._file_index[path] = new_idx
                local_to_linked[local_idx] = new_idx
        for offset, file_idx, line, column, flags in obj_file.lines:
            mapped = local_to_linked.get(file_idx, 0)
            # Bake the final logical address so consumers don't have to know
            # the linker's base/code-offset math.
            final_offset = self.base_address + code_offset + offset
            self.linked_lines.append((final_offset, mapped, line, column, flags))

    def _ingest_object(self, obj_file: ObjectFile, code_offset: int) -> None:
        self.linked_code.extend(obj_file.code)
        for sym in obj_file.symbols:
            self._ingest_symbol(sym, code_offset)
        for offset, symbol_name, relocation_type in obj_file.relocations:
            self.linked_relocations.append((code_offset + offset, symbol_name, relocation_type))
        for offset, expression, size_bytes in obj_file.expression_relocations:
            self.linked_expression_relocations.append((code_offset + offset, expression, size_bytes))
        self.linked_aliases.extend(obj_file.aliases)
        self._ingest_lines(obj_file, code_offset)

    def _resolve_symbols(self) -> None:
        self._external_symbols_needed: set[str] = set()
        current_code_offset = 0
        for obj_file in self.object_files:
            self._ingest_object(obj_file, current_code_offset)
            current_code_offset += len(obj_file.code)

    def _check_unresolved(self) -> None:
        unresolved_symbols = self._external_symbols_needed - set(self.symbol_map.keys())
        if unresolved_symbols:
            raise UnresolvedSymbolError(unresolved_symbols)

    def _resolve_aliases(self) -> None:
        """Resolve aliases iteratively. An alias may depend on another alias."""
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
            unresolved = {name for name, _ in remaining}
            raise UnresolvedSymbolError(unresolved)

    def _apply_relocations(self) -> None:
        base_address = self.base_address

        for offset, symbol_name, relocation_type in self.linked_relocations:
            if symbol_name not in self.symbol_map:
                raise UnresolvedSymbolError({symbol_name})

            symbol_address = self.symbol_map[symbol_name]

            match relocation_type:
                case RelocationType.ABSOLUTE_16:
                    if not 0 <= symbol_address <= 0xFFFF:
                        raise RelocationError(
                            symbol_name,
                            "16-bit absolute",
                            symbol_address,
                            "is out of range (must be 0x0000-0xFFFF)",
                        )
                    struct.pack_into("<H", self.linked_code, offset, symbol_address)
                case RelocationType.ABSOLUTE_24:
                    if not 0 <= symbol_address <= 0xFFFFFF:
                        raise RelocationError(
                            symbol_name,
                            "24-bit absolute",
                            symbol_address,
                            "is out of range (must be 0x000000-0xFFFFFF)",
                        )
                    self._write_le24(offset, symbol_address)
                case RelocationType.RELATIVE_16:
                    # For relative relocations, current PC = base_address + offset
                    current_pc = base_address + offset
                    target_address = symbol_address - (current_pc + 2)
                    if not -0x8000 <= target_address <= 0x7FFF:
                        raise RelocationError(
                            symbol_name,
                            "16-bit relative",
                            target_address,
                            "is out of range (must be -0x8000 to 0x7FFF)",
                        )
                    struct.pack_into("<h", self.linked_code, offset, target_address)
                case RelocationType.RELATIVE_24:
                    # For relative relocations, current PC = base_address + offset
                    current_pc = base_address + offset
                    target_address = symbol_address - (current_pc + 3)
                    if not -0x800000 <= target_address <= 0x7FFFFF:
                        raise RelocationError(
                            symbol_name,
                            "24-bit relative",
                            target_address,
                            "is out of range (must be -0x800000 to 0x7FFFFF)",
                        )
                    self._write_le24(offset, target_address & 0xFFFFFF)
                case _:
                    raise ValueError(f"Unknown relocation type: {relocation_type}")

    def _apply_expression_relocations(self) -> None:
        """Evaluate expressions with resolved symbols and apply to code"""
        for offset, expression, size_bytes in self.linked_expression_relocations:
            # Simple expression evaluator for basic arithmetic
            # This handles expressions like "EXTERN_VALUE + 0x8000"
            evaluated_value = self._evaluate_expression(expression)

            # Apply the evaluated value to the code
            if size_bytes == 1:
                struct.pack_into("<B", self.linked_code, offset, evaluated_value & 0xFF)
            elif size_bytes == 2:
                struct.pack_into("<H", self.linked_code, offset, evaluated_value & 0xFFFF)
            elif size_bytes == 3:
                if not -0x800000 <= evaluated_value <= 0xFFFFFF:
                    raise ExpressionEvaluationError(
                        expression,
                        f"result {evaluated_value:#x} is out of 24-bit range",
                    )
                self._write_le24(offset, evaluated_value & 0xFFFFFF)
            else:
                raise ExpressionEvaluationError(
                    expression,
                    f"unsupported operand size: {size_bytes} bytes",
                )

    def _evaluate_expression(self, expression: str) -> int:
        """Simple expression evaluator that resolves symbols from the symbol map"""
        # This is a simple evaluator for basic expressions
        # Replace symbols with their values and evaluate
        expr_to_eval = self._substitute_symbols(expression)

        # Safely evaluate the expression using Python's eval with no builtins
        try:
            return cast(int, eval(expr_to_eval, {"__builtins__": {}}, {}))
        except (SyntaxError, NameError, TypeError, ValueError) as e:
            raise ExpressionEvaluationError(expression, str(e)) from e

    def _substitute_symbols(self, expression: str) -> str:
        """Replace symbol occurrences with their numeric values without touching substrings."""

        def replace(match: Match[str]) -> str:
            token = match.group(0)
            if token in self.symbol_map:
                return str(self.symbol_map[token])
            return token

        return SYMBOL_TOKEN_RE.sub(replace, expression)

    def _write_le24(self, offset: int, value: int) -> None:
        """Write a 24-bit little endian value into the linked code buffer."""
        self.linked_code[offset : offset + 3] = bytes(
            (
                value & 0xFF,
                (value >> 8) & 0xFF,
                (value >> 16) & 0xFF,
            )
        )
