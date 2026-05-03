"""Tests for `.import` propagating .incbin auto-symbols."""
import os
from pathlib import Path

from a816.parse.codegen import _extract_public_symbols_from_source


def test_incbin_symbols_propagate_via_import(tmp_path, monkeypatch):
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


def test_incbin_underscore_path_stays_private(tmp_path, monkeypatch):
    """`.incbin "_priv.dat"` must NOT propagate — auto-symbol
    `_priv_dat` is underscore-prefixed → module-private."""
    monkeypatch.chdir(tmp_path)
    Path("_priv.dat").write_bytes(b"\x00")
    src = tmp_path / "mod.s"
    src.write_text('.incbin "_priv.dat"\n')
    syms = _extract_public_symbols_from_source(src)
    assert "_priv_dat" not in syms
    assert "_priv_dat__size" not in syms


def test_incbin_alongside_label(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data.bin").write_bytes(b"x")
    src = tmp_path / "mod.s"
    src.write_text('foo:\n    rts\n.incbin "data.bin"\n')
    syms = _extract_public_symbols_from_source(src)
    assert "foo" in syms
    assert "data_bin" in syms
