"""`[defines]` in `a816.toml` declares the names a build passes with `-D`.

Every `-D` flag tripped W0002 (dq6 7, cacheguard 5, Bahamut Lagoon 1), and
the only way out was a `; noqa: W0002` per `.if`. Declared once with a
default, the name is defined for `a816 check` and bound by every build.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.cli import _dispatch_subcommand
from a816.config import A816Config, load_a816_toml, merge_build_settings
from a816.exceptions import A816ConfigError
from a816.fluff import lint_text
from a816.module_builder import build_with_imports

MAIN = '"""M."""\n.alloc code at 0x008000 {\n    .db DEBUG, LANG_FR\n}\n'


def _project(root: Path, defines: str) -> Path:
    (root / "a816.toml").write_text(f'entrypoint = "main.s"\n[defines]\n{defines}', encoding="utf-8")
    (root / "main.s").write_text(MAIN, encoding="utf-8")
    return root / "main.s"


def _config(root: Path, defines: str) -> A816Config:
    _project(root, defines)
    config = load_a816_toml(root / "a816.toml")
    assert config is not None
    return config


def test_defines_load_with_their_defaults(tmp_path: Path) -> None:
    config = _config(tmp_path, 'DEBUG = 0\nLANG = "fr"\n')

    assert config.defines == {"DEBUG": 0, "LANG": "fr"}


def test_a_project_without_defines_declares_none(tmp_path: Path) -> None:
    (tmp_path / "a816.toml").write_text('entrypoint = "main.s"\n', encoding="utf-8")
    config = load_a816_toml(tmp_path / "a816.toml")

    assert config is not None and config.defines == {}


@pytest.mark.parametrize("value", ["true", "1.5", "[1]", "{ a = 1 }"])
def test_a_default_of_another_type_is_rejected(tmp_path: Path, value: str) -> None:
    with pytest.raises(A816ConfigError) as error:
        _config(tmp_path, f"DEBUG = {value}\n")

    assert error.value.code.code == "E0510"


def test_a_name_no_source_can_spell_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(A816ConfigError) as error:
        _config(tmp_path, '"no-dash" = 1\n')

    assert error.value.code.code == "E0510"


def test_defines_must_be_a_table(tmp_path: Path) -> None:
    (tmp_path / "a816.toml").write_text("defines = 1\n", encoding="utf-8")

    with pytest.raises(A816ConfigError) as error:
        load_a816_toml(tmp_path / "a816.toml")

    assert error.value.code.code == "E0510"


def test_a_caller_symbol_wins_over_the_default(tmp_path: Path) -> None:
    config = _config(tmp_path, "DEBUG = 0\nLANG_FR = 0\n")

    settings = merge_build_settings(config, symbols={"DEBUG": 1})

    assert settings.symbols == {"DEBUG": 1, "LANG_FR": 0}


def test_without_a_config_the_caller_symbols_pass_through() -> None:
    assert merge_build_settings(None, symbols={"DEBUG": 1}).symbols == {"DEBUG": 1}


def _api_rom(root: Path, **kwargs: object) -> bytes:
    result = build_with_imports(
        main_source=root / "main.s",
        output_file=root / "out.sfc",
        output_dir=root / "obj",
        output_format="sfc",
        **kwargs,  # type: ignore[arg-type]
    )
    assert result.exit_code == 0, result.diagnostics
    return (root / "out.sfc").read_bytes()[:2]


def test_an_api_build_binds_the_defaults(tmp_path: Path) -> None:
    _project(tmp_path, "DEBUG = 3\nLANG_FR = 4\n")

    assert _api_rom(tmp_path) == bytes([3, 4])


def test_an_api_build_symbol_overrides_a_default(tmp_path: Path) -> None:
    _project(tmp_path, "DEBUG = 3\nLANG_FR = 4\n")

    assert _api_rom(tmp_path, symbols={"DEBUG": 9}) == bytes([9, 4])


def _cli_rom(root: Path, *flags: str) -> bytes:
    out = root / "out.sfc"
    code = _dispatch_subcommand(["build", str(root / "main.s"), "-f", "sfc", "-o", str(out), *flags])
    assert code == 0
    return out.read_bytes()[:2]


def test_a_cli_build_binds_the_defaults(tmp_path: Path) -> None:
    _project(tmp_path, "DEBUG = 3\nLANG_FR = 4\n")

    assert _cli_rom(tmp_path) == bytes([3, 4])


def test_a_cli_dash_d_overrides_a_default(tmp_path: Path) -> None:
    _project(tmp_path, "DEBUG = 3\nLANG_FR = 4\n")

    assert _cli_rom(tmp_path, "-D", "DEBUG=9") == bytes([9, 4])


def test_a_cli_compile_only_binds_the_defaults(tmp_path: Path) -> None:
    main = _project(tmp_path, "DEBUG = 3\nLANG_FR = 4\n")

    assert _dispatch_subcommand(["build", "-c", str(main)]) == 0


def test_check_counts_a_declared_name_as_defined(tmp_path: Path) -> None:
    main = _project(tmp_path, "DEBUG = 0\n")
    source = '"""M."""\n.if DEBUG {\n    .db 1\n}\n'
    main.write_text(source, encoding="utf-8")

    assert [d.code for d in lint_text(source, main) if d.code == "W0002"] == []


def test_check_still_warns_on_an_undeclared_name(tmp_path: Path) -> None:
    main = _project(tmp_path, "DEBUG = 0\n")
    source = '"""M."""\n.if DEBUGG {\n    .db 1\n}\n'
    main.write_text(source, encoding="utf-8")

    assert [d.code for d in lint_text(source, main) if d.code == "W0002"] == ["W0002"]
