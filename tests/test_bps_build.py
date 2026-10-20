"""`a816 build -f bps`: the `[bps]` table, the metadata, the linked patch."""

from __future__ import annotations

import logging
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from a816 import bps
from a816.cli import _dispatch_subcommand
from a816.config import BpsConfig, load_a816_toml
from a816.exceptions import A816ConfigError
from a816.linker import Linker
from a816.module_builder import build_with_imports
from a816.object_file import ObjectFile
from a816.program import Program
from tests.bps_roms import SOURCE_SIZE, source_rom

ROM_SIZE = 0x20000
MAIN = """\"\"\"M.\"\"\"
.alloc first at 0x008010 {
    .db 1, 2, 3
}
.alloc second at 0x018000 {
    .db 4, 5
}
.alloc past at 0x028000 {
    .db 6
}
"""
BPS_TABLE = f"""
[bps]
source_size = {SOURCE_SIZE:#x}
source_crc32 = {zlib.crc32(source_rom()):#x}

[bps.metadata]
name = "Test & <Co>"
version = "${{VERSION}}"
"""


def _project(root: Path, bps_table: str = BPS_TABLE, defines: str = 'VERSION = "dev"') -> Path:
    toml = f"""entrypoint = "main.s"
rom_size = {ROM_SIZE:#x}

[map.1]
address = "00-7d,80-ff:8000-ffff"
mask = 0x8000

[defines]
{defines}
{bps_table}"""
    (root / "a816.toml").write_text(toml, encoding="utf-8")
    (root / "main.s").write_text(MAIN, encoding="utf-8")
    return root / "main.s"


def _expected() -> bytes:
    image = bytearray(source_rom()) + bytes(ROM_SIZE - SOURCE_SIZE)
    image[0x0010:0x0013] = b"\x01\x02\x03"
    image[0x8000:0x8002] = b"\x04\x05"
    image[0x10000] = 6
    return bytes(image)


def _build(root: Path, *flags: str, output: str = "out.bps.unsealed") -> int | None:
    return _dispatch_subcommand(["build", str(root / "main.s"), "-f", "bps", "-o", str(root / output), *flags])


def _config(root: Path, bps_table: str) -> BpsConfig | None:
    _project(root, bps_table)
    config = load_a816_toml(root / "a816.toml")
    assert config is not None
    return config.bps


class TestConfig:
    def test_loads_the_table(self, tmp_path: Path) -> None:
        assert _config(tmp_path, BPS_TABLE) == BpsConfig(
            SOURCE_SIZE, zlib.crc32(source_rom()), {"name": "Test & <Co>", "version": "${VERSION}"}
        )

    def test_a_project_without_one_has_none(self, tmp_path: Path) -> None:
        assert _config(tmp_path, "") is None

    @pytest.mark.parametrize(
        ("table", "message"),
        [
            ("bps = 1\n", "must be a table"),
            ("[bps]\nsource_size = 1\nsource_crc32 = 2\nsize = 3\n", "unknown keys: size"),
            ("[bps]\nsource_crc32 = 2\n", "source_size must be"),
            ('[bps]\nsource_size = "1"\nsource_crc32 = 2\n', "source_size must be"),
            ("[bps]\nsource_size = 1\nsource_crc32 = true\n", "source_crc32 must be"),
            ("[bps]\nsource_size = -1\nsource_crc32 = 2\n", "source_size must be"),
            ("[bps]\nsource_size = 1\nsource_crc32 = 0x100000000\n", "does not fit in 32 bits"),
            ("[bps]\nsource_size = 1\nsource_crc32 = 2\nmetadata = 3\n", "metadata must be a table"),
            ('[bps]\nsource_size = 1\nsource_crc32 = 2\n[bps.metadata]\n"a b" = "x"\n', "not an attribute name"),
            ("[bps]\nsource_size = 1\nsource_crc32 = 2\n[bps.metadata]\nname = 3\n", "name must be a string"),
        ],
    )
    def test_a_malformed_table_is_e0513(self, tmp_path: Path, table: str, message: str) -> None:
        (tmp_path / "a816.toml").write_text(table, encoding="utf-8")
        with pytest.raises(A816ConfigError, match=message) as excinfo:
            load_a816_toml(tmp_path / "a816.toml")
        assert excinfo.value.code.code == "E0513"


class TestMetadata:
    def test_fills_and_escapes(self) -> None:
        xml = bps.metadata_xml({"name": 'A & "B"', "version": "v${VERSION}"}, {"VERSION": "0x10"})
        assert xml == '<patch name="A &amp; &quot;B&quot;" version="v0x10"/>'

    def test_an_empty_table_gives_no_metadata(self) -> None:
        assert bps.metadata_xml({}, {}) == ""

    def test_an_undefined_name_is_e0514(self) -> None:
        with pytest.raises(A816ConfigError, match=r"`\$\{COMMIT\}` in \[bps.metadata\] commit") as excinfo:
            bps.metadata_xml({"commit": "${COMMIT}"}, {})
        assert excinfo.value.code.code == "E0514"


