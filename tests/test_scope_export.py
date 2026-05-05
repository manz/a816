"""Promote macro and scope labels to a NamedScope parent."""

from __future__ import annotations

from a816.program import Program


def _resolved(source: str) -> Program:
    program = Program()
    error, nodes = program.parser.parse(source)
    assert error is None, error
    program.resolve_labels(nodes)
    return program


def _public_symbols(program: Program) -> dict[str, int | str]:
    """What the root scope sees — what callers can resolve."""
    root = program.resolver.scopes[0]
    flat: dict[str, int | str] = {}
    flat.update(root.symbols)
    flat.update(root.labels)
    return flat


def test_scope_promotes_labels_to_dotted_names() -> None:
    program = _resolved(
        """
        *=0x008000
        .scope inventory {
        init:
            rts
        render:
            rts
        }
        """
    )
    public = _public_symbols(program)
    assert "inventory.init" in public
    assert "inventory.render" in public


def test_scope_skips_underscore_prefixed_names() -> None:
    program = _resolved(
        """
        *=0x008000
        .scope inventory {
        public_entry:
            rts
        _helper:
            rts
        }
        """
    )
    public = _public_symbols(program)
    assert "inventory.public_entry" in public
    assert "inventory._helper" not in public
    assert "inventory.__size" not in public  # struct-only convention


def test_scope_keeps_dunder_names() -> None:
    """Struct-style `__size` exports must still pass the underscore filter."""
    program = _resolved(
        """
        .struct OAM {
            byte a
            byte b
        }
        """
    )
    public = _public_symbols(program)
    assert public["OAM.__size"] == 2


def test_macro_inside_scope_publishes_labels() -> None:
    program = _resolved(
        """
        .macro engine() {
        init:
            rts
        render:
            rts
        _local:
            rts
        }
        *=0x008000
        .scope inventory {
            engine()
        }
        """
    )
    public = _public_symbols(program)
    assert "inventory.init" in public
    assert "inventory.render" in public
    assert "inventory._local" not in public


def test_macro_outside_scope_does_not_leak() -> None:
    """Bare `engine()` calls keep labels private (no NamedScope to bubble to)."""
    program = _resolved(
        """
        .macro engine() {
        init:
            rts
        }
        *=0x008000
        engine()
        """
    )
    public = _public_symbols(program)
    # Labels stay buried in the anon arg-binding scope when there's no
    # NamedScope wrapping the call site.
    assert "init" not in public
