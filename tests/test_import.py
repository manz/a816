"""Tests for the .import directive and module system."""

import tempfile
from pathlib import Path

import pytest

from a816.linker import Linker
from a816.object_file import ObjectFile, SymbolType
from a816.parse.ast.nodes import ImportAstNode
from a816.parse.mzparser import MZParser
from a816.parse.nodes import ExternNode
from a816.program import Program


class TestImportParsing:
    """Tests for parsing the .import directive."""

    def test_parse_import_directive(self) -> None:
        """Test that .import "module" is parsed correctly."""
        source = '.import "vwf"'
        result = MZParser.parse_as_ast(source, "test.s")

        assert len(result.nodes) == 1
        assert isinstance(result.nodes[0], ImportAstNode)
        assert result.nodes[0].module_name == "vwf"

    def test_parse_import_with_path(self) -> None:
        """Test that .import "path/to/module" is parsed correctly."""
        source = '.import "battle/sram"'
        result = MZParser.parse_as_ast(source, "test.s")

        assert len(result.nodes) == 1
        assert isinstance(result.nodes[0], ImportAstNode)
        assert result.nodes[0].module_name == "battle/sram"

    def test_parse_multiple_imports(self) -> None:
        """Test multiple .import directives."""
        source = """
.import "vwf"
.import "dialog"
.import "utils/math"
"""
        result = MZParser.parse_as_ast(source, "test.s")

        import_nodes = [n for n in result.nodes if isinstance(n, ImportAstNode)]
        assert len(import_nodes) == 3
        assert import_nodes[0].module_name == "vwf"
        assert import_nodes[1].module_name == "dialog"
        assert import_nodes[2].module_name == "utils/math"

    def test_import_ast_representation(self) -> None:
        """Test ImportAstNode representation methods."""
        source = '.import "vwf"'
        result = MZParser.parse_as_ast(source, "test.s")
        node = result.nodes[0]

        assert isinstance(node, ImportAstNode)
        assert node.to_representation() == ("import", "vwf")
        assert node.to_canonical() == '.import "vwf"'


class TestImportCodeGen:
    """Tests for code generation from .import directive."""

    def test_import_generates_extern_nodes_from_object_file(self) -> None:
        """Test that .import generates ExternNode for each symbol in .o file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a module with public and private symbols
            # Symbols starting with _ are private (local to module)
            module_code = """
public_func:
    lda #0x01
    rts

public_data = 0x1000

_helper:
    rts
"""
            module_file = Path(tmpdir) / "mymodule.s"
            module_file.write_text(module_code)

            # Compile the module to .o file
            module_obj = Path(tmpdir) / "mymodule.o"
            program = Program()
            result = program.assemble_as_object(str(module_file), module_obj)
            assert result == 0

            # Create a consumer that imports the module
            consumer_code = '.import "mymodule"'
            consumer_file = Path(tmpdir) / "consumer.s"
            consumer_file.write_text(consumer_code)

            # Parse and generate code for consumer
            consumer_program = Program()
            consumer_program.add_module_path(tmpdir)

            with open(consumer_file, encoding="utf-8") as f:
                content = f.read()
                _, nodes = consumer_program.parser.parse(content, str(consumer_file))

            # Should have generated ExternNode for public symbols
            extern_nodes = [n for n in nodes if isinstance(n, ExternNode)]
            extern_names = {n.symbol_name for n in extern_nodes}

            assert "public_func" in extern_names
            assert "public_data" in extern_names
            # Private symbol (starts with _) should not be exported
            assert "_helper" not in extern_names

    def test_import_generates_extern_nodes_from_source_file(self) -> None:
        """Test that .import falls back to source file when .o doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a module source file (no .o file)
            # Symbols starting with _ are private
            module_code = """
vwf_init:
    lda #0x01
    rts

vwf_render:
    lda #0x02
    rts

_private_helper:
    rts
"""
            module_file = Path(tmpdir) / "vwf.s"
            module_file.write_text(module_code)

            # Create a consumer that imports the module
            consumer_code = '.import "vwf"'
            consumer_file = Path(tmpdir) / "main.s"
            consumer_file.write_text(consumer_code)

            # Parse and generate code for consumer
            program = Program()
            program.add_module_path(tmpdir)

            with open(consumer_file, encoding="utf-8") as f:
                content = f.read()
                _, nodes = program.parser.parse(content, str(consumer_file))

            # Should have generated ExternNode for public symbols
            extern_nodes = [n for n in nodes if isinstance(n, ExternNode)]
            extern_names = {n.symbol_name for n in extern_nodes}

            assert "vwf_init" in extern_names
            assert "vwf_render" in extern_names
            # Private symbol (starts with _) should not be exported
            assert "_private_helper" not in extern_names


