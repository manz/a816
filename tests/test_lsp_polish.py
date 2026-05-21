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


def test_typed_bind_instance_fields_goto_definable() -> None:
    """`hdma_ch6 := (addr as DMAChannel)` then `hdma_ch6.A1TL` should jump
    to the bind line so the user can see where the alias came from."""
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
    assert "hdma_ch6" in doc.symbols
    assert "hdma_ch6.A1TL" in doc.symbols
    assert "hdma_ch6.DMAP" in doc.symbols


def test_bare_struct_type_name_is_goto_definable() -> None:
    """`(addr as PPU)` should jump to the `.struct PPU { ... }` line."""
    server = A816LanguageServer()
    content = """.struct PPU {
    byte INIDISP
}
"""
    doc = A816Document("file:///ppu_type.s", content)
    server.documents[doc.uri] = doc
    assert "PPU" in doc.symbols


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


def test_goto_def_on_stdlib_import_resolves_to_wheel_module() -> None:
    """`.import "@std/snes/ppu"` resolves to the bundled stdlib file."""
    server = A816LanguageServer()
    resolved = server._resolve_module_path("@std/snes/ppu", "file:///irrelevant.s")
    assert resolved is not None
    assert resolved.endswith("a816/stdlib/snes/ppu.s")


def test_workspace_index_picks_up_stdlib_symbols() -> None:
    """A document that imports `@std/snes/ppu` exposes PPU.* via the
    workspace index — goto-def on `PPU.INIDISP` lands in the stdlib file."""
    import tempfile
    from pathlib import Path

    from a816.lsp.server import WorkspaceIndex

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        entrypoint = root / "main.s"
        entrypoint.write_text(
            '"""Project root."""\n;! a816-lsp entrypoint\n.import "@std/snes/ppu"\n*=0x008000\n    lda PPU_BASE\n',
            encoding="utf-8",
        )
        workspace = WorkspaceIndex(root)
        workspace.rebuild()
        # PPU_BASE comes from the stdlib's ppu.s; the crawler should have
        # indexed it as a symbol.
        assert "PPU_BASE" in workspace.symbols
        # Likewise for the struct's flat field constants.
        assert "PPU.INIDISP" in workspace.symbols


def test_fluff_s001_does_not_fire_on_stdlib_struct_via_import() -> None:
    """Importing `@std/snes/ppu` makes `PPU` known to the linter."""
    from pathlib import Path

    from a816.fluff_lint import lint_text

    src = '"""Root."""\n.import "@std/snes/ppu"\n*=0x008000\n    lda.w (PPU_BASE as PPU).INIDISP\n'
    diagnostics = lint_text(src, Path("ff4.s"))
    s001_hits = [d for d in diagnostics if d.code == "S001"]
    assert not s001_hits, f"unexpected S001 diagnostics: {[d.message for d in s001_hits]}"


def test_fluff_s001_does_not_fire_on_module_path_struct() -> None:
    """`module_paths` from a816.toml lets user imports resolve too."""
    import tempfile
    from pathlib import Path

    from a816.fluff_lint import lint_text

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        modules = root / "modules"
        modules.mkdir()
        (modules / "player.s").write_text(
            ".struct Player {\n    word x\n    word y\n}\n",
            encoding="utf-8",
        )
        src = '"""Root."""\n.import "player"\n*=0x008000\n    lda.w (0x7e0000 as Player).x\n'
        diagnostics = lint_text(
            src,
            root / "ff4.s",
            module_paths=[modules],
        )
        s001 = [d for d in diagnostics if d.code == "S001"]
        assert not s001


def test_hover_on_struct_field_returns_type() -> None:
    """`Type.field` (no aux) hover shows the field type + struct."""
    server = A816LanguageServer()
    content = """.struct OAM {
    word x
    word y
    byte tile
}

    lda #OAM.tile
"""
    doc = A816Document("file:///hover_field.s", content)
    server.documents[doc.uri] = doc
    line_index = 6
    line = doc.lines[line_index]
    col = line.find("OAM")
    hover = server._handle_hover(
        HoverParams(
            text_document=TextDocumentIdentifier(uri=doc.uri),
            position=Position(line=line_index, character=col + 4),
        )
    )
    assert hover is not None
    body = hover.contents.value if hasattr(hover.contents, "value") else str(hover.contents)
    assert "byte" in body
    assert "tile" in body


def test_typed_bind_fields_resolved_from_stdlib_import() -> None:
    """Typed-bind referencing a struct from `@std/...` indexes its fields."""
    server = A816LanguageServer()
    content = """.import "@std/snes/dma"

ch6 := (0x4360 as DMAChannel)
    sta.l ch6.A1TL
"""
    doc = A816Document("file:///ch6.s", content)
    server.documents[doc.uri] = doc
    syms = doc.symbols
    # The stdlib lookup must walk the .import and find DMAChannel.
    assert "ch6" in syms
    assert "ch6.A1TL" in syms


def test_semantic_tokens_render_cast_and_binop() -> None:
    """Cast inner expressions + binary operators get highlighted."""
    server = A816LanguageServer()
    content = """.struct Pt {
    word x
}

p := (0x7e0000 + 0x10 as Pt)
"""
    doc = A816Document("file:///cast_binop.s", content)
    tokens = server._extract_semantic_tokens_from_ast(doc)
    types = {t["type"] for t in tokens}
    # 3=number, 5=operator — both must appear.
    assert 3 in types and 5 in types


def test_dotted_word_span_extracts_full_path() -> None:
    """`_dotted_word_span` stretches across `.` boundaries."""
    server = A816LanguageServer()
    line = "    lda #INIDISP.force_blank.mask"
    word = server._dotted_word_span(line, line.index("force"))
    assert word == "INIDISP.force_blank.mask"


def test_directive_nodes_emit_semantic_tokens() -> None:
    """Every directive type included in `_DIRECTIVE_TYPES` produces a token."""
    server = A816LanguageServer()
    content = """.scope demo {
    .a8
    .for n := 0, 4 {
        nop
    }
}
.pool p {
    range 0x028000 0x028fff
}
"""
    doc = A816Document("file:///dirs.s", content)
    tokens = server._extract_semantic_tokens_from_ast(doc)
    # Directive type id is 7 in the legend.
    assert any(t["type"] == 7 for t in tokens)


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
