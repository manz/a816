"""Module builder for automatic dependency resolution and compilation.

This module handles the automatic discovery, compilation, and linking of
modules referenced via .import directives.
"""

import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from a816.program import Program

from a816.linker import Linker
from a816.module_loader import resolve_module
from a816.object_file import ObjectFile
from a816.parse.ast.nodes import (
    AstNode,
    ImportAstNode,
)
from a816.parse.mzparser import A816Parser

logger = logging.getLogger("a816.module_builder")


@dataclass
class BuildResult:
    """Structured result from build operations."""

    exit_code: int
    symbol_map: dict[str, int] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)
    program: "Program | None" = None
    debug_info_path: Path | None = None


class ModuleGraph:
    """Represents the dependency graph of modules."""

    def __init__(self) -> None:
        self.modules: dict[str, Path] = {}  # module_name -> source_path
        self.dependencies: dict[str, set[str]] = defaultdict(set)  # module -> set of dependencies

    def add_module(self, name: str, source_path: Path) -> None:
        """Add a module to the graph."""
        self.modules[name] = source_path

    def add_dependency(self, module: str, depends_on: str) -> None:
        """Record that 'module' depends on 'depends_on'."""
        self.dependencies[module].add(depends_on)

    def topological_sort(self) -> list[str]:
        """Return modules in compilation order (dependencies first).

        Raises:
            ValueError: If there's a circular dependency.
        """
        visited: set[str] = set()
        temp_visited: set[str] = set()
        result: list[str] = []

        def visit(module: str) -> None:
            if module in temp_visited:
                raise ValueError(f"Circular dependency detected involving {module}")
            if module in visited:
                return

            temp_visited.add(module)

            # Sort dependencies so the visit order (and thus the
            # compilation/placement order) is stable regardless of
            # PYTHONHASHSEED. `dependencies` is a set, whose iteration
            # order Python randomizes per process; leaving it unsorted
            # makes the emitted ROM non-deterministic across builds.
            for dep in sorted(self.dependencies.get(module, set())):
                if dep in self.modules:  # Only visit if it's in our graph
                    visit(dep)

            temp_visited.remove(module)
            visited.add(module)
            result.append(module)

        for module in self.modules:
            if module not in visited:
                visit(module)

        return result


