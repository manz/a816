"""Targeted tests filling diff-coverage holes on this branch.

Grouped by source module — each test names the specific lines it
covers so a future bisect can tell when one regresses."""

from __future__ import annotations

import argparse
import logging
import tempfile
from pathlib import Path

import pytest

from a816.cli import _apply_a816_toml, _apply_experimental
from a816.parse.nodes import NodeError
from a816.program import Program


class TestCliExperimental:
    """cli.py:174-178 — `--experimental` flag dispatch."""

    def test_track_register_size_sets_resolver_flag(self) -> None:
        program = Program()
        assert program.resolver.track_register_size is False
        _apply_experimental(program, ["track_register_size"])
        assert program.resolver.track_register_size is True

    def test_unknown_flag_warns_and_leaves_resolver_untouched(self, caplog: pytest.LogCaptureFixture) -> None:
        program = Program()
        with caplog.at_level(logging.WARNING, logger="a816"):
            _apply_experimental(program, ["does_not_exist"])
        assert program.resolver.track_register_size is False
        assert any("does_not_exist" in r.message for r in caplog.records)

    def test_none_flag_list_is_noop(self) -> None:
        program = Program()
        _apply_experimental(program, None)
        assert program.resolver.track_register_size is False


class TestA816TomlExperimentalMirror:
    """cli.py:55-58 — `[experimental]` table in a816.toml flips into args.experimental."""

    def _seed(self, tmp_path: Path, toml_body: str) -> argparse.Namespace:
        (tmp_path / "a816.toml").write_text(toml_body, encoding="utf-8")
        main = tmp_path / "main.s"
        main.write_text("nop\n", encoding="utf-8")
        return argparse.Namespace(
            input_files=[main],
            include_paths=[],
            module_paths=[],
            experimental=[],
        )

    def test_toml_experimental_flag_appears_in_args(self, tmp_path: Path) -> None:
        args = self._seed(tmp_path, 'entrypoint = "main.s"\n[experimental]\ntrack_register_size = true\n')
        _apply_a816_toml(args)
        assert "track_register_size" in args.experimental

    def test_cli_flag_wins_no_duplicate_added(self, tmp_path: Path) -> None:
        args = self._seed(tmp_path, 'entrypoint = "main.s"\n[experimental]\ntrack_register_size = true\n')
        args.experimental = ["track_register_size"]
        _apply_a816_toml(args)
        assert args.experimental.count("track_register_size") == 1

    def test_disabled_toml_flag_not_propagated(self, tmp_path: Path) -> None:
        args = self._seed(tmp_path, 'entrypoint = "main.s"\n[experimental]\ntrack_register_size = false\n')
        _apply_a816_toml(args)
        assert "track_register_size" not in (args.experimental or [])


class TestAllocAtSizeValidation:
    """parse/codegen/pool.py:193-194 — `at ADDR size N` rejects non-positive N."""

    def _assemble(self, src: str) -> None:
        program = Program()
        from tests import StubWriter

        program.assemble_string_with_emitter(src, "t.s", StubWriter())

    def test_size_zero_rejected(self) -> None:
        with pytest.raises(NodeError, match="size must be positive"):
            self._assemble(".alloc at 0x008000 size 0 { .db 0xEA }\n")


class TestAssignExternObjectMode:
    """parse/codegen/symbols.py:163-167 — `alias = external_symbol` in OBJECT mode
    publishes to the object writer's alias table."""

    def test_alias_to_extern_is_published_in_object_mode(self) -> None:
        src = ".extern target\nfont_ptr = target + 0x40\n"
        with tempfile.TemporaryDirectory() as tmp:
            obj_path = Path(tmp) / "out.o"
            main = Path(tmp) / "main.s"
            main.write_text(src, encoding="utf-8")
            program = Program()
            assert program.assemble_as_object(str(main), obj_path) == 0

            from a816.object_file import ObjectFile

            obj = ObjectFile.from_file(str(obj_path))
            alias_names = {name for name, _expr in obj.aliases}
            assert "font_ptr" in alias_names

    def test_alias_to_extern_in_non_object_mode_raises(self) -> None:
        program = Program()
        from tests import StubWriter

        with pytest.raises(NodeError, match="external symbols only allowed in object"):
            program.assemble_string_with_emitter(
                ".extern target\nfont_ptr = target + 0x40\n",
                "t.s",
                StubWriter(),
            )
