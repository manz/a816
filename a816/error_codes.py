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
E_PARSER_MISSING_OPERAND = ErrorCode("E0115", "parser", "opcode needs an operand")
E_PARSER_STRUCT_ARRAY_COUNT = ErrorCode("E0120", "parser", "struct array count must be a positive integer")
E_PARSER_STRUCT_BITFIELD_ARRAY = ErrorCode("E0121", "parser", "bit-field struct fields cannot be arrays")
E_PARSER_ISTRUCT_DUPLICATE_FIELD = ErrorCode("E0122", "parser", "field initialized twice in `.istruct`")
E_PARSER_ISTRUCT_STRING_IN_LIST = ErrorCode("E0123", "parser", "string inside an initializer list")

# --- Symbol resolution (E0200..) ---
E_SYMBOL_NOT_DEFINED = ErrorCode("E0200", "symbols", "symbol not defined in scope")
E_SYMBOL_EXTERNAL_NOT_ALLOWED = ErrorCode("E0201", "symbols", "external reference outside object mode")
E_SYMBOL_UNRESOLVABLE_EXPRESSION = ErrorCode("E0202", "symbols", "expression failed to evaluate")
E_SYMBOL_UNKNOWN_POOL = ErrorCode("E0205", "symbols", "placement into an undeclared pool")
E_SYMBOL_RESERVE_UNKNOWN_TYPE = ErrorCode("E0206", "symbols", "`.reserve ... as` names an unknown struct type")
E_SYMBOL_UNKNOWN_MACRO = ErrorCode("E0207", "symbols", "macro not defined")
E_SYMBOL_MACRO_ARITY = ErrorCode("E0208", "symbols", "macro called with the wrong number of arguments")
E_SYMBOL_NOT_A_VALUE = ErrorCode("E0209", "symbols", "symbol names a block, not a value")
E_SYMBOL_EAGER_FORWARD_REF = ErrorCode("E0210", "symbols", "`:=` references a symbol not yet defined")

# --- Codegen (E0300..) ---
E_CODEGEN_NODE_ERROR = ErrorCode("E0300", "codegen", "node failed during emission")
E_CODEGEN_STRUCT_UNKNOWN_TYPE = ErrorCode("E0301", "codegen", "unknown struct field type")
E_CODEGEN_STRUCT_SELF_REFERENCE = ErrorCode("E0302", "codegen", "struct field cannot reference its own type")
E_CODEGEN_STRUCT_REDEFINED = ErrorCode("E0303", "codegen", "struct redefined")
E_CODEGEN_TYPED_BIND_UNKNOWN_TYPE = ErrorCode("E0304", "codegen", "typed bind references unknown struct type")
E_CODEGEN_TYPED_BIND_NON_INT = ErrorCode("E0305", "codegen", "typed bind base must evaluate to an address")
E_CODEGEN_BAD_OPERAND_SIZE = ErrorCode("E0306", "codegen", "operand size mismatch")
E_CODEGEN_BAD_ADDRESSING_MODE = ErrorCode("E0307", "codegen", "addressing mode not supported by opcode")
E_CODEGEN_MAP_CONFLICT = ErrorCode("E0308", "codegen", "conflicting `.map` declaration")
E_CODEGEN_IMMEDIATE_OVERFLOW = ErrorCode("E0309", "codegen", "byte immediate does not fit in 8 bits")
E_CODEGEN_UNPLACED_CODE = ErrorCode("E0310", "codegen", "code emitted outside any placement")
E_CODEGEN_IMPORT_IN_PLACEMENT = ErrorCode("E0311", "codegen", "`.import` inside a placement context")
E_CODEGEN_DIVISION_BY_ZERO = ErrorCode("E0312", "codegen", "division or modulo by zero")
E_CODEGEN_UNDECIDABLE_SIZE = ErrorCode("E0313", "codegen", "operand size of a link-time symbol needs a suffix")
E_CODEGEN_BRANCH_RANGE = ErrorCode("E0315", "codegen", "branch target out of range")
E_CODEGEN_BRANCH_UNMAPPED = ErrorCode("E0316", "codegen", "branch target has no ROM address")
E_CODEGEN_UNMAPPED_BANK = ErrorCode("E0317", "codegen", "address in a bank no `.map` region covers")
E_CODEGEN_POOL_OVERFLOW = ErrorCode("E0318", "codegen", "alloc does not fit in its pool")
E_CODEGEN_MISMATCHED_TYPES = ErrorCode("E0319", "codegen", "operator applied to a string and a number")
E_CODEGEN_NOT_TOO_WIDE = ErrorCode("E0320", "codegen", "`~` operand wider than 32 bits")
E_CODEGEN_SIZE_OPERAND = ErrorCode("E0321", "codegen", "`sizeof` / `countof` names nothing it can size")
E_CODEGEN_ISTRUCT_UNKNOWN_TYPE = ErrorCode("E0330", "codegen", "`.istruct` names an unknown struct type")
E_CODEGEN_ISTRUCT_UNKNOWN_FIELD = ErrorCode("E0331", "codegen", "initializer names a field the struct lacks")
E_CODEGEN_ISTRUCT_VALUE_KIND = ErrorCode("E0332", "codegen", "initializer value does not fit the field's type")
E_CODEGEN_ISTRUCT_TOO_LONG = ErrorCode("E0333", "codegen", "initializer longer than its array field")
E_CODEGEN_ISTRUCT_NON_ASCII = ErrorCode("E0334", "codegen", "non-ASCII character in a string initializer")
E_CODEGEN_ISTRUCT_BIT_RUN_TOO_WIDE = ErrorCode("E0335", "codegen", "initialized bit-field run wider than 32 bits")
E_CODEGEN_CROSS_BANK_BODY = ErrorCode("E0336", "codegen", "`cross_bank` body holds something other than data")

