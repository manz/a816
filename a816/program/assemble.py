"""AssembleMixin: parse + resolve + emit pipeline entry points.

Top-level methods invoked by the CLI (`a816 ...`) and the build pipeline
(`module_builder.build_with_imports*`). Each method opens a temporary
assembly mode (DIRECT / OBJECT) on the resolver context, invokes the
parser + resolver passes, and routes byte output through the right
writer.
"""

from __future__ import annotations

import logging
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

from a816.context import AssemblyMode
from a816.cpu.cpu_65c816 import RomType
from a816.exceptions import AssemblyError
from a816.object_file import SymbolSection, SymbolType
from a816.parse.mzparser import A816Parser
from a816.parse.nodes import NodeError
from a816.protocols import NodeProtocol
from a816.writers import IPSWriter, ObjectWriter, OverlapError, SFCWriter, WriteAuditor, Writer

if TYPE_CHECKING:
    from a816.symbols import Resolver

logger = logging.getLogger("a816")

_ASSEMBLY_FAILED_MSG = "Assembly failed: %s"


class AssembleMixin:
    """Pipeline entry points. Mixed into `Program`."""

    if TYPE_CHECKING:
        resolver: Resolver
        logger: logging.Logger
        parser: A816Parser
        dump_symbols: bool
        _program_nodes: list[NodeProtocol]

        def _mark_import_winners(self, program_nodes: list[NodeProtocol]) -> None: ...
        def resolve_labels(self, program_nodes: list[NodeProtocol]) -> None: ...
        def emit(self, program: list[NodeProtocol], writer: Writer) -> None: ...
        def emit_with_relocations(self, program: list[NodeProtocol], object_writer: ObjectWriter) -> None: ...
        def _flush_emit_trace(self, output_path: Path) -> None: ...

    def assemble_string_with_emitter(self, input_program: str, filename: str, emitter: Writer) -> None:
        """Assemble `input_program` to `emitter`.

        Raises:
          AssemblyError: parser-level failure (wraps the formatted location
            string the parser produces).
          NodeError: codegen-level failure (location-aware; subclass of
            A816Error so a single `except A816Error` catches both).
        """
        error, nodes = self.parser.parse(input_program, filename)

        if error is not None:
            raise AssemblyError(error)

        self._mark_import_winners(nodes)
        self.logger.debug("Resolving labels")
        self.resolve_labels(nodes)

        if self.dump_symbols:
            self.resolver.dump_symbol_map()

        # Stash the resolved node list so the .adbg producer can introspect
        # LinkedModuleNode placements after emission.
        self._program_nodes = list(nodes)
        self.emit(nodes, self._wrap_emitter_for_overlap_audit(emitter))

    def _wrap_emitter_for_overlap_audit(self, emitter: Writer) -> Writer:
        """Auto-wrap SFC / IPS emitters so overlapping writes get reported.

        `ObjectWriter` is left untouched — it tracks sections in a richer
        structure and the linker has its own overlap pass.
        """
        mode = self.resolver.context.overlap_mode
        if mode == "off" or isinstance(emitter, ObjectWriter):
            return emitter
        return WriteAuditor(emitter, mode=mode)  # type: ignore[arg-type]

    def assemble_with_emitter(self, asm_file: str, emitter: Writer) -> int:
        """CLI-facing wrapper around `assemble_string_with_emitter`.

        Always returns an exit code; never calls `sys.exit`. Embedders that
        want structured failures should call `assemble_string_with_emitter`
        directly and catch `A816Error`.

        Returns:
          0   on success
          128 on `A816Error` (covers both `AssemblyError` and `NodeError`)
          -1  on `RuntimeError` (mapping / bus failures bubbling up)
        """
        warnings.warn(
            "Direct assembly mode is deprecated; the build path is object "
            "compilation + linking (`assemble_as_object` / `build_with_imports`). "
            "New features land in object mode only.",
            DeprecationWarning,
            stacklevel=2,
        )
        previous_mode = self.resolver.context.mode
        try:
            self.resolver.context.mode = AssemblyMode.DIRECT

            with open(asm_file, encoding="utf-8") as f:
                input_program = f.read()
                try:
                    self.assemble_string_with_emitter(input_program, asm_file, emitter)
                except AssemblyError as e:
                    # Parser failure: the message already carries source
                    # location + caret + hint. A Python traceback would
                    # bury that under irrelevant frames for a CLI user, so
                    # `logger.error` (not `logger.exception`) is intentional.
                    logger.error(str(e))  # NOSONAR python:S8572
                    return 128
                except NodeError as e:
                    # NodeError already carries source location + hint;
                    # surface that as the user-facing line, keep the
                    # traceback at debug for postmortem.
                    logger.error(str(e))  # NOSONAR python:S8572
                    logger.debug("Codegen failure traceback", exc_info=True)
                    return 128
                except OverlapError as e:
                    # Section-overlap = hard error since the default flip
                    # to `overlap_mode="error"`. Message names both
                    # sections + bytes; no traceback needed.
                    logger.error(str(e))  # NOSONAR python:S8572
                    return 128

        except RuntimeError as e:
            self.logger.exception(_ASSEMBLY_FAILED_MSG, e)
            return -1
        finally:
            self.resolver.context.mode = previous_mode

        self.logger.debug("Success !")
        return 0

    def assemble(self, asm_file: str, sfc_file: Path) -> int:
        """
        Compile asmfile to a SFC ROM.
        :param asm_file:
        :param sfc_file:
        :return: error code
        """
        with open(sfc_file, "wb") as f:
            sfc_emitter = SFCWriter(f)
            exit_code = self.assemble_with_emitter(asm_file, sfc_emitter)
        self._flush_emit_trace(sfc_file)
        return exit_code

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
            self.logger.exception(_ASSEMBLY_FAILED_MSG, e)
            return -1

    def _classify_object_symbol(
        self, name: str, value: int, label_names: set[str], absolute_label_names: set[str]
    ) -> tuple[SymbolType, SymbolSection, int]:
        # Anonymous-block labels stay LOCAL (would otherwise leak as globals);
        # `_` prefix marks private; root-scope (or NamedScope dotted) is GLOBAL.
        if name.startswith("_") or not self.resolver.is_root_scope_symbol(name):
            symbol_type = SymbolType.LOCAL
        else:
            symbol_type = SymbolType.GLOBAL
        if name in absolute_label_names:
            section = SymbolSection.ABS_LABEL
        elif name in label_names:
            section = SymbolSection.CODE
        else:
            section = SymbolSection.DATA
        # Symbols carry their absolute logical address — relocatable modules
        # apply a delta at .import time; pinned modules use the value as-is.
        return symbol_type, section, value

    def _export_object_symbols(self, object_writer: ObjectWriter) -> None:
        label_names = {n for n, _ in self.resolver.get_all_labels(mangle_nested=True)}
        absolute_label_names = {n for n, _ in self.resolver.get_all_absolute_labels(mangle_nested=True)}
        self._publish_named_scope_bare_names(object_writer)
        for name, value in self.resolver.get_all_symbols():
            if self._should_skip_symbol_export(name, label_names):
                continue
            sym_type, section, sym_value = self._classify_object_symbol(name, value, label_names, absolute_label_names)
            object_writer.add_symbol(name, sym_value, sym_type, section)

    def _publish_named_scope_bare_names(self, object_writer: ObjectWriter) -> None:
        # Mirror NamedScope members under their BARE name as LOCAL
        # alongside the dotted GLOBAL. The dotted form (`render.foo`)
        # is what the linker globally dedupes / resolves; the bare
        # form (`foo`) keeps kintsuki's `lookup_symbol_addr("foo")`
        # working for legacy adbg consumers that expect bare names.
        # LOCAL across modules collides first-write-wins via
        # `setdefault` in the linker — matches the underscore-private
        # convention.
        from a816.object_file import SymbolSection, SymbolType
        from a816.symbols import NamedScope

        for scope in self.resolver.scopes:
            if not isinstance(scope, NamedScope):
                continue
            for bare_name, addr in scope.get_labels():
                if "." in bare_name:
                    continue  # already-dotted via _publish_named_dotted
                object_writer.add_symbol(bare_name, addr, SymbolType.LOCAL, SymbolSection.CODE)

    def _should_skip_symbol_export(self, name: str, label_names: set[str]) -> bool:
        # Symbols declared `.extern` AND also defined locally in this
        # compile unit (e.g. main `.include`s both the `.extern foo`
        # declaration and the `foo:` definition): prefer the local
        # definition — emit it as GLOBAL/LOCAL so the linker's local
        # resolution wins. Otherwise the .o would double-publish
        # (EXTERNAL stub from ExternNode + nothing for the real
        # definition), and the linker reports `unresolved external`
        # for a symbol it actually owns.
        if self.resolver.current_scope.is_external_symbol(name) and name not in label_names:
            return True  # purely external, owner provides it
        if name in self.resolver.pool_stat_symbol_names:
            return True  # pool stat snapshots are per-module, not linker-visible
        if name in self.resolver.imported_symbol_names:
            return True  # contributed by an inlined `.import`; owner's `.o` is the sole source
        return False

    def assemble_with_object_emitter(self, asm_file: str, object_writer: ObjectWriter) -> int:
        """Assemble with object file emission, collecting symbols and relocations."""
        previous_mode = self.resolver.context.mode
        previous_writer = self.resolver.context.object_writer
        try:
            self.resolver.context.mode = AssemblyMode.OBJECT
            self.resolver.context.object_writer = object_writer

            with open(asm_file, encoding="utf-8") as f:
                input_program = f.read()

            try:
                error, nodes = self.parser.parse(input_program, asm_file)
                if error is not None:
                    self.logger.error(error)
                    return -1

                self._mark_import_winners(nodes)
                self.logger.debug("Resolving labels")
                self.resolve_labels(nodes)
                self._export_object_symbols(object_writer)

                if self.dump_symbols:
                    self.resolver.dump_symbol_map()

                self.emit_with_relocations(nodes, object_writer)
            except NodeError as e:
                logger.error(str(e))  # NOSONAR python:S8572
                logger.debug("Object emit failure traceback", exc_info=True)
                return -1
        except RuntimeError as e:
            self.logger.exception(_ASSEMBLY_FAILED_MSG, e)
            return -1
        finally:
            self.resolver.context.mode = previous_mode
            self.resolver.context.object_writer = previous_writer

        self.logger.debug("Success !")
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
        self._flush_emit_trace(ips_file)
        return exit_code
