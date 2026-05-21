"""LSP polish — fallback tokens, .incbin goto-def, bit-field aux hover,
scanner per-line recovery, struct field goto-def."""

from __future__ import annotations

from lsprotocol.types import HoverParams, Position, TextDocumentIdentifier

from a816.lsp.server import A816Document, A816LanguageServer


def test_semantic_tokens_fall_back_when_parse_fails() -> None:
    """Scanner error → AST empty → line-tokenizer kicks in."""
    server = A816LanguageServer()
    content = """; comment
    lda #0x42
    invalid_input @#$%
    nop
    """
    doc = A816Document("file:///fallback.s", content)
    tokens = server._extract_semantic_tokens_from_ast(doc)
    # Fallback must produce non-empty tokens despite the scanner error.
    assert len(tokens) > 0


def test_incbin_symbols_are_goto_definable() -> None:
    """`.incbin "assets/intro.bin"` exposes `assets_intro_bin` and `..._bin__size`
    for goto-def — both indexed to the directive's source line."""
    import tempfile
    from pathlib import Path

    server = A816LanguageServer()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "assets").mkdir()
        (root / "assets" / "intro.bin").write_bytes(b"\x00" * 16)
        src = root / "main.s"
        src.write_text('.incbin "assets/intro.bin"\n', encoding="utf-8")
        doc = A816Document(src.as_uri(), src.read_text(), include_paths=[root])
        server.documents[doc.uri] = doc
        # The recorded symbols come from `_record_incbin`; the sanitised
        # base is `assets_intro_bin`.
        symbols = doc.symbols
        assert "assets_intro_bin" in symbols
        assert "assets_intro_bin__size" in symbols
        # Both should resolve to the directive line; off-by-one across LSP
        # 0/1-based is normalised already, so check that lines match.
        for sym in ("assets_intro_bin", "assets_intro_bin__size"):
            assert symbols[sym][0].line == symbols["assets_intro_bin"][0].line


def test_bit_field_aux_symbols_are_goto_definable() -> None:
    """`Type.field.mask` and `Type.field.shift` join the LSP symbol index."""
    server = A816LanguageServer()
    content = """.struct INIDISP {
    u4 brightness
    u3 unused
    u1 force_blank
}
"""
    doc = A816Document("file:///inidisp.s", content)
    server.documents[doc.uri] = doc
    syms = doc.symbols
    assert "INIDISP.brightness" in syms
    assert "INIDISP.brightness.mask" in syms
    assert "INIDISP.brightness.shift" in syms
    assert "INIDISP.force_blank.mask" in syms


def test_hover_on_bit_field_mask_shows_value() -> None:
    """Hover on `INIDISP.force_blank.mask` returns the computed `0x80`."""
    server = A816LanguageServer()
    content = """.struct INIDISP {
    u4 brightness
    u3 unused
    u1 force_blank
}

    lda #INIDISP.force_blank.mask
"""
    doc = A816Document("file:///inidisp_hover.s", content)
    server.documents[doc.uri] = doc

    # Cursor positioned inside `INIDISP.force_blank.mask` on the lda line.
    line_index = 6
    line = doc.lines[line_index]
    col = line.find("INIDISP")
    hover = server._handle_hover(
        HoverParams(
            text_document=TextDocumentIdentifier(uri=doc.uri),
            position=Position(line=line_index, character=col + 4),
        )
    )
    assert hover is not None
    body = hover.contents.value if hasattr(hover.contents, "value") else str(hover.contents)
    assert "0x80" in body
    assert "force_blank" in body


def test_hover_on_bit_field_shift_shows_value() -> None:
    server = A816LanguageServer()
    content = """.struct INIDISP {
    u4 brightness
    u3 unused
    u1 force_blank
}

    lda #INIDISP.force_blank.shift
"""
    doc = A816Document("file:///inidisp_shift.s", content)
    server.documents[doc.uri] = doc

    line_index = 6
    line = doc.lines[line_index]
    col = line.find("INIDISP")
    hover = server._handle_hover(
        HoverParams(
            text_document=TextDocumentIdentifier(uri=doc.uri),
            position=Position(line=line_index, character=col + 4),
        )
    )
    assert hover is not None
    body = hover.contents.value if hasattr(hover.contents, "value") else str(hover.contents)
    # LSB of force_blank in a packed INIDISP is bit 7.
    assert "7" in body


def test_scanner_recovers_per_line_and_collects_errors() -> None:
    """A broken `.directive` line shouldn't kill all subsequent tokens."""
    from a816.parse.mzparser import A816Parser

    result = A816Parser.parse_as_ast(
        """lda #0x42
.lalala bad
nop
.borked thing
lda.l 0x123456
""",
        "recovery.s",
    )
    # Two errors collected, but the three valid statements still parsed.
    assert result.parse_errors is not None
    assert len(result.parse_errors) == 2
    assert len(result.nodes) == 3


def test_polished_lex_errors_carry_codes_and_hints() -> None:
    """Invalid addressing index emits a stellar diagnostic with a code + hint.

    The other lex polish target (invalid size specifier) only surfaces when
    the scanner has a newline to recover to; otherwise the parser sees the
    truncated token stream and produces a different (also useful) error.
    """
    from a816.parse.mzparser import A816Parser

    r = A816Parser.parse_as_ast("lda 0x00, O\n", "x.s")
    assert r.parse_error is not None
    assert r.parse_error.code == "E0001"
    assert "X, Y, or S" in (r.parse_error.hint or "")
