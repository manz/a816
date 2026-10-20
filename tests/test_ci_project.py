"""`a816 ci lint|build|seal`: the steps that work on the project."""

from __future__ import annotations

import zlib
from pathlib import Path

import pytest

from a816 import bps
from a816.ci import ci_main
from a816.ci.process import CiError
from a816.ci.project import entrypoint, run_lint, seal_patch
from tests.bps_roms import SOURCE_SIZE, source_rom
from tests.ci_fakes import FakeRun, actions_in, written

_TOML = 'entrypoint = "main.s"\nrom_size = 0x80000\n[map.1]\naddress = "00-7d,80-ff:8000-ffff"\nmask = 0x8000\n'
_MAIN = '"""M."""\n.alloc at 0x008000 {\n    rts\n}\n'


class Fluff:
    def __init__(self, **statuses: int) -> None:
        self.statuses = statuses
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> int:
        self.calls.append(argv)
        return self.statuses.get(argv[0], 0)


def test_lint_formats_then_checks() -> None:
    fluff = Fluff()
    run_lint(["src"], check_format=True, fluff=fluff)
    assert fluff.calls == [["format", "--diff", "src"], ["check", "src"]]


def test_lint_checks_after_a_format_failure() -> None:
    fluff = Fluff(format=1)
    assert (run_lint([], check_format=True, fluff=fluff), len(fluff.calls)) == (1, 2)


def test_lint_fails_on_check() -> None:
    assert run_lint([], check_format=True, fluff=Fluff(check=1)) == 1


def test_lint_can_skip_the_format() -> None:
    fluff = Fluff()
    run_lint([], check_format=False, fluff=fluff)
    assert fluff.calls == [["check"]]


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a816.toml").write_text(_TOML, encoding="utf-8")
    (tmp_path / "main.s").write_text(_MAIN, encoding="utf-8")
    monkeypatch.chdir(tmp_path)


def test_lint_runs_fluff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path, monkeypatch)
    assert ci_main(["lint", "--paths", "main.s"], actions_in(tmp_path), FakeRun()) == 0


def test_the_entry_input_wins(tmp_path: Path) -> None:
    assert entrypoint("other.s", tmp_path) == "other.s"


def test_the_entry_defaults_to_the_toml_entrypoint(tmp_path: Path) -> None:
    (tmp_path / "a816.toml").write_text(_TOML, encoding="utf-8")
    assert Path(entrypoint("", tmp_path)).name == "main.s"


def test_no_entry_and_no_toml_fails(tmp_path: Path) -> None:
    with pytest.raises(CiError, match="no entrypoint"):
        entrypoint("", tmp_path)


@pytest.mark.parametrize("name", ["rom.sfc", "rom.smc"])
def test_a_single_rom_output_is_a_rom(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    # cacheguard's report: one `--outputs` path came out as an IPS whatever its extension.
    _project(tmp_path, monkeypatch)
    ci_main(["build", "--outputs", f"out/{name}"], actions_in(tmp_path), FakeRun())
    assert not (tmp_path / "out" / name).read_bytes().startswith(b"PATCH")


def test_a_single_ips_output_is_an_ips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path, monkeypatch)
    ci_main(["build", "--outputs", "out/a.ips"], actions_in(tmp_path), FakeRun())
    assert (tmp_path / "out" / "a.ips").read_bytes().startswith(b"PATCH")


def test_an_unknown_extension_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path, monkeypatch)
    assert ci_main(["build", "--outputs", "out/a.bin"], actions_in(tmp_path), FakeRun()) == 1


def test_build_writes_every_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path, monkeypatch)
    ci_main(["build", "--outputs", "out/a.ips\nout/a.sfc", "--defines", "X=1"], actions_in(tmp_path), FakeRun())
    assert sorted(p.name for p in (tmp_path / "out").glob("a.*")) == ["a.ips", "a.ips.adbg", "a.sfc", "a.sfc.adbg"]


def test_build_outputs_the_debug_info(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path, monkeypatch)
    ci_main(["build", "--outputs", "out/a.ips"], actions_in(tmp_path), FakeRun())
    assert written(tmp_path, "output") == "debug-info=out/a.ips.adbg\n"


def test_a_failed_build_returns_its_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path, monkeypatch)
    (tmp_path / "main.s").write_text("    lda undefined_symbol\n", encoding="utf-8")
    assert ci_main(["build", "--outputs", "out/a.ips"], actions_in(tmp_path), FakeRun()) != 0


def test_build_needs_an_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path, monkeypatch)
    assert ci_main(["build", "--outputs", ""], actions_in(tmp_path), FakeRun()) == 1


def _unsealed(tmp_path: Path) -> Path:
    patch = tmp_path / "x.bps.unsealed"
    source = source_rom()
    patch.write_bytes(
        bps.encode_linear(
            [(0x10, b"hack")], source_size=SOURCE_SIZE, source_crc32=zlib.crc32(source), target_size=SOURCE_SIZE
        )
    )
    return patch


def test_seal_writes_the_sealed_patch(tmp_path: Path) -> None:
    sealed = seal_patch(_unsealed(tmp_path), source_rom(), None)
    assert (sealed.name, bps.info(sealed.read_bytes()).sealed) == ("x.bps", True)


def test_seal_refuses_other_patches(tmp_path: Path) -> None:
    with pytest.raises(CiError, match="only .bps.unsealed and .ips"):
        seal_patch(tmp_path / "x.sfc", b"", None)


def test_seal_outputs_the_sealed_paths(tmp_path: Path) -> None:
    patch = _unsealed(tmp_path)
    (tmp_path / "base.sfc").write_bytes(source_rom())
    ci_main(["seal", "--rom", str(tmp_path / "base.sfc"), "--patches", str(patch)], actions_in(tmp_path), FakeRun())
    assert written(tmp_path, "output") == f"sealed={tmp_path / 'x.bps'}\n"
