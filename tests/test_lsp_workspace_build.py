"""Behaviour pins for the deferred, parallel workspace index build.

The regression these guard against: `didOpen` used to walk and parse the
whole project inline, so the definition and semantic-token requests an
editor fires immediately after attach were answered from an empty server
and the buffer stayed uncoloured until the next keystroke.
"""

from __future__ import annotations

from pathlib import Path

from lsprotocol.types import (
    DidOpenTextDocumentParams,
    DocumentSymbolParams,
    Position,
    SemanticTokensParams,
    TextDocumentIdentifier,
    TextDocumentItem,
)

from a816.lsp.server import A816LanguageServer
from a816.lsp.workspace import WorkspaceIndex, _index_workers

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "lsp_project"
MAIN = FIXTURE_ROOT / "src" / "main.s"


def _index_fingerprint(index: WorkspaceIndex) -> tuple[list[str], ...]:
    return (
        sorted(index.documents),
        sorted(index.labels),
        sorted(index.symbols),
        sorted(index.macros),
        sorted(index.pools),
        sorted(index.allocs),
    )


def _server_with_unbuilt_index() -> tuple[A816LanguageServer, WorkspaceIndex]:
    server = A816LanguageServer()
    index = WorkspaceIndex(FIXTURE_ROOT)
    server.workspace_index = index
    server._ensure_workspace_index = lambda: server.workspace_index  # type: ignore[method-assign]
    return server, index


def _did_open_params(path: Path) -> DidOpenTextDocumentParams:
    return DidOpenTextDocumentParams(
        text_document=TextDocumentItem(
            uri=path.as_uri(), language_id="a816", version=1, text=path.read_text(encoding="utf-8")
        )
    )


def test_prepare_resolves_search_paths_without_indexing() -> None:
    """`prepare` is the cheap half of `rebuild`: config, no walk."""
    index = WorkspaceIndex(FIXTURE_ROOT)
    index.prepare()

    assert index.entrypoint == MAIN.resolve()
    assert (FIXTURE_ROOT / "src").resolve() in index.include_paths
    assert (FIXTURE_ROOT / "modules").resolve() in index.module_paths
    assert index.prepared is True
    # The expensive part is precisely what must not have happened.
    assert index.built is False
    assert index.documents == {}


def test_parallel_parse_matches_single_threaded_walk() -> None:
    """Fanning the parse across threads must not perturb the index."""
    threaded = WorkspaceIndex(FIXTURE_ROOT)
    threaded.rebuild()

    serial = WorkspaceIndex(FIXTURE_ROOT)
    serial._parse_discovered = lambda discovered: [  # type: ignore[method-assign]
        serial._parse_one(item) for item in discovered
    ]
    serial.rebuild()

    assert threaded.documents  # the fixture actually indexes something
    assert _index_fingerprint(threaded) == _index_fingerprint(serial)


def test_index_workers_stay_within_cap() -> None:
    assert 1 <= _index_workers() <= 8


def test_single_document_walk_skips_the_thread_pool() -> None:
    """One file in, one document out, with no pool spun up for nothing."""
    index = WorkspaceIndex(FIXTURE_ROOT)
    index.prepare()
    constants = (FIXTURE_ROOT / "src" / "constants.s").resolve()

    documents = index._parse_discovered([(constants, constants.read_text(encoding="utf-8"))])

    assert [doc.uri for doc in documents] == [constants.as_uri()]


async def test_did_open_answers_before_the_workspace_is_indexed() -> None:
    """The pin: the opened buffer resolves its own symbols straight away,
    while the project walk is still only scheduled."""
    server, index = _server_with_unbuilt_index()

    await server._handle_did_open(_did_open_params(MAIN))

    assert server._workspace_build_task is not None
    assert not server._workspace_build_task.done()

    # The buffer's own symbols are the ones an editor asks for first.
    symbols = server._handle_document_symbols(
        DocumentSymbolParams(text_document=TextDocumentIdentifier(uri=MAIN.as_uri()))
    )
    assert [symbol.name for symbol in symbols if symbol.name == "main"] == ["main"]

    tokens = server._handle_semantic_tokens_full(
        SemanticTokensParams(text_document=TextDocumentIdentifier(uri=MAIN.as_uri()))
    )
    assert tokens is not None
    assert tokens.data, "semantic tokens must not wait on the project walk"

    await server._workspace_build_task


async def test_background_build_swaps_in_a_complete_index() -> None:
    """The replacement is published whole, and the client is told to
    re-request the tokens it asked for while the index was empty."""
    server, index = _server_with_unbuilt_index()
    refreshes: list[object] = []

    def record_refresh(arg: object, callback: object = None) -> None:
        refreshes.append(arg)

    server.server.workspace_semantic_tokens_refresh = record_refresh  # type: ignore[assignment]

    await server._handle_did_open(_did_open_params(MAIN))
    assert server._workspace_build_task is not None
    await server._workspace_build_task

    rebuilt = server.workspace_index
    assert rebuilt is not None
    assert rebuilt is not index, "index must be swapped, never mutated in place"
    assert rebuilt.built is True
    assert "main" in rebuilt.labels
    assert refreshes == [None]
    assert server._workspace_build_task is None


async def test_build_is_scheduled_once() -> None:
    server, index = _server_with_unbuilt_index()

    await server._handle_did_open(_did_open_params(MAIN))
    first = server._workspace_build_task
    assert first is not None

    server._schedule_workspace_build(index)
    assert server._workspace_build_task is first

    await first


async def test_build_failure_leaves_the_server_usable() -> None:
    """A broken walk must not wedge the session or the swap slot."""
    server, index = _server_with_unbuilt_index()

    def explode(_: WorkspaceIndex) -> WorkspaceIndex:
        raise RuntimeError("index build blew up")

    server._rebuilt_index = explode  # type: ignore[assignment]

    await server._build_workspace(index)

    assert server._workspace_build_task is None
    assert server.workspace_index is index


def test_schedule_without_a_running_loop_builds_inline() -> None:
    """Unit tests and non-async drivers still get a usable index."""
    server, index = _server_with_unbuilt_index()

    server._schedule_workspace_build(index)

    assert index.built is True
    assert server._workspace_build_task is None
    assert "main" in index.labels


def test_ensure_does_not_walk_an_already_prepared_index() -> None:
    """`_ensure_workspace_index` runs on every request. Re-preparing there
    would clear the live index each time, so the flag has to hold."""
    server = A816LanguageServer()
    index = WorkspaceIndex(Path.cwd())
    index.prepared = True
    index.built = True
    index.labels["sentinel"] = (Position(line=0, character=0), "file:///sentinel")
    server.workspace_index = index

    assert server._ensure_workspace_index() is index
    assert "sentinel" in index.labels


def test_rebuild_marks_the_index_prepared_and_built() -> None:
    index = WorkspaceIndex(FIXTURE_ROOT)
    index.rebuild()

    assert index.prepared is True
    assert index.built is True


def test_refresh_survives_a_client_without_the_request() -> None:
    """Refresh is best effort. A client that cannot do it must not take
    the build down with it."""
    server, _ = _server_with_unbuilt_index()

    def unsupported(arg: object, callback: object = None) -> None:
        raise AttributeError("client has no semantic token refresh")

    server.server.workspace_semantic_tokens_refresh = unsupported  # type: ignore[assignment]

    server._refresh_semantic_tokens()
