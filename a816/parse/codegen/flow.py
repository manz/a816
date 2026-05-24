"""Control flow: `.for`, `.if`, code-lookup, macro definition + application."""

from __future__ import annotations

from typing import cast

from a816.exceptions import (
    ExternalExpressionReference,
    ExternalSymbolReference,
    SymbolNotDefined,
)
from a816.parse.ast.expression import eval_expression
from a816.parse.ast.nodes import (
    BlockAstNode,
    CodeLookupAstNode,
    ExpressionAstNode,
    ForAstNode,
    IfAstNode,
    MacroApplyAstNode,
    MacroAstNode,
    Term,
)
from a816.parse.codegen.base import GenNodes, MacroDefinitions, _code_gen, generators
from a816.parse.nodes import NodeError, PopScopeNode, ScopeNode, SymbolNode
from a816.parse.tokens import Token, TokenType
from a816.symbols import Resolver


def generate_for(
    node: ForAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    code: GenNodes = []
    from_val = cast(int, eval_expression(node.min_value, resolver))
    to_val = cast(int, eval_expression(node.max_value, resolver))
    for k in range(from_val, to_val):
        resolver.append_internal_scope()
        resolver.use_next_scope()
        code.append(ScopeNode(resolver))
        code.append(
            SymbolNode(
                node.symbol,
                ExpressionAstNode([Term(Token(TokenType.NUMBER, str(k)))]),
                resolver,
            )
        )
        code += _code_gen(node.body.body, resolver, macro_definitions)
        code.append(PopScopeNode(resolver))
        resolver.restore_scope()
    return code


def generate_if(
    node: IfAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    code = []
    if_branch_true = node.block
    if_branch_false = node.else_block

    try:
        condition = eval_expression(node.expression, resolver)
    except (KeyError, SymbolNotDefined):
        # Symbol not yet defined - this can happen with forward label references
        # like `.if END_OF_FREE_SPACE > 0x1ffff`. Labels are resolved in a later
        # pass, so we treat unresolved symbols as false during code generation.
        condition = False
    if condition:
        code += _code_gen(if_branch_true.body, resolver, macro_definitions)
    elif if_branch_false:
        code += _code_gen(if_branch_false.body, resolver, macro_definitions)
    return code


def generate_code_lookup(
    node: CodeLookupAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    value = resolver.current_scope.value_for(node.symbol)

    if isinstance(value, BlockAstNode):
        return _code_gen(value.body, resolver, macro_definitions)
    else:
        raise NodeError(f"{node.symbol} is not a code block ({value})", file_info)


def _expression_touches_local_label(expr: ExpressionAstNode, resolver: Resolver) -> bool:
    """Return True if any identifier in ``expr`` resolves to a module-local CODE label."""
    if not resolver.context.is_object_mode:
        return False
    for term in expr.tokens:
        tok = getattr(term, "token", None)
        if tok is None or tok.type != TokenType.IDENTIFIER:
            continue
        if resolver.current_scope.find_label_scope(tok.value) is not None:
            return True
    return False


def generate_macro_application(
    node: MacroApplyAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    code: GenNodes = []
    macro_def: MacroAstNode = macro_definitions[node.name]
    macro_code = macro_def.block
    macro_args = macro_def.args
    macro_args_values = node.args

    if len(macro_args_values) != len(macro_args):
        raise NodeError(
            f"Macro '{node.name}' expects {len(macro_args)} argument(s), got {len(macro_args_values)}", file_info
        )

    resolver.append_scope()
    resolver.use_next_scope()
    code.append(ScopeNode(resolver))

    for index, arg in enumerate(macro_args):
        value = macro_args_values[index]
        try:
            if isinstance(value, BlockAstNode):
                resolver.current_scope.add_symbol(arg, value)
            elif isinstance(value, ExpressionAstNode) and _expression_touches_local_label(value, resolver):
                # The macro arg expression references a module-local CODE label.
                # Bake it as an alias (text expression) so eval at emit time goes
                # through the relocation pipeline and the value reflects the
                # module's final placement, not the compile-time base.
                from a816.parse.ast.expression import (
                    _inline_aliases,
                    canonicalize_local_label_refs,
                    reconstruct_expression,
                )

                expr_str = _inline_aliases(reconstruct_expression(value), resolver)
                expr_str = canonicalize_local_label_refs(expr_str, resolver)
                resolver.current_scope.add_external_alias(arg, expr_str)
            else:
                resolver.current_scope.add_symbol(arg, eval_expression(value, resolver))
        except SymbolNotDefined:
            # Defer the resolve to the emit part.
            code.append(SymbolNode(arg, value, resolver))
        except (ExternalExpressionReference, ExternalSymbolReference) as e:
            # Macro argument expression references externs; treat the bound
            # name as an alias locally. Do NOT publish to the object writer:
            # the binding is invocation-local, and any extern relocations
            # generated inside the macro body inline the alias on the way out.
            expr_str = e.symbol_name if isinstance(e, ExternalSymbolReference) else e.expression_str
            resolver.current_scope.add_external_alias(arg, expr_str)
    code += _code_gen(macro_code.body, resolver, macro_definitions)
    code.append(PopScopeNode(resolver))
    resolver.restore_scope()
    return code


def generate_macro(
    node: MacroAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    macro_definitions[node.name] = node
    return []


generators["for"] = generate_for
generators["if"] = generate_if
generators["code_lookup"] = generate_code_lookup
generators["macro_apply"] = generate_macro_application
generators["macro"] = generate_macro
