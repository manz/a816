"""Stable error code registry.

Every user-facing assembler error carries one of these codes so users can
search docs by code and tooling (LSP, fluff) can suppress / explain
individual diagnostics.

Codes are stable across releases — once assigned, never re-purposed.
Add new ones at the end of their category block.

Categories:
  E0001..E0099 — scanner / lexing
  E0100..E0199 — parser
  E0200..E0299 — symbol resolution
  E0300..E0399 — codegen
  E0400..E0499 — linker / object files
  E0500..E0599 — I/O / config
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorCode:
    """Stable error identifier + human-readable category."""

    code: str
    category: str
    short_description: str

    def __str__(self) -> str:
        return self.code


# --- Scanner (E0001..) ---
E_SCANNER_INVALID_INPUT = ErrorCode("E0001", "scanner", "invalid input character")
E_SCANNER_UNTERMINATED_STRING = ErrorCode("E0002", "scanner", "unterminated string literal")
E_SCANNER_UNKNOWN_KEYWORD = ErrorCode("E0003", "scanner", "unknown directive keyword")

# --- Parser (E0100..) ---
E_PARSER_UNEXPECTED_TOKEN = ErrorCode("E0100", "parser", "unexpected token")
E_PARSER_EXPECTED_TOKEN = ErrorCode("E0101", "parser", "missing expected token")
E_PARSER_INVALID_EXPRESSION = ErrorCode("E0102", "parser", "invalid expression")
E_PARSER_STRUCT_DUPLICATE_FIELD = ErrorCode("E0103", "parser", "duplicate struct field")
E_PARSER_TYPED_BIND_NEEDS_ASSIGN = ErrorCode("E0104", "parser", "typed cast bind requires `:=`")
E_PARSER_FIELD_ACCESS_NEEDS_CAST = ErrorCode("E0105", "parser", "field access requires typed cast")
E_PARSER_UNKNOWN_DIRECTIVE_ATTR = ErrorCode("E0106", "parser", "unknown directive attribute")
E_PARSER_POOL_NO_RANGES = ErrorCode("E0107", "parser", "pool declares no ranges")
E_PARSER_UNKNOWN_POOL_STRATEGY = ErrorCode("E0108", "parser", "unknown pool strategy")
E_PARSER_INCLUDE_FAILED = ErrorCode("E0109", "parser", "include file unreadable")

# --- Symbol resolution (E0200..) ---
E_SYMBOL_NOT_DEFINED = ErrorCode("E0200", "symbols", "symbol not defined in scope")
E_SYMBOL_EXTERNAL_NOT_ALLOWED = ErrorCode("E0201", "symbols", "external reference outside object mode")
E_SYMBOL_UNRESOLVABLE_EXPRESSION = ErrorCode("E0202", "symbols", "expression failed to evaluate")

# --- Codegen (E0300..) ---
E_CODEGEN_NODE_ERROR = ErrorCode("E0300", "codegen", "node failed during emission")
E_CODEGEN_STRUCT_UNKNOWN_TYPE = ErrorCode("E0301", "codegen", "unknown struct field type")
E_CODEGEN_STRUCT_SELF_REFERENCE = ErrorCode("E0302", "codegen", "struct field cannot reference its own type")
E_CODEGEN_STRUCT_REDEFINED = ErrorCode("E0303", "codegen", "struct redefined")
E_CODEGEN_TYPED_BIND_UNKNOWN_TYPE = ErrorCode("E0304", "codegen", "typed bind references unknown struct type")
E_CODEGEN_TYPED_BIND_NON_INT = ErrorCode("E0305", "codegen", "typed bind base must evaluate to an address")
E_CODEGEN_BAD_OPERAND_SIZE = ErrorCode("E0306", "codegen", "operand size mismatch")
E_CODEGEN_BAD_ADDRESSING_MODE = ErrorCode("E0307", "codegen", "addressing mode not supported by opcode")

# --- Linker (E0400..) ---
E_LINKER_DUPLICATE_SYMBOL = ErrorCode("E0400", "linker", "duplicate global symbol")
E_LINKER_UNRESOLVED_SYMBOL = ErrorCode("E0401", "linker", "unresolved external symbol")
E_LINKER_RELOCATION_RANGE = ErrorCode("E0402", "linker", "relocation out of range")
E_LINKER_EXPRESSION = ErrorCode("E0403", "linker", "relocation expression failed")

# --- I/O / config (E0500..) ---
E_IO_FILE_NOT_FOUND = ErrorCode("E0500", "io", "file not found")
E_CONFIG_INVALID = ErrorCode("E0501", "config", "invalid project config")


_BY_CODE: dict[str, ErrorCode] = {obj.code: obj for obj in globals().values() if isinstance(obj, ErrorCode)}


def lookup(code: str) -> ErrorCode | None:
    """Return the registered ErrorCode for `code`, or None if unknown."""
    return _BY_CODE.get(code)


def all_codes() -> list[ErrorCode]:
    """Every registered error code, sorted by numeric value for catalog output."""
    return sorted(_BY_CODE.values(), key=lambda e: e.code)
