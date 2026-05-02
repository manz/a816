import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from a816.context import AssemblyMode
from a816.cpu.cpu_65c816 import RomType
from a816.object_file import ObjectFile, SymbolSection, SymbolType
from a816.parse.mzparser import MZParser
from a816.parse.nodes import (
    BinaryNode,
    CodePositionNode,
    IncludeIpsNode,
    LabelNode,
    LinkedModuleNode,
    NodeError,
    SymbolNode,
)
from a816.protocols import NodeProtocol
from a816.symbols import Resolver
from a816.writers import IPSWriter, ObjectWriter, SFCWriter, Writer

logger = logging.getLogger("a816")


@dataclass
class _EmitState:
    """Mutable state threaded through Program.emit's per-node helpers."""

    current_block: bytes
    current_block_addr: int


@dataclass
class _ObjectEmitState:
    """Mutable state threaded through Program.emit_with_relocations."""

    current_block: bytes


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
        self._debug_capture: bool = False
        # (address, filename, line, column) recorded during emit() when capture is on.
        self._debug_lines: list[tuple[int, str, int, int]] = []
        self._linked_modules: list[Any] = []  # list[LinkedModuleNode]; typed loosely to avoid cycle
        self._program_nodes: list[NodeProtocol] = []

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

    @staticmethod
    def _mark_import_winners(program_nodes: list[NodeProtocol]) -> None:
        """Tag every duplicate `.import "foo"` LinkedModuleNode except the
        last as `is_loser=True`.

        `.import` is idempotent across the program: a module may appear
        in an `.include`'d patch file as well as the main source for
        symbol-visibility reasons. Only the last occurrence emits bytes
        and consumes PC space; earlier ones still bind symbols (for
        scope visibility in the surrounding source) but otherwise are
        no-ops in both `pc_after` and `emit`. Marking happens before
        `resolve_labels` so the winner/loser distinction is consistent
        across the address-resolution and emission passes.
        """
        last_idx: dict[str, int] = {}
        for idx, node in enumerate(program_nodes):
            if isinstance(node, LinkedModuleNode):
                last_idx[node.module_name] = idx
        for idx, node in enumerate(program_nodes):
            if isinstance(node, LinkedModuleNode):
                node.is_loser = last_idx[node.module_name] != idx

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

    def _to_physical(self, logical_address: int) -> int:
        """Translate a logical SNES bus address to its physical ROM offset.

        IPS/SFC writers expect physical (file) offsets. The legacy emit()
        path got this for free because resolver.pc tracks physical, but
        regions carry logical bases — convert at the write boundary.

        Falls back to the logical address if no mapping is configured for
        the current rom_type (some rom types have no default bus); the
        caller would have written the logical address pre-multi-region
        anyway, so the fallback preserves existing behavior.
        """
        try:
            bus = self.resolver.get_bus()
            addr = bus.get_address(logical_address)
            physical = addr.physical
        except KeyError:
            return logical_address
        if physical is None:
            return logical_address
        return physical

    def emit(self, program: list[NodeProtocol], writer: Writer) -> None:
        """Emit machine code from resolved nodes to a writer.

        Iterates through program nodes, generating machine code bytes and
        writing them to the output writer. Handles code position changes
        and IPS block includes.

        Args:
            program: List of resolved executable nodes.
            writer: Output writer (IPSWriter, SFCWriter, etc.).
        """
        state = _EmitState(current_block=b"", current_block_addr=self.resolver.pc)
        for node in program:
            self._emit_one(node, writer, state)
        self._flush_pending(writer, state)

    def _emit_one(self, node: NodeProtocol, writer: Writer, state: _EmitState) -> None:
        """Dispatch a single node to the right emission path."""
        if isinstance(node, LinkedModuleNode):
            self._emit_linked_module(node, writer, state)
            return
        self._emit_default(node, writer, state)
        if isinstance(node, CodePositionNode):
            self._handle_code_position(writer, state)
        if isinstance(node, IncludeIpsNode):
            self._emit_ips_blocks(node, writer)

    def _emit_linked_module(self, node: LinkedModuleNode, writer: Writer, state: _EmitState) -> None:
        """Emit a `.import`'d module's regions and refresh emission state.

        Loser duplicates are pure no-ops (pc_after also bailed out so
        the resolver PC is untouched — leaving current_block alone keeps
        surrounding inline source addressed correctly).
        """
        if node.is_loser:
            return
        self._flush_pending(writer, state)
        blocks = node.emit_blocks(self.resolver.reloc_address)
        for base, block in blocks:
            if block:
                writer.write_block(block, self._to_physical(base))
        # Only relocatable modules consume linear PC at the import site;
        # pinned regions land at their declared `*=` and the importer's
        # PC stays where it was.
        if blocks and node.relocatable:
            advance = len(blocks[0][1])
            self.resolver.pc += advance
            self.resolver.reloc_address += advance
        state.current_block_addr = self.resolver.pc

    def _emit_default(self, node: NodeProtocol, writer: Writer, state: _EmitState) -> None:
        """Emit a non-LinkedModule node and accumulate its bytes."""
        del writer  # accumulation only — flush happens at boundaries
        pre_emit_addr = self.resolver.reloc_address.logical_value
        node_bytes = node.emit(self.resolver.reloc_address)
        if not node_bytes:
            return
        self._record_debug_line(node, pre_emit_addr)
        state.current_block += node_bytes
        self.resolver.pc += len(node_bytes)
        self.resolver.reloc_address += len(node_bytes)

    def _handle_code_position(self, writer: Writer, state: _EmitState) -> None:
        """Flush at a `*=` boundary and re-anchor for subsequent bytes."""
        self._flush_pending(writer, state)
        state.current_block_addr = self.resolver.pc

    @staticmethod
    def _emit_ips_blocks(node: IncludeIpsNode, writer: Writer) -> None:
        """Pass an `.includeips`-loaded patch's blocks straight through."""
        for block_addr, block in node.blocks:
            writer.write_block(block, block_addr)

    @staticmethod
    def _flush_pending(writer: Writer, state: _EmitState) -> None:
        """Write the accumulated current_block at its anchor and reset."""
        if state.current_block:
            writer.write_block(state.current_block, state.current_block_addr)
            state.current_block = b""

    def _record_object_line(self, node: NodeProtocol, offset: int, object_writer: ObjectWriter) -> None:
        """Record an addr->line entry on the ObjectWriter so .o files carry line info."""
        info = getattr(node, "file_info", None)
        if info is None:
            return
        position = getattr(info, "position", None)
        if position is None or position.file is None:
            return
        object_writer.add_line(offset, position.file.filename, position.line, position.column)

    def _record_debug_line(self, node: NodeProtocol, address: int) -> None:
        """If debug capture is on, record one line entry for the node."""
        if not getattr(self, "_debug_capture", False):
            return
        info = getattr(node, "file_info", None)
        if info is None:
            return
        position = getattr(info, "position", None)
        if position is None or position.file is None:
            return
        self._debug_lines.append((address, position.file.filename, position.line, position.column))

    def emit_with_relocations(self, program: list[NodeProtocol], object_writer: ObjectWriter) -> None:
        """Emit code into per-region object-file buckets.

        A new region opens on every CodePositionNode. Relocation/line offsets
        recorded by emitting nodes are region-relative byte offsets, decoupled
        from `resolver.pc` (which CodePositionNode rewrites to a physical
        address).
        """
        original_pc = self.resolver.pc
        original_reloc = self.resolver.reloc_address

        # Seed the initial (implicit) region at the resolver's reloc_address.
        # If the source begins with `*=`, that emit immediately closes this
        # placeholder region and opens a new explicit one.
        object_writer.start_region(self.resolver.reloc_address.logical_value, explicit=False)
        state = _ObjectEmitState(current_block=b"")
        try:
            for node in program:
                self._object_emit_one(node, object_writer, state)
            self._flush_object_block(object_writer, state)
        finally:
            self.resolver.pc = original_pc
            self.resolver.reloc_address = original_reloc

    def _object_emit_one(
        self, node: NodeProtocol, object_writer: ObjectWriter, state: "_ObjectEmitState"
    ) -> None:
        """Emit one node into the current object-writer region.

        Splits the dispatch the way `emit()` does so each branch — the
        common byte accumulator, the `*=` boundary, and the `.includeips`
        passthrough — owns a single concern.
        """
        self._accumulate_object_bytes(node, object_writer, state)
        if isinstance(node, CodePositionNode):
            self._object_open_region(object_writer, state, explicit=True)
        if isinstance(node, IncludeIpsNode):
            self._object_emit_ips_blocks(node, object_writer, state)

    def _accumulate_object_bytes(
        self, node: NodeProtocol, object_writer: ObjectWriter, state: "_ObjectEmitState"
    ) -> None:
        node_bytes = node.emit(self.resolver.reloc_address)
        if not node_bytes:
            return
        self._record_object_line(node, object_writer.relocation_offset(), object_writer)
        state.current_block += node_bytes
        object_writer.mark_emitted(len(node_bytes))
        self.resolver.pc += len(node_bytes)
        self.resolver.reloc_address += len(node_bytes)

    def _object_open_region(
        self, object_writer: ObjectWriter, state: "_ObjectEmitState", *, explicit: bool
    ) -> None:
        """Flush any pending block then open a fresh region at the new PC."""
        self._flush_object_block(object_writer, state)
        object_writer.start_region(self.resolver.reloc_address.logical_value, explicit=explicit)

    def _object_emit_ips_blocks(
        self, node: IncludeIpsNode, object_writer: ObjectWriter, state: "_ObjectEmitState"
    ) -> None:
        """Pass an `.includeips`-loaded patch through as one region per block."""
        self._flush_object_block(object_writer, state)
        for block_addr, block in node.blocks:
            object_writer.start_region(block_addr, explicit=True)
            object_writer.write_block(block, block_addr)

    @staticmethod
    def _flush_object_block(object_writer: ObjectWriter, state: "_ObjectEmitState") -> None:
        if state.current_block:
            object_writer.write_block(state.current_block, 0)
            state.current_block = b""

    def assemble_string_with_emitter(self, input_program: str, filename: str, emitter: Writer) -> str | None:
        error, nodes = self.parser.parse(input_program, filename)

        if error is not None:
            return error

        self._mark_import_winners(nodes)
        self.logger.info("Resolving labels")
        self.resolve_labels(nodes)

        if self.dump_symbols:
            self.resolver.dump_symbol_map()

        # Stash the resolved node list so the .adbg producer can introspect
        # LinkedModuleNode placements after emission.
        self._program_nodes = list(nodes)
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

    def _classify_object_symbol(
        self, name: str, value: int, label_names: set[str]
    ) -> tuple[SymbolType, SymbolSection, int]:
        # Anonymous-block labels stay LOCAL (would otherwise leak as globals);
        # `_` prefix marks private; root-scope (or NamedScope dotted) is GLOBAL.
        if name.startswith("_") or not self.resolver.is_root_scope_symbol(name):
            symbol_type = SymbolType.LOCAL
        else:
            symbol_type = SymbolType.GLOBAL
        section = SymbolSection.CODE if name in label_names else SymbolSection.DATA
        # Symbols carry their absolute logical address — relocatable modules
        # apply a delta at .import time; pinned modules use the value as-is.
        return symbol_type, section, value

    def _export_object_symbols(self, object_writer: ObjectWriter) -> None:
        label_names = {n for n, _ in self.resolver.get_all_labels(mangle_nested=True)}
        for name, value in self.resolver.get_all_symbols():
            if self.resolver.current_scope.is_external_symbol(name):
                continue  # already added by ExternNode
            sym_type, section, sym_value = self._classify_object_symbol(name, value, label_names)
            object_writer.add_symbol(name, sym_value, sym_type, section)

    def assemble_with_object_emitter(
        self, asm_file: str, object_writer: ObjectWriter, prelude: str | None = None
    ) -> int:
        """Assemble with object file emission, collecting symbols and relocations."""
        previous_mode = self.resolver.context.mode
        previous_writer = self.resolver.context.object_writer
        try:
            self.resolver.context.mode = AssemblyMode.OBJECT
            self.resolver.context.object_writer = object_writer

            with open(asm_file, encoding="utf-8") as f:
                input_program = f.read()
            if prelude:
                input_program = prelude + "\n" + input_program

            try:
                error, nodes = self.parser.parse(input_program, asm_file)
                if error is not None:
                    self.logger.error(error)
                    return -1

                self._mark_import_winners(nodes)
                self.logger.info("Resolving labels")
                self.resolve_labels(nodes)
                self._export_object_symbols(object_writer)

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

    def enable_debug_capture(self) -> None:
        """Turn on per-node line capture for `.adbg` emission."""
        self._debug_capture = True
        self._debug_lines = []

    def build_debug_info(self, main_source: Path | str) -> "Any":
        """Build a DebugInfo from captured state. Returns a a816.debug_info.DebugInfo."""
        from a816.debug_info import DebugInfo, LineEntry, ModuleEntry, SymbolEntry, SymbolKind, SymbolScope
        from a816.parse.nodes import LinkedModuleNode

        info = DebugInfo()
        main_path = str(Path(main_source))
        info.add_file(main_path)
        # Index 0 covers the entry-point translation unit; module 0 is __main__.
        info.modules.append(ModuleEntry(name="__main__", file_idx=0, base=0))

        # One entry per LinkedModuleNode: source file, name, load base.
        module_index_by_name: dict[str, int] = {"__main__": 0}
        for node in self._program_nodes:
            if not isinstance(node, LinkedModuleNode):
                continue
            base = getattr(node, "base_address", 0)
            file_idx = info.add_file(node.module_name + ".s")
            module_index_by_name[node.module_name] = info.add_module(node.module_name, file_idx, base)

        # Symbols: every label gets a SymbolEntry. Module ownership resolves
        # by walking known module bases; falls back to NO_MODULE for the main TU.
        for name, value in self.resolver.get_all_labels():
            module_idx = self._guess_module(value, info)
            info.symbols.append(
                SymbolEntry(
                    name=name,
                    address=value,
                    scope=SymbolScope.GLOBAL,
                    module_idx=module_idx,
                    kind=SymbolKind.LABEL,
                )
            )

        # Line entries collected during emit().
        for address, filename, line, column in self._debug_lines:
            file_idx = info.add_file(filename)
            module_idx = self._guess_module_by_filename(filename, module_index_by_name)
            info.lines.append(
                LineEntry(
                    address=address,
                    file_idx=file_idx,
                    line=line,
                    column=column,
                    module_idx=module_idx,
                )
            )

        return info

    def _guess_module(self, address: int, info: "Any") -> int:
        """Pick the module whose base is closest to (and ≤) `address`."""
        from a816.debug_info import NO_MODULE

        best_idx = NO_MODULE
        best_base = -1
        for idx, module in enumerate(info.modules):
            if module.base <= address and module.base > best_base:
                best_idx = idx
                best_base = module.base
        return best_idx

    def _guess_module_by_filename(self, filename: str, by_name: dict[str, int]) -> int:
        from a816.debug_info import NO_MODULE

        # Module sources end with `<name>.s`; match by basename without extension.
        stem = Path(filename).stem
        return by_name.get(stem, NO_MODULE)

    def import_linked_symbols(self, linked_obj: ObjectFile) -> None:
        """Register a linked ObjectFile's symbols into the resolver.

        After Linker.link() resolves symbols to final addresses, the link-mode
        callers (link_as_patch / link_as_sfc) need to expose those names to
        exports_symbol_file and .adbg producers. CODE labels land in scope.labels
        (so get_all_labels picks them up); DATA constants stay in scope.symbols.
        """
        scope = self.resolver.current_scope
        for name, value, sym_type, section in linked_obj.symbols:
            if sym_type == SymbolType.EXTERNAL:
                continue
            if section == SymbolSection.CODE:
                scope.labels[name] = value
            scope.symbols[name] = value

    def write_debug_info_for_linked(self, linked_obj: ObjectFile, output_path: Path) -> Path | None:
        """Write a `.adbg` next to a linked output. Returns the written path."""
        from a816.debug_info import (
            DebugInfo,
            LineEntry,
            ModuleEntry,
            SymbolEntry,
            SymbolKind,
            SymbolScope,
        )

        # Lines are stored region-relative; bake the final logical address.
        all_lines: list[tuple[int, int, int, int, int]] = []
        for region in linked_obj.regions:
            for offset, file_idx, line, column, flags in region.lines:
                all_lines.append((region.base_address + offset, file_idx, line, column, flags))
        if not linked_obj.symbols and not all_lines:
            return None

        info = DebugInfo()
        info.files = list(linked_obj.files)
        if not info.files:
            info.files = ["<linked>"]
        # Without per-object module names available at this layer, fold every
        # linked symbol under module 0 ("<linked>").
        info.modules.append(ModuleEntry(name="<linked>", file_idx=0, base=self._get_code_start_address(linked_obj)))
        scope_by_type = {
            SymbolType.GLOBAL: SymbolScope.GLOBAL,
            SymbolType.LOCAL: SymbolScope.LOCAL,
            SymbolType.EXTERNAL: SymbolScope.EXTERNAL,
        }
        for name, value, sym_type, section in linked_obj.symbols:
            scope_kind = scope_by_type[sym_type]
            kind = SymbolKind.LABEL if section == SymbolSection.CODE else SymbolKind.CONSTANT
            info.symbols.append(SymbolEntry(name=name, address=value, scope=scope_kind, module_idx=0, kind=kind))
        for address, file_idx, line, column, flags in all_lines:
            info.lines.append(
                LineEntry(address=address, file_idx=file_idx, line=line, column=column, module_idx=0, flags=flags)
            )

        from a816.debug_info import write as write_debug_info

        adbg_path = output_path.with_suffix(output_path.suffix + ".adbg")
        write_debug_info(info, adbg_path)
        return adbg_path

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

        self.import_linked_symbols(linked_obj)
        try:
            with open(ips_file, "wb") as f:
                ips_emitter = IPSWriter(f, copier_header)
                ips_emitter.begin()

                for region in linked_obj.regions:
                    if region.code:
                        ips_emitter.write_block(region.code, self._to_physical(region.base_address))

                ips_emitter.end()
                self.write_debug_info_for_linked(linked_obj, ips_file)
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
        self.import_linked_symbols(linked_obj)
        try:
            with open(sfc_file, "wb") as f:
                sfc_emitter = SFCWriter(f)
                sfc_emitter.begin()

                for region in linked_obj.regions:
                    if region.code:
                        sfc_emitter.write_block(region.code, self._to_physical(region.base_address))

                sfc_emitter.end()
                self.write_debug_info_for_linked(linked_obj, sfc_file)
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
