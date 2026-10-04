"""Coverage for `a816.config` — `a816.toml` discovery and parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.config import A816Config, discover_a816_config, find_a816_toml, load_a816_toml
from a816.exceptions import A816ConfigError


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
        'entrypoint = "src/main.s"\ninclude-paths = ["src/include"]\nmodule-paths  = ["src/modules"]\n',
        encoding="utf-8",
    )
    loaded = load_a816_toml(cfg)
    assert loaded is not None
    assert loaded.root == tmp_path
    assert loaded.entrypoint == (tmp_path / "src" / "main.s").resolve()
    assert loaded.include_paths == [(tmp_path / "src" / "include").resolve()]
    assert loaded.module_paths == [(tmp_path / "src" / "modules").resolve()]


def test_discover_combines_find_and_load(tmp_path: Path) -> None:
    nested = tmp_path / "src" / "deeper"
    nested.mkdir(parents=True)
    (tmp_path / "a816.toml").write_text('entrypoint = "main.s"\n', encoding="utf-8")
    cfg = discover_a816_config(nested)
    assert cfg is not None
    assert cfg.entrypoint == (tmp_path / "main.s").resolve()


def _load(tmp_path: Path, body: str) -> A816Config:
    cfg = tmp_path / "a816.toml"
    cfg.write_text(body, encoding="utf-8")
    loaded = load_a816_toml(cfg)
    assert loaded is not None
    return loaded


def _config_error_code(tmp_path: Path, body: str) -> str:
    cfg = tmp_path / "a816.toml"
    cfg.write_text(body, encoding="utf-8")
    with pytest.raises(A816ConfigError) as info:
        load_a816_toml(cfg)
    return info.value.code.code


_SRAM_MAP = "[map.3]\nbank_range = [0x70, 0x7d]\naddr_range = [0x0000, 0x7fff]\nmask = 0x8000\nwritable = true\n"


def test_bus_map_defaults_to_empty(tmp_path: Path) -> None:
    assert _load(tmp_path, 'entrypoint = "main.s"\n').bus_map == []


def test_map_entry_parses_every_key(tmp_path: Path) -> None:
    body = (
        "[map.1]\nbank_range = [0xc0, 0xfd]\naddr_range = [0x0000, 0xffff]\n"
        "mask = 0x10000\nmirror_bank_range = [0x40, 0x7d]\n"
    )
    shape = _load(tmp_path, body).bus_map[0].shape()
    assert shape == ("1", (0xC0, 0xFD), (0x0000, 0xFFFF), 0x10000, False, (0x40, 0x7D))


def test_map_entry_writable(tmp_path: Path) -> None:
    assert _load(tmp_path, _SRAM_MAP).bus_map[0].writeable is True


def test_map_integer_identifier_matches_directive_spelling(tmp_path: Path) -> None:
    body = _SRAM_MAP.replace("[map.3]", "[map.0x42]")
    assert _load(tmp_path, body).bus_map[0].identifier == "66"


def test_mapper_lorom_expands_to_default_bus(tmp_path: Path) -> None:
    shapes = [m.shape() for m in _load(tmp_path, 'mapper = "lorom"\n').bus_map]
    assert shapes == [
        ("1", (0x00, 0x6F), (0x8000, 0xFFFF), 0x8000, False, (0x80, 0xCF)),
        ("2", (0x7E, 0x7F), (0x0000, 0xFFFF), 0x10000, True, None),
    ]


def test_mapper_hirom_expands_to_default_bus(tmp_path: Path) -> None:
    shapes = [m.shape() for m in _load(tmp_path, 'mapper = "hirom"\n').bus_map]
    assert shapes == [
        ("1", (0x40, 0x7F), (0x0000, 0xFFFF), 0x10000, False, (0xC0, 0xFF)),
        ("2", (0x7E, 0x7F), (0x0000, 0xFFFF), 0x10000, True, None),
    ]


def test_mapper_recorded(tmp_path: Path) -> None:
    assert _load(tmp_path, 'mapper = "hirom"\n').mapper == "hirom"


def test_mapper_and_map_are_mutually_exclusive(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, 'mapper = "lorom"\n' + _SRAM_MAP) == "E0507"


def test_unknown_mapper_is_rejected(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, 'mapper = "exhirom"\n') == "E0504"


def test_non_string_mapper_is_rejected(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, "mapper = 1\n") == "E0504"


def test_map_unknown_key_is_rejected(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, _SRAM_MAP + "bogus = 1\n") == "E0505"


def test_map_missing_key_is_rejected(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, _SRAM_MAP.replace("mask = 0x8000\n", "")) == "E0505"


def test_map_must_be_array_of_tables(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, "map = 3\n") == "E0505"


def test_map_range_needs_two_integers(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, _SRAM_MAP.replace("[0x70, 0x7d]", "[0x70]")) == "E0506"


def test_map_range_rejects_booleans(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, _SRAM_MAP.replace("[0x70, 0x7d]", "[true, 0x7d]")) == "E0506"


def test_map_mask_must_be_integer(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, _SRAM_MAP.replace("mask = 0x8000", "mask = '0x8000'")) == "E0506"


def test_map_writable_must_be_boolean(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, _SRAM_MAP.replace("writable = true", "writable = 1")) == "E0506"


def test_map_identifier_must_be_an_integer(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, _SRAM_MAP.replace("[map.3]", "[map.sram]")) == "E0506"


def test_repeated_map_table_is_invalid_toml(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, _SRAM_MAP + _SRAM_MAP) == "E0501"


def test_map_keys_spelling_one_number_are_rejected(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, _SRAM_MAP + _SRAM_MAP.replace("[map.3]", "[map.0x3]")) == "E0505"


def test_invalid_toml_is_reported_not_ignored(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, "entrypoint = \n") == "E0501"


def test_config_error_names_the_file(tmp_path: Path) -> None:
    cfg = tmp_path / "a816.toml"
    cfg.write_text('mapper = "exhirom"\n', encoding="utf-8")
    with pytest.raises(A816ConfigError) as info:
        load_a816_toml(cfg)
    assert str(cfg) in info.value.format()
