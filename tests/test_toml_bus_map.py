"""`a816.toml` bus regions seeded onto every translation unit.

`[[map]]` / `mapper` reach each module's resolver bus before its own
`.map` lines run, so a module without a local `.map` still places code
in the project's banks; a local `.map` identical to the toml one is a
no-op and a conflicting one fails on the source line (E0308).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from a816.cli import _apply_a816_toml, _build_arg_parser, _run_assemble
from a816.exceptions import A816ConfigError
from a816.module_builder import BuildResult, ModuleBuilder, build_with_imports
from a816.object_file import BusMapping, ObjectFile
from a816.program import Program

_ROM = BusMapping("1", (0xC0, 0xFF), (0x0000, 0xFFFF), 0x1_0000)
_ROM_MAP_LINE = ".map identifier=1 bank_range=0xc0, 0xff addr_range=0x0000, 0xffff mask=0x10000\n"
_ROM_MAP_CONFLICT = ".map identifier=1 bank_range=0xc0, 0xfe addr_range=0x0000, 0xffff mask=0x10000\n"
_PLACED_CODE = ".alloc at 0xf00000 {\n    rts\n}\n"
_ROM_TOML = (
    'entrypoint = "main.s"\n'
    "[[map]]\nidentifier = 1\nbank_range = [0xc0, 0xff]\naddr_range = [0x0000, 0xffff]\nmask = 0x10000\n"
)


def _compile(tmp_path: Path, source: str, bus_map: list[BusMapping]) -> tuple[int, Path]:
    src = tmp_path / "main.s"
    src.write_text(source, encoding="utf-8")
    obj = tmp_path / "main.o"
    program = Program()
    program.resolver.context.bus_map = list(bus_map)
    return program.assemble_as_object(str(src), obj), obj


def _shapes(obj: Path) -> list[tuple[object, ...]]:
    return [
        (m.identifier, m.bank_range, m.addr_range, m.mask, m.writeable, m.mirror_bank_range)
        for m in ObjectFile.from_file(str(obj)).bus_mappings
    ]


_ROM_SHAPE = ("1", (0xC0, 0xFF), (0x0000, 0xFFFF), 0x1_0000, False, None)


def test_module_without_local_map_places_code_in_toml_bank(tmp_path: Path) -> None:
    rc, _ = _compile(tmp_path, _PLACED_CODE, [_ROM])
    assert rc == 0


def test_module_without_seed_rejects_the_same_bank(tmp_path: Path) -> None:
    rc, _ = _compile(tmp_path, _PLACED_CODE, [])
    assert rc != 0


def test_seeded_map_is_serialized_once(tmp_path: Path) -> None:
    _, obj = _compile(tmp_path, _PLACED_CODE, [_ROM])
    assert _shapes(obj) == [_ROM_SHAPE]


def test_identical_local_map_is_accepted(tmp_path: Path) -> None:
    rc, _ = _compile(tmp_path, _ROM_MAP_LINE + _PLACED_CODE, [_ROM])
    assert rc == 0


def test_identical_local_map_does_not_duplicate_serialization(tmp_path: Path) -> None:
    _, obj = _compile(tmp_path, _ROM_MAP_LINE + _PLACED_CODE, [_ROM])
    assert _shapes(obj) == [_ROM_SHAPE]


def test_conflicting_local_map_fails(tmp_path: Path) -> None:
    rc, _ = _compile(tmp_path, _ROM_MAP_CONFLICT + _PLACED_CODE, [_ROM])
    assert rc != 0


def test_conflicting_local_map_reports_e0308(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _compile(tmp_path, _ROM_MAP_CONFLICT + _PLACED_CODE, [_ROM])
    assert "error[E0308]: conflicting `.map '1'`" in caplog.text


def test_conflicting_local_map_points_at_source_line(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _compile(tmp_path, "\n" + _ROM_MAP_CONFLICT + _PLACED_CODE, [_ROM])
    assert "main.s:2:" in caplog.text


def test_conflicting_local_map_hint_names_the_toml(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _compile(tmp_path, _ROM_MAP_CONFLICT + _PLACED_CODE, [_ROM])
    assert "a816.toml" in caplog.text


def test_parse_mode_seeds_the_bus() -> None:
    program = Program()
    program.resolver.context.bus_map = [_ROM]
    program.parser.parse("nop\n", "memory.s")
    assert "1" in program.resolver.bus.mappings


def _build_two_modules(root: Path, bus_map: list[BusMapping]) -> BuildResult:
    (root / "a.s").write_text(".alloc at 0xf00000 {\na_entry:\n    rts\n}\n", encoding="utf-8")
    (root / "main.s").write_text('.import "a"\n.alloc at 0xf10000 {\n    jsr.l a_entry\n}\n', encoding="utf-8")
    return build_with_imports(
        main_source=root / "main.s",
        output_file=root / "out.ips",
        module_paths=[root],
        output_dir=root / "obj",
        bus_map=bus_map,
    )


def test_build_links_modules_sharing_seeded_map(tmp_path: Path) -> None:
    result = _build_two_modules(tmp_path, [_ROM])
    assert result.exit_code == 0, result.diagnostics


def test_build_places_code_through_seeded_map(tmp_path: Path) -> None:
    result = _build_two_modules(tmp_path, [_ROM])
    assert result.symbol_map["a_entry"] == 0xF00000


def _builder(root: Path, bus_map: list[BusMapping]) -> ModuleBuilder:
    builder = ModuleBuilder(module_paths=[root], output_dir=root / "obj", bus_map=bus_map)
    builder.discover_imports(root / "main.s")
    return builder


def test_cached_object_is_reused_under_the_same_map(tmp_path: Path) -> None:
    _build_two_modules(tmp_path, [_ROM])
    assert _builder(tmp_path, [_ROM])._needs_recompilation("a") is False


def test_changing_the_toml_map_invalidates_cached_objects(tmp_path: Path) -> None:
    _build_two_modules(tmp_path, [_ROM])
    wider = BusMapping("1", (0x80, 0xFF), (0x0000, 0xFFFF), 0x1_0000)
    assert _builder(tmp_path, [wider])._needs_recompilation("a") is True


def _args(tmp_path: Path, toml: str, *argv: str) -> argparse.Namespace:
    (tmp_path / "a816.toml").write_text(toml, encoding="utf-8")
    (tmp_path / "main.s").write_text(_PLACED_CODE, encoding="utf-8")
    return _build_arg_parser().parse_args([*argv, str(tmp_path / "main.s")])


def test_cli_reads_bus_map_from_toml(tmp_path: Path) -> None:
    args = _args(tmp_path, _ROM_TOML)
    _apply_a816_toml(args)
    assert args.bus_map == [_ROM]


def test_cli_mapper_selects_the_matching_rom_type(tmp_path: Path) -> None:
    args = _args(tmp_path, 'mapper = "hirom"\n')
    _apply_a816_toml(args)
    assert args.mapping == "high"


def test_cli_defaults_to_low_without_mapper(tmp_path: Path) -> None:
    args = _args(tmp_path, _ROM_TOML)
    _apply_a816_toml(args)
    assert args.mapping == "low"


def test_cli_defaults_to_low_without_toml(tmp_path: Path) -> None:
    (tmp_path / "main.s").write_text("nop\n", encoding="utf-8")
    args = _build_arg_parser().parse_args([str(tmp_path / "main.s")])
    _apply_a816_toml(args)
    assert args.mapping == "low"


def test_cli_accepts_agreeing_m_flag(tmp_path: Path) -> None:
    args = _args(tmp_path, 'mapper = "lorom"\n', "-m", "low2")
    _apply_a816_toml(args)
    assert args.mapping == "low2"


def test_cli_rejects_m_flag_disagreeing_with_mapper(tmp_path: Path) -> None:
    args = _args(tmp_path, 'mapper = "lorom"\n', "-m", "high")
    with pytest.raises(A816ConfigError, match="disagrees"):
        _apply_a816_toml(args)


def test_cli_build_uses_toml_map(tmp_path: Path) -> None:
    args = _args(tmp_path, _ROM_TOML, "-o", str(tmp_path / "out.ips"), "--obj-dir", str(tmp_path / "obj"))
    assert _run_assemble(args) == 0


def test_cli_compile_only_seeds_toml_map(tmp_path: Path) -> None:
    args = _args(tmp_path, _ROM_TOML, "-c")
    _run_assemble(args)
    assert _shapes(tmp_path / "main.o") == [_ROM_SHAPE]


def test_cli_link_of_sources_seeds_toml_map(tmp_path: Path) -> None:
    args = _args(tmp_path, _ROM_TOML, "--no-auto-imports", "-o", str(tmp_path / "out.ips"))
    assert _run_assemble(args) == 0