# --- Linker (E0400..) ---
E_LINKER_DUPLICATE_SYMBOL = ErrorCode("E0400", "linker", "duplicate global symbol")
E_LINKER_UNRESOLVED_SYMBOL = ErrorCode("E0401", "linker", "unresolved external symbol")
E_LINKER_RELOCATION_RANGE = ErrorCode("E0402", "linker", "relocation out of range")
E_LINKER_EXPRESSION = ErrorCode("E0403", "linker", "relocation expression failed")
E_LINKER_POOL_OVERFLOW = ErrorCode("E0404", "linker", "alloc does not fit in its pool at link time")
E_LINKER_UNDECLARED_POOL = ErrorCode("E0405", "linker", "alloc request names a pool no object declares")
E_LINKER_POOL_OVERLAP = ErrorCode("E0406", "linker", "allocs from different pools overlap")
E_LINKER_ASSERT_FAILED = ErrorCode("E0407", "linker", "a `.assert` is false once addresses are final")
E_LINKER_BLOCK_OVERLAP = ErrorCode("E0408", "linker", "two placed blocks write the same ROM bytes")

# --- I/O / config (E0500..) ---
E_IO_FILE_NOT_FOUND = ErrorCode("E0500", "io", "file not found")
E_CONFIG_INVALID = ErrorCode("E0501", "config", "invalid project config")
E_IO_NOT_IPS = ErrorCode("E0502", "io", "`.include_ips` file is not an IPS patch")
E_CONFIG_BAD_EXPERIMENTAL = ErrorCode("E0503", "config", "`[experimental]` flag is not true or false")
E_CONFIG_UNKNOWN_MAPPER = ErrorCode("E0504", "config", "`mapper` is no longer supported; name a `board`")
E_CONFIG_BAD_MAP_ENTRY = ErrorCode("E0505", "config", "malformed `[map.N]` entry")
E_CONFIG_BAD_MAP_VALUE = ErrorCode("E0506", "config", "`[map.N]` value of the wrong type")
E_CONFIG_UNKNOWN_BOARD = ErrorCode("E0509", "config", "`board` names no known cartridge board")


_BY_CODE: dict[str, ErrorCode] = {obj.code: obj for obj in globals().values() if isinstance(obj, ErrorCode)}


def lookup(code: str) -> ErrorCode | None:
    """Return the registered ErrorCode for `code`, or None if unknown."""
    return _BY_CODE.get(code)


def all_codes() -> list[ErrorCode]:
    """Every registered error code, sorted by numeric value for catalog output."""
    return sorted(_BY_CODE.values(), key=lambda e: e.code)