class TestCliBuild:
    def test_writes_an_unsealed_patch_over_the_source(self, tmp_path: Path) -> None:
        _project(tmp_path)
        assert _build(tmp_path) == 0
        patch = (tmp_path / "out.bps.unsealed").read_bytes()
        assert bps.apply(patch, source_rom(), unsealed=True) == _expected()

    def test_pads_the_target_to_rom_size(self, tmp_path: Path) -> None:
        _project(tmp_path)
        _build(tmp_path)
        assert bps.info((tmp_path / "out.bps.unsealed").read_bytes()).target_size == ROM_SIZE

    def test_writes_the_output_path_as_given(self, tmp_path: Path) -> None:
        _project(tmp_path)
        assert _build(tmp_path, output="game.bps") == 0
        assert sorted(p.name for p in tmp_path.glob("game.bps*")) == ["game.bps", "game.bps.adbg"]

    def test_metadata_takes_the_define_default(self, tmp_path: Path) -> None:
        _project(tmp_path)
        _build(tmp_path)
        metadata = bps.info((tmp_path / "out.bps.unsealed").read_bytes()).metadata
        assert metadata == '<patch name="Test &amp; &lt;Co&gt;" version="dev"/>'

    def test_metadata_keeps_a_dash_d_as_spelled(self, tmp_path: Path) -> None:
        _project(tmp_path)
        _build(tmp_path, "-D", "VERSION=0x10")
        assert 'version="0x10"' in bps.info((tmp_path / "out.bps.unsealed").read_bytes()).metadata

    def test_the_link_path_writes_the_same_patch(self, tmp_path: Path) -> None:
        _project(tmp_path)
        _build(tmp_path)
        assert _build(tmp_path, "--no-auto-imports", output="linked.bps.unsealed") == 0
        linked = (tmp_path / "linked.bps.unsealed").read_bytes()
        assert linked == (tmp_path / "out.bps.unsealed").read_bytes()

    def test_without_a_bps_table_is_e0513(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        _project(tmp_path, bps_table="")
        with caplog.at_level(logging.ERROR):
            assert _build(tmp_path) == 1
        assert "E0513" in caplog.text

    def test_an_undefined_metadata_name_is_e0514(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        _project(tmp_path, defines="")
        with caplog.at_level(logging.ERROR):
            assert _build(tmp_path) == 1
        assert "E0514" in caplog.text

    def test_a_copier_header_is_e0515(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        _project(tmp_path)
        with caplog.at_level(logging.ERROR):
            assert _build(tmp_path, "--copier-header") == 1
        assert "E0515" in caplog.text

    def test_an_unknown_format_is_refused(self, tmp_path: Path) -> None:
        _project(tmp_path)
        with pytest.raises(SystemExit):
            _dispatch_subcommand(["build", str(tmp_path / "main.s"), "-f", "ups"])


class TestApiBuild:
    def test_reads_the_table_from_a816_toml(self, tmp_path: Path) -> None:
        main = _project(tmp_path)
        result = build_with_imports(
            main,
            tmp_path / "out.bps.unsealed",
            output_format="bps",
            output_dir=tmp_path / "obj",
            symbols={"VERSION": 3},
        )
        assert result.exit_code == 0, result.diagnostics
        patch = (tmp_path / "out.bps.unsealed").read_bytes()
        assert bps.apply(patch, source_rom(), unsealed=True) == _expected()
        assert 'version="3"' in bps.info(patch).metadata

    def test_link_as_refuses_an_unknown_format(self, tmp_path: Path) -> None:
        (tmp_path / "m.s").write_text("*=0x008000\n.db 1\n", encoding="utf-8")
        assert Program().assemble_as_object(str(tmp_path / "m.s"), tmp_path / "m.o") == 0
        linked = Linker([ObjectFile.from_file(str(tmp_path / "m.o"))]).link(base_address=0x8000)
        program = Program()
        with pytest.raises(ValueError, match="unknown output format 'ups'"):
            program.link_as("ups", linked, tmp_path / "out")


@pytest.mark.skipif(shutil.which("flips") is None, reason="flips is not on PATH")
def test_flips_applies_the_sealed_build(tmp_path: Path) -> None:
    _project(tmp_path)
    _build(tmp_path)
    sealed = bps.seal((tmp_path / "out.bps.unsealed").read_bytes(), source_rom())
    (tmp_path / "out.bps").write_bytes(sealed)
    (tmp_path / "source.sfc").write_bytes(source_rom())
    result = subprocess.run(
        ["flips", "--apply", "--exact", tmp_path / "out.bps", tmp_path / "source.sfc", tmp_path / "out.sfc"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout
    assert (tmp_path / "out.sfc").read_bytes() == _expected()
    assert struct.unpack("<I", sealed[-8:-4])[0] == zlib.crc32(_expected())
