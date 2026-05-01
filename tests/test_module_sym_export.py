"""Module GLOBAL labels must reach the .sym output, BuildResult.symbol_map, and .adbg."""

from __future__ import annotations

import tempfile
from pathlib import Path

from a816.debug_info import read as read_debug_info
from a816.linker import Linker
from a816.module_builder import build_with_imports_direct
from a816.object_file import ObjectFile
from a816.program import Program


def _write(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_direct_build_exposes_module_global_in_symbol_map() -> None:
    main_src = """*=0x008000
.import "vwf"

main:
    jsr.l vwf_render
    rts
"""

    vwf_src = """vwf_render:
    rep #0x30
    rts
"""

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        modules = root / "modules"
        modules.mkdir()
        main_file = root / "main.s"
        _write(main_file, main_src)
        _write(modules / "vwf.s", vwf_src)

        result = build_with_imports_direct(
            main_source=main_file,
            output_file=root / "out.ips",
            output_format="ips",
            module_paths=[modules],
            output_dir=root / "obj",
        )

        assert result.exit_code == 0, result.diagnostics
        assert "main" in result.symbol_map
        assert "vwf_render" in result.symbol_map
        # vwf_render lands somewhere after main; bsnes wants bank:offset.
        assert isinstance(result.symbol_map["vwf_render"], int)


def test_direct_build_emits_adbg_with_module_symbol() -> None:
    main_src = """*=0x008000
.import "vwf"

main:
    jsr.l vwf_render
    rts
"""

    vwf_src = """vwf_render:
    rep #0x30
    rts
"""

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        modules = root / "modules"
        modules.mkdir()
        main_file = root / "main.s"
        _write(main_file, main_src)
        _write(modules / "vwf.s", vwf_src)

        result = build_with_imports_direct(
            main_source=main_file,
            output_file=root / "out.ips",
            output_format="ips",
            module_paths=[modules],
            output_dir=root / "obj",
        )

        assert result.exit_code == 0, result.diagnostics
        assert result.debug_info_path is not None
        assert result.debug_info_path.exists()

        debug = read_debug_info(result.debug_info_path)
        assert "vwf" in {m.name for m in debug.modules}
        assert "vwf_render" in {s.name for s in debug.symbols}
        assert "main" in {s.name for s in debug.symbols}
        # At least one line entry tied to a real source file.
        assert debug.lines, "expected at least one line entry"


def test_link_path_propagates_lines_into_adbg() -> None:
    main_src = """*=0x8000
.extern far_helper

main:
    jsr.l far_helper
    rts
"""

    helper_src = """far_helper:
    rtl
"""

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        main_file = root / "main.s"
        helper_file = root / "helper.s"
        _write(main_file, main_src)
        _write(helper_file, helper_src)

        main_obj = root / "main.o"
        helper_obj = root / "helper.o"
        assert Program().assemble_as_object(str(main_file), main_obj) == 0
        assert Program().assemble_as_object(str(helper_file), helper_obj) == 0

        loaded_main = ObjectFile.from_file(str(main_obj))
        loaded_helper = ObjectFile.from_file(str(helper_obj))
        # Line tables must survive the .o round-trip.
        assert loaded_main.lines, "main.o should carry line entries"
        assert loaded_helper.lines, "helper.o should carry line entries"

        linker = Linker([loaded_main, loaded_helper])
        linked = linker.link(base_address=0x8000)

        out_program = Program()
        ips_file = root / "out.ips"
        assert out_program.link_as_patch(linked, ips_file) == 0

        adbg_file = ips_file.with_suffix(ips_file.suffix + ".adbg")
        assert adbg_file.exists()
        debug = read_debug_info(adbg_file)
        # Linked .adbg should inherit lines from both objects.
        assert debug.lines, "expected linked .adbg to carry line entries"
        # main.s line numbers should appear; helper.s addresses should be > main.
        files = set(debug.files)
        assert any("main.s" in f for f in files)
        assert any("helper.s" in f for f in files)


def test_exports_sym_file_includes_linker_globals() -> None:
    main_src = """*=0x8000
.extern far_helper

main:
    jsr.l far_helper
    rts
"""

    helper_src = """far_helper:
    rtl
"""

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        main_file = root / "main.s"
        helper_file = root / "helper.s"
        _write(main_file, main_src)
        _write(helper_file, helper_src)

        main_obj = root / "main.o"
        helper_obj = root / "helper.o"
        program_main = Program()
        assert program_main.assemble_as_object(str(main_file), main_obj) == 0
        program_helper = Program()
        assert program_helper.assemble_as_object(str(helper_file), helper_obj) == 0

        linker = Linker([ObjectFile.from_file(str(main_obj)), ObjectFile.from_file(str(helper_obj))])
        linked = linker.link(base_address=0x8000)

        out_program = Program()
        ips_file = root / "out.ips"
        assert out_program.link_as_patch(linked, ips_file) == 0

        sym_file = root / "out.sym"
        out_program.exports_symbol_file(str(sym_file))
        content = sym_file.read_text()
        assert "main" in content
        assert "far_helper" in content
