"""Pure single-line emitters: opcodes, operands, data directives, macro applies.

Free functions taking `(node, options)` — no formatter-class state needed.
"""

from __future__ import annotations

from collections.abc import Callable

from a816.cpu.cpu_65c816 import AddressingMode
from a816.formatter.options import FormattingOptions
from a816.parse.ast.nodes import DataNode, MacroApplyAstNode, OpcodeAstNode


def _format_immediate(operand: str, _comma: str, _index: str | None) -> str:
    return operand if operand.startswith("#") else f"#{operand}"


def _format_indirect(operand: str, _comma: str, _index: str | None) -> str:
    return f"({operand})"


def _format_indirect_long(operand: str, _comma: str, _index: str | None) -> str:
    return f"[{operand}]"


def _format_dp_or_sr(operand: str, comma: str, index: str | None) -> str:
    inner = f"{operand}{comma}{index}" if index else operand
    return f"({inner})"


def format_operand(opcode_ast: OpcodeAstNode, options: FormattingOptions) -> str:
    """Format an opcode operand for its addressing mode."""
    if not opcode_ast.operand:
        return ""

    operand = opcode_ast.operand.to_canonical().strip()
    addressing_mode = opcode_ast.addressing_mode
    index = opcode_ast.index.lower() if opcode_ast.index else None
    comma = ", " if options.space_after_comma else ","

    def with_index(base: str) -> str:
        return f"{base}{comma}{index}" if index else base

    wrappers: dict[AddressingMode, Callable[[str], str]] = {
        AddressingMode.indirect_indexed: lambda o: with_index(f"({o})"),
        AddressingMode.indirect_indexed_long: lambda o: with_index(f"[{o}]"),
        AddressingMode.stack_indexed_indirect_indexed: lambda o: with_index(f"({o}{comma}s)"),
    }
    if addressing_mode in wrappers:
        return wrappers[addressing_mode](operand)

    simple: dict[AddressingMode, Callable[[str, str, str | None], str]] = {
        AddressingMode.immediate: _format_immediate,
        AddressingMode.indirect: _format_indirect,
        AddressingMode.indirect_long: _format_indirect_long,
        AddressingMode.dp_or_sr_indirect_indexed: _format_dp_or_sr,
    }
    if addressing_mode in simple:
        return simple[addressing_mode](operand, comma, index)
    return with_index(operand)


def format_opcode(opcode_ast: OpcodeAstNode, options: FormattingOptions) -> str:
    """Format an opcode instruction."""
    opcode = opcode_ast.opcode.lower()
    if opcode_ast.value_size:
        opcode += f".{opcode_ast.value_size.lower()}"
    operand = format_operand(opcode_ast, options)
    if operand:
        return f"{opcode} {operand}"
    return f"{opcode}"


def format_data(data_ast: DataNode, options: FormattingOptions) -> str:
    """Format a data directive."""
    directive = f".{data_ast.kind}"
    values = [expr.to_canonical() for expr in data_ast.data]
    operand = ", ".join(values) if options.space_after_comma else ",".join(values)
    return f"{directive} {operand}"


def format_macro_apply(apply_ast: MacroApplyAstNode, options: FormattingOptions) -> str:
    """Format a macro application."""
    if not apply_ast.args:
        return f"{apply_ast.name}()"
    args = [arg.to_canonical() for arg in apply_ast.args]
    arg_str = ", ".join(args) if options.space_after_comma else ",".join(args)
    return f"{apply_ast.name}({arg_str})"
