import tempfile
from pathlib import Path

import pytest

from a816.exceptions import UnresolvedSymbolError
from a816.linker import Linker
from a816.object_file import ObjectFile, SymbolType
from a816.program import Program


class TestSeparateCompilation:
    def test_compile_single_file_to_object(self) -> None:
        """Test compiling a single assembly file to object file"""

        # Create a simple assembly program
        asm_code = """main:
    lda #0x01
    sta 0x2000
    rts"""

        with tempfile.TemporaryDirectory() as tmpdir:
            # Write assembly file
            asm_file = Path(tmpdir) / "test.s"
            asm_file.write_text(asm_code)

            # Compile to object file
            obj_file = Path(tmpdir) / "test.o"
            program = Program()
            result = program.assemble_as_object(str(asm_file), obj_file)

            assert result == 0
            assert obj_file.exists()

            # Verify object file can be read
            obj = ObjectFile.read(str(obj_file))
            assert len(obj.code) > 0
            assert len(obj.symbols) > 0

            # Should have 'main' symbol
            symbol_names = [name for name, _, _, _ in obj.symbols]
            assert "main" in symbol_names

    def test_link_multiple_object_files(self) -> None:
        """Test linking multiple object files together"""

        # Create two assembly files - for now, avoid forward/external references
        # until we implement proper external symbol handling
        file1_code = """global_func:
    lda #0x01
    sta 0x2000
    rts"""

        file2_code = """main:
    lda #0x02
    rts"""

        with tempfile.TemporaryDirectory() as tmpdir:
            # Write assembly files
            asm_file1 = Path(tmpdir) / "file1.s"
            asm_file2 = Path(tmpdir) / "file2.s"
            asm_file1.write_text(file1_code)
            asm_file2.write_text(file2_code)

            # Compile to object files
            obj_file1 = Path(tmpdir) / "file1.o"
            obj_file2 = Path(tmpdir) / "file2.o"

            program1 = Program()
            program2 = Program()

            result1 = program1.assemble_as_object(str(asm_file1), obj_file1)
            result2 = program2.assemble_as_object(str(asm_file2), obj_file2)

            assert result1 == 0
            assert result2 == 0
            assert obj_file1.exists()
            assert obj_file2.exists()

            # Link object files
            obj1 = ObjectFile.read(str(obj_file1))
            obj2 = ObjectFile.read(str(obj_file2))

            linker = Linker([obj1, obj2])
            linked_obj = linker.link()

            # Verify linked object
            assert len(linked_obj.code) > 0
            assert len(linked_obj.symbols) > 0

            # Should have symbols from both files
            symbol_names = [name for name, _, _, _ in linked_obj.symbols]
            assert "main" in symbol_names
            assert "global_func" in symbol_names

    def test_create_ips_from_linked_objects(self) -> None:
        """Test creating IPS patch from linked object files"""

        asm_code = """*=0x8000
main:
    lda #0x42
    sta 0x2000
    rts"""

        with tempfile.TemporaryDirectory() as tmpdir:
            # Write assembly file
            asm_file = Path(tmpdir) / "test.s"
            asm_file.write_text(asm_code)

            # Compile to object file
            obj_file = Path(tmpdir) / "test.o"
            program = Program()
            result = program.assemble_as_object(str(asm_file), obj_file)
            assert result == 0

            # Link (single file)
            obj = ObjectFile.read(str(obj_file))
            linker = Linker([obj])
            linked_obj = linker.link()

            # Create IPS patch
            ips_file = Path(tmpdir) / "test.ips"
            result = program.link_as_patch(linked_obj, ips_file)
            assert result == 0
            assert ips_file.exists()

            # Verify IPS file has correct header
            with open(ips_file, "rb") as f:
                header = f.read(5)
                assert header == b"PATCH"

    def test_extern_symbol_declaration(self) -> None:
        """Test that extern symbols can be declared and compiled"""

        asm_code = """.extern external_func

main:
    lda #0x01
    rts"""

        with tempfile.TemporaryDirectory() as tmpdir:
            # Write assembly file
            asm_file = Path(tmpdir) / "test.s"
            asm_file.write_text(asm_code)

            # Compile to object file
            obj_file = Path(tmpdir) / "test.o"
            program = Program()
            result = program.assemble_as_object(str(asm_file), obj_file)

            assert result == 0
            assert obj_file.exists()

            # Verify object file contains external symbol
            obj = ObjectFile.read(str(obj_file))
            symbol_names = [name for name, _, symbol_type, _ in obj.symbols]
            symbol_types = {name: symbol_type for name, _, symbol_type, _ in obj.symbols}

            assert "external_func" in symbol_names
            assert symbol_types["external_func"] == SymbolType.EXTERNAL
            assert "main" in symbol_names
            assert symbol_types["main"] == SymbolType.GLOBAL

    def test_external_symbol_linking(self) -> None:
        """Test linking files with external symbol dependencies"""

        # File 1: provides external_func
        provider_code = """external_func:
    sta 0x2000
    rts"""

        # File 2: uses external_func
        consumer_code = """.extern external_func

main:
    lda #0x42
    jsr.w external_func
    rts"""

        with tempfile.TemporaryDirectory() as tmpdir:
            # Write assembly files
            provider_file = Path(tmpdir) / "provider.s"
            consumer_file = Path(tmpdir) / "consumer.s"
            provider_file.write_text(provider_code)
            consumer_file.write_text(consumer_code)

            # Compile to object files
            provider_obj = Path(tmpdir) / "provider.o"
            consumer_obj = Path(tmpdir) / "consumer.o"

            program1 = Program()
            program2 = Program()

            result1 = program1.assemble_as_object(str(provider_file), provider_obj)
            result2 = program2.assemble_as_object(str(consumer_file), consumer_obj)

            assert result1 == 0
            assert result2 == 0

            # Link object files
            provider_obj_data = ObjectFile.read(str(provider_obj))
            consumer_obj_data = ObjectFile.read(str(consumer_obj))

            linker = Linker([provider_obj_data, consumer_obj_data])
            linked_obj = linker.link()

            # Verify linking succeeded
            assert len(linked_obj.code) > 0

            # Verify all symbols are resolved
            symbol_names = [name for name, _, _, _ in linked_obj.symbols]
            assert "main" in symbol_names
            assert "external_func" in symbol_names

    def test_unresolved_external_symbol_error(self) -> None:
        """Test that unresolved external symbols cause linking to fail"""

        asm_code = """.extern missing_symbol

main:
    jsr.w missing_symbol
    rts"""

        with tempfile.TemporaryDirectory() as tmpdir:
            # Write assembly file
            asm_file = Path(tmpdir) / "test.s"
            asm_file.write_text(asm_code)

            # Compile to object file
            obj_file = Path(tmpdir) / "test.o"
            program = Program()
            result = program.assemble_as_object(str(asm_file), obj_file)
            assert result == 0

            # Try to link with unresolved external
            obj = ObjectFile.read(str(obj_file))
            linker = Linker([obj])

            # Should raise error for unresolved symbol
            with pytest.raises(UnresolvedSymbolError) as exc_info:
                linker.link()
            assert "missing_symbol" in exc_info.value.symbols

    def test_object_file_format_roundtrip(self) -> None:
        """Test that object file format can be written and read correctly"""

        from a816.object_file import RelocationType, SymbolSection, SymbolType

        # Create test object file data
        test_code = b"\xa9\x01\x8d\x00\x20\x60"  # lda #$01, sta $2000, rts
        test_symbols = [
            ("main", 0, SymbolType.GLOBAL, SymbolSection.CODE),
            (".local", 3, SymbolType.LOCAL, SymbolSection.CODE),
        ]
        test_relocations = [(1, "external_symbol", RelocationType.ABSOLUTE_16)]

        with tempfile.TemporaryDirectory() as tmpdir:
            obj_file = Path(tmpdir) / "test.o"

            # Create and write object file
            original_obj = ObjectFile(test_code, test_symbols, test_relocations)
            original_obj.write(str(obj_file))

            # Read it back
            loaded_obj = ObjectFile.read(str(obj_file))

            # Verify all data matches
            assert loaded_obj.code == test_code
            assert loaded_obj.symbols == test_symbols
            assert loaded_obj.relocations == test_relocations
