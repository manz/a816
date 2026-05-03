"""Tests for `.import` propagating .incbin auto-symbols."""

from pathlib import Path

import pytest

from a816.parse.codegen import _extract_public_symbols_from_source


def test_incbin_symbols_propagate_via_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Public .incbin auto-symbols (`<base>` and `<base>__size`) appear
    in the extracted public symbol list so an `.import`-er can resolve
    them as bare names without an extra `.extern`."""
    monkeypatch.chdir(tmp_path)
    Path("blob.dat").write_bytes(b"\x00\x01\x02\x03")
    src = tmp_path / "mod.s"
    src.write_text('.incbin "blob.dat"\n')
    syms = _extract_public_symbols_from_source(src)
    assert "blob_dat" in syms, syms
    assert "blob_dat__size" in syms, syms


def test_incbin_underscore_path_stays_private(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`.incbin "_priv.dat"` must NOT propagate — auto-symbol
    `_priv_dat` is underscore-prefixed → module-private."""
    monkeypatch.chdir(tmp_path)
    Path("_priv.dat").write_bytes(b"\x00")
    src = tmp_path / "mod.s"
    src.write_text('.incbin "_priv.dat"\n')
    syms = _extract_public_symbols_from_source(src)
    assert "_priv_dat" not in syms
    assert "_priv_dat__size" not in syms


def test_incbin_alongside_label(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    Path("data.bin").write_bytes(b"x")
    src = tmp_path / "mod.s"
    src.write_text('foo:\n    rts\n.incbin "data.bin"\n')
    syms = _extract_public_symbols_from_source(src)
    assert "foo" in syms
    assert "data_bin" in syms


def test_scope_members_emit_dotted_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`.scope foo { bar: ... }` exports `foo.bar` so importers don't
    need to re-declare each scoped label as `.extern`."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "mod.s"
    src.write_text(".scope render_allocator {\ninit: rts\nincrement: rts\n}\n")
    syms = _extract_public_symbols_from_source(src)
    assert "render_allocator.init" in syms
    assert "render_allocator.increment" in syms
    # Bare names should not leak — only the qualified form is public.
    assert "init" not in syms
    assert "increment" not in syms


def test_scope_with_underscore_label_stays_private(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Underscore-prefix on the label still hides it; `foo._helper` is
    still private even though the scope `foo` is public."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "mod.s"
    src.write_text(".scope foo {\npublic: rts\n_helper: rts\n}\n")
    syms = _extract_public_symbols_from_source(src)
    assert "foo.public" in syms
    assert "foo._helper" not in syms
