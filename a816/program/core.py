"""`Program` core: state, paths, label resolution, traces, import dedup.

Mixin classes contribute emission, assembly, debug-info, and link
handlers; this module owns the constructor, the bookkeeping fields,
and the shared helpers every mixin reaches into via `self`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from a816.object_file import ObjectFile
from a816.parse.mzparser import A816Parser
from a816.parse.nodes import (
    AllocNode,
    BinaryNode,
    LabelNode,
    LinkedModuleNode,
    SymbolNode,
)
from a816.program.assemble import AssembleMixin
from a816.program.debug import DebugMixin
from a816.program.emit import EmitMixin
from a816.program.link import LinkMixin
from a816.program.object_emit import ObjectEmitMixin
from a816.protocols import NodeProtocol
from a816.symbols import Resolver
from a816.writers import ObjectWriter

logger = logging.getLogger("a816")

_UNKNOWN_SRC = "<unknown>"


class Program(EmitMixin, ObjectEmitMixin, AssembleMixin, DebugMixin, LinkMixin):
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

    def __init__(self, parser: A816Parser | None = None, dump_symbols: bool = False):
        """Initialize the assembler program.

        Args:
            parser: Optional custom parser instance. If None, creates a default A816Parser.
            dump_symbols: If True, prints the symbol table after assembly.
        """
        self.resolver = Resolver()
        self.logger = logging.getLogger("a816")
        self.dump_symbols = dump_symbols
        self.parser = parser or A816Parser(self.resolver)
        self._debug_capture: bool = False
        # (address, filename, line, column) recorded during emit() when capture is on.
        self._debug_lines: list[tuple[int, str, int, int]] = []
        self._linked_modules: list[Any] = []  # list[LinkedModuleNode]; typed loosely to avoid cycle
        self._program_nodes: list[NodeProtocol] = []
        # (snes_logical, physical, size, src) appended when A816_EMIT_TRACE=1.
        self._emit_trace: list[tuple[int, int, int, str]] = []

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

        Walks into `AllocNode.body` so a `.import` nested inside a
        `.alloc` block participates in the same dedup as top-level
        imports — otherwise the same module emits twice (once at the
        surrounding org pointer, once at the allocator-chosen address).
        """
        winners: dict[str, LinkedModuleNode] = {}
        all_nodes: list[LinkedModuleNode] = []
        Program._collect_linked_modules(program_nodes, all_nodes)
        # Last-occurrence wins, matching the historical top-level rule.
        for node in all_nodes:
            winners[node.module_name] = node
        for node in all_nodes:
            node.is_loser = winners[node.module_name] is not node

    @staticmethod
    def _collect_linked_modules(nodes: list[NodeProtocol], out: list[LinkedModuleNode]) -> None:
        for node in nodes:
            if isinstance(node, LinkedModuleNode):
                out.append(node)
            elif isinstance(node, AllocNode):
                Program._collect_linked_modules(node.body, out)

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

        # Run the freespace allocator between passes so .alloc / .relocate
        # blocks see their final addresses when binding labels in pass 2.
        self.resolver.allocate_pools()

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
        sections carry logical bases — convert at the write boundary.

        Falls back to the logical address if no mapping is configured for
        the current rom_type (some rom types have no default bus); the
        caller would have written the logical address pre-multi-section
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

    @staticmethod
    def _emit_trace_enabled() -> bool:
        return os.environ.get("A816_EMIT_TRACE") == "1"

    def _trace_block(self, snes: int, phys: int, size: int, src: str = _UNKNOWN_SRC) -> None:
        if self._emit_trace_enabled():
            self._emit_trace.append((snes, phys, size, src))

    def _flush_emit_trace(self, output_path: Path) -> None:
        """Write any accumulated emit-trace records next to output_path."""
        if not self._emit_trace_enabled():
            return
        log_path = output_path.with_suffix(output_path.suffix + ".emit.log")
        with open(log_path, "w", encoding="utf-8") as logf:
            for snes, phys, size, src in self._emit_trace:
                logf.write(f"snes=${snes & 0xFFFFFF:06X}  phys=0x{phys & 0xFFFFFF:06X}  size={size}  src={src}\n")
        self._emit_trace = []

    def _trace_linked_sections(self, linked_obj: ObjectFile) -> None:
        """Replay a linked ObjectFile's sections into the trace buffer."""
        if not self._emit_trace_enabled():
            return
        files = getattr(linked_obj, "files", []) or []
        for section in linked_obj.sections:
            if not section.code:
                continue
            snes = section.base_address
            phys = self._to_physical(snes)
            size = len(section.code)
            if section.lines:
                _, file_idx, line, *_rest = section.lines[0]
                src = f"{files[file_idx]}:{line}" if 0 <= file_idx < len(files) else _UNKNOWN_SRC
            else:
                src = _UNKNOWN_SRC
            self._emit_trace.append((snes, phys, size, src))

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
