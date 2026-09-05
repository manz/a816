"""Behaviour pins for the deferred, parallel workspace index build.

The regression these guard against: `didOpen` used to walk and parse the
whole project inline, so the definition and semantic-token requests an
editor fires immediately after attach were answered from an empty server
and the buffer stayed uncoloured until the next keystroke.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from lsprotocol.types import (
    DidOpenTextDocumentParams,
    DocumentSymbolParams,
    Position,
    SemanticTokensParams,
    TextDocumentIdentifier,
    TextDocumentItem,
)

import a816.lsp.server as server_module
from a816.lsp.document import A816Document
from a816.lsp.server import A816LanguageServer
from a816.lsp.workspace import IndexProgress, ProgressCallback, WorkspaceIndex, _index_workers

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

    def parse_serially(
        discovered: list[tuple[Path, str]], progress: ProgressCallback | None = None
    ) -> list[A816Document]:
        return [serial._parse_one(item) for item in discovered]

    serial._parse_discovered = parse_serially  # type: ignore[method-assign]
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
    server, _ = _server_with_unbuilt_index()

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


class _RecordingProgress:
    """Stands in for pygls' work-done progress channel."""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.begun: list[tuple[str, object]] = []
        self.reported: list[tuple[str, object]] = []
        self.ended: list[tuple[str, object]] = []

    async def create_async(self, token: str) -> None:
        self.created.append(token)

    def begin(self, token: str, value: object) -> None:
        self.begun.append((token, value))

    def report(self, token: str, value: object) -> None:
        self.reported.append((token, value))

    def end(self, token: str, value: object) -> None:
        self.ended.append((token, value))


def _server_reporting_progress() -> tuple[A816LanguageServer, WorkspaceIndex, _RecordingProgress]:
    server, index = _server_with_unbuilt_index()
    channel = _RecordingProgress()
    server._progress_channel = lambda: channel  # type: ignore[method-assign]
    server._client_supports_progress = lambda: True  # type: ignore[method-assign]
    return server, index, channel


def test_rebuild_reports_discovery_then_parse() -> None:
    """The callback has to see both phases, and parsing has to finish."""
    events: list[IndexProgress] = []
    WorkspaceIndex(FIXTURE_ROOT).rebuild(events.append)

    phases = [event.phase for event in events]
    assert "discover" in phases
    assert "parse" in phases

    discovery = [event for event in events if event.phase == "discover"]
    assert [event.done for event in discovery] == list(range(1, len(discovery) + 1))
    assert all(event.total == 0 for event in discovery), "discovery cannot know its size up front"

    parses = [event for event in events if event.phase == "parse"]
    assert parses[-1].done == parses[-1].total == len(discovery)
    assert all(event.detail.endswith(".s") for event in parses)


def test_rebuild_without_a_callback_still_indexes() -> None:
    index = WorkspaceIndex(FIXTURE_ROOT)
    index.rebuild()
    assert "main" in index.labels


async def test_build_announces_progress_to_the_client() -> None:
    server, _, channel = _server_reporting_progress()

    await server._handle_did_open(_did_open_params(MAIN))
    assert server._workspace_build_task is not None
    await server._workspace_build_task

    assert len(channel.created) == 1
    token = channel.created[0]
    assert token.startswith("a816-index-")
    assert [entry[0] for entry in channel.begun] == [token]
    assert channel.begun[0][1].title == "Indexing a816 workspace"  # type: ignore[attr-defined]
    assert channel.ended and channel.ended[0][0] == token
    assert "Indexed" in channel.ended[0][1].message  # type: ignore[attr-defined]


async def test_progress_ends_even_when_the_build_fails() -> None:
    """A wedged progress notification is a spinner that never stops."""
    server, index, channel = _server_reporting_progress()

    def explode(_: WorkspaceIndex, progress: ProgressCallback | None = None) -> WorkspaceIndex:
        raise RuntimeError("index build blew up")

    server._rebuilt_index = explode  # type: ignore[assignment]

    await server._build_workspace(index)

    assert channel.ended, "a failed build must still close its progress token"
    assert "failed" in channel.ended[0][1].message.lower()  # type: ignore[attr-defined]


async def test_progress_is_silent_for_a_client_without_the_capability() -> None:
    server, index = _server_with_unbuilt_index()
    channel = _RecordingProgress()
    server._progress_channel = lambda: channel  # type: ignore[method-assign]
    server._client_supports_progress = lambda: False  # type: ignore[method-assign]

    await server._build_workspace(index)

    assert channel.created == []
    assert channel.begun == []
    assert channel.ended == []
    assert server.workspace_index is not None
    assert "main" in server.workspace_index.labels


