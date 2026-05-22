"""Label / symbol / extern / import / assign / macro-apply / code-lookup nodes."""

from __future__ import annotations

from typing import Any

from a816.parse.ast.nodes.base import AstNode, ExpressionAstNode
from a816.parse.ast.nodes.containers import BlockAstNode
from a816.parse.tokens import Token


class LabelAstNode(AstNode):
    label: str

    def __init__(self, label: str, file_info: Token) -> None:
        super().__init__("label", file_info)
        self.label = label

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.label

    def to_canonical(self) -> str:
        return f"{self.label}:"


class SymbolAffectationAstNode(AstNode):
    def __init__(self, symbol: str, value: ExpressionAstNode, file_info: Token):
        super().__init__("symbol", file_info)
        self.symbol = symbol
        self.value = value

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.symbol, self.value.to_representation()[0]

    def to_canonical(self) -> str:
        value_str = self.value.to_canonical()
        return f"{self.symbol} = {value_str}"


class LabelDeclAstNode(AstNode):
    """AST node for `.label NAME = ADDR` directive.

    Names a constant address as a label without moving the position counter.
    Behaves like a code label for tooling: emitted as a LABEL record in
    `.adbg`, resolvable via lookup_label, documentable by fluff.
    """

    def __init__(self, symbol: str, value: ExpressionAstNode, file_info: Token):
        super().__init__("label_decl", file_info)
        self.symbol = symbol
        self.value = value

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.symbol, self.value.to_representation()[0]

    def to_canonical(self) -> str:
        return f".label {self.symbol} = {self.value.to_canonical()}"


class ExternAstNode(AstNode):
    def __init__(self, symbol: str, file_info: Token):
        super().__init__("extern", file_info)
        self.symbol = symbol

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.symbol

    def to_canonical(self) -> str:
        return f".extern {self.symbol}"


class ImportAstNode(AstNode):
    """AST node for .import "module" directive.

    Imports all public symbols from a module (object file or source file).
    This is similar to Turbo Pascal's 'uses' clause.
    """

    module_name: str

    def __init__(self, module_name: str, file_info: Token) -> None:
        super().__init__("import", file_info)
        self.module_name = module_name

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.module_name

    def to_canonical(self) -> str:
        return f'.import "{self.module_name}"'


class AssignAstNode(AstNode):
    def __init__(self, symbol: str, value: ExpressionAstNode, file_info: Token):
        super().__init__("assign", file_info)
        self.symbol = symbol
        self.value = value

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.symbol, self.value.to_representation()[0]

    def to_canonical(self) -> str:
        return f"{self.symbol} := {self.value.to_canonical()}"


class CodeLookupAstNode(AstNode):
    def __init__(self, symbol: str, file_info: Token):
        super().__init__("code_lookup", file_info)
        self.symbol = symbol

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.symbol

    def to_canonical(self) -> str:
        return f"{{{{{self.symbol}}}}}"


class MacroApplyAstNode(AstNode):
    name: str
    args: list[ExpressionAstNode | BlockAstNode]

    def __init__(
        self,
        name: str,
        args: list[ExpressionAstNode | BlockAstNode],
        file_info: Token,
    ):
        super().__init__("macro_apply", file_info)
        self.name = name
        self.args = args

    def to_representation(self) -> tuple[Any, ...]:
        apply_args = []

        for arg in self.args:
            if isinstance(arg, ExpressionAstNode):
                apply_args.append(arg.to_representation()[0])
            else:
                apply_args.append(arg.to_representation())

        return (
            self.kind,
            self.name,
            ("apply_args", apply_args),
        )

    def to_canonical(self) -> str:
        if not self.args:
            return f"{self.name}()"
        args_str = ", ".join(arg.to_canonical() for arg in self.args)
        return f"{self.name}({args_str})"
