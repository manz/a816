"""ValueNode + ExpressionNode."""

from __future__ import annotations

from a816.error_codes import E_SYMBOL_EXTERNAL_NOT_ALLOWED, E_SYMBOL_NOT_DEFINED
from a816.exceptions import ExternalExpressionReference, ExternalSymbolReference, SymbolNotDefined
from a816.parse.ast.expression import eval_expression
from a816.parse.ast.nodes import ExpressionAstNode
from a816.parse.nodes.errors import NodeError, undefined_symbol_error
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
        self.external_symbols: set[str] = set()
        """Extern names the value waits on (object mode); empty once it resolved locally."""

    def _touches_local_label(self) -> bool:
        from a816.parse.ast.expression import identifier_tokens

        return any(
            self.resolver.current_scope.find_label_scope(token.value) is not None
            for token in identifier_tokens(self.expression.tokens)
        )

    def _record_local_label_relocation(self) -> None:
        """A module-local label moves with the module's placement: record the
        expression for the linker to evaluate at the final address."""
        from a816.parse.ast.expression import link_expression

        if self._touches_local_label():
            self._deferred_expression = link_expression(self.expression, self.resolver)

    def get_value(self) -> int | str:  # type:ignore
        try:
            value = eval_expression(self.expression, self.resolver)
            self.external_symbols = set()
            if self.resolver.context.is_object_mode and isinstance(value, int):
                # Module-local label refs: record the original expression so the
                # linker can re-evaluate against the module's final placement.
                self._record_local_label_relocation()
            return value
        except ExternalExpressionReference as e:
            if self.resolver.context.is_object_mode:
                self._deferred_expression = e.expression
                self._external_symbols = e.external_symbols
                self.external_symbols = set(e.external_symbols)
                return 0
            raise NodeError(
                f"Expression contains external symbols: {e.expression_str}",
                self.file_info,
                code=str(E_SYMBOL_EXTERNAL_NOT_ALLOWED),
            ) from e
        except ExternalSymbolReference as e:
            if self.resolver.context.is_object_mode:
                # A macro argument bound to an extern is an alias: the
                # relocation names what it stands for, not the argument.
                from a816.parse.ast.expression import link_expression

                self._deferred_expression = link_expression(self.expression, self.resolver)
                self.external_symbols = {e.symbol_name}
                return 0
            raise NodeError(
                f"{e} ({self}) is not defined in the current scope.", self.file_info, code=str(E_SYMBOL_NOT_DEFINED)
            ) from e
        except SymbolNotDefined as e:
            raise undefined_symbol_error(e, self.file_info, self.resolver.current_scope) from e

    def get_value_string_len(self) -> int:
        value = self.get_value()
        if not isinstance(value, int):
            raise TypeError(f"Expected int, got {type(value).__name__}")
        return len(hex(value)) - 2

    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.expression.to_representation()[0]})"
