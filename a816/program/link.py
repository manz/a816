"""LinkMixin: write a linked ObjectFile to IPS / SFC + emit a merged `.adbg`."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from a816.cpu.cpu_65c816 import RomType
from a816.object_file import ObjectFile, SymbolSection, SymbolType
from a816.writers import IPSWriter, SFCWriter

if TYPE_CHECKING:
    from a816.symbols import Resolver


class LinkMixin:
    """Linker-output handler set. Mixed into `Program`."""

    if TYPE_CHECKING:
        resolver: Resolver
        logger: logging.Logger

        def _to_physical(self, logical_address: int) -> int: ...
        def _trace_linked_sections(self, linked_obj: ObjectFile) -> None: ...
        def _flush_emit_trace(self, output_path: Path) -> None: ...

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
            elif section == SymbolSection.ABS_LABEL:
                scope.absolute_labels[name] = value
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

        # Lines are stored section-relative; bake the final logical address.
        all_lines: list[tuple[int, int, int, int, int]] = []
        for section in linked_obj.sections:
            for offset, file_idx, line, column, flags in section.lines:
                all_lines.append((section.base_address + offset, file_idx, line, column, flags))
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
        label_sections = (SymbolSection.CODE, SymbolSection.ABS_LABEL)
        for name, value, sym_type, section in linked_obj.symbols:
            scope_kind = scope_by_type[sym_type]
            kind = SymbolKind.LABEL if section in label_sections else SymbolKind.CONSTANT
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

                for section in linked_obj.sections:
                    if section.code:
                        ips_emitter.write_block(section.code, self._to_physical(section.base_address))

                ips_emitter.end()
                self._trace_linked_sections(linked_obj)
                self._flush_emit_trace(ips_file)
                self.write_debug_info_for_linked(linked_obj, ips_file)
                self.logger.info("Successfully created IPS patch")
                return 0

        except OSError:
            self.logger.exception("Failed to create IPS patch")
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

                for section in linked_obj.sections:
                    if section.code:
                        sfc_emitter.write_block(section.code, self._to_physical(section.base_address))

                sfc_emitter.end()
                self._trace_linked_sections(linked_obj)
                self._flush_emit_trace(sfc_file)
                self.write_debug_info_for_linked(linked_obj, sfc_file)
                self.logger.info("Successfully created SFC file")
                return 0

        except OSError:
            self.logger.exception("Failed to create SFC file")
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
