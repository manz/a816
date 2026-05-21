"""`a816.parse.parser_states`: token stream → AST.

Entry points: `parse_initial` (top-level program) and `parse_expression_ep`
(single expression, used by the expression module's late-evaluation path).
"""

from __future__ import annotations

from a816.parse.parser_states.core import parse_initial
from a816.parse.parser_states.expr import parse_expression_ep

__all__ = ["parse_expression_ep", "parse_initial"]
