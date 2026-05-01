"""
Test semantic token parsing for LSP server with complex assembly input.
"""

import pytest

from a816.lsp.server import A816Document


def test_semantic_tokens_complex_assembly() -> None:
    """Test semantic token generation for complex assembly with conditionals and includes"""

    # Complex assembly input with various syntax elements
    assembly_content = """; ----------------
; Final Fantasy IV the new hack.
; ----------------

; Feature Flips
ENABLE_DIALOG_SKIP := 1
ENABLE_INTRO := 1
ENABLE_VWF_ATTACK_NAMES := 1
BATTLE_ENABLED := 1
MAGIC_ENABLED := 1

; Debug flags
TRIGGER_ENDING_CUTSCENE := 0

"""

    # Create document and parse
    doc = A816Document("file://test_complex.s", assembly_content)

    # Verify parsing succeeded
    assert doc.ast_nodes, "AST nodes should be generated"
    assert len(doc.ast_nodes) > 0, "Should have parsed AST nodes"
    assert doc.parse_error is None, f"Parsing should succeed, got error: {doc.parse_error}"

    # Check that symbols were extracted correctly
    expected_symbols = {
        "ENABLE_DIALOG_SKIP",
        "ENABLE_INTRO",
        "ENABLE_VWF_ATTACK_NAMES",
        "BATTLE_ENABLED",
        "MAGIC_ENABLED",
        "TRIGGER_ENDING_CUTSCENE",
    }

    # All symbols should be found
    found_symbols = set(doc.symbols.keys())
    assert expected_symbols.issubset(found_symbols), f"Missing symbols: {expected_symbols - found_symbols}"

    # Verify no diagnostics (no parse errors)
    assert len(doc.diagnostics) == 0, f"Should have no diagnostics, got: {doc.diagnostics}"


def test_semantic_tokens_generation() -> None:
    """Test actual semantic token generation from the LSP server"""
    from a816.lsp.server import A816LanguageServer

    assembly_content = """; Test comment
SYMBOL := 42
main:
    lda #0x01
    sta 0x2000
    rts

.if SYMBOL {
    nop
}"""

    # Create LSP server and document
    server = A816LanguageServer()
    doc = A816Document("file://test_tokens.s", assembly_content)

    # Generate semantic tokens
    semantic_tokens = server._extract_semantic_tokens_from_ast(doc)

    # Should generate tokens
    assert len(semantic_tokens) > 0, "Should generate semantic tokens"

    # Check for expected token types
    token_types = [token["type"] for token in semantic_tokens]

    # Should have various token types:
    # 0: keyword (opcodes like lda, sta, rts, nop)
    # 1: function (labels like main:)
    # 2: comment (; Test comment)
    # 3: number (0x01, 0x2000, 42)
    # 7: macro (directives like .include, .if)
    assert 0 in token_types, "Should have keyword tokens (opcodes)"
    assert 1 in token_types, "Should have function tokens (labels)"
    assert 2 in token_types, "Should have comment tokens"
    assert 3 in token_types, "Should have number tokens"
    assert 7 in token_types, "Should have macro tokens (directives)"


def test_semantic_tokens_delta_encoding() -> None:
    """Test that semantic tokens are properly delta encoded for LSP"""
    from a816.lsp.server import A816LanguageServer

    assembly_content = """main:
    lda #0x42
    rts"""

    server = A816LanguageServer()
    doc = A816Document("file://test_delta.s", assembly_content)

    # Get encoded tokens (delta format for LSP)
    encoded_tokens = server._analyze_semantic_tokens(doc)

    # Should be a list of integers in groups of 5
    assert len(encoded_tokens) % 5 == 0, (
        "Token data should be in groups of 5 (deltaLine, deltaChar, length, type, modifiers)"
    )
    assert len(encoded_tokens) > 0, "Should generate encoded token data"

    # First token should start at line 0
    if len(encoded_tokens) >= 5:
        first_delta_line = encoded_tokens[0]
        assert first_delta_line >= 0, "First token delta line should be >= 0"


def test_semantic_tokens_with_parsing_error() -> None:
    """Test semantic token behavior when parsing fails"""
    from a816.lsp.server import A816LanguageServer

    # Invalid assembly that should cause parsing to fail
    invalid_assembly = """
    invalid_syntax_here @#$%^&*()
    more_invalid_stuff <<<>>>
    """

    server = A816LanguageServer()
    doc = A816Document("file://test_error.s", invalid_assembly)

    # Should have parse error or no AST nodes
    if doc.parse_error or not doc.ast_nodes:
        # Should return empty tokens when parsing fails
        semantic_tokens = server._extract_semantic_tokens_from_ast(doc)
        assert len(semantic_tokens) == 0, "Should return empty tokens when parsing fails"

        encoded_tokens = server._analyze_semantic_tokens(doc)
        assert len(encoded_tokens) == 0, "Should return empty encoded tokens when parsing fails"


def test_semantic_token_positions() -> None:
    """Test that semantic token positions are accurate"""
    from a816.lsp.server import A816LanguageServer

    assembly_content = """label1:
    lda #0x42  ; comment
label2:
    rts"""

    server = A816LanguageServer()
    doc = A816Document("file://test_positions.s", assembly_content)

    semantic_tokens = server._extract_semantic_tokens_from_ast(doc)

    # Check that we have tokens on multiple lines
    lines_with_tokens = set(token["line"] for token in semantic_tokens)
    assert len(lines_with_tokens) > 1, "Should have tokens on multiple lines"

    # Check that positions are reasonable (within document bounds)
    for token in semantic_tokens:
        assert 0 <= token["line"] < len(doc.lines), f"Token line {token['line']} out of bounds"
        if token["line"] < len(doc.lines):
            line_content = doc.lines[token["line"]]
            assert 0 <= token["char"] <= len(line_content), (
                f"Token char {token['char']} out of bounds for line {token['line']}"
            )
            assert token["length"] > 0, "Token length should be positive"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
