"""`a816.parse.codegen`: AST → executable nodes.

The public entry is `code_gen`; submodule imports trigger registration
of every generator into `base.generators`, so the dispatch dict is fully
populated by the time external callers hit `code_gen`.
"""

from __future__ import annotations

# noqa: F401 — these imports populate base.generators via module-load side effects.
from a816.parse.codegen import (
    blocks,  # noqa: F401
    directives,  # noqa: F401
    flow,  # noqa: F401
    modules,  # noqa: F401
    opcodes,  # noqa: F401
    pool,  # noqa: F401
    structs,  # noqa: F401
    symbols,  # noqa: F401
)
from a816.parse.codegen.base import code_gen
from a816.parse.codegen.modules import _extract_public_symbols_from_source

__all__ = ["_extract_public_symbols_from_source", "code_gen"]
