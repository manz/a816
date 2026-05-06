"""Coverage for `a816.config` — `a816.toml` discovery and parsing."""

from __future__ import annotations

from pathlib import Path

from a816.config import discover_a816_config, find_a816_toml, load_a816_toml


def test_find_walks_up(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    nested = root / "src" / "deeper"
    nested.mkdir(parents=True)
    (root / "a816.toml").write_text('entrypoint = "main.s"\n', encoding="utf-8")
    found = find_a816_toml(nested)
    assert found is not None
    assert found.parent == root


def test_find_returns_none_when_missing(tmp_path: Path) -> None:
    assert find_a816_toml(tmp_path) is None


def test_load_resolves_paths(tmp_path: Path) -> None:
    cfg = tmp_path / "a816.toml"
    cfg.write_text(
        'entrypoint = "src/main.s"\n'
        'include-paths = ["src/include"]\n'
        'module-paths  = ["src/modules"]\n'
        'prelude       = "src/prelude.s"\n',
        encoding="utf-8",
    )
    loaded = load_a816_toml(cfg)
    assert loaded is not None
    assert loaded.root == tmp_path
    assert loaded.entrypoint == (tmp_path / "src" / "main.s").resolve()
    assert loaded.include_paths == [(tmp_path / "src" / "include").resolve()]
    assert loaded.module_paths == [(tmp_path / "src" / "modules").resolve()]
    assert loaded.prelude_file == (tmp_path / "src" / "prelude.s").resolve()


def test_load_returns_none_on_decode_error(tmp_path: Path) -> None:
    cfg = tmp_path / "a816.toml"
    cfg.write_text("not = valid = toml = at all", encoding="utf-8")
    assert load_a816_toml(cfg) is None


def test_discover_combines_find_and_load(tmp_path: Path) -> None:
    nested = tmp_path / "src" / "deeper"
    nested.mkdir(parents=True)
    (tmp_path / "a816.toml").write_text('entrypoint = "main.s"\n', encoding="utf-8")
    cfg = discover_a816_config(nested)
    assert cfg is not None
    assert cfg.entrypoint == (tmp_path / "main.s").resolve()
