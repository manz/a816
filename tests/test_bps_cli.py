"""`a816 bps seal|apply|info`."""

from __future__ import annotations

import shutil
import subprocess
import zlib
from pathlib import Path

import pytest

from a816 import bps
from a816.cli import _dispatch_subcommand
from tests.bps_roms import SOURCE_SIZE, delta_target, source_rom

_FIXTURE = Path(__file__).parent / "fixtures" / "bps" / "flips_delta.bps"
_BLOCKS = [(0x10, b"\x01\x02"), (SOURCE_SIZE + 4, b"\x03")]


def _expected() -> bytes:
    image = bytearray(source_rom()) + bytes(8)
    image[0x10:0x12] = b"\x01\x02"
    image[SOURCE_SIZE + 4] = 3
    return bytes(image)


@pytest.fixture
def unsealed(tmp_path: Path) -> Path:
    patch = bps.encode_linear(
        _BLOCKS,
        source_size=SOURCE_SIZE,
        source_crc32=zlib.crc32(source_rom()),
        target_size=SOURCE_SIZE + 8,
        metadata='<patch name="x"/>',
    )
    (tmp_path / "game.bps.unsealed").write_bytes(patch)
    (tmp_path / "base.sfc").write_bytes(source_rom())
    return tmp_path / "game.bps.unsealed"


def _bps(*argv: str | Path) -> int | None:
    return _dispatch_subcommand(["bps", *map(str, argv)])


class TestSeal:
    def test_writes_the_patch_without_unsealed(self, unsealed: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert _bps("seal", unsealed, "--source", unsealed.parent / "base.sfc") == 0
        sealed = unsealed.parent / "game.bps"
        assert bps.apply(sealed.read_bytes(), source_rom()) == _expected()
        assert f"patched ROM CRC32 {zlib.crc32(_expected()):08X}" in capsys.readouterr().out

    def test_writes_to_o(self, unsealed: Path) -> None:
        assert _bps("seal", unsealed, "--source", unsealed.parent / "base.sfc", "-o", unsealed.parent / "x.bps") == 0
        assert bps.info((unsealed.parent / "x.bps").read_bytes()).sealed

    def test_a_patch_not_named_unsealed_needs_o(self, unsealed: Path) -> None:
        renamed = unsealed.rename(unsealed.parent / "game.bps")
        with pytest.raises(SystemExit):
            _bps("seal", renamed, "--source", unsealed.parent / "base.sfc")

    def test_a_wrong_rom_is_e0512(self, unsealed: Path, capsys: pytest.CaptureFixture[str]) -> None:
        (unsealed.parent / "base.sfc").write_bytes(bytes(0x200) + source_rom())
        assert _bps("seal", unsealed, "--source", unsealed.parent / "base.sfc") == 1
        err = capsys.readouterr().err
        assert "E0512" in err
        assert "copier header" in err
        assert not (unsealed.parent / "game.bps").exists()

    def test_a_missing_rom_is_e0500(self, unsealed: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert _bps("seal", unsealed, "--source", unsealed.parent / "nope.sfc") == 1
        assert "io error[E0500]" in capsys.readouterr().err

    def test_an_unreadable_path_is_an_io_error(self, unsealed: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert _bps("seal", unsealed, "--source", unsealed.parent) == 1
        assert "io error" in capsys.readouterr().err


class TestApply:
    def test_applies_a_flips_delta_patch(self, tmp_path: Path) -> None:
        (tmp_path / "base.sfc").write_bytes(source_rom())
        assert _bps("apply", _FIXTURE, "--source", tmp_path / "base.sfc", "-o", tmp_path / "out.sfc") == 0
        assert (tmp_path / "out.sfc").read_bytes() == delta_target()

    def test_an_unsealed_patch_needs_the_flag(self, unsealed: Path, capsys: pytest.CaptureFixture[str]) -> None:
        out = unsealed.parent / "out.sfc"
        assert _bps("apply", unsealed, "--source", unsealed.parent / "base.sfc", "-o", out) == 1
        assert "E0511" in capsys.readouterr().err
        assert _bps("apply", unsealed, "--source", unsealed.parent / "base.sfc", "-o", out, "--unsealed") == 0
        assert out.read_bytes() == _expected()


class TestInfo:
    def test_prints_an_unsealed_patch(self, unsealed: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert _bps("info", unsealed) == 0
        out = capsys.readouterr().out
        assert f"source: {SOURCE_SIZE:#x} bytes, CRC32 {zlib.crc32(source_rom()):08X}" in out
        assert f"target: {SOURCE_SIZE + 8:#x} bytes, CRC32 unsealed" in out
        assert 'metadata: <patch name="x"/>' in out

    def test_prints_a_sealed_patch_without_metadata(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert _bps("info", _FIXTURE) == 0
        out = capsys.readouterr().out
        assert f"CRC32 {zlib.crc32(delta_target()):08X}" in out
        assert "metadata" not in out

    def test_a_non_patch_is_e0511(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        (tmp_path / "x.ips").write_bytes(b"PATCHEOF")
        assert _bps("info", tmp_path / "x.ips") == 1
        assert "bps error[E0511]" in capsys.readouterr().err


@pytest.mark.skipif(shutil.which("flips") is None, reason="flips is not on PATH")
def test_flips_takes_the_sealed_patch(unsealed: Path) -> None:
    assert _bps("seal", unsealed, "--source", unsealed.parent / "base.sfc") == 0
    out = unsealed.parent / "out.sfc"
    result = subprocess.run(
        ["flips", "--apply", "--exact", unsealed.parent / "game.bps", unsealed.parent / "base.sfc", out],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout
    assert out.read_bytes() == _expected()
