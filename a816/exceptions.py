from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from a816.error_codes import ErrorCode
    from a816.parse.tokens import Token
    from a816.pool import PoolOverflowError


class A816Error(Exception):
    """Base exception class for all assembler errors."""


class AssemblyError(A816Error):
    """Raised by `Program.assemble_string_with_emitter` when parsing fails.

    Carries a pre-formatted message (already including source location when
    the parser attached one). Codegen failures keep raising `NodeError`
    directly; both share `A816Error` as a base so embedders can catch the
    entire family with one `except A816Error`.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


class SymbolNotDefined(A816Error):
    """Raised when a symbol is not found in the current scope.

    `token` is the expression term that named the symbol, set by the
    expression evaluator so the diagnostic can put its caret under the
    identifier instead of the enclosing opcode or directive.
    """

    def __init__(self, name: str, token: Token | None = None) -> None:
        super().__init__(name)
        self.name = name
        self.token = token


class ExternalSymbolReference(A816Error):
    """Raised when referencing external symbols during compilation."""

    def __init__(self, symbol_name: str):
        self.symbol_name = symbol_name
        super().__init__(f"External symbol reference: {symbol_name}")


class ExternalExpressionReference(A816Error):
    """Raised when expressions contain external symbols."""

    def __init__(self, expression_str: str, symbols: set[str]) -> None:
        self.expression_str = expression_str
        self.external_symbols = symbols
        super().__init__(f"Expression contains external symbols: {expression_str}")


class UnableToEvaluateSize(A816Error):
    """Raised during size evaluation failures."""


class FormattingError(A816Error):
    """Raised when the formatter cannot process the input."""


class A816ConfigError(A816Error):
    """Raised when `a816.toml` holds a value the assembler cannot use."""

    def __init__(self, code: ErrorCode, message: str, config_path: Path | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.config_path = config_path

    def format(self) -> str:
        # Late import: intentional to avoid circular dependency with errors module
        from a816.errors import format_error_simple

        details = [("config", str(self.config_path))] if self.config_path is not None else None
        return format_error_simple(f"config error[{self.code}]", self.message, details=details)


# =============================================================================
# Linker Errors
# =============================================================================

LINKER_ERROR_LABEL = "linker error"


class LinkerError(A816Error):
    """Base class for all linker-related errors."""

    def format(self) -> str:
        """Format the error with colors for display."""
        # Late import: intentional to avoid circular dependency with errors module
        from a816.errors import format_error_simple

        return format_error_simple(LINKER_ERROR_LABEL, str(self))


class DuplicateSymbolError(LinkerError):
    """Raised when the same global symbol is defined in multiple object files."""

    def __init__(self, symbol_name: str) -> None:
        self.symbol_name = symbol_name
        super().__init__(f"duplicate symbol '{symbol_name}'")

    def format(self) -> str:
        # Late import: intentional to avoid circular dependency with errors module
        from a816.errors import format_linker_error

        return format_linker_error(
            f"symbol '{self.symbol_name}' is already defined",
            symbol=self.symbol_name,
            hint="each global symbol can only be defined once across all object files",
        )


class UnresolvedSymbolError(LinkerError):
    """Raised when external symbols cannot be resolved during linking."""

    def __init__(self, symbols: set[str]) -> None:
        self.symbols = symbols
        if len(symbols) == 1:
            symbol = next(iter(symbols))
            message = f"unresolved symbol '{symbol}'"
        else:
            message = f"unresolved symbols: {', '.join(sorted(symbols))}"
        super().__init__(message)

    def format(self) -> str:
        # Late import: intentional to avoid circular dependency with errors module
        from a816.errors import format_linker_error

        if len(self.symbols) == 1:
            symbol = next(iter(self.symbols))
            return format_linker_error(
                f"symbol '{symbol}' is not defined",
                symbol=symbol,
                hint="add the object file that defines this symbol, or check for typos",
            )
        else:
            return format_linker_error(
                f"{len(self.symbols)} symbols are not defined",
                symbols=self.symbols,
                hint="add object files that define these symbols, or check for typos",
            )


class RelocationError(LinkerError):
    """Raised when a relocation cannot be applied."""

    def __init__(
        self,
        symbol_name: str,
        relocation_type: str,
        value: int,
        reason: str,
    ) -> None:
        self.symbol_name = symbol_name
        self.relocation_type = relocation_type
        self.value = value
        self.reason = reason
        super().__init__(f"{relocation_type} relocation failed for '{symbol_name}'")

    def format(self) -> str:
        # Late import: intentional to avoid circular dependency with errors module
        from a816.errors import format_error_simple

        return format_error_simple(
            LINKER_ERROR_LABEL,
            f"relocation for '{self.symbol_name}' failed",
            details=[
                ("type", self.relocation_type),
                ("value", f"{self.value:#x}"),
                ("reason", self.reason),
            ],
        )


class ExpressionEvaluationError(LinkerError):
    """Raised when an expression cannot be evaluated during linking."""

    def __init__(self, expression: str, reason: str) -> None:
        self.expression = expression
        self.reason = reason
        super().__init__(f"failed to evaluate '{expression}'")

    def format(self) -> str:
        # Late import: intentional to avoid circular dependency with errors module
        from a816.errors import format_error_simple

        return format_error_simple(
            LINKER_ERROR_LABEL,
            "cannot evaluate expression",
            details=[
                ("expression", self.expression),
                ("reason", self.reason),
            ],
        )


class PoolOverflowLinkError(LinkerError):
    """Raised when the link-time allocator cannot fit an alloc in its pool."""

    def __init__(self, overflow: PoolOverflowError, location: str | None = None) -> None:
        self.overflow = overflow
        self.location = location
        super().__init__(str(overflow))

    def format(self) -> str:
        # Late import: intentional to avoid circular dependency with errors module
        from a816.error_codes import E_LINKER_POOL_OVERFLOW
        from a816.errors import format_error_simple

        details = [("pool", self.overflow.pool_name)]
        if self.location is not None:
            details.append(("alloc body", self.location))
        details.append(("hint", self.overflow.hint))
        return format_error_simple(f"{LINKER_ERROR_LABEL}[{E_LINKER_POOL_OVERFLOW}]", str(self), details=details)


class UndeclaredPoolError(LinkerError):
    """Raised when an alloc request names a pool no linked object declares."""

    def __init__(self, pool_name: str, alloc_name: str) -> None:
        self.pool_name = pool_name
        self.alloc_name = alloc_name
        super().__init__(f"pool alloc {alloc_name!r} references undeclared pool {pool_name!r}")

    def format(self) -> str:
        # Late import: intentional to avoid circular dependency with errors module
        from a816.error_codes import E_LINKER_UNDECLARED_POOL
        from a816.errors import format_error_simple

        return format_error_simple(f"{LINKER_ERROR_LABEL}[{E_LINKER_UNDECLARED_POOL}]", str(self))


# =============================================================================
# Bus / Mapping Errors
# =============================================================================


class UnmappedBankError(A816Error):
    """Raised when an address lands in a bank no `.map` region covers.

    The bus `lookup` table only carries banks declared by a `.map`
    directive (or the rom_type's default mapping). A `.pool` / `.alloc` /
    `*=` placing code or data at an unmapped bank used to surface as a bare
    `KeyError: <bank>` deep in the traceback - useless to anyone reading the
    build output. This carries the bank, the banks that *are* mapped, and an
    optional source location so the diagnostic points at the offending
    construct.
    """

    def __init__(
        self,
        bank: int,
        logical_address: int | None = None,
        mapped_banks: list[int] | None = None,
    ) -> None:
        self.bank = bank
        self.logical_address = logical_address
        self.mapped_banks = sorted(mapped_banks) if mapped_banks else []
        super().__init__(f"bank ${bank:02X} is not covered by any `.map` region")

    @staticmethod
    def _format_ranges(banks: list[int]) -> str:
        """Collapse a sorted bank list into contiguous `$xx-$yy` ranges."""
        if not banks:
            return "(none)"
        ranges: list[str] = []
        start = prev = banks[0]
        for bank in banks[1:]:
            if bank == prev + 1:
                prev = bank
                continue
            ranges.append(f"${start:02X}" if start == prev else f"${start:02X}-${prev:02X}")
            start = prev = bank
        ranges.append(f"${start:02X}" if start == prev else f"${start:02X}-${prev:02X}")
        return ", ".join(ranges)

    def mapped_ranges(self) -> str:
        """The banks the bus does cover, as `$xx-$yy` ranges."""
        return self._format_ranges(self.mapped_banks)

    def format(self) -> str:
        # Late import: intentional to avoid circular dependency with errors module
        from a816.errors import format_error_simple

        details: list[tuple[str, str]] = [("bank", f"${self.bank:02X}")]
        if self.logical_address is not None:
            details.append(("address", f"${self.logical_address:06X}"))
        details.append(("mapped banks", self.mapped_ranges()))
        message = f"bank ${self.bank:02X} is not covered by any `.map` region (and is not backed by the configured ROM)"
        return format_error_simple("error", message, details=details)


# =============================================================================
# CPU/Opcode Errors
# =============================================================================


class OpcodeError(A816Error):
    """Base class for opcode-related errors."""


class BranchOutOfRangeError(OpcodeError):
    """Raised when a relative branch displacement does not fit its offset field."""

    def __init__(self, delta: int, allowed: str) -> None:
        self.delta = delta
        self.allowed = allowed
        super().__init__(f"branch target out of range: offset {delta} exceeds {allowed}")


class BranchTargetUnmappedError(OpcodeError):
    """Raised when a relative branch targets an address with no ROM location."""

    def __init__(self, target: int) -> None:
        self.target = target
        super().__init__(f"branch target {target:#x} has no ROM address; relative branches cannot reach RAM")


class MissingOperandError(OpcodeError):
    """Raised when an opcode requires an operand but none was provided."""

    def __init__(self, opcode_name: str) -> None:
        self.opcode_name = opcode_name
        super().__init__(f"Opcode '{opcode_name}' requires an operand")


class UnsupportedAddressingError(OpcodeError):
    """Raised when an addressing mode is not supported for an opcode."""

    def __init__(self, opcode_name: str, addressing_mode: str) -> None:
        self.opcode_name = opcode_name
        self.addressing_mode = addressing_mode
        super().__init__(f"Opcode '{opcode_name}' does not support {addressing_mode} addressing")


class OperandSizeError(OpcodeError):
    """Raised when an opcode doesn't support the specified operand size."""

    def __init__(self, opcode_name: str, size: str) -> None:
        self.opcode_name = opcode_name
        self.size = size
        size_names = {"b": "byte (.b)", "w": "word (.w)", "l": "long (.l)"}
        size_display = size_names.get(size, size)
        super().__init__(f"Opcode '{opcode_name}' does not support {size_display} operand size")
