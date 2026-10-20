"""Several outputs from one link: repeatable `-o`, `extra_outputs` in the API."""

from __future__ import annotations

from pathlib import Path

import pytest

from a816 import bps
from a816.cli import _dispatch_subcommand
from a816.module_builder import build_with_imports
from a816.program.link import format_for_path
from tests.bps_roms import source_rom
from tests.test_bps_build import _expected, _project


def _build(root: Path, *argv: str) -> int | None:
    return _dispatch_subcommand(["build", str(root / "main.s"), *argv])


def _alone(root: Path, fmt: str, name: str) -> bytes:
    """The same output built on its own, for comparison."""
    out = root / "alone" / name
    out.parent.mkdir(exist_ok=True)
    assert _build(root, "-f", fmt, "-o", str(out)) == 0
    return out.read_bytes()


@pytest.mark.parametrize(
    ("name", "fmt"),
    [
        ("game.ips", "ips"),
        ("game.sfc", "sfc"),
        ("GAME.SMC", "sfc"),
        ("game.bps", "bps"),
        ("game.bps.unsealed", "bps"),
        ("game.bin", None),
        ("a.out", None),
    ],
)
def test_format_for_path(name: str, fmt: str | None) -> None:
    assert format_for_path(Path(name)) == fmt


class TestCli:
    @pytest.mark.parametrize("flags", [(), ("--no-auto-imports",)])
    def test_writes_every_output_from_one_link(self, tmp_path: Path, flags: tuple[str, ...]) -> None:
        _project(tmp_path)
        outputs = ["-o", str(tmp_path / "g.ips"), "-o", str(tmp_path / "g.bps.unsealed"), "-o", str(tmp_path / "g.sfc")]
        assert _build(tmp_path, *flags, *outputs) == 0
        assert (tmp_path / "g.ips").read_bytes() == _alone(tmp_path, "ips", "g.ips")
        assert (tmp_path / "g.sfc").read_bytes() == _alone(tmp_path, "sfc", "g.sfc")
        assert bps.apply((tmp_path / "g.bps.unsealed").read_bytes(), source_rom(), unsealed=True) == _expected()

    def test_each_output_gets_its_adbg(self, tmp_path: Path) -> None:
        _project(tmp_path)
        _build(tmp_path, "-o", str(tmp_path / "g.ips"), "-o", str(tmp_path / "g.bps.unsealed"))
        assert sorted(p.name for p in tmp_path.glob("g.*.adbg")) == ["g.bps.unsealed.adbg", "g.ips.adbg"]

    def test_a_single_o_keeps_f_whatever_its_extension(self, tmp_path: Path) -> None:
        _project(tmp_path)
        assert _build(tmp_path, "-o", str(tmp_path / "x.sfc")) == 0
        assert (tmp_path / "x.sfc").read_bytes().startswith(b"PATCH")

    def test_several_o_refuse_f(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _project(tmp_path)
        with pytest.raises(SystemExit):
            _build(tmp_path, "-f", "ips", "-o", str(tmp_path / "a.ips"), "-o", str(tmp_path / "b.sfc"))
        assert "with several -o each takes its format from its extension" in capsys.readouterr().err

    def test_several_o_need_known_extensions(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _project(tmp_path)
        with pytest.raises(SystemExit):
            _build(tmp_path, "-o", str(tmp_path / "a.ips"), "-o", str(tmp_path / "b.bin"))
        assert "b.bin: name no format" in capsys.readouterr().err


class TestApi:
    def test_extra_outputs_share_the_link(self, tmp_path: Path) -> None:
        main = _project(tmp_path)
        result = build_with_imports(
            main,
            tmp_path / "g.ips",
            output_dir=tmp_path / "obj",
            extra_outputs=[("bps", tmp_path / "g.bps.unsealed")],
        )
        assert result.exit_code == 0, result.diagnostics
        assert result.debug_info_path == tmp_path / "g.ips.adbg"
        assert bps.apply((tmp_path / "g.bps.unsealed").read_bytes(), source_rom(), unsealed=True) == _expected()

    def test_an_unknown_extra_format_writes_nothing(self, tmp_path: Path) -> None:
        main = _project(tmp_path)
        result = build_with_imports(
            main, tmp_path / "g.ips", output_dir=tmp_path / "obj", extra_outputs=[("ups", tmp_path / "g.ups")]
        )
        assert result.exit_code == 1
        assert result.diagnostics == ["Unknown output format: ups"]
        assert not (tmp_path / "g.ips").exists()