class TestImportWithLinking:
    """Tests for .import integration with the linker."""

    def test_import_and_link_workflow(self) -> None:
        """Test complete workflow: import module, compile, and link."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create the module
            module_code = """
module_func:
    sta 0x2000
    rts
"""
            module_file = Path(tmpdir) / "mylib.s"
            module_file.write_text(module_code)

            # Compile module to .o
            module_obj_path = Path(tmpdir) / "mylib.o"
            module_program = Program()
            result = module_program.assemble_as_object(str(module_file), module_obj_path)
            assert result == 0

            # Create consumer that imports and uses the module
            consumer_code = """
.import "mylib"

main:
    lda #0x42
    jsr.w module_func
    rts
"""
            consumer_file = Path(tmpdir) / "main.s"
            consumer_file.write_text(consumer_code)

            # Compile consumer to .o
            consumer_obj_path = Path(tmpdir) / "main.o"
            consumer_program = Program()
            consumer_program.add_module_path(tmpdir)
            result = consumer_program.assemble_as_object(str(consumer_file), consumer_obj_path)
            assert result == 0

            # Link both object files
            module_obj = ObjectFile.from_file(str(module_obj_path))
            consumer_obj = ObjectFile.from_file(str(consumer_obj_path))

            linker = Linker([module_obj, consumer_obj])
            linked = linker.link()

            # Verify linking succeeded
            assert len(linked.code) > 0
            symbol_names = {name for name, _, _, _ in linked.symbols}
            assert "main" in symbol_names
            assert "module_func" in symbol_names

    def test_import_multiple_modules_and_link(self) -> None:
        """Test importing multiple modules and linking."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create module A
            module_a_code = """
func_a:
    lda #0x01
    rts
"""
            module_a_file = Path(tmpdir) / "module_a.s"
            module_a_file.write_text(module_a_code)

            # Create module B
            module_b_code = """
func_b:
    lda #0x02
    rts
"""
            module_b_file = Path(tmpdir) / "module_b.s"
            module_b_file.write_text(module_b_code)

            # Compile modules to .o
            module_a_obj_path = Path(tmpdir) / "module_a.o"
            module_b_obj_path = Path(tmpdir) / "module_b.o"

            Program().assemble_as_object(str(module_a_file), module_a_obj_path)
            Program().assemble_as_object(str(module_b_file), module_b_obj_path)

            # Create consumer that imports both modules
            consumer_code = """
.import "module_a"
.import "module_b"

main:
    jsr.w func_a
    jsr.w func_b
    rts
"""
            consumer_file = Path(tmpdir) / "main.s"
            consumer_file.write_text(consumer_code)

            # Compile consumer to .o
            consumer_obj_path = Path(tmpdir) / "main.o"
            consumer_program = Program()
            consumer_program.add_module_path(tmpdir)
            result = consumer_program.assemble_as_object(str(consumer_file), consumer_obj_path)
            assert result == 0

            # Link all object files
            module_a_obj = ObjectFile.from_file(str(module_a_obj_path))
            module_b_obj = ObjectFile.from_file(str(module_b_obj_path))
            consumer_obj = ObjectFile.from_file(str(consumer_obj_path))

            linker = Linker([module_a_obj, module_b_obj, consumer_obj])
            linked = linker.link()

            # Verify linking succeeded
            symbol_names = {name for name, _, _, _ in linked.symbols}
            assert "main" in symbol_names
            assert "func_a" in symbol_names
            assert "func_b" in symbol_names


