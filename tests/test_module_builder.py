"""Tests for the module builder (automatic import resolution and compilation)."""

import tempfile
from pathlib import Path

import pytest

from a816.module_builder import BuildResult, ModuleBuilder, ModuleGraph, build_with_imports


class TestModuleGraph:
    """Tests for the ModuleGraph dependency graph."""

    def test_add_module(self) -> None:
        """Test adding modules to the graph."""
        graph = ModuleGraph()
        graph.add_module("vwf", Path("src/vwf.s"))
        graph.add_module("dialog", Path("src/dialog.s"))

        assert "vwf" in graph.modules
        assert "dialog" in graph.modules
        assert graph.modules["vwf"] == Path("src/vwf.s")

    def test_add_dependency(self) -> None:
        """Test adding dependencies between modules."""
        graph = ModuleGraph()
        graph.add_module("main", Path("main.s"))
        graph.add_module("vwf", Path("src/vwf.s"))
        graph.add_dependency("main", "vwf")

        assert "vwf" in graph.dependencies["main"]

    def test_topological_sort_simple(self) -> None:
        """Test topological sort with simple dependencies."""
        graph = ModuleGraph()
        graph.add_module("main", Path("main.s"))
        graph.add_module("vwf", Path("vwf.s"))
        graph.add_module("libmz", Path("libmz.s"))

        # main -> vwf -> libmz
        graph.add_dependency("main", "vwf")
        graph.add_dependency("vwf", "libmz")

        order = graph.topological_sort()

        # libmz should come before vwf, vwf before main
        assert order.index("libmz") < order.index("vwf")
        assert order.index("vwf") < order.index("main")

    def test_topological_sort_diamond(self) -> None:
        """Test topological sort with diamond dependency pattern."""
        graph = ModuleGraph()
        graph.add_module("main", Path("main.s"))
        graph.add_module("a", Path("a.s"))
        graph.add_module("b", Path("b.s"))
        graph.add_module("common", Path("common.s"))

        # main -> a -> common
        # main -> b -> common
        graph.add_dependency("main", "a")
        graph.add_dependency("main", "b")
        graph.add_dependency("a", "common")
        graph.add_dependency("b", "common")

        order = graph.topological_sort()

        # common should come before both a and b
        assert order.index("common") < order.index("a")
        assert order.index("common") < order.index("b")
        # a and b should come before main
        assert order.index("a") < order.index("main")
        assert order.index("b") < order.index("main")

    def test_circular_dependency_raises_error(self) -> None:
        """Test that circular dependencies are detected."""
        graph = ModuleGraph()
        graph.add_module("a", Path("a.s"))
        graph.add_module("b", Path("b.s"))

        graph.add_dependency("a", "b")
        graph.add_dependency("b", "a")

        with pytest.raises(ValueError, match="Circular dependency"):
            graph.topological_sort()


class TestModuleBuilder:
    """Tests for the ModuleBuilder class."""

    def test_discover_imports_single_file(self) -> None:
        """Test import discovery for a file with no imports."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "main.s"
            source.write_text("""
main:
    lda #0x42
    rts
""")

            builder = ModuleBuilder()
            builder.discover_imports(source)

            assert "__main__" in builder.graph.modules
            assert len(builder.graph.modules) == 1

    def test_discover_imports_with_dependency(self) -> None:
        """Test import discovery with one dependency."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create module
            vwf_source = Path(tmpdir) / "vwf.s"
            vwf_source.write_text("""
vwf_render:
    lda #0x01
    rts
""")

            # Create main file that imports vwf
            main_source = Path(tmpdir) / "main.s"
            main_source.write_text("""
.import "vwf"

main:
    jsr.w vwf_render
    rts
""")

            builder = ModuleBuilder(module_paths=[Path(tmpdir)])
            builder.discover_imports(main_source)

            assert "__main__" in builder.graph.modules
            assert "vwf" in builder.graph.modules
            assert "vwf" in builder.graph.dependencies["__main__"]

    def test_discover_transitive_imports(self) -> None:
        """Test discovery of transitive dependencies."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create libmz (no deps)
            libmz_source = Path(tmpdir) / "libmz.s"
            libmz_source.write_text("""
wait_for_vblank:
    rts
""")

            # Create vwf (depends on libmz)
            vwf_source = Path(tmpdir) / "vwf.s"
            vwf_source.write_text("""
.import "libmz"

vwf_render:
    jsr.w wait_for_vblank
    rts
""")

            # Create main (depends on vwf)
            main_source = Path(tmpdir) / "main.s"
            main_source.write_text("""
