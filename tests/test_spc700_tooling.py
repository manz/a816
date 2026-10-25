"""SPC700 in the tools: a `*.spc.s` file lints, formats and edits as SPC700."""

from __future__ import annotations

from pathlib import Path

import pytest
from lsprotocol.types import (
    CompletionParams,
    HoverParams,
    Position,
    PrepareRenameParams,
    SignatureHelpParams,
    TextDocumentIdentifier,
    TextDocumentPositionParams,
)

from a816.fluff import lint_text
from a816.formatter import A816Formatter
from a816.lsp.server import A816Document, A816LanguageServer
from a816.lsp.workspace import WorkspaceIndex

_DRIVER = """\"\"\"Driver.\"\"\"
.alloc driver at 0x0400 {
start:
    mov a, #0x12
_idle:
    mov1 c, 0x1234.5
loop:
    bne loop
    ret
}
"""

_MAIN = """\"\"\"Main.\"\"\"
.alloc image at 0x018000 {
    .embed "driver"
}
.alloc boot at 0x008000 {
    lda.w #driver.__base
    jsr.w driver.start
}
"""


def _open(server: A816LanguageServer, path: Path) -> str:
    uri = path.as_uri()
    server.documents[uri] = A816Document(uri, path.read_text(encoding="utf-8"))
    return uri


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "driver.spc.s").write_text(_DRIVER, encoding="utf-8")
    (tmp_path / "main.s").write_text(_MAIN, encoding="utf-8")
    return tmp_path


def _at(uri: str, line: int, column: int) -> TextDocumentPositionParams:
    return TextDocumentPositionParams(
        text_document=TextDocumentIdentifier(uri=uri), position=Position(line=line, character=column)
    )


def test_an_unused_private_label_is_found_in_spc700_code() -> None:
    hits = lint_text(_DRIVER, Path("driver.spc.s"))
    assert [(hit.code, hit.line) for hit in hits] == [("F002", 5)]


def test_ret_ends_the_flow_before_a_label() -> None:
    source = '"""D."""\n.alloc d at 0x0400 {\n    ret\n_next:\n    nop\n}\n'
    (hit,) = lint_text(source, Path("d.spc.s"))
    assert hit.fix is None


def test_an_spc700_file_formats_its_operands() -> None:
    formatted = A816Formatter().format_text(".alloc d at 0x0400 {\n    mov   a,#0x12\n}\n", "d.spc.s")
    assert "    mov a, #0x12\n" in formatted


def _completions(tmp_path: Path, name: str, prefix: str) -> set[str]:
    server = A816LanguageServer()
    uri = _open(server, _written(tmp_path / name, f"    {prefix}\n"))
    position = Position(line=0, character=4 + len(prefix))
    params = CompletionParams(text_document=TextDocumentIdentifier(uri=uri), position=position)
    return {item.label for item in server._handle_completions(params).items}


def _written(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_completions_offer_spc700_mnemonics_and_registers(tmp_path: Path) -> None:
    assert {"pcall", "psw"} <= _completions(tmp_path, "d.spc.s", "p")


def test_completions_leave_out_65c816_mnemonics_in_spc700_code(tmp_path: Path) -> None:
    assert "pha" not in _completions(tmp_path, "d.spc.s", "p")


def test_completions_leave_out_spc700_mnemonics_in_65c816_code(tmp_path: Path) -> None:
    assert "pcall" not in _completions(tmp_path, "m.s", "p")


def test_hover_lists_the_operand_forms(project: Path) -> None:
    server = A816LanguageServer()
    uri = _open(server, project / "driver.spc.s")
    hover = server._handle_hover(HoverParams(text_document=TextDocumentIdentifier(uri=uri), position=Position(5, 5)))
    assert hover is not None
    assert "`mov1 c, mem.bit`" in str(hover.contents.value)  # type: ignore[union-attr]


def test_signature_help_lists_the_operand_forms(project: Path) -> None:
    server = A816LanguageServer()
    uri = _open(server, project / "driver.spc.s")
    params = SignatureHelpParams(text_document=TextDocumentIdentifier(uri=uri), position=Position(line=5, character=9))
    help_ = server._handle_signature_help(params)
    assert help_ is not None
    assert "mov1 mem.bit, c" in [signature.label for signature in help_.signatures]


def test_a_mnemonic_cannot_be_renamed(project: Path) -> None:
    server = A816LanguageServer()
    uri = _open(server, project / "driver.spc.s")
    params = PrepareRenameParams(text_document=TextDocumentIdentifier(uri=uri), position=Position(line=5, character=5))
    assert server._handle_prepare_rename(params) is None


def test_an_spc700_instruction_is_an_opcode_token(project: Path) -> None:
    server = A816LanguageServer()
    uri = _open(server, project / "driver.spc.s")
    tokens = server._extract_semantic_tokens_from_ast(server.documents[uri])
    assert {"line": 3, "type": 0} in [{"line": t["line"], "type": t["type"]} for t in tokens]


def test_the_embed_directive_goes_to_the_program(project: Path) -> None:
    server = A816LanguageServer()
    uri = _open(server, project / "main.s")
    (location,) = server._handle_definition(_at(uri, 2, 13)) or []
    assert location.uri.endswith("/driver.spc.s")


def test_a_program_label_goes_to_its_definition(project: Path) -> None:
    server = A816LanguageServer()
    uri = _open(server, project / "main.s")
    (location,) = server._handle_definition(_at(uri, 6, 17)) or []
    assert (location.uri.rsplit("/", 1)[-1], location.range.start.line) == ("driver.spc.s", 2)


def test_the_program_base_goes_to_the_embed(project: Path) -> None:
    server = A816LanguageServer()
    uri = _open(server, project / "main.s")
    (location,) = server._handle_definition(_at(uri, 5, 20)) or []
    assert (location.uri, location.range.start.line) == (uri, 2)


def test_a_missing_program_has_no_definition(project: Path) -> None:
    (project / "driver.spc.s").unlink()
    server = A816LanguageServer()
    uri = _open(server, project / "main.s")
    assert server._handle_definition(_at(uri, 6, 17)) is None


def test_the_workspace_follows_an_embed(project: Path) -> None:
    index = WorkspaceIndex(project)
    includes = index._extract_includes(project / "main.s", _MAIN)
    assert includes == [project / "driver.spc.s"]
