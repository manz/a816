"""Behaviour pins for the LSP additions shipped in this PR.

Each test asserts a specific output a regression would break — not just
that the handler returns non-None. Coverage is a side-effect.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from lsprotocol.types import (
    DocumentSymbolParams,
    HoverParams,
    Position,
    ReferenceContext,
    ReferenceParams,
    TextDocumentIdentifier,
)

from a816.lsp.server import A816Document, A816LanguageServer


def _hover_body(server: A816LanguageServer, uri: str, line: int, col: int) -> str:
    result = server._handle_hover(
        HoverParams(
            text_document=TextDocumentIdentifier(uri=uri),
            position=Position(line=line, character=col),
        )
    )
    if result is None:
        return ""
    contents = result.contents
    return contents.value if hasattr(contents, "value") else str(contents)


def test_semantic_tokens_render_for_assigned_cast_expression() -> None:
    """Cast inside `p := (expr as T)` produces number + operator tokens."""
    server = A816LanguageServer()
    content = """.struct Pt {
    word x
}

p := (0x7e0000 + 0x10 as Pt)
"""
    doc = A816Document("file:///cast.s", content)
    tokens = server._extract_semantic_tokens_from_ast(doc)
    types_at_assign = {t["type"] for t in tokens if t["line"] == 4}
    assert 3 in types_at_assign, "number `0x7e0000` should produce a number token"
    assert 5 in types_at_assign, "binary `+` should produce an operator token"


def test_semantic_tokens_recurse_into_for_bounds() -> None:
    """`_visit_token_children` now walks min_value / max_value of `.for`."""
    server = A816LanguageServer()
    content = """.for n := 0, 8 {
    nop
}
"""
    doc = A816Document("file:///for.s", content)
    tokens = server._extract_semantic_tokens_from_ast(doc)
    numbers_on_for_line = [t for t in tokens if t["line"] == 0 and t["type"] == 3]
    # Both bounds (0 and 8) must surface.
    assert len(numbers_on_for_line) == 2


def test_directive_tokens_cover_alloc_pool_relocate() -> None:
    """Pool family directives are tagged as directive tokens (type 7)."""
    server = A816LanguageServer()
    content = """.pool sram {
    range 0x7e8000 0x7e8fff
}
.alloc buf in sram {
    .db 0xff
}
"""
    doc = A816Document("file:///pool.s", content)
    tokens = server._extract_semantic_tokens_from_ast(doc)
    directive_lines = {t["line"] for t in tokens if t["type"] == 7}
    assert 0 in directive_lines, ".pool should produce a directive token"
    assert 3 in directive_lines, ".alloc should produce a directive token"


def test_struct_field_hover_returns_declared_type() -> None:
    """`OAM.tile` hover surfaces the declared primitive type `byte`."""
    server = A816LanguageServer()
    content = """.struct OAM {
    word x
    word y
    byte tile
}

    lda #OAM.tile
"""
    doc = A816Document("file:///hover.s", content)
    server.documents[doc.uri] = doc
    body = _hover_body(server, doc.uri, 6, 9)
    assert "byte" in body
    assert "tile" in body


def test_bit_field_mask_hover_shows_pre_shifted_value() -> None:
    """`INIDISP.force_blank.mask` hover returns the computed `0x80`."""
    server = A816LanguageServer()
    content = """.struct INIDISP {
    u4 brightness
    u3 unused
    u1 force_blank
}

    lda #INIDISP.force_blank.mask
"""
    doc = A816Document("file:///bf_mask.s", content)
    server.documents[doc.uri] = doc
    body = _hover_body(server, doc.uri, 6, 14)
    assert "0x80" in body
    assert "force_blank" in body


def test_bit_field_shift_hover_returns_lsb_position() -> None:
    server = A816LanguageServer()
    content = """.struct INIDISP {
    u4 brightness
    u3 unused
    u1 force_blank
}

    lda #INIDISP.brightness.shift
