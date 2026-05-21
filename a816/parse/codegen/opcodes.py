"""Opcode generation + typed-operand width inference + register-width warnings."""

from __future__ import annotations

from typing import cast

from a816.cpu.types import AddressingMode, ValueSize
from a816.parse.ast.nodes import (
    BlockAstNode,
    ExpressionAstNode,
    OpcodeAstNode,
    Term,
)
from a816.parse.codegen.base import GenNodes, MacroDefinitions, generators, logger
from a816.parse.nodes import ExpressionNode, NodeError, OpcodeNode
from a816.parse.tokens import Token, TokenType
from a816.protocols import NodeProtocol
from a816.symbols import Resolver


def _infer_typed_operand_size(operand: ExpressionAstNode | None, resolver: Resolver) -> str | None:
    """Pick `b`/`w`/`l` from a typed-instance field reference, else None.

    Covers the `lda p.field` shorthand: when the operand is exactly one
    dotted-identifier term and its base name is in
    ``resolver.typed_instance_addr_width``, use that addressing width.
    Compound expressions (`p.x + 1`, casts) fall back to the existing
    string-heuristic so this is purely additive.
    """
    if operand is None or not isinstance(operand, ExpressionAstNode):
        return None
    if len(operand.tokens) != 1:
        return None
    token = operand.tokens[0]
    if not isinstance(token, Term) or token.token.type != TokenType.IDENTIFIER:
        return None
    name = token.token.value
    if "." not in name:
        return None
    instance = name.split(".", 1)[0]
    return resolver.typed_instance_addr_width.get(instance)


_A_OPCODES = {"lda", "sta", "adc", "sbc", "and", "ora", "eor", "cmp", "bit"}
_X_Y_OPCODES = {"ldx", "stx", "ldy", "sty", "cpx", "cpy"}


def _opcode_register_width(opcode: str, resolver: Resolver) -> int | None:
    """Bytes-per-register for the current REP/SEP state, or None for opcodes
    that don't touch A/X/Y."""
    if opcode in _A_OPCODES:
        return resolver.a_size // 8
    if opcode in _X_Y_OPCODES:
        return resolver.i_size // 8
    return None


def _typed_field_lookup(operand: ExpressionAstNode | None, resolver: Resolver) -> tuple[str, str, int] | None:
    """Resolve a `p.field` operand to `(instance, full_name, field_width)`."""
    if operand is None or not isinstance(operand, ExpressionAstNode) or len(operand.tokens) != 1:
        return None
    token = operand.tokens[0]
    if not isinstance(token, Term) or token.token.type != TokenType.IDENTIFIER:
        return None
    name = token.token.value
    if "." not in name:
        return None
    instance, field_path = name.split(".", 1)
    type_name = resolver.typed_instances.get(instance)
    if type_name is None:
        return None
    layout = resolver.struct_layouts.get(type_name) or []
    field_entry = next(((p, o, w) for (p, o, w) in layout if p == field_path), None)
    if field_entry is None:
        return None
    _path, _offset, field_width = field_entry
    return instance, name, field_width


def _register_label(opcode: str) -> str:
    return "A" if opcode in _A_OPCODES else "X/Y"


def _rep_sep_payload(register_label: str, field_width: int) -> int:
    if register_label == "A":
        return 0x20
    return 0x10 if field_width == 2 else 0


def _maybe_warn_register_width_mismatch(node: OpcodeAstNode, resolver: Resolver) -> None:
    """Warn when a typed-field access asks for a width the current
    `a8`/`a16`/`i8`/`i16` state can't satisfy in one transfer.

    Only fires for the canonical `lda p.field` / `sta p.field` /
    `ldx p.field` shorthand; richer expressions silently skip.
    """
    lookup = _typed_field_lookup(node.operand, resolver)
    if lookup is None:
        return
    _instance, full_name, field_width = lookup
    register_width = _opcode_register_width(node.opcode, resolver)
    if register_width is None or field_width == register_width:
        return
    register_label = _register_label(node.opcode)
    rep_sep = "rep" if field_width > register_width else "sep"
    logger.warning(
        "field width (%d byte%s) does not match %s register size (%d bits) on `%s %s`; consider `%s #$%02x` first",
        field_width,
        "" if field_width == 1 else "s",
        register_label,
        register_width * 8,
        node.opcode,
        full_name,
        rep_sep,
        _rep_sep_payload(register_label, field_width),
    )


def generate_opcode(
    node: OpcodeAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    code: list[NodeProtocol] = []
    size = None

    if isinstance(node.operand, BlockAstNode):
        raise NodeError("Opcode operand must not be code", file_info)

    size = node.value_size
    if size is None:
        inferred = _infer_typed_operand_size(node.operand, resolver)
        if inferred in ("b", "w", "l"):
            size = cast(ValueSize, inferred)
    _maybe_warn_register_width_mismatch(node, resolver)
    opcode = node.opcode
    mode = node.addressing_mode
    if mode == AddressingMode.none:
        code.append(OpcodeNode(opcode, addressing_mode=mode, file_info=file_info, resolver=resolver))
    else:
        operand = node.operand
        assert operand is not None

        if mode in (
            AddressingMode.direct_indexed,
            AddressingMode.indirect_indexed,
            AddressingMode.indirect_indexed_long,
            AddressingMode.dp_or_sr_indirect_indexed,
            AddressingMode.stack_indexed_indirect_indexed,
        ):
            code.append(
                OpcodeNode(
                    opcode,
                    addressing_mode=mode,
                    size=size,
                    value_node=ExpressionNode(operand, resolver, file_info),
                    index=node.index,
                    file_info=file_info,
                    resolver=resolver,
                )
            )
        else:
            code.append(
                OpcodeNode(
                    opcode,
                    addressing_mode=mode,
                    size=size,
                    value_node=ExpressionNode(operand, resolver, file_info),
                    file_info=file_info,
                    resolver=resolver,
                )
            )

    return code


generators["opcode"] = generate_opcode
