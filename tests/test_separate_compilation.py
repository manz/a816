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
            obj = ObjectFile.from_file(str(obj_file))
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
            obj1 = ObjectFile.from_file(str(obj_file1))
            obj2 = ObjectFile.from_file(str(obj_file2))

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
            obj = ObjectFile.from_file(str(obj_file))
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
            obj = ObjectFile.from_file(str(obj_file))
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
            provider_obj_data = ObjectFile.from_file(str(provider_obj))
            consumer_obj_data = ObjectFile.from_file(str(consumer_obj))

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
            obj = ObjectFile.from_file(str(obj_file))
            linker = Linker([obj])

            # Should raise error for unresolved symbol
            with pytest.raises(UnresolvedSymbolError) as exc_info:
                linker.link()
            assert "missing_symbol" in exc_info.value.symbols

    def test_constant_assignment_with_extern(self) -> None:
        """A constant defined as `name = extern_sym + N` defers to link time."""

        producer = """target:
    lda #0x42
    rts"""

        consumer = """.extern target

font_ptr = target + 0x10
font_high = (target >> 16) & 0xFF

main:
    lda.w #(font_ptr & 0xFFFF)
    sta 0x2000
    lda #font_high
    sta 0x2002
    rts"""

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / "producer.s").write_text(producer)
            (tmp / "consumer.s").write_text(consumer)

            program_a = Program()
            assert program_a.assemble_as_object(str(tmp / "producer.s"), tmp / "producer.o") == 0

            program_b = Program()
            assert program_b.assemble_as_object(str(tmp / "consumer.s"), tmp / "consumer.o") == 0

            obj_consumer = ObjectFile.from_file(str(tmp / "consumer.o"))
            assert ("font_ptr", "target + 0x10") in obj_consumer.aliases
            assert any(name == "font_high" for name, _ in obj_consumer.aliases)

            obj_producer = ObjectFile.from_file(str(tmp / "producer.o"))
            linker = Linker([obj_producer, obj_consumer], base_address=0x8000)
            linked = linker.link()
            assert "font_ptr" in linker.symbol_map
            assert linker.symbol_map["font_ptr"] == 0x8000 + 0x10
            assert "font_high" in linker.symbol_map
            assert linker.symbol_map["font_high"] == 0
            assert len(linked.code) > 0

    def test_anonymous_block_label_does_not_leak_as_global(self) -> None:
        """Labels declared inside `{}` blocks must export as LOCAL, never GLOBAL.

        Two scopes can both define `loop`/`exit` (e.g. multiple `{...}` blocks
        inside a module). They should not collide as GLOBAL exports.
        """
        source = """outer_func:
    nop
{
loop:
    nop
exit:
    rts
}
other_func:
{
loop:
    nop
    rts
}
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            asm = Path(tmpdir) / "blocks.s"
            obj = Path(tmpdir) / "blocks.o"
            asm.write_text(source)
            assert Program().assemble_as_object(str(asm), obj) == 0

            o = ObjectFile.from_file(str(obj))
            globals_ = {n for n, _, t, _ in o.symbols if t == SymbolType.GLOBAL}
            assert "outer_func" in globals_
            assert "other_func" in globals_
            # Block-scoped names never become GLOBAL — both `loop`/`exit`
            # come from `{}` scopes and would otherwise collide.
            assert "loop" not in globals_
            assert "exit" not in globals_

    def test_module_local_label_relocates_to_placement(self) -> None:
        """Internal absolute refs (PEA internal_label) update when the module moves."""
        source = """_entry:
    pea.w internal_label & 0xFFFF
    rts
