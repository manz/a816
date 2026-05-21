"""`a816.parse.nodes`: executable node classes. Facade re-export.

Node classes were previously defined in a single module. They now live
in topical submodules (errors / expr / symbols / opcode / module / data
/ alloc / position / text) and are re-exported here so the historic
import surface (`from a816.parse.nodes import LabelNode, NodeError, ...`)
keeps working unchanged.
"""

from __future__ import annotations

from a816.parse.nodes.alloc import AllocNode, RelocateNode
from a816.parse.nodes.data import (
    BinaryNode,
    ByteNode,
    DebugNode,
    LongNode,
    RegisterSizeNode,
    WordNode,
    _SizedValueNode,
)
from a816.parse.nodes.errors import NodeError, UnknownOpcodeError
from a816.parse.nodes.expr import ExpressionNode, ValueNode
from a816.parse.nodes.module import LinkedModuleNode, logger
from a816.parse.nodes.opcode import OpcodeNode
from a816.parse.nodes.position import (
    CodePositionNode,
    IncludeIpsNode,
    PopScopeNode,
    RelocationAddressNode,
    ScopeNode,
)
from a816.parse.nodes.symbols import ExternNode, LabelDeclNode, LabelNode, SymbolNode
from a816.parse.nodes.text import (
    AbstractTextNode,
    AsciiNode,
    PointerNode,
    TableNode,
    TextNode,
    variable_expansion,
)

__all__ = [
    "AbstractTextNode",
    "AllocNode",
    "AsciiNode",
    "BinaryNode",
    "ByteNode",
    "CodePositionNode",
    "DebugNode",
    "ExpressionNode",
    "ExternNode",
    "IncludeIpsNode",
    "LabelDeclNode",
    "LabelNode",
    "LinkedModuleNode",
    "LongNode",
    "NodeError",
    "OpcodeNode",
    "PointerNode",
    "PopScopeNode",
    "RegisterSizeNode",
    "RelocateNode",
    "RelocationAddressNode",
    "ScopeNode",
    "SymbolNode",
    "TableNode",
    "TextNode",
    "UnknownOpcodeError",
    "ValueNode",
    "WordNode",
    "_SizedValueNode",
    "logger",
    "variable_expansion",
]
