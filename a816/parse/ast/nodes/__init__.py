"""`a816.parse.ast.nodes`: AST node hierarchy. Facade re-export.

Node classes were previously defined in a single 911-line module. They
now live in topical submodules and are re-exported here so the historic
import surface (`from a816.parse.ast.nodes import X`) keeps working.
"""

from __future__ import annotations

from a816.parse.ast.nodes.base import (
    AstNode,
    BinOp,
    CastAccessExprNode,
    CastValueExprNode,
    ExpressionAstNode,
    ExprNode,
    Parenthesis,
    Term,
    UnaryOp,
)
from a816.parse.ast.nodes.containers import (
    BlockAstNode,
    CompoundAstNode,
    ForAstNode,
    IfAstNode,
    MacroAstNode,
    ScopeAstNode,
)
from a816.parse.ast.nodes.directives import (
    AsciiAstNode,
    CodePositionAstNode,
    CodeRelocationAstNode,
    CommentAstNode,
    DataNode,
    DebugAstNode,
    DocstringAstNode,
    FileInfoAstNode,
    IncludeAstNode,
    IncludeBinaryAstNode,
    IncludeIpsAstNode,
    MapArgs,
    MapAstNode,
    RegisterSizeAstNode,
    TableAstNode,
    TextAstNode,
)
from a816.parse.ast.nodes.opcode import OpcodeAstNode, index_map
from a816.parse.ast.nodes.pool import (
    AllocAstNode,
    PoolAstNode,
    PoolRangeExpr,
    ReclaimAstNode,
    RelocateAstNode,
)
from a816.parse.ast.nodes.struct import StructAstNode
from a816.parse.ast.nodes.symbols import (
    AssignAstNode,
    CodeLookupAstNode,
    ExternAstNode,
    ImportAstNode,
    LabelAstNode,
    LabelDeclAstNode,
    MacroApplyAstNode,
    SymbolAffectationAstNode,
)
from a816.parse.ast.nodes.unions import DeclAstNode, KeywordAstNode

__all__ = [
    "AllocAstNode",
    "AsciiAstNode",
    "AssignAstNode",
    "AstNode",
    "BinOp",
    "BlockAstNode",
    "CastAccessExprNode",
    "CastValueExprNode",
    "CodeLookupAstNode",
    "CodePositionAstNode",
    "CodeRelocationAstNode",
    "CommentAstNode",
    "CompoundAstNode",
    "DataNode",
    "DebugAstNode",
    "DeclAstNode",
    "DocstringAstNode",
    "ExprNode",
    "ExpressionAstNode",
    "ExternAstNode",
    "FileInfoAstNode",
    "ForAstNode",
    "IfAstNode",
    "ImportAstNode",
    "IncludeAstNode",
    "IncludeBinaryAstNode",
    "IncludeIpsAstNode",
    "KeywordAstNode",
    "LabelAstNode",
    "LabelDeclAstNode",
    "MacroApplyAstNode",
    "MacroAstNode",
    "MapArgs",
    "MapAstNode",
    "OpcodeAstNode",
    "Parenthesis",
    "PoolAstNode",
    "PoolRangeExpr",
    "ReclaimAstNode",
    "RegisterSizeAstNode",
    "RelocateAstNode",
    "ScopeAstNode",
    "StructAstNode",
    "SymbolAffectationAstNode",
    "TableAstNode",
    "Term",
    "TextAstNode",
    "UnaryOp",
    "index_map",
]