def test_reporter_emits_one_notification_per_percent() -> None:
    """200 files must not mean 200 notifications."""
    server, _, channel = _server_reporting_progress()
    loop = asyncio.new_event_loop()
    try:
        reporter = server._progress_reporter("token", loop)
        assert reporter is not None
        for done in range(1, 201):
            reporter(IndexProgress(phase="parse", done=done, total=200, detail="f.s"))
        loop.call_soon(loop.stop)
        loop.run_forever()
    finally:
        loop.close()

    # 0 through 100 inclusive, once each, rather than 200 notifications.
    assert [value.percentage for _, value in channel.reported] == list(range(101))  # type: ignore[attr-defined]


def test_reporter_is_absent_without_a_token() -> None:
    server, _, _ = _server_reporting_progress()
    loop = asyncio.new_event_loop()
    try:
        assert server._progress_reporter(None, loop) is None
    finally:
        loop.close()


class _BrokenProgress(_RecordingProgress):
    """A client that advertised progress and then refuses every call."""

    async def create_async(self, token: str) -> None:
        raise RuntimeError("client went away")

    def report(self, token: str, value: object) -> None:
        raise RuntimeError("client went away")

    def end(self, token: str, value: object) -> None:
        raise RuntimeError("client went away")


async def test_build_survives_a_client_that_refuses_the_token() -> None:
    """Progress is decoration. Losing it must not lose the index."""
    server, index = _server_with_unbuilt_index()
    server._progress_channel = _BrokenProgress  # type: ignore[method-assign]
    server._client_supports_progress = lambda: True  # type: ignore[method-assign]

    await server._build_workspace(index)

    assert server.workspace_index is not None
    assert "main" in server.workspace_index.labels


def test_progress_calls_swallow_a_broken_channel() -> None:
    server, _ = _server_with_unbuilt_index()
    broken = _BrokenProgress()
    server._progress_channel = lambda: broken  # type: ignore[method-assign]

    server._report_index_progress("token", IndexProgress("parse", 1, 2, "f.s"), 50)
    server._end_index_progress("token", "done")


def test_progress_capability_is_false_before_initialize() -> None:
    """`client_capabilities` raises until a client handshakes."""
    assert A816LanguageServer()._client_supports_progress() is False


def test_progress_channel_defaults_to_the_pygls_one() -> None:
    server = A816LanguageServer()
    assert server._progress_channel() is server.server.work_done_progress


async def test_requests_wait_for_the_open_parse() -> None:
    """The pin: a request racing didOpen must see the parsed document,
    not an empty table."""
    server, _ = _server_with_unbuilt_index()
    opening = asyncio.create_task(server._handle_did_open(_did_open_params(MAIN)))
    await asyncio.sleep(0)  # let didOpen register its pending parse

    await server._document_ready(MAIN.as_uri())
    symbols = server._handle_document_symbols(
        DocumentSymbolParams(text_document=TextDocumentIdentifier(uri=MAIN.as_uri()))
    )
    assert [s.name for s in symbols if s.name == "main"] == ["main"]

    await opening
    assert server._workspace_build_task is not None
    await server._workspace_build_task


async def test_document_ready_ignores_unknown_documents() -> None:
    """Nothing in flight means nothing to wait for."""
    server, _ = _server_with_unbuilt_index()
    await server._document_ready("file:///never/opened.s")


async def test_a_failed_parse_still_releases_waiters() -> None:
    """A document that fails to parse must unblock its requests rather
    than hang them forever."""
    server, _ = _server_with_unbuilt_index()

    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("parser blew up")

    with patch.object(server_module, "A816Document", explode), pytest.raises(RuntimeError):
        await server._handle_did_open(_did_open_params(MAIN))

    assert server._pending_parses == {}
    await asyncio.wait_for(server._document_ready(MAIN.as_uri()), timeout=1)

    if server._workspace_build_task is not None:
        await server._workspace_build_task


async def test_pending_parse_is_cleared_after_open() -> None:
    server, _ = _server_with_unbuilt_index()
    await server._handle_did_open(_did_open_params(MAIN))

    assert server._pending_parses == {}
    assert server._workspace_build_task is not None
    await server._workspace_build_task
