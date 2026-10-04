"""`build_with_imports` honours `a816.toml` like `a816 build` does.

ff4 and dq6 build through the Python API from their `build.py`; before
this, `[map.N]`, `mapper`, paths and `[experimental]` only reached CLI
builds, so the same project built differently through the two entry points.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.config import A816Config, merge_build_settings
from a816.exceptions import A816ConfigError
from a816.module_builder import BuildResult, build_with_imports
from a816.object_file import BusMapping

_ROM = BusMapping("1", (0xC0, 0xFF), (0x0000, 0xFFFF), 0x1_0000)
_ROM_TOML = 'entrypoint = "main.s"\nrom_size = 0x400000\n[map.1]\naddress = "c0-ff:0000-ffff"\n'


def _build(root: Path, toml: str | None, **kwargs: object) -> BuildResult:
    if toml is not None:
        (root / "a816.toml").write_text(toml, encoding="utf-8")
    (root / "a.s").write_text(".alloc at 0xf00000 {\na_entry:\n    rts\n}\n", encoding="utf-8")
    (root / "main.s").write_text('.import "a"\n.alloc at 0xf10000 {\n    jsr.l a_entry\n}\n', encoding="utf-8")
    return build_with_imports(
        main_source=root / "main.s",
        output_file=root / "out.ips",
        output_dir=root / "obj",
        **kwargs,  # type: ignore[arg-type]
    )


def test_api_build_seeds_the_toml_bus_map(tmp_path: Path) -> None:
    result = _build(tmp_path, _ROM_TOML)
    assert result.exit_code == 0, result.diagnostics


def test_api_build_can_opt_out_of_the_toml(tmp_path: Path) -> None:
    result = _build(tmp_path, _ROM_TOML, use_a816_toml=False)
    assert result.exit_code != 0, "without the toml map, bank $F0 is unmapped"


def test_api_build_rejects_the_removed_mapper_key(tmp_path: Path) -> None:
    with pytest.raises(A816ConfigError):
        _build(tmp_path, 'mapper = "hirom"\nrom_size = 0x400000\n', mapping="low")


def _config(tmp_path: Path, **fields: object) -> A816Config:
    return A816Config(config_path=tmp_path / "a816.toml", **fields)  # type: ignore[arg-type]


def test_merge_without_config_passes_caller_values_through() -> None:
    settings = merge_build_settings(None, mapping="high", experimental=["track_register_size"])
    assert (settings.mapping, settings.bus_map, settings.experimental) == ("high", [], ["track_register_size"])


def test_merge_fills_unset_values_from_the_toml(tmp_path: Path) -> None:
    config = _config(tmp_path, include_paths=[tmp_path / "inc"], module_paths=[tmp_path / "mod"], bus_map=[_ROM])
    settings = merge_build_settings(config)
    assert (settings.include_paths, settings.module_paths, settings.bus_map) == (
        [tmp_path / "inc"],
        [tmp_path / "mod"],
        [_ROM],
    )


def test_merge_prefers_caller_paths_over_the_toml(tmp_path: Path) -> None:
    config = _config(tmp_path, include_paths=[tmp_path / "inc"])
    settings = merge_build_settings(config, include_paths=[tmp_path / "mine"])
    assert settings.include_paths == [tmp_path / "mine"]


def test_merge_unions_experimental_flags(tmp_path: Path) -> None:
    config = _config(tmp_path, experimental={"track_register_size": True, "off_flag": False})
    settings = merge_build_settings(config, experimental=["cli_flag"])
    assert settings.experimental == ["cli_flag", "track_register_size"]
