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


_SRAM_MAP = '[map.3]\naddress = "70-7d,f0-ff:0000-7fff"\nmask = 0x8000\nwritable = true\n'
_ROM_MAP = 'rom_size = 0x200000\n[map.1]\naddress = "00-7d,80-ff:8000-ffff"\nmask = 0x8000\n'


def test_bus_map_defaults_to_empty(tmp_path: Path) -> None:
    assert _load(tmp_path, 'entrypoint = "main.s"\n').bus_map == []


def test_map_entry_parses_every_key(tmp_path: Path) -> None:
    body = (
        'rom_size = 0x800000\n[map.1]\naddress = "00-3f:8000-ffff"\nmask = 0x8000\nbase = 0x400000\nwritable = false\n'
    )
    shape = _load(tmp_path, body).bus_map[0].shape()
    assert shape == ("1", (0, 0), (0, 0), 0x8000, False, None, "00-3f:8000-ffff", 0x400000, 0x800000)


def test_map_mask_and_base_default_to_zero(tmp_path: Path) -> None:
    region = _load(tmp_path, 'rom_size = 0x400000\n[map.1]\naddress = "c0-ff:0000-ffff"\n').bus_map[0]
    assert (region.mask, region.base) == (0, 0)


def test_rom_region_requires_rom_size(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, _ROM_MAP.replace("rom_size = 0x200000\n", "")) == "E0506"


def test_ram_only_regions_need_no_rom_size(tmp_path: Path) -> None:
    assert _load(tmp_path, _SRAM_MAP).bus_map[0].rom_size == 0


def test_rom_size_must_be_positive(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, _ROM_MAP.replace("0x200000", "0")) == "E0506"


def test_rom_size_must_be_an_integer(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, _ROM_MAP.replace("0x200000", '"2MB"')) == "E0506"


def test_legacy_range_keys_are_rejected(tmp_path: Path) -> None:
    message = _config_error_message(tmp_path, _SRAM_MAP + "bank_range = [0x70, 0x7d]\n")
    assert message == "[map.3]: unknown keys bank_range"


def test_map_integer_identifier_matches_directive_spelling(tmp_path: Path) -> None:
    body = _SRAM_MAP.replace("[map.3]", "[map.0x42]")
    assert _load(tmp_path, body).bus_map[0].identifier == "66"


@pytest.mark.parametrize(("mapper", "board"), [("lorom", "SHVC-1A0N-30"), ("hirom", "SHVC-1J0N-20")])
def test_removed_mapper_names_the_board_to_write(tmp_path: Path, mapper: str, board: str) -> None:
    message = _config_error_message(tmp_path, f'mapper = "{mapper}"\n')
    assert message == f"`mapper` is no longer supported: write `board = {board!r}` and `rom_size`"


def test_unknown_mapper_is_rejected(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, 'mapper = "exhirom"\n') == "E0504"


def test_non_string_mapper_is_rejected(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, "mapper = 1\n") == "E0504"


def test_map_unknown_key_is_rejected(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, _SRAM_MAP + "bogus = 1\n") == "E0505"


def test_map_missing_key_is_rejected(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, _SRAM_MAP.replace('address = "70-7d,f0-ff:0000-7fff"\n', "")) == "E0505"


def test_map_must_be_a_table_of_tables(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, "map = 3\n") == "E0505"


def _config_error_message(tmp_path: Path, body: str) -> str:
    cfg = tmp_path / "a816.toml"
    cfg.write_text(body, encoding="utf-8")
    with pytest.raises(A816ConfigError) as info:
        load_a816_toml(cfg)
    return info.value.message


def test_map_missing_key_message_names_only_missing_keys(tmp_path: Path) -> None:
    message = _config_error_message(tmp_path, _SRAM_MAP.replace('address = "70-7d,f0-ff:0000-7fff"\n', ""))
    assert message == "[map.3]: missing keys address"


def test_map_unknown_key_message_names_only_unknown_keys(tmp_path: Path) -> None:
    message = _config_error_message(tmp_path, _SRAM_MAP + "bogus = 1\n")
    assert message == "[map.3]: unknown keys bogus"


def test_experimental_flag_must_be_boolean(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, '[experimental]\ntrack_register_size = "yes"\n') == "E0503"


def test_experimental_must_be_a_table(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, "experimental = 1\n") == "E0503"


def test_experimental_flags_are_loaded(tmp_path: Path) -> None:
    body = "[experimental]\ntrack_register_size = true\nother = false\n"
    assert _load(tmp_path, body).experimental == {"track_register_size": True, "other": False}


def test_map_address_must_be_a_string(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, _SRAM_MAP.replace('"70-7d,f0-ff:0000-7fff"', "0x70")) == "E0506"


def test_map_address_needs_banks_and_window(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, _SRAM_MAP.replace('"70-7d,f0-ff:0000-7fff"', '"70-7d"')) == "E0506"


def test_map_address_rejects_reversed_ranges(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, _SRAM_MAP.replace('"70-7d,f0-ff:0000-7fff"', '"7d-70:0000-7fff"')) == "E0506"


def test_map_address_rejects_non_hex(tmp_path: Path) -> None:
    assert _config_error_code(tmp_path, _SRAM_MAP.replace('"70-7d,f0-ff:0000-7fff"', '"zz:0000-7fff"')) == "E0506"


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