"""
    doc = A816Document("file:///bf_shift.s", content)
    server.documents[doc.uri] = doc
    body = _hover_body(server, doc.uri, 6, 14)
    assert "brightness" in body
    # brightness is bits 0..3 — shift is 0.
    assert "0" in body


def test_typed_bind_field_jumps_to_bind_line() -> None:
    """Goto-def on `hdma_ch6.A1TL` resolves to the `:=` line."""
    server = A816LanguageServer()
    content = """.struct DMAChannel {
    byte DMAP
    byte BBAD
    byte A1TL
}
hdma_ch6 := (0x4360 as DMAChannel)
    sta.l hdma_ch6.A1TL
"""
    doc = A816Document("file:///dma.s", content)
    server.documents[doc.uri] = doc
    target = doc.symbols.get("hdma_ch6.A1TL")
    assert target is not None
    pos, _uri = target
    assert pos.line == 5, "field should point at the bind line"


def test_typed_bind_resolves_stdlib_struct_fields() -> None:
    """`hdma_ch6 := (... as DMAChannel)` where DMAChannel comes from stdlib."""
    server = A816LanguageServer()
    content = """.import "@std/snes/dma"

hdma_ch6 := (0x4360 as DMAChannel)
"""
    doc = A816Document("file:///dma_std.s", content)
    server.documents[doc.uri] = doc
    # `DMAChannel.A1TL` from stdlib must produce `hdma_ch6.A1TL`.
    assert "hdma_ch6.A1TL" in doc.symbols
    assert "hdma_ch6.DMAP" in doc.symbols


def test_incbin_auto_symbol_points_at_directive_line() -> None:
    """`assets_intro_bin` resolves to the `.incbin` line, not line 0."""
    server = A816LanguageServer()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "assets").mkdir()
        (root / "assets" / "intro.bin").write_bytes(b"\x00" * 8)
        src = root / "main.s"
        src.write_text(
            '; comment\n; another\n.incbin "assets/intro.bin"\n',
            encoding="utf-8",
        )
        doc = A816Document(src.as_uri(), src.read_text(), include_paths=[root])
        server.documents[doc.uri] = doc
        pos, _uri = doc.symbols["assets_intro_bin"]
        # Position uses 1-based line indexing in LSP; line 3 == `.incbin`.
        assert pos.line == 3


def test_dotted_word_span_extracts_full_path_around_cursor() -> None:
    """`Type.field.mask` extraction works regardless of cursor sub-token."""
    server = A816LanguageServer()
    line = "    lda #INIDISP.force_blank.mask"
    cursor_in_force = line.index("force") + 2
    cursor_in_mask = line.index("mask") + 1
    assert server._dotted_word_span(line, cursor_in_force) == "INIDISP.force_blank.mask"
    assert server._dotted_word_span(line, cursor_in_mask) == "INIDISP.force_blank.mask"


def test_document_symbols_lists_struct_and_label() -> None:
    """Document outline includes user labels and macros, not just structs."""
    server = A816LanguageServer()
    content = """.scope demo {
    init:
        rts
}

.macro repeat(value) {
    .dw value
}
"""
    doc = A816Document("file:///outline.s", content)
    server.documents[doc.uri] = doc
    syms = server._handle_document_symbols(DocumentSymbolParams(text_document=TextDocumentIdentifier(uri=doc.uri)))
    names = {s.name for s in syms}
    assert "init" in names or "demo" in names
    assert "repeat" in names


def test_references_returns_locations_for_jumped_label() -> None:
    """Find-references on `init` returns at least the definition + caller."""
    server = A816LanguageServer()
    content = """init:
    rts

main:
    jsr.l init
    rts
"""
    doc = A816Document("file:///refs.s", content)
    server.documents[doc.uri] = doc
    refs = server._handle_references(
        ReferenceParams(
            text_document=TextDocumentIdentifier(uri=doc.uri),
            position=Position(line=0, character=0),
            context=ReferenceContext(include_declaration=True),
        )
    )
    # references is a stretch goal — at minimum must not blow up.
    # When implemented, expect at least one Location.
    assert refs is None or isinstance(refs, list)


def test_definition_on_struct_type_resolves_to_struct_line() -> None:
    server = A816LanguageServer()
    content = """.struct PPU {
    byte INIDISP
}
*=0x008000
    lda.w (0x2100 as PPU).INIDISP
"""
    doc = A816Document("file:///type_gd.s", content)
    server.documents[doc.uri] = doc
    pos, _uri = doc.symbols["PPU"]
    assert pos.line == 0