class TestModulePath:
    """Tests for module search path functionality."""

    def test_add_module_path(self) -> None:
        """Test adding module search paths."""
        program = Program()

        program.add_module_path("/path/to/modules")
        program.add_module_path(Path("/another/path"))

        assert len(program.resolver.context.module_paths) == 2
        assert Path("/path/to/modules") in program.resolver.context.module_paths
        assert Path("/another/path") in program.resolver.context.module_paths

    def test_add_duplicate_module_path(self) -> None:
        """Test that duplicate paths are not added."""
        program = Program()

        program.add_module_path("/path/to/modules")
        program.add_module_path("/path/to/modules")

        assert len(program.resolver.context.module_paths) == 1

    def test_module_resolved_from_search_path(self) -> None:
        """Test that modules are resolved from search paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a subdirectory with the module
            modules_dir = Path(tmpdir) / "modules"
            modules_dir.mkdir()

            module_code = """
lib_func:
    rts
"""
            module_file = modules_dir / "mylib.s"
            module_file.write_text(module_code)

            # Create consumer in a different directory
            src_dir = Path(tmpdir) / "src"
            src_dir.mkdir()

            consumer_code = '.import "mylib"'
            consumer_file = src_dir / "main.s"
            consumer_file.write_text(consumer_code)

            # With module path, it should succeed
            program_with_path = Program()
            program_with_path.add_module_path(modules_dir)

            with open(consumer_file, encoding="utf-8") as f:
                content = f.read()
                _, nodes = program_with_path.parser.parse(content, str(consumer_file))

            extern_nodes = [n for n in nodes if isinstance(n, ExternNode)]
            extern_names = {n.symbol_name for n in extern_nodes}
            assert "lib_func" in extern_names


class TestImportPrivateSymbols:
    """Tests for private symbol handling in imports.

    Convention: symbols starting with underscore (_) are private/local.
    """

    def test_underscore_prefixed_labels_are_private(self) -> None:
        """Test that labels starting with _ are not exported (local)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_code = """
public_label:
    lda #0x01
_private_label:
    sta 0x2000
    rts
"""
            module_file = Path(tmpdir) / "module.s"
            module_file.write_text(module_code)

            # Compile to object file
            obj_path = Path(tmpdir) / "module.o"
            program = Program()
            program.assemble_as_object(str(module_file), obj_path)

            # Check exported symbols
            obj = ObjectFile.from_file(str(obj_path))
            global_symbols = [name for name, _, sym_type, _ in obj.symbols if sym_type == SymbolType.GLOBAL]
            local_symbols = [name for name, _, sym_type, _ in obj.symbols if sym_type == SymbolType.LOCAL]

            assert "public_label" in global_symbols
            assert "_private_label" in local_symbols
            assert "_private_label" not in global_symbols

    def test_import_only_gets_public_symbols(self) -> None:
        """Test that .import only creates extern nodes for public symbols."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_code = """
public_func:
    lda #0x01
_private_helper:
    lda #0x02
    rts

PUBLIC_CONST = 0x1000
_private_const = 0x2000
"""
            module_file = Path(tmpdir) / "module.s"
            module_file.write_text(module_code)

            # Compile module
            obj_path = Path(tmpdir) / "module.o"
            Program().assemble_as_object(str(module_file), obj_path)

            # Import the module
            consumer_code = '.import "module"'
            consumer_file = Path(tmpdir) / "main.s"
            consumer_file.write_text(consumer_code)

            program = Program()
            program.add_module_path(tmpdir)

            with open(consumer_file, encoding="utf-8") as f:
                content = f.read()
                _, nodes = program.parser.parse(content, str(consumer_file))

            extern_nodes = [n for n in nodes if isinstance(n, ExternNode)]
            extern_names = {n.symbol_name for n in extern_nodes}

            # Public symbols should be imported
            assert "public_func" in extern_names
            assert "PUBLIC_CONST" in extern_names

            # Private symbols (starting with _) should not be imported
            assert "_private_helper" not in extern_names
            assert "_private_const" not in extern_names


class TestImportErrors:
    """Tests for error handling in imports."""

    def test_import_nonexistent_module_raises_error(self) -> None:
        """Test that importing a non-existent module raises an error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            consumer_code = '.import "nonexistent_module"'
            consumer_file = Path(tmpdir) / "main.s"
            consumer_file.write_text(consumer_code)

            program = Program()

            with pytest.raises(Exception) as exc_info:
                with open(consumer_file, encoding="utf-8") as f:
                    content = f.read()
                    program.parser.parse(content, str(consumer_file))

            assert "nonexistent_module" in str(exc_info.value)