internal_label:
    .db 0xAA
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            asm = Path(tmpdir) / "mod.s"
            obj = Path(tmpdir) / "mod.o"
            asm.write_text(source)
            assert Program().assemble_as_object(str(asm), obj) == 0

            o = ObjectFile.from_file(str(obj))
            assert any("internal_label" in expr for _, expr, _ in o.expression_relocations), (
                "expected an expression relocation referencing the internal label"
            )

            # Place the module at base 0x9000 and verify the patched 16-bit
            # operand reflects internal_label's new placement.
            linker = Linker([o], base_address=0x9000)
            linker.link()
            target = linker.symbol_map["internal_label"] & 0xFFFF
            patched = linker.linked_code[1] | (linker.linked_code[2] << 8)
            assert patched == target, f"PEA operand {patched:#x} does not match relocated label {target:#x}"

    def test_constant_aliases_to_local_label_get_relocated(self) -> None:
        """A `name = local_label + N` binding must defer to link time, not bake."""
        source = """ptr_low = target & 0xFFFF
entry:
    lda.w #ptr_low
    rts
target:
    .db 0xCC
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            asm = Path(tmpdir) / "alias.s"
            obj = Path(tmpdir) / "alias.o"
            asm.write_text(source)
            assert Program().assemble_as_object(str(asm), obj) == 0

            o = ObjectFile.from_file(str(obj))
            alias_names = {name for name, _ in o.aliases}
            assert "ptr_low" in alias_names, "ptr_low's RHS touches a local label, must be exported as alias"

            # Linker evaluates alias against the linked symbol map.
            linker = Linker([o], base_address=0x9000)
            linker.link()
            assert "target" in linker.symbol_map
            assert linker.symbol_map["ptr_low"] == linker.symbol_map["target"] & 0xFFFF

    def test_named_scope_labels_export_as_code_not_data(self) -> None:
        """`.scope foo { bar: ... }` exports `foo.bar` as a CODE label.

        Earlier the dotted name landed in the parent's symbols dict only,
        so the section heuristic mis-tagged it DATA — propagating the
        compile-time address as a constant across modules and breaking
        cross-module JSR targets after relocation.
        """
        source = """.scope render_allocator {
init_with_tile_id:
    sta.l 0x702f00
    rts
init:
    pha
    rts
}
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            asm = Path(tmpdir) / "scope.s"
            obj = Path(tmpdir) / "scope.o"
            asm.write_text(source)
            assert Program().assemble_as_object(str(asm), obj) == 0

            o = ObjectFile.from_file(str(obj))
            sym_table = {n: (a, t, s) for n, a, t, s in o.symbols}
            assert "render_allocator.init_with_tile_id" in sym_table
            _, _, section = sym_table["render_allocator.init_with_tile_id"]
            assert section.value == 0, (
                "scope-exported labels must be CODE so the linker rebases them, "
                "not DATA constants frozen at the module's compile-time base"
            )

            # Linker rebases against new module placement.
            linker = Linker([o], base_address=0x9000)
            linker.link()
            init_addr = linker.symbol_map["render_allocator.init_with_tile_id"]
            # init_with_tile_id is the first label, offset 0 → linked at base.
            assert init_addr & 0xFFFF == 0x9000

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
            loaded_obj = ObjectFile.from_file(str(obj_file))

            # Verify all data matches
            assert loaded_obj.code == test_code
            assert loaded_obj.symbols == test_symbols
            assert loaded_obj.relocations == test_relocations

    def test_create_sfc_from_linked_objects(self) -> None:
        """Test creating SFC file from linked object files"""

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
            obj = ObjectFile.from_file(str(obj_file))
            linker = Linker([obj])
            linked_obj = linker.link()

            # Create SFC file
            sfc_file = Path(tmpdir) / "test.sfc"
            result = program.link_as_sfc(linked_obj, sfc_file)
            assert result == 0
            assert sfc_file.exists()

            # Verify SFC file contains code
            with open(sfc_file, "rb") as f:
                content = f.read()
                assert len(content) > 0

    def test_link_as_patch_with_mapping(self) -> None:
        """Test creating IPS patch with different ROM mappings"""

        asm_code = """*=0x8000
main:
    lda #0x42
    rts"""

        with tempfile.TemporaryDirectory() as tmpdir:
            asm_file = Path(tmpdir) / "test.s"
            asm_file.write_text(asm_code)

            obj_file = Path(tmpdir) / "test.o"
            program = Program()
            result = program.assemble_as_object(str(asm_file), obj_file)
            assert result == 0

            obj = ObjectFile.from_file(str(obj_file))
            linker = Linker([obj])
            linked_obj = linker.link()

            # Test with different mappings
            for mapping in ["low", "low2", "high"]:
                ips_file = Path(tmpdir) / f"test_{mapping}.ips"
                result = program.link_as_patch(linked_obj, ips_file, mapping=mapping)
                assert result == 0
                assert ips_file.exists()

    def test_link_as_patch_with_copier_header(self) -> None:
        """Test creating IPS patch with copier header"""

        asm_code = """*=0x8000
main:
    lda #0x42
    rts"""

        with tempfile.TemporaryDirectory() as tmpdir:
            asm_file = Path(tmpdir) / "test.s"
            asm_file.write_text(asm_code)

            obj_file = Path(tmpdir) / "test.o"
            program = Program()
            result = program.assemble_as_object(str(asm_file), obj_file)
            assert result == 0

            obj = ObjectFile.from_file(str(obj_file))
            linker = Linker([obj])
            linked_obj = linker.link()

            ips_file = Path(tmpdir) / "test.ips"
            result = program.link_as_patch(linked_obj, ips_file, copier_header=True)
            assert result == 0
            assert ips_file.exists()

    def test_exports_symbol_file(self) -> None:
        """Test exporting symbol file for debugger"""

        asm_code = """*=0x8000
main:
    lda #0x42
helper:
    sta 0x2000
    rts"""

        with tempfile.TemporaryDirectory() as tmpdir:
            asm_file = Path(tmpdir) / "test.s"
            asm_file.write_text(asm_code)

            # Assemble to set up the symbol table
            ips_file = Path(tmpdir) / "test.ips"
            program = Program()
            result = program.assemble_as_patch(str(asm_file), ips_file)
            assert result == 0

            # Export symbols
            sym_file = Path(tmpdir) / "test.sym"
            program.exports_symbol_file(str(sym_file))
            assert sym_file.exists()

            # Verify symbol file format
            content = sym_file.read_text()
            assert "[labels]" in content
            assert "main" in content
            assert "helper" in content

    def test_get_physical_address(self) -> None:
        """Test physical address calculation"""

        program = Program()
        # Set up resolver with a known address
        program.resolver.pc = 0x8000

        # Default mapping should work
        physical = program.get_physical_address(0x8000)
        assert physical is not None

    def test_get_physical_address_error(self) -> None:
        """Test physical address error for unmapped address"""

        program = Program()

        # Address with no physical mapping should raise KeyError (unmapped bank)
        with pytest.raises(KeyError):
            program.get_physical_address(0xFFFFFF)

    def test_link_as_patch_empty_code(self) -> None:
        """Test link_as_patch with empty linked object"""

        from a816.object_file import ObjectFile

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create empty object file
            empty_obj = ObjectFile(b"", [], [])

            ips_file = Path(tmpdir) / "test.ips"
            program = Program()
            result = program.link_as_patch(empty_obj, ips_file)
            assert result == 0

            # IPS file should still be valid (just header + EOF)
            with open(ips_file, "rb") as f:
                content = f.read()
                assert content.startswith(b"PATCH")
                assert content.endswith(b"EOF")

    def test_link_as_sfc_empty_code(self) -> None:
        """Test link_as_sfc with empty linked object"""

        from a816.object_file import ObjectFile

        with tempfile.TemporaryDirectory() as tmpdir:
            empty_obj = ObjectFile(b"", [], [])

            sfc_file = Path(tmpdir) / "test.sfc"
            program = Program()
            result = program.link_as_sfc(empty_obj, sfc_file)
            assert result == 0
            assert sfc_file.exists()

    def test_multi_region_module_compiles_to_separate_regions(self) -> None:
        """A module with two `*=` directives produces two regions in the .o."""
        source = """*=0x008000
first_label:
    nop
*=0x018000
second_label:
    nop
    nop
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            asm = Path(tmpdir) / "multi.s"
            obj = Path(tmpdir) / "multi.o"
            asm.write_text(source)
            assert Program().assemble_as_object(str(asm), obj) == 0

            o = ObjectFile.from_file(str(obj))
            assert o.relocatable is False, "explicit *= must mark module as pinned"
            bases = [r.base_address for r in o.regions]
            assert 0x008000 in bases
            assert 0x018000 in bases
            by_base = {r.base_address: r for r in o.regions}
            assert by_base[0x008000].code == b"\xea"
            assert by_base[0x018000].code == b"\xea\xea"

            by_name = {name: address for name, address, _, _ in o.symbols}
            assert by_name["first_label"] == 0x008000
            assert by_name["second_label"] == 0x018000

    def test_multi_region_link_keeps_regions_at_declared_bases(self) -> None:
        """Pinned multi-region modules ignore the linker base_address."""
        source = """*=0x008000