class ModuleBuilder:
    """Handles automatic module discovery, compilation, and linking."""

    def __init__(
        self,
        module_paths: list[Path] | None = None,
        output_dir: Path | None = None,
        symbols: dict[str, int | str] | None = None,
        include_paths: list[Path] | None = None,
    ) -> None:
        """Initialize the module builder.

        Args:
            module_paths: Directories to search for modules.
            output_dir: Directory to write compiled .o files.
            symbols: Predefined symbols (e.g., LANG=1) for conditional compilation.
            include_paths: Directories to search for .include files.
        """
        self.module_paths = module_paths or []
        self.output_dir = output_dir or Path("build/obj")
        self.symbols: dict[str, int | str] = symbols or {}
        self.include_paths: list[Path] = include_paths or []
        self.graph = ModuleGraph()
        self._discovered: set[str] = set()

    def discover_imports(self, source_file: Path, parsed_nodes: list[AstNode] | None = None) -> None:
        """Recursively discover all imports starting from a source file.

        Args:
            source_file: The main source file to start from.
            parsed_nodes: Optional pre-parsed AST for source_file to avoid a redundant parse.
        """
        self._discover_imports_recursive(source_file, "__main__", parsed_nodes)

    def _discover_imports_recursive(
        self, source_path: Path, module_name: str, parsed_nodes: list[AstNode] | None = None
    ) -> None:
        """Recursively discover imports from a source file."""
        if module_name in self._discovered:
            return

        self._discovered.add(module_name)
        self.graph.add_module(module_name, source_path)

        try:
            if parsed_nodes is not None:
                nodes = parsed_nodes
            else:
                content = source_path.read_text(encoding="utf-8")
                nodes = A816Parser.parse_as_ast(content, str(source_path)).nodes

            imports = self._collect_imports(nodes)

            for import_name in imports:
                self.graph.add_dependency(module_name, import_name)

                # Find the source file for this import
                import_source = self._resolve_module_source(import_name, source_path.parent)
                if import_source:
                    self._discover_imports_recursive(import_source, import_name)
                else:
                    logger.warning(f"Could not find source for module '{import_name}'")

        except OSError as e:
            logger.error(f"Error reading {source_path}: {e}")  # NOSONAR python:S8572
            logger.debug("Source read traceback", exc_info=True)
            raise

    def _collect_imports(self, nodes: list[AstNode]) -> list[str]:
        """Collect all import names from AST nodes."""
        from a816.parse.ast.visitor import walk

        return [node.module_name for node in walk(nodes) if isinstance(node, ImportAstNode)]

    def _resolve_module_source(self, module_name: str, base_dir: Path) -> Path | None:
        """Find the source file for a module via the shared `module_loader`.

        Search order: stdlib `@std/...` → `base_dir` → configured `module_paths`.
        """
        return resolve_module(module_name, ".s", [base_dir, *self.module_paths])

    def _needs_recompilation(self, module_name: str) -> bool:
        """Whether a module's own files changed since its `.o` was built.

        Dirty when the object is missing, its dependency sidecar is missing
        (so a pre-feature `.o` rebuilds once), the cached object was built from
        a different source path (object names like `__main__` collide across
        unrelated builds sharing one `--obj-dir`), or any recorded dependency
        (the source, an `.include`d file, or an `.incbin`/`.table` asset) is
        missing or newer than the object. Import-graph propagation (a
        dependency *module* recompiling) is handled by the caller in `build`,
        which walks modules dependencies-first.

        Args:
            module_name: The module name

        Returns:
            True if the module needs recompilation.
        """
        if module_name not in self.graph.modules:
            return True

        obj_path = self._get_obj_path(module_name)
        if not obj_path.exists():
            return True

        deps_path = self._deps_path(module_name)
        if not deps_path.exists():
            return True

        deps = [dep for dep in deps_path.read_text(encoding="utf-8").splitlines() if dep]
        # The source that built this object is recorded in its sidecar; if the
        # current source path isn't there, the object belongs to a different
        # file that mapped to the same module name, so rebuild.
        if os.path.abspath(str(self.graph.modules[module_name])) not in deps:
            return True

        obj_mtime = obj_path.stat().st_mtime
        for dep in deps:
            dep_file = Path(dep)
            if not dep_file.exists() or dep_file.stat().st_mtime > obj_mtime:
                return True
        return False

    def _get_obj_path(self, module_name: str) -> Path:
        """Get the object file path for a module."""
        # Handle modules with path separators (e.g., "battle/sram")
        obj_name = module_name.replace("/", "_") + ".o"
        return self.output_dir / obj_name

    def _deps_path(self, module_name: str) -> Path:
        """Sidecar listing every file a module's `.o` was built from."""
        return self._get_obj_path(module_name).with_suffix(".deps")

    def _write_deps(self, module_name: str, source_path: Path, obj: ObjectFile, asset_files: set[str]) -> None:
        """Record the module's dependency set next to its `.o`.

        The set unions the source, every file in the object's source-file
        table (the module plus its `.include`s), and the asset paths the
        resolver collected (`.incbin` / `.table`). Stored as absolute paths,
        one per line, so the next build can stat them directly.
        """
        deps = {os.path.abspath(str(source_path))}
        deps.update(os.path.abspath(f) for f in obj.files)
        deps.update(asset_files)
        self._deps_path(module_name).write_text("\n".join(sorted(deps)) + "\n", encoding="utf-8")

    def _compile_module(
        self, module_name: str, source_path: Path, obj_path: Path, constants: dict[str, int]
    ) -> set[str]:
        """Compile one module to its `.o`; return the asset paths it read.

        The returned set (absolute `.incbin` / `.table` paths) is folded into
        the dependency sidecar by the caller so editing an asset invalidates
        the cache.
        """
        from a816.program import Program

        logger.info(f"Compiling {module_name}: {source_path} -> {obj_path}")
        program = Program()
        program.add_module_path(self.output_dir)
        for path in self.module_paths:
            program.add_module_path(path)
        for inc_path in self.include_paths:
            program.add_include_path(inc_path)
        for name, value in self.symbols.items():
            program.resolver.current_scope.add_symbol(name, value)
        for name, value in constants.items():
            program.resolver.current_scope.add_symbol(name, value)
            # Constants accumulated from previously-built modules are seeded
            # into this module's resolver as raw symbols so codegen can read
            # their values, but they're owned by the contributing module's
            # `.o`. Mark them imported so `_export_object_symbols` doesn't
            # re-publish them here - otherwise every downstream `.o` gains
            # a duplicate GLOBAL and the linker rejects the build.
            program.resolver.imported_symbol_names.add(name)
        result = program.assemble_as_object(str(source_path), obj_path)
        if result != 0:
            raise RuntimeError(f"Failed to compile module '{module_name}'")
        return set(program.resolver.dependency_files)

    @staticmethod
    def _accumulate_constants(obj: ObjectFile, accumulated: dict[str, int]) -> None:
        from a816.object_file import SymbolSection as ObjSymbolSection
        from a816.object_file import SymbolType as ObjSymbolType

        # ABS_LABEL is a `.label`-declared address - propagate it like a
        # DATA constant so dependent modules see the binding without needing
        # an explicit `.extern`.
        constant_sections = (ObjSymbolSection.DATA, ObjSymbolSection.ABS_LABEL)
        for name, value, sym_type, section in obj.symbols:
            if sym_type == ObjSymbolType.GLOBAL and section in constant_sections:
                accumulated[name] = value

    def build(self, main_source: Path, parsed_main_nodes: list[AstNode] | None = None) -> ObjectFile:
        """Build all modules in topo order, then link."""
        self.discover_imports(main_source, parsed_main_nodes)
        compilation_order = self.graph.topological_sort()
        logger.info(f"Compilation order: {compilation_order}")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        object_files: list[ObjectFile] = []
        accumulated_constants: dict[str, int] = {}
        recompiled: set[str] = set()
        for module_name in compilation_order:
            source_path = self.graph.modules[module_name]
            obj_path = self._get_obj_path(module_name)
            # A module must rebuild when its own files changed OR when any
            # module it imports recompiled, since its `.o` bakes in the importee's
            # exported constants, so a stale `.o` would carry old values.
            # Compilation order is dependencies-first, so a direct check
            # against `recompiled` is transitive.
            dep_recompiled = any(dep in recompiled for dep in self.graph.dependencies.get(module_name, set()))
            if dep_recompiled or self._needs_recompilation(module_name):
                asset_files = self._compile_module(module_name, source_path, obj_path, accumulated_constants)
                recompiled.add(module_name)
                obj = ObjectFile.from_file(str(obj_path))
                self._write_deps(module_name, source_path, obj, asset_files)
            else:
                logger.info(f"Module {module_name} is up to date")
                obj = ObjectFile.from_file(str(obj_path))
            self._accumulate_constants(obj, accumulated_constants)
            object_files.append(obj)

        if len(object_files) == 1 and not _object_needs_linking(object_files[0]):
            return object_files[0]
        logger.info(f"Linking {len(object_files)} module(s)")
        return Linker(object_files).link(base_address=0x8000)


