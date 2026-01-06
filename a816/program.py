import logging
from pathlib import Path

from a816.cpu.cpu_65c816 import RomType
from a816.object_file import ObjectFile, SymbolSection, SymbolType
from a816.parse.mzparser import MZParser
from a816.parse.nodes import (
    BinaryNode,
    CodePositionNode,
    IncludeIpsNode,
    LabelNode,
    NodeError,
    NodeProtocol,
    SymbolNode,
)
from a816.symbols import Resolver
from a816.writers import IPSWriter, ObjectWriter, SFCWriter, Writer

logger = logging.getLogger("a816")


class Program:
    def __init__(self, parser: MZParser | None = None, dump_symbols: bool = False):
        self.resolver = Resolver()
        self.logger = logging.getLogger("x816")
        self.dump_symbols = dump_symbols
        self.parser = parser or MZParser(self.resolver)

    def get_physical_address(self, logical_address: int) -> int:
        physical_address = self.resolver.get_bus().get_address(logical_address).physical
        if physical_address is not None:
            return physical_address
        else:
            raise RuntimeError(f"{logical_address} has no physical address.")

    def resolver_reset(self) -> None:
        """resets the resolver"""
        self.resolver.pc = 0x000000
        self.resolver.last_used_scope = 0
        self.resolver.current_scope = self.resolver.scopes[0]

    def resolve_labels(self, program_nodes: list[NodeProtocol]) -> None:
        """
        Resolves the labels
        :param program_nodes:
        :return:
        """
        self.resolver.last_used_scope = 0

        previous_pc = self.resolver.reloc_address

        for node in program_nodes:
            if isinstance(node, SymbolNode):
                continue
            previous_pc = node.pc_after(previous_pc)

        self.resolver_reset()

        previous_pc = self.resolver.reloc_address
        for node in program_nodes:
            if isinstance(node, LabelNode) or isinstance(node, BinaryNode):
                continue
            previous_pc = node.pc_after(previous_pc)
        self.resolver_reset()

    def emit(self, program: list[NodeProtocol], writer: Writer) -> None:
        current_block = b""
        current_block_addr = self.resolver.pc
        for node in program:
            node_bytes = node.emit(self.resolver.reloc_address)

            if node_bytes:
                current_block += node_bytes
                self.resolver.pc += len(node_bytes)
                self.resolver.reloc_address += len(node_bytes)

            if isinstance(node, CodePositionNode):  # or isinstance(node, RelocationAddressNode):
                if len(current_block) > 0:
                    writer.write_block(current_block, current_block_addr)
                current_block_addr = self.resolver.pc
                current_block = b""

            if isinstance(node, IncludeIpsNode):
                for block_addr, block in node.blocks:
                    writer.write_block(block, block_addr)

        if len(current_block) > 0:
            writer.write_block(current_block, current_block_addr)

    def emit_with_relocations(self, program: list[NodeProtocol], object_writer: ObjectWriter) -> None:
        """
        Emit code while tracking symbols that need relocation for object files.
        This is similar to emit() but also identifies and records relocations.
        """
        current_block = b""
        current_offset = 0

        # For object files, we don't use absolute addresses - everything is relative to start of object
        original_pc = self.resolver.pc
        original_reloc = self.resolver.reloc_address
        self.resolver.pc = 0
        self.resolver.reloc_address = self.resolver.get_bus().get_address(0)

        try:
            for node in program:
                # TODO: Add logic to detect external symbol references and create relocations
                # For now, emit normally - full relocation detection would require
                # analyzing the node types and their symbol references
                node_bytes = node.emit(self.resolver.reloc_address)

                if node_bytes:
                    current_block += node_bytes
                    self.resolver.pc += len(node_bytes)
                    self.resolver.reloc_address += len(node_bytes)

                if isinstance(node, CodePositionNode):
                    if len(current_block) > 0:
                        object_writer.write_block(current_block, current_offset)
                        current_offset += len(current_block)
                    current_block = b""

                if isinstance(node, IncludeIpsNode):
                    for _block_addr, block in node.blocks:
                        object_writer.write_block(block, current_offset)
                        current_offset += len(block)

            if len(current_block) > 0:
                object_writer.write_block(current_block, current_offset)

        finally:
            # Restore original state
            self.resolver.pc = original_pc
            self.resolver.reloc_address = original_reloc

    def assemble_string_with_emitter(self, input_program: str, filename: str, emitter: Writer) -> str | None:
        error, nodes = self.parser.parse(input_program, filename)

        if error is not None:
            return error

        self.logger.info("Resolving labels")
        self.resolve_labels(nodes)

        if self.dump_symbols:
            self.resolver.dump_symbol_map()

        self.emit(nodes, emitter)

        return None

    def assemble_with_emitter(self, asm_file: str, emitter: Writer) -> int:
        try:
            with open(asm_file, encoding="utf-8") as f:
                input_program = f.read()
                try:
                    error = self.assemble_string_with_emitter(input_program, asm_file, emitter)
                    if error is not None:
                        logger.error(error)
                        exit(128)
                except NodeError as e:
                    logger.error(str(e))
                    exit(128)

        except RuntimeError as e:
            self.logger.error(e)
            return -1

        self.logger.info("Success !")
        return 0

    def assemble(self, asm_file: str, sfc_file: Path) -> int:
        """
        Compile asmfile.
        :param asm_file:
        :param sfc_file:
        :return: error code
        """
        with open(sfc_file, "wb") as f:
            sfc_emitter = SFCWriter(f)
            return self.assemble_with_emitter(asm_file, sfc_emitter)

    def assemble_as_object(self, asm_file: str, output_file: Path) -> int:
        """
        Compile assembly file to object file for later linking.
        :param asm_file: Input assembly file
        :param output_file: Output object file path
        :return: error code
        """
        object_writer = ObjectWriter(str(output_file))
        object_writer.begin()

        try:
            exit_code = self.assemble_with_object_emitter(asm_file, object_writer)
            object_writer.end()
            return exit_code
        except RuntimeError as e:
            self.logger.error(e)
            return -1

    def assemble_with_object_emitter(self, asm_file: str, object_writer: ObjectWriter) -> int:
        """
        Assemble with object file emission, collecting symbols and relocations.
        """
        try:
            # Attach object writer to resolver for extern handling
            self.resolver._object_writer = object_writer

            with open(asm_file, encoding="utf-8") as f:
                input_program = f.read()
                try:
                    error, nodes = self.parser.parse(input_program, asm_file)

                    if error is not None:
                        self.logger.error(error)
                        return -1

                    self.logger.info("Resolving labels")
                    self.resolve_labels(nodes)

                    # Extract symbols from resolver and add to object file
                    for name, value in self.resolver.get_all_symbols():
                        # Skip external symbols as they're already added by ExternNode
                        if not self.resolver.current_scope.is_external_symbol(name):
                            # For object files, treat most symbols as global by default
                            # unless they start with '.' (local convention)
                            symbol_type = SymbolType.LOCAL if name.startswith(".") else SymbolType.GLOBAL

                            # Check if this is a label (code-relative) or constant (absolute)
                            is_label = name in [label_name for label_name, _ in self.resolver.get_all_labels()]
                            section = SymbolSection.CODE if is_label else SymbolSection.DATA

                            object_writer.add_symbol(name, value, symbol_type, section)

                    if self.dump_symbols:
                        self.resolver.dump_symbol_map()

                    self.emit_with_relocations(nodes, object_writer)

                except NodeError as e:
                    logger.error(str(e))
                    return -1

        except RuntimeError as e:
            self.logger.error(e)
            return -1
        finally:
            # Clean up object writer reference
            if hasattr(self.resolver, "_object_writer"):
                delattr(self.resolver, "_object_writer")

        self.logger.info("Success !")
        return 0

    def assemble_as_patch(
        self,
        asm_file: str,
        ips_file: Path,
        mapping: str | None = None,
        copier_header: bool = False,
    ) -> int:
        if mapping is not None:
            address_mapping = {
                "low": RomType.low_rom,
                "low2": RomType.low_rom_2,
                "high": RomType.high_rom,
            }
            self.resolver.rom_type = address_mapping[mapping]

        if self.dump_symbols:
            self.resolver.dump_symbol_map()
        with open(ips_file, "wb") as f:
            ips_emitter = IPSWriter(f, copier_header)
            ips_emitter.begin()
            exit_code = self.assemble_with_emitter(asm_file, ips_emitter)
            ips_emitter.end()
            return exit_code

    def link_as_patch(
        self, linked_obj: ObjectFile, ips_file: Path, mapping: str | None = None, copier_header: bool = False
    ) -> int:
        """
        Create IPS patch from linked object file.
        """
        if mapping is not None:
            address_mapping = {
                "low": RomType.low_rom,
                "low2": RomType.low_rom_2,
                "high": RomType.high_rom,
            }
            self.resolver.rom_type = address_mapping[mapping]

        try:
            with open(ips_file, "wb") as f:
                ips_emitter = IPSWriter(f, copier_header)
                ips_emitter.begin()

                # Write the linked code as a single block
                # For now, assume it starts at PC=0 and needs to be relocated based on symbols
                if linked_obj.code:
                    # TODO: Implement proper address resolution from linked symbols
                    # For now, write at a default address
                    start_address = 0x8000  # Default SNES code start
                    ips_emitter.write_block(linked_obj.code, start_address)

                ips_emitter.end()
                self.logger.info("Successfully created IPS patch")
                return 0

        except Exception as e:
            self.logger.error(f"Failed to create IPS patch: {e}")
            return -1

    def link_as_sfc(self, linked_obj: ObjectFile, sfc_file: Path) -> int:
        """
        Create SFC file from linked object file.
        """
        try:
            with open(sfc_file, "wb") as f:
                sfc_emitter = SFCWriter(f)
                sfc_emitter.begin()

                # Write the linked code
                if linked_obj.code:
                    # TODO: Implement proper address resolution from linked symbols
                    start_address = 0x8000  # Default SNES code start
                    sfc_emitter.write_block(linked_obj.code, start_address)

                sfc_emitter.end()
                self.logger.info("Successfully created SFC file")
                return 0

        except Exception as e:
            self.logger.error(f"Failed to create SFC file: {e}")
            return -1

    def exports_symbol_file(self, filename: str) -> None:
        """
        Exports the symbols into a file suited for bsnes sym debugger.
        :param filename:
        :return:
        """
        with open(filename, "w", encoding="utf-8") as output_file:
            labels = self.resolver.get_all_labels()
            output_file.write("[labels]\n")
            for name, value in labels:
                bank = value >> 16 & 0xFF
                offset = value & 0xFFFF
                output_file.write(f"{bank:2x}:{offset:4x} {name}\n")