entry:
    nop
*=0x018000
data:
    .db 0x42
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            asm = Path(tmpdir) / "mod.s"
            obj = Path(tmpdir) / "mod.o"
            asm.write_text(source)
            assert Program().assemble_as_object(str(asm), obj) == 0
            o = ObjectFile.from_file(str(obj))

            linker = Linker([o], base_address=0x9000)
            linked = linker.link()
            assert {r.base_address for r in linked.regions} == {0x008000, 0x018000}
            assert linker.symbol_map["entry"] == 0x008000
            assert linker.symbol_map["data"] == 0x018000

    def test_multi_region_cross_region_symbol_reloc(self) -> None:
        """A reference from region A to a label in region B patches the right region."""
        source = """*=0x008000
entry:
    .dw far_label & 0xFFFF
*=0x018000
far_label:
    .db 0x77
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            asm = Path(tmpdir) / "cross.s"
            obj = Path(tmpdir) / "cross.o"
            asm.write_text(source)
            assert Program().assemble_as_object(str(asm), obj) == 0
            o = ObjectFile.from_file(str(obj))

            linker = Linker([o], base_address=0)
            linked = linker.link()
            by_base = {r.base_address: r for r in linked.regions}
            patched = by_base[0x008000].code
            target = linker.symbol_map["far_label"] & 0xFFFF
            assert patched[0] | (patched[1] << 8) == target

    def test_loser_import_does_not_shift_surrounding_layout(self) -> None:
        """A skipped duplicate `.import` must consume zero PC — not its size.

        Regression: the loser `.import` used to advance both pc_after and
        the emit driver's PC by `len(region 0)`. When the loser sat in
        a source-inlined patch file (e.g. ff4-modules' battle/sram.s
        carrying `.import "dakuten"` while ff4.s also imports dakuten
        later), the surrounding inline source ended up shifted forward
        by the module's size, producing a phantom gap in the IPS that
        the CPU later executed as garbage.

        Layout under the fix: the `.import` site between the two inline
        labels takes zero space; the labels straddle exactly the inline
        bytes that follow, with the module placed once at its winning
        site.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            mod = tmp / "mod.s"
            mod.write_text("payload:\n    .db 0xCC, 0xCC, 0xCC, 0xCC\n")
            assert Program().assemble_as_object(str(mod), tmp / "mod.o") == 0

            inline_patch = tmp / "patch.s"
            inline_patch.write_text('before_import:\n    .db 0xAA\n.import "mod"\nafter_import:\n    .db 0xBB\n')

            main = tmp / "main.s"
            # *=0x008000  -> inline patch (loser .import inside)
            #             -> *=0x009000 -> winner .import "mod"
            main.write_text('*=0x008000\n.include "patch.s"\n*=0x009000\n.import "mod"\n')

            program = Program()
            program.add_module_path(tmp)
            program.add_include_path(tmp)
            ips = tmp / "out.ips"
            assert program.assemble_as_patch(str(main), ips) == 0

            scope = program.resolver.current_scope
            before = scope.labels.get("before_import")
            after = scope.labels.get("after_import")
            assert before is not None and after is not None
            # Two labels with one inline byte between them — and crucially
            # NOT separated by the (4-byte) module size. The loser must
            # not advance the importer's PC.
            assert (after.logical_value if hasattr(after, "logical_value") else after) - (
                before.logical_value if hasattr(before, "logical_value") else before
            ) == 1, "loser .import must consume zero PC space"

            # Module payload still appears once, at the winning *=0x009000 site.
            assert ips.read_bytes().count(b"\xcc\xcc\xcc\xcc") == 1

    def test_skipped_import_flushes_pending_block_before_advancing(self) -> None:
        """Inline bytes around a skipped duplicate .import keep their addresses.

        Regression: when an earlier `.import` was demoted to symbol-only
        (because a later `.import` of the same module became the winner),
        the emit driver advanced the PC by the module's size *without*
        flushing the pending current_block first. The next flush keyed
        the previously-accumulated bytes against the post-skip address
        instead of where they were really emitted, so any inline code
        sitting between two .imports overwrote unrelated ROM downstream.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            mod = tmp / "mod.s"
            mod.write_text("payload:\n    .db 0xCC, 0xCC, 0xCC, 0xCC\n")
            assert Program().assemble_as_object(str(mod), tmp / "mod.o") == 0

            # *=0x008000  -> 3 inline bytes (0xAA AA AA) -> first .import
            # (skipped) -> 2 more inline bytes (0xBB BB) -> *=0x009000 ->
            # second .import (emits payload). The pre-skip flush bug
            # would push the BB BB pair past the module size and into
            # ROM that was meant to stay untouched.
            main = tmp / "main.s"
            main.write_text(
                '*=0x008000\n.db 0xAA, 0xAA, 0xAA\n.import "mod"\n.db 0xBB, 0xBB\n*=0x009000\n.import "mod"\n'
            )
            ips = tmp / "out.ips"
            program = Program()
            program.add_module_path(tmp)
            assert program.assemble_as_patch(str(main), ips) == 0

            content = ips.read_bytes()
            # Module payload appears once, at the second .import site
            # (SNES 0x009000 → LoROM physical 0x000800).
            assert content.count(b"\xcc\xcc\xcc\xcc") == 1
            # Inline AA AA AA must land at SNES 0x008000 → physical 0,
            # and BB BB at PC 3 + len(mod region) (physical 0x000007 in
            # LoROM since region is 4 bytes).
            records = self._parse_ips_records(content)
            placements = {phys: data for phys, data in records}
            assert placements.get(0x000000, b"")[:3] == b"\xaa\xaa\xaa"
            # BB BB must immediately follow the module-sized gap, not
            # land somewhere far away thanks to a stale current_block_addr.
            bb_seen = any(b"\xbb\xbb" in data for _, data in records)
            assert bb_seen, "inline bytes after skipped .import did not land in IPS"

    @staticmethod
    def _parse_ips_records(content: bytes) -> list[tuple[int, bytes]]:
        """Walk an IPS file and return [(physical_offset, data), ...]."""
        out: list[tuple[int, bytes]] = []
        i = 5  # skip "PATCH"
        while i < len(content) - 3:
            if content[i : i + 3] == b"EOF":
                break
            offset = int.from_bytes(content[i : i + 3], "big")
            i += 3
            size = int.from_bytes(content[i : i + 2], "big")
            i += 2
            if size == 0:
                # RLE record — skip; not produced by a816's IPS writer.
                i += 3
                continue
            out.append((offset, content[i : i + size]))
            i += size
        return out

    def test_duplicate_import_emits_module_bytes_once_at_last_site(self) -> None:
        """Two .import "foo" statements emit foo's bytes once, at the second site.

        Regression: the same module being imported in an .include'd patch
        file and again in the main source caused .import to materialize
        the module's bytes at BOTH sites. The earlier emission landed at
        whatever PC the prior *= happened to leave behind, clobbering
        unrelated ROM. .import is now idempotent — only the last
        occurrence emits, the earlier ones still publish the symbols via
        pc_after so subsequent code can reference the module.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            mod = tmp / "mod.s"
            mod.write_text("payload:\n    .db 0x42, 0x42, 0x42, 0x42\n")
            assert Program().assemble_as_object(str(mod), tmp / "mod.o") == 0

            main = tmp / "main.s"
            main.write_text(
                "*=0x008000\n"
                '.import "mod"\n'  # first import — symbol-only
                "*=0x009000\n"
                '.import "mod"\n'  # second import — emits payload here
            )
            ips = tmp / "out.ips"
            program = Program()
            program.add_module_path(tmp)
            assert program.assemble_as_patch(str(main), ips) == 0

            content = ips.read_bytes()
            assert content.count(b"\x42\x42\x42\x42") == 1, (
                f"module bytes must appear exactly once in the IPS, got {content.count(b'\\x42\\x42\\x42\\x42')}"
            )

    def test_multiple_intra_region_expression_relocations_get_distinct_offsets(self) -> None:
        """Three references to the same forward label must record three offsets.

        Regression: ObjectWriter.relocation_offset() used to read only
        _region_bytes_emitted, which only advances on write_block. Inside
        a single region, every reloc emitted before the next *= boundary
        therefore reported the same offset (the last flushed position),
        and the linker patched only one of them — the other call sites
        kept the compile-time placeholder and jumped to garbage.
        """
        source = """\
caller1:
    jsr.w target
caller2:
    jsr.w target
caller3:
    jsr.w target
target:
    rts
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            asm = Path(tmpdir) / "fanout.s"
            obj = Path(tmpdir) / "fanout.o"
            asm.write_text(source)
            assert Program().assemble_as_object(str(asm), obj) == 0
            o = ObjectFile.from_file(str(obj))
            offsets = sorted(off for r in o.regions for off, expr, _ in r.expression_relocations if expr == "target")
            assert len(offsets) == 3, f"expected 3 expression relocations, got {len(offsets)}: {offsets}"
            assert len(set(offsets)) == 3, f"expected 3 distinct offsets, got {offsets}"
            # Each jsr.w is 3 bytes (opcode + 2-byte operand). Reloc points
            # to the operand byte, so offsets are 1, 4, 7.
            assert offsets == [1, 4, 7]

    def test_multi_region_module_writes_multiple_ips_blocks(self) -> None:
        """Linking a multi-region module emits one IPS block per region.

        IPS records are keyed by physical (file) offset, so logical
        SNES addresses must go through the LoROM bus mapping at the
        write boundary — the same translation Program.emit() applies
        via resolver.pc.
        """
        source = """*=0x008000
.db 0x11, 0x22
*=0x028000
.db 0x33
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            asm = Path(tmpdir) / "two.s"
            obj = Path(tmpdir) / "two.o"
            asm.write_text(source)
            assert Program().assemble_as_object(str(asm), obj) == 0
            linked = Linker([ObjectFile.from_file(str(obj))]).link()

            ips_path = Path(tmpdir) / "out.ips"
            assert Program().link_as_patch(linked, ips_path) == 0
            content = ips_path.read_bytes()
            assert content.startswith(b"PATCH")
            # Headers are 24-bit big-endian physical offsets:
            #   *=0x008000 (LoROM) → physical 0x000000
            #   *=0x028000 (LoROM) → physical 0x010000
            # Length 2 then payload "\x11\x22" for region 0,
            # length 1 then "\x33" for region 1.
            assert b"\x00\x00\x00\x00\x02\x11\x22" in content
            assert b"\x01\x00\x00\x00\x01\x33" in content
