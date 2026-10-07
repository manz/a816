from __future__ import annotations

from dataclasses import dataclass
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
        self.note: str | None = None
        """Context for the hint, e.g. the macro argument the name was passed as."""


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

    def __init__(
        self, symbol_name: str, definitions: list[tuple[str, int | None]] | None = None, hint: str | None = None
    ) -> None:
        self.symbol_name = symbol_name
        # (where, value) per clashing definition, first one first; no value
        # for an alloc, whose address the allocator has yet to pick.
        self.definitions = definitions or []
        self.hint = hint or "each global symbol can only be defined once across all object files"
        super().__init__(f"duplicate symbol '{symbol_name}'")

    def format(self) -> str:
        # Late import: intentional to avoid circular dependency with errors module
        from a816.error_codes import E_LINKER_DUPLICATE_SYMBOL
        from a816.errors import format_error_simple

        details = [("symbol", self.symbol_name)]
        details += [
            ("defined", where if value is None else f"{where} = {value:#x}") for where, value in self.definitions
        ]
        details.append(("hint", self.hint))
        return format_error_simple(
            f"{LINKER_ERROR_LABEL}[{E_LINKER_DUPLICATE_SYMBOL}]",
            f"symbol '{self.symbol_name}' is already defined",
            details,
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


@dataclass(frozen=True)
class PlacedSpan:
    """One placed allocation, as the cross-pool overlap check sees it."""

    pool: str
    alloc: str
    start: int
    end: int  # exclusive
    source: str = ""  # `file:line` of the request, when known


class PoolOverlapLinkError(LinkerError):
    """Raised when allocations from two pools that may not share bytes overlap."""

    def __init__(self, clashes: list[tuple[PlacedSpan, PlacedSpan]]) -> None:
        self.clashes = clashes
        first, second = clashes[0]
        super().__init__(
            f"{_describe_span(first)} overlaps {_describe_span(second)}"
            + (f" (+{len(clashes) - 1} more)" if len(clashes) > 1 else "")
        )

    def format(self) -> str:
        # Late import: intentional to avoid circular dependency with errors module
        from a816.error_codes import E_LINKER_POOL_OVERLAP
        from a816.errors import format_error_simple

        details = [("overlap", f"{_describe_span(a)} x {_describe_span(b)}") for a, b in self.clashes]
        details.append(("hint", "memory used in turns belongs in one pool: `contexts A, B` and `in POOL.A`"))
        return format_error_simple(f"{LINKER_ERROR_LABEL}[{E_LINKER_POOL_OVERLAP}]", str(self), details)


def _describe_span(span: PlacedSpan) -> str:
    where = f" at {span.source}" if span.source else ""
    return f"`{span.alloc}` in pool `{span.pool}` (0x{span.start:06x}..0x{span.end - 1:06x}){where}"


@dataclass(frozen=True)
class EmittedBlock:
    """One placed block of ROM bytes, as the overlap check sees it."""

    name: str  # `name in pool p`, `name`, or `alloc at $40:8000`
    logical: int  # first byte, as the source addresses it
    start: int  # file offset of the first byte
    end: int  # file offset, exclusive
    source: str = ""  # `file:line` of the block's first line, when known
    pooled: bool = False  # placed by a pool's allocator, not pinned


class BlockOverlapLinkError(LinkerError):
    """Raised when two placed blocks would write the same ROM bytes."""

    def __init__(self, clashes: list[tuple[EmittedBlock, EmittedBlock]]) -> None:
        self.clashes = clashes
        first, second = clashes[0]
        more = f" (+{len(clashes) - 1} more)" if len(clashes) > 1 else ""
        super().__init__(f"{first.name} overlaps {second.name}{more}")

    def format(self) -> str:
        # Late import: intentional to avoid circular dependency with errors module
        from a816.error_codes import E_LINKER_BLOCK_OVERLAP
        from a816.errors import format_error_simple

        details: list[tuple[str, str]] = []
        for first, second in self.clashes:
            details.append(("overlap", f"{_describe_block(first)} x {_describe_block(second)}"))
            details.append(("shared", _shared_bytes(first, second)))
        details.append(("hint", _overlap_hint(self.clashes[0])))
        return format_error_simple(f"{LINKER_ERROR_LABEL}[{E_LINKER_BLOCK_OVERLAP}]", str(self), details)


def _snes_address(address: int) -> str:
    return f"${address >> 16:02X}:{address & 0xFFFF:04X}"


def _describe_block(block: EmittedBlock) -> str:
    where = f" at {block.source}" if block.source else ""
    size = _bytes(block.end - block.start)
    span = size if block.name.startswith("block at ") else f"{size} from {_snes_address(block.logical)}"
    return f"{block.name} ({span}){where}"


def _shared_bytes(first: EmittedBlock, second: EmittedBlock) -> str:
    """The bytes both blocks write; `second` starts inside `first`."""
    return f"{_bytes(min(first.end, second.end) - second.start)} from {_snes_address(second.logical)}"


def _bytes(count: int) -> str:
    return f"{count} byte" if count == 1 else f"{count} bytes"


def _overlap_hint(clash: tuple[EmittedBlock, EmittedBlock]) -> str:
    if all(block.pooled for block in clash):
        return "two pools hand out the same bytes; keep their ranges apart"
    return "move or shrink one of the blocks so their bytes stay apart"


class LinkAssertError(LinkerError):
    """Raised when `.assert` checks fail once every address is final."""

    def __init__(self, failures: list[tuple[str, str, str]]) -> None:
        # (message, expression, source) per failed assert
        self.failures = failures
        message, _expression, _source = failures[0]
        more = f" (+{len(failures) - 1} more)" if len(failures) > 1 else ""
        super().__init__(f"assertion failed: {message}{more}")

    def format(self) -> str:
        # Late import: intentional to avoid circular dependency with errors module
        from a816.error_codes import E_LINKER_ASSERT_FAILED
        from a816.errors import format_error_simple

        details = [
            ("assert", f"{message}: `{expression}`" + (f" at {source}" if source else ""))
            for message, expression, source in self.failures
        ]
        return format_error_simple(f"{LINKER_ERROR_LABEL}[{E_LINKER_ASSERT_FAILED}]", str(self), details)


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


class UndecidableOperandSizeError(OpcodeError):
    """Raised when an unsized operand names a symbol resolved only at link:
    its size (and so the opcode form) can't be inferred at compile time."""

    def __init__(self, symbols: set[str]) -> None:
        self.symbols = symbols
        names = ", ".join(f"`{name}`" for name in sorted(symbols))
        super().__init__(f"operand size can't be inferred: {names} is resolved at link")


class CrossBankTransferError(OpcodeError):
    """A bare `jsr` / `jmp` names a target in another bank: the absolute form
    can't reach it, and the long form changes what the code means (`jsl`
    returns with `rtl`), so the source must say."""

    def __init__(self, mnemonic: str, target: int, caller_bank: int) -> None:
        self.mnemonic = mnemonic
        self.target = target
        self.caller_bank = caller_bank
        super().__init__(
            f"`{mnemonic}` into bank ${target >> 16 & 0xFF:02X} from bank ${caller_bank:02X}: "
            f"a bare `{mnemonic}` only reaches its own bank"
        )


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
