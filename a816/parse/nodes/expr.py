"""ValueNode + ExpressionNode."""

from __future__ import annotations

import re

from a816.diagnostics.suggest import did_you_mean_hint as _did_you_mean_hint
from a816.error_codes import E_SYMBOL_NOT_DEFINED as _E_SYMBOL_NOT_DEFINED
from a816.exceptions import ExternalExpressionReference, ExternalSymbolReference, SymbolNotDefined
from a816.parse.ast.expression import eval_expression
from a816.parse.ast.nodes import ExpressionAstNode
from a816.parse.nodes.errors import NodeError
from a816.parse.tokens import Token
from a816.protocols import ValueNodeProtocol
from a816.symbols import Resolver


class ValueNode(ValueNodeProtocol):
    def __init__(self, value: str) -> None:
        self.value = value

    def get_value(self) -> int:
        return int(self.value, 16)

    def get_value_string_len(self) -> int:
        value_length = len(self.value)
        return value_length

    def __str__(self) -> str:
        return f"ValueNode({self.value})"


class ExpressionNode(ValueNodeProtocol):
    def __init__(self, expression: ExpressionAstNode, resolver: Resolver, file_info: Token) -> None:
        self.expression = expression
        self.resolver = resolver
        self.file_info = file_info

    def _compute_local_label_renames(self) -> tuple[dict[str, str], bool]:
        """Return (rename map, touches_any_label). Nested-scope label refs
        get rewritten to their EXPORTED form so the linker resolves them
        against `symbol_map` (which holds the exported names, not the
        source-level bare names). Defers to `Resolver.exported_label_name`,
        which mirrors `_export_name`: NamedScope members become `Name.label`,
        anonymous nested scopes get the `__sc<idx>__` mangle, and root /
        AllocBodyScope labels keep their bare name."""
        from a816.parse.tokens import TokenType

        rename: dict[str, str] = {}
        touches_label = False
        for t in self.expression.tokens:
            tok = getattr(t, "token", None)
            if tok is None or tok.type != TokenType.IDENTIFIER:
                continue
            if self.resolver.current_scope.find_label_scope(tok.value) is None:
                continue
            touches_label = True
            exported = self.resolver.exported_label_name(tok.value)
            if exported != tok.value:
                rename[tok.value] = exported
        return rename, touches_label

    def _record_local_label_relocation(self) -> None:
        from a816.parse.ast.expression import _inline_aliases, reconstruct_expression

        rename, touches_label = self._compute_local_label_renames()
        if not touches_label:
            return
        expr_str = _inline_aliases(reconstruct_expression(self.expression), self.resolver)
        for short, mangled in rename.items():
            expr_str = re.sub(rf"\b{re.escape(short)}\b", mangled, expr_str)
        self._deferred_expression = expr_str
        self._local_label_renames = rename

    def get_value(self) -> int | str:  # type:ignore
        try:
            value = eval_expression(self.expression, self.resolver)
            if self.resolver.context.is_object_mode and isinstance(value, int):
                # Module-local label refs: record the original expression so the
                # linker can re-evaluate against the module's final placement.
                self._record_local_label_relocation()
            return value
        except ExternalExpressionReference as e:
            if self.resolver.context.is_object_mode:
                self._deferred_expression = e.expression_str
                self._external_symbols = e.external_symbols
                return 0
            raise NodeError(f"Expression contains external symbols: {e.expression_str}", self.file_info) from e
        except ExternalSymbolReference as e:
            if self.resolver.context.is_object_mode:
                # Inline-substitute any aliases so macro-arg names
                # (`jump_table`, `count`, etc.) bound by
                # `add_external_alias` get replaced with their underlying
                # extern expression. Without this, the relocation
                # serialises the raw macro-arg name and the linker
                # reports it as an unresolved external - which it is,
                # because nobody outside the macro invocation knows that
                # name.
                from a816.parse.ast.expression import _inline_aliases, reconstruct_expression

                self._deferred_expression = _inline_aliases(reconstruct_expression(self.expression), self.resolver)
                return 0
            raise NodeError(f"{e} ({self}) is not defined in the current scope.", self.file_info) from e
        except SymbolNotDefined as e:
            raise NodeError(
                f"`{e}` is not defined in the current scope",
                self.file_info,
                code=str(_E_SYMBOL_NOT_DEFINED),
                hint=_did_you_mean_hint(str(e), self.resolver.current_scope),
            ) from e

    def get_value_string_len(self) -> int:
        value = self.get_value()
        if not isinstance(value, int):
            raise TypeError(f"Expected int, got {type(value).__name__}")
        return len(hex(value)) - 2

    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.expression.to_representation()[0]})"