class TestImportExpressionRelocations:
    """Tests for expression relocation handling in imported modules."""

    def test_expression_relocations_applied_in_direct_assembly(self) -> None:
        """Test that expression relocations in imported modules are applied.

        When a module references external symbols with expressions like
        'external_table + 2', these should be resolved at import time.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create module that references an external symbol with expression
            module_code = """
.extern data_table

get_data:
    lda.l data_table        ; Should be replaced with actual address
    rts

get_data_plus2:
    lda.l data_table + 2    ; Expression relocation
    rts
"""
            module_file = Path(tmpdir) / "module.s"
            module_file.write_text(module_code)

            # Compile module to .o
            obj_path = Path(tmpdir) / "module.o"
            Program().assemble_as_object(str(module_file), obj_path)

            # Verify the object file has expression relocations
            obj = ObjectFile.from_file(str(obj_path))
            assert len(obj.expression_relocations) >= 1, "Module should have expression relocations"

            # Create main file that defines the external symbol and imports module
            main_code = """
*=0x8000
.import "module"

data_table:
    .db 0x11, 0x22, 0x33, 0x44

main:
    jsr.w get_data
    jsr.w get_data_plus2
    rts
"""
            main_file = Path(tmpdir) / "main.s"
            main_file.write_text(main_code)

            # Assemble in direct assembly mode
            output_path = Path(tmpdir) / "output.sfc"
            program = Program()
            program.add_module_path(tmpdir)
            from a816.context import AssemblyMode

            program.resolver.context.mode = AssemblyMode.DIRECT

            result = program.assemble(str(main_file), output_path)
            assert result == 0, "Assembly should succeed"

            # Read the output and verify relocations were applied
            with open(output_path, "rb") as f:
                code = f.read()

            # The module code should have the actual address, not 0x000000
            # data_table is at 0x8000 + module_size (module comes first due to import)
            # Find the LDA.L instruction bytes and verify they're not zero
            # LDA.L opcode is 0xAF, followed by 3-byte address

            # Look for 0xAF (LDA.L) followed by non-zero address
            found_valid_lda = False
            for i in range(len(code) - 4):
                if code[i] == 0xAF:  # LDA.L opcode
                    addr = code[i + 1] | (code[i + 2] << 8) | (code[i + 3] << 16)
                    if addr != 0 and addr >= 0x8000:
                        found_valid_lda = True
                        break

            assert found_valid_lda, "Expression relocations should be applied (LDA.L should have valid address)"

    def test_multiple_expression_relocations(self) -> None:
        """Test multiple expression relocations in a single module."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Module with multiple external references
            module_code = """
.extern table_a
.extern table_b

func1:
    ldx.w table_a
    ldy.w table_b
    rts

func2:
    lda.l table_a + 4
    sta.l table_b + 8
    rts
"""
            module_file = Path(tmpdir) / "multi_reloc.s"
            module_file.write_text(module_code)

            obj_path = Path(tmpdir) / "multi_reloc.o"
            Program().assemble_as_object(str(module_file), obj_path)

            # Verify multiple relocations exist
            obj = ObjectFile.from_file(str(obj_path))
            expr_reloc_count = len(obj.expression_relocations)
            assert expr_reloc_count >= 2, f"Expected multiple relocations, got {expr_reloc_count}"

            # Main file
            main_code = """
*=0x8000
.import "multi_reloc"

table_a:
    .dw 0x1111, 0x2222, 0x3333, 0x4444

table_b:
    .dw 0xAAAA, 0xBBBB, 0xCCCC, 0xDDDD, 0xEEEE

start:
    jsr.w func1
    jsr.w func2
    rts
"""
            main_file = Path(tmpdir) / "main.s"
            main_file.write_text(main_code)

            output_path = Path(tmpdir) / "output.sfc"
            program = Program()
            program.add_module_path(tmpdir)
            from a816.context import AssemblyMode

            program.resolver.context.mode = AssemblyMode.DIRECT

            result = program.assemble(str(main_file), output_path)
            assert result == 0, "Assembly should succeed with multiple relocations"
