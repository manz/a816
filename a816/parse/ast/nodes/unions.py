"""Type-alias unions used by parser_states + codegen dispatch."""

from __future__ import annotations

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
    IncludeAstNode,
    IncludeBinaryAstNode,
    IncludeIpsAstNode,
    MapAstNode,
    RegisterSizeAstNode,
    ReserveAstNode,
    ReserveTypedAstNode,
    TableAstNode,
    TextAstNode,
)
from a816.parse.ast.nodes.opcode import OpcodeAstNode
from a816.parse.ast.nodes.pool import AllocAstNode, PoolAstNode, ReclaimAstNode, RelocateAstNode
from a816.parse.ast.nodes.struct import StructAstNode
from a816.parse.ast.nodes.struct_instance import StructInstanceAstNode
from a816.parse.ast.nodes.symbols import (
    CodeLookupAstNode,
    ExternAstNode,
    ImportAstNode,
    LabelAstNode,
    LabelDeclAstNode,
    MacroApplyAstNode,
    SymbolAffectationAstNode,
)

KeywordAstNode = (
    ScopeAstNode
    | MapAstNode
    | MacroAstNode
    | IfAstNode
    | ForAstNode
    | DataNode
    | TextAstNode
    | AsciiAstNode
    | IncludeAstNode
    | IncludeIpsAstNode
    | IncludeBinaryAstNode
    | BlockAstNode
    | TableAstNode
    | StructAstNode
    | StructInstanceAstNode
    | ExternAstNode
    | ImportAstNode
    | DebugAstNode
    | RegisterSizeAstNode
    | LabelDeclAstNode
    | PoolAstNode
    | AllocAstNode
    | RelocateAstNode
    | ReclaimAstNode
    | ReserveAstNode
    | ReserveTypedAstNode
)

DeclAstNode = (
    CodeLookupAstNode
    | LabelAstNode
    | CompoundAstNode
    | CodePositionAstNode
    | CodeRelocationAstNode
    | OpcodeAstNode
    | KeywordAstNode
    | MacroApplyAstNode
    | SymbolAffectationAstNode
    | IfAstNode
    | CommentAstNode
)