def _object_needs_linking(obj: ObjectFile) -> bool:
    """Whether a lone object still has link-time work to resolve.

    A single fully-resolved pinned module can be emitted as-is, but pool
    placement, symbol/expression relocations, and aliases are only applied
    by `Linker.link()`. A relocatable module, or one carrying any of those,
    must go through the linker - otherwise placeholder operands (e.g. a
    `.dw OFF` where `OFF = lbl - base`) ship unresolved as 0.
    """
    return bool(
        obj.relocatable
        or obj.aliases
        or obj.pool_allocs
        or any(s.relocations or s.expression_relocations for s in obj.sections)
    )


def _apply_experimental_flags(program: "Program", flags: list[str] | None) -> None:
    """Set experimental feature flags on `program.resolver`.

    Known flags:
      - `track_register_size` - let `rep`/`sep` with constant
        immediate operands mutate `resolver.a_size` / `i_size`.
        Off by default; legacy sources rely on value-driven width
        inference only.
    """
    for flag in flags or []:
        if flag == "track_register_size":
            program.resolver.track_register_size = True
        else:
            logger.warning(f"unknown experimental flag: {flag}")


def build_with_imports(
    main_source: str | Path,
    output_file: str | Path,
    output_format: str = "ips",
    module_paths: list[Path] | None = None,
    output_dir: Path | None = None,
    symbols: dict[str, int | str] | None = None,
    copier_header: bool = False,
    include_paths: list[Path] | None = None,
    overlap_mode: str | None = None,
    experimental: list[str] | None = None,
    mapping: str | None = None,
) -> BuildResult:
    """Build a project: compile every `.import`ed module to `.o`, link.

    Args:
        main_source: Path to the main source file.
        output_file: Path to the output file (IPS or SFC).
        output_format: Output format ("ips" or "sfc").
        module_paths: Additional directories to search for modules.
        output_dir: Directory for compiled object files.
        symbols: Predefined symbols (-D-style) seeded into every
            module's resolver before codegen.
        copier_header: Whether to add copier header offset for IPS.
        include_paths: Additional directories to search for .include files.
        overlap_mode: How to handle overlapping writes (error/warn/off).
        experimental: List of experimental feature flags to enable.

    Returns:
        BuildResult with exit_code, symbol_map, diagnostics, and program.
    """
    main_source = Path(main_source)
    output_file = Path(output_file)

    paths = module_paths or []
    if main_source.parent not in paths:
        paths = [main_source.parent] + paths

    parse_result = A816Parser.parse_as_ast(main_source.read_text(encoding="utf-8"), str(main_source))
    main_nodes = parse_result.nodes

    try:
        builder = ModuleBuilder(
            module_paths=paths,
            output_dir=output_dir,
            symbols=symbols,
            include_paths=include_paths,
        )

        linked = builder.build(main_source, parsed_main_nodes=main_nodes)

        # Output the final file
        from a816.program import Program

        program = Program(overlap_mode=overlap_mode)
        _apply_experimental_flags(program, experimental)
        program.enable_debug_capture()

        if output_format == "ips":
            exit_code = program.link_as_patch(linked, output_file, mapping=mapping, copier_header=copier_header)
        elif output_format == "sfc":
            exit_code = program.link_as_sfc(linked, output_file, mapping=mapping)
        else:
            logger.error(f"Unknown output format: {output_format}")
            return BuildResult(exit_code=1, diagnostics=[f"Unknown output format: {output_format}"])

        symbol_map = dict(program.resolver.get_all_labels())
        # `.label`-declared names are absolute addresses that should appear in
        # the exported symbol map alongside real labels.
        symbol_map.update(program.resolver.get_all_absolute_labels())
        debug_path: Path | None = None
        if exit_code == 0:
            candidate = output_file.with_suffix(output_file.suffix + ".adbg")
            if candidate.exists():
                debug_path = candidate
        return BuildResult(
            exit_code=exit_code,
            symbol_map=symbol_map,
            program=program,
            debug_info_path=debug_path,
        )

    except Exception as e:
        logger.error(f"Build failed: {e}")  # NOSONAR python:S8572
        logger.debug("Build traceback", exc_info=True)
        return BuildResult(exit_code=1, diagnostics=[str(e)])
