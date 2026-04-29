import logging
from pathlib import Path

from a816.context import AssemblyMode
from a816.cpu.cpu_65c816 import RomType
from a816.object_file import ObjectFile, SymbolSection, SymbolType
from a816.parse.mzparser import MZParser
from a816.parse.nodes import (
    BinaryNode,
    CodePositionNode,
    IncludeIpsNode,
    LabelNode,
    NodeError,
    SymbolNode,
)
from a816.protocols import NodeProtocol
from a816.symbols import Resolver
from a816.writers import IPSWriter, ObjectWriter, SFCWriter, Writer

logger = logging.getLogger("a816")


class Program:
    """Main assembler program orchestrating parsing, symbol resolution, and code emission.

    The Program class is the central entry point for assembling 65c816 code. It manages:
    - Parsing assembly source files into AST and executable nodes
    - Symbol resolution across multiple passes
    - Code emission to various output formats (IPS patches, SFC files, object files)
    - Separate compilation and linking workflows

    Example:
        >>> program = Program()
        >>> result = program.assemble_as_patch("source.s", Path("output.ips"))
        >>> if result == 0:
        ...     print("Assembly successful")
    """

    def __init__(self, parser: MZParser | None = None, dump_symbols: bool = False):
        """Initialize the assembler program.

        Args:
            parser: Optional custom parser instance. If None, creates a default MZParser.
            dump_symbols: If True, prints the symbol table after assembly.
        """
        self.resolver = Resolver()
        self.logger = logging.getLogger("a816")
        self.dump_symbols = dump_symbols
        self.parser = parser or MZParser(self.resolver)

    def add_module_path(self, path: str | Path) -> None:
        """Add a directory to the module search path for .import directives.

        Args:
            path: Directory path to add to the search path.
        """
        module_path = (Path(path) if isinstance(path, str) else path).resolve()
        if module_path not in self.resolver.context.module_paths:
            self.resolver.context.module_paths.append(module_path)

    def add_include_path(self, path: str | Path) -> None:
        """Add a directory to the include search path for .include directives.

        Args:
            path: Directory path to add to the search path.
        """
        include_path = (Path(path) if isinstance(path, str) else path).resolve()
        if include_path not in self.resolver.context.include_paths:
            self.resolver.context.include_paths.append(include_path)

    def get_physical_address(self, logical_address: int) -> int:
        """Convert a logical SNES address to a physical ROM address.

        Args:
            logical_address: The SNES logical address (e.g., 0x8000).

        Returns:
            The corresponding physical ROM address.

        Raises:
            RuntimeError: If the address has no physical mapping.
            KeyError: If the bank is not mapped in the current ROM type.
        """
        physical_address = self.resolver.get_bus().get_address(logical_address).physical
        if physical_address is not None:
            return physical_address
        else:
            raise RuntimeError(f"{logical_address} has no physical address.")

    def resolver_reset(self) -> None:
        """Reset the resolver state to initial values.

        Resets PC, scope tracking, and current scope pointer for a fresh pass.
        """
        self.resolver.pc = 0x000000
        self.resolver.last_used_scope = 0
        self.resolver.current_scope = self.resolver.scopes[0]

    def resolve_labels(self, program_nodes: list[NodeProtocol]) -> None:
        """Resolve all labels and symbols through multi-pass processing.

        Performs two passes over the program nodes:
        1. First pass: Process symbol definitions and forward references
        2. Second pass: Process label definitions with known addresses

        Args:
            program_nodes: List of executable nodes from parsing.
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
        """Emit machine code from resolved nodes to a writer.

        Iterates through program nodes, generating machine code bytes and
        writing them to the output writer. Handles code position changes
        and IPS block includes.

        Args:
            program: List of resolved executable nodes.
            writer: Output writer (IPSWriter, SFCWriter, etc.).
        """
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
                # Note: External symbol references are handled during parsing/codegen.
                # The OpcodeNode and ExpressionNode classes already create relocations
                # when they encounter external symbols (via ExternalSymbolReference).
                # Here we simply emit the code - relocations are stored in the ObjectWriter.
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

    def assemble_with_emitter(self, asm_file: str, emitter: Writer, prelude: str | None = None) -> int:
        previous_mode = self.resolver.context.mode
        try:
            # Set direct assembly mode so .import includes module code
            self.resolver.context.mode = AssemblyMode.DIRECT

            with open(asm_file, encoding="utf-8") as f:
                input_program = f.read()
                if prelude:
                    input_program = prelude + "\n" + input_program
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
        finally:
            self.resolver.context.mode = previous_mode

        self.logger.info("Success !")
        return 0

    def assemble(self, asm_file: str, sfc_file: Path, prelude: str | None = None) -> int:
        """
        Compile asmfile.
        :param asm_file:
        :param sfc_file:
        :param prelude: Optional prelude content to prepend to the source.
        :return: error code
        """
        with open(sfc_file, "wb") as f:
            sfc_emitter = SFCWriter(f)
            return self.assemble_with_emitter(asm_file, sfc_emitter, prelude=prelude)

    def assemble_as_object(self, asm_file: str, output_file: Path, prelude: str | None = None) -> int:
        """
        Compile assembly file to object file for later linking.
        :param asm_file: Input assembly file
        :param output_file: Output object file path
        :param prelude: Optional prelude content to prepend to the source
        :return: error code
        """
        object_writer = ObjectWriter(str(output_file))
        object_writer.begin()

        try:
            exit_code = self.assemble_with_object_emitter(asm_file, object_writer, prelude=prelude)
            object_writer.end()
            return exit_code
        except RuntimeError as e:
            self.logger.error(e)
            return -1

    def assemble_with_object_emitter(
        self, asm_file: str, object_writer: ObjectWriter, prelude: str | None = None
    ) -> int:
        """
        Assemble with object file emission, collecting symbols and relocations.
        """
        previous_mode = self.resolver.context.mode
        previous_writer = self.resolver.context.object_writer
        try:
            # Set object compilation mode
            self.resolver.context.mode = AssemblyMode.OBJECT
            self.resolver.context.object_writer = object_writer

            # Base logical address for converting symbol values to module-relative offsets.
            base_address = self.resolver.reloc_address.logical_value

            with open(asm_file, encoding="utf-8") as f:
                input_program = f.read()
                if prelude:
                    input_program = prelude + "\n" + input_program
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
                            # Anonymous-block labels stay LOCAL (would otherwise
                            # leak as globals); explicit `_` prefix also marks
                            # private; everything reachable at the root scope
                            # (or via a NamedScope dotted export) is GLOBAL.
                            if name.startswith("_") or not self.resolver.is_root_scope_symbol(name):
                                symbol_type = SymbolType.LOCAL
                            else:
                                symbol_type = SymbolType.GLOBAL

                            # Check if this is a label (code-relative) or constant (absolute)
                            is_label = name in [label_name for label_name, _ in self.resolver.get_all_labels()]
                            section = SymbolSection.CODE if is_label else SymbolSection.DATA

                            # For CODE symbols (labels), convert from logical address to offset
                            # by subtracting the base address
                            if section == SymbolSection.CODE and isinstance(value, int):
                                symbol_value = value - base_address
                            else:
                                symbol_value = value

                            object_writer.add_symbol(name, symbol_value, symbol_type, section)

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
            self.resolver.context.mode = previous_mode
            self.resolver.context.object_writer = previous_writer

        self.logger.info("Success !")
        return 0

    def assemble_as_patch(
        self,
        asm_file: str,
        ips_file: Path,
        mapping: str | None = None,
        copier_header: bool = False,
        prelude: str | None = None,
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
            exit_code = self.assemble_with_emitter(asm_file, ips_emitter, prelude=prelude)
            ips_emitter.end()
            return exit_code

    def link_as_patch(
        self, linked_obj: ObjectFile, ips_file: Path, mapping: str | None = None, copier_header: bool = False
    ) -> int:
        """Create IPS patch from linked object file.

        Args:
            linked_obj: The linked object file containing code and symbols.
            ips_file: Output path for the IPS patch file.
            mapping: ROM mapping type ('low', 'low2', 'high'). Default is 'low'.
            copier_header: If True, adds 0x200 offset for copier headers.

        Returns:
            0 on success, -1 on failure.
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

                if linked_obj.code:
                    # Determine start address from CODE symbols, default to 0x8000
                    start_address = self._get_code_start_address(linked_obj)
                    ips_emitter.write_block(linked_obj.code, start_address)

                ips_emitter.end()
                self.logger.info("Successfully created IPS patch")
                return 0

        except OSError as e:
            self.logger.error(f"Failed to create IPS patch: {e}")
            return -1

    def link_as_sfc(self, linked_obj: ObjectFile, sfc_file: Path) -> int:
        """Create SFC file from linked object file.

        Args:
            linked_obj: The linked object file containing code and symbols.
            sfc_file: Output path for the SFC ROM file.

        Returns:
            0 on success, -1 on failure.
        """
        try:
            with open(sfc_file, "wb") as f:
                sfc_emitter = SFCWriter(f)
                sfc_emitter.begin()

                if linked_obj.code:
                    # Determine start address from CODE symbols, default to 0x8000
                    start_address = self._get_code_start_address(linked_obj)
                    sfc_emitter.write_block(linked_obj.code, start_address)

                sfc_emitter.end()
                self.logger.info("Successfully created SFC file")
                return 0

        except OSError as e:
            self.logger.error(f"Failed to create SFC file: {e}")
            return -1

    def _get_code_start_address(self, linked_obj: ObjectFile) -> int:
        """Determine the start address for code from linked object symbols.

        Finds the lowest address among CODE section symbols to determine
        where the code block should be written.

        Args:
            linked_obj: The linked object file with symbols.

        Returns:
            The lowest CODE symbol address, or 0x8000 as default.
        """
        code_addresses = [
            value
            for name, value, sym_type, section in linked_obj.symbols
            if section == SymbolSection.CODE and sym_type != SymbolType.EXTERNAL
        ]
        if code_addresses:
            return min(code_addresses)
        return 0x8000  # Default SNES code start

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