.import "vwf"

main:
    jsr.w vwf_render
    rts
""")

            builder = ModuleBuilder(module_paths=[Path(tmpdir)])
            builder.discover_imports(main_source)

            assert "__main__" in builder.graph.modules
            assert "vwf" in builder.graph.modules
            assert "libmz" in builder.graph.modules

            # Check dependency chain
            assert "vwf" in builder.graph.dependencies["__main__"]
            assert "libmz" in builder.graph.dependencies["vwf"]

    def test_build_single_module(self) -> None:
        """Test building a single module with no dependencies."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "main.s"
            source.write_text("""
main:
    lda #0x42
    sta 0x2000
    rts
""")

            builder = ModuleBuilder(output_dir=Path(tmpdir) / "obj")
            result = builder.build(source)

            assert result is not None
            assert len(result.code) > 0

    def test_build_with_imports(self) -> None:
        """Test building with automatic import resolution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create module
            lib_source = Path(tmpdir) / "mylib.s"
            lib_source.write_text("""
lib_func:
    lda #0x01
    sta 0x2000
    rts
""")

            # Create main
            main_source = Path(tmpdir) / "main.s"
            main_source.write_text("""
.import "mylib"

main:
    jsr.w lib_func
    rts
""")

            builder = ModuleBuilder(
                module_paths=[Path(tmpdir)],
                output_dir=Path(tmpdir) / "obj",
            )
            result = builder.build(main_source)

            assert result is not None
            assert len(result.code) > 0

            # Check that both modules' symbols are in the result
            symbol_names = {name for name, _, _, _ in result.symbols}
            assert "main" in symbol_names
            assert "lib_func" in symbol_names


class TestBuildWithImportsFunction:
    """Tests for the build_with_imports convenience function."""

    def test_build_to_ips(self) -> None:
        """Test building to IPS format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create source
            source = Path(tmpdir) / "main.s"
            source.write_text("""
*=0x8000
main:
    lda #0x42
    rts
""")

            output = Path(tmpdir) / "output.ips"

            result = build_with_imports(
                main_source=source,
                output_file=output,
                output_format="ips",
                output_dir=Path(tmpdir) / "obj",
            )

            assert isinstance(result, BuildResult)
            assert result.exit_code == 0
            assert output.exists()

            # Verify IPS header
            content = output.read_bytes()
            assert content[:5] == b"PATCH"

    def test_build_with_symbols(self) -> None:
        """Test building with predefined symbols."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create source that uses conditional compilation
            source = Path(tmpdir) / "main.s"
            source.write_text("""
*=0x8000
.if LANG == 1 {
main_fr:
    lda #0x01
    rts
} else {
main_en:
    lda #0x02
    rts
}
""")

            output = Path(tmpdir) / "output.ips"

            result = build_with_imports(
                main_source=source,
                output_file=output,
                output_format="ips",
                symbols={"LANG": 1},
                output_dir=Path(tmpdir) / "obj",
            )

            assert result.exit_code == 0
            assert output.exists()

    def test_build_with_module_paths(self) -> None:
        """Test building with custom module paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create module in a subdirectory
            modules_dir = Path(tmpdir) / "modules"
            modules_dir.mkdir()

            lib_source = modules_dir / "mylib.s"
            lib_source.write_text("""
lib_func:
    sta 0x2000
    rts
""")

            # Create main in a different directory
            src_dir = Path(tmpdir) / "src"
            src_dir.mkdir()

            main_source = src_dir / "main.s"
            main_source.write_text("""
.import "mylib"

*=0x8000
main:
    lda #0x42
    jsr.w lib_func
    rts
""")

            output = Path(tmpdir) / "output.ips"

            result = build_with_imports(
                main_source=main_source,
                output_file=output,
                output_format="ips",
                module_paths=[modules_dir],
                output_dir=Path(tmpdir) / "obj",
            )

            assert result.exit_code == 0
            assert output.exists()

    def test_build_result_has_symbol_map(self) -> None:
        """Test that BuildResult contains symbol_map after successful build."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "main.s"
            source.write_text("""
*=0x8000
main:
    lda #0x42
    rts
end_main:
""")

            output = Path(tmpdir) / "output.ips"

            result = build_with_imports(
                main_source=source,
                output_file=output,
                output_format="ips",
                output_dir=Path(tmpdir) / "obj",
            )

            assert result.exit_code == 0
            assert "main" in result.symbol_map
            assert result.symbol_map["main"] == 0x8000
            assert result.program is not None
