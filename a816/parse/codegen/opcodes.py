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
from a816.parse.codegen.base import GenNodes, MacroDefinitions, generators
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
    opcode = node.opcode
    mode = node.addressing_mode
    if mode == AddressingMode.none:
        code.append(OpcodeNode(opcode, addressing_mode=mode, file_info=file_info, resolver=resolver))
    elif mode == AddressingMode.block_move:
        assert node.operand is not None and node.operand2 is not None
        code.append(
            OpcodeNode(
                opcode,
                addressing_mode=mode,
                value_node=ExpressionNode(node.operand, resolver, file_info),
                value_node2=ExpressionNode(node.operand2, resolver, file_info),
                file_info=file_info,
                resolver=resolver,
            )
        )
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
