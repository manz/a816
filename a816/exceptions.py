class A816Error(Exception):
    """Base exception class for all assembler errors."""

    pass


class SymbolNotDefined(A816Error):
    """Raised when a symbol is not found in the current scope."""

    pass


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

    pass


class FormattingError(A816Error):
    """Raised when the formatter cannot process the input."""

    pass


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


# =============================================================================
# CPU/Opcode Errors
# =============================================================================


class OpcodeError(A816Error):
    """Base class for opcode-related errors."""

    pass


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
