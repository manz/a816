"""Module builder for automatic dependency resolution and compilation.

This module handles the automatic discovery, compilation, and linking of
modules referenced via .import directives.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from a816.program import Program

from a816.linker import Linker
from a816.object_file import ObjectFile
from a816.parse.ast.nodes import (
    AstNode,
    CodePositionAstNode,
    CodeRelocationAstNode,
    ImportAstNode,
)
from a816.parse.mzparser import MZParser

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

            for dep in self.dependencies.get(module, set()):
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
        prelude_file: Path | None = None,
    ) -> None:
        """Initialize the module builder.

        Args:
            module_paths: Directories to search for modules.
            output_dir: Directory to write compiled .o files.
            symbols: Predefined symbols (e.g., LANG=1) for conditional compilation.
            include_paths: Directories to search for .include files.
            prelude_file: Config file prepended to every module compilation.
        """
        self.module_paths = module_paths or []
        self.output_dir = output_dir or Path("build/obj")
        self.symbols: dict[str, int | str] = symbols or {}
        self.include_paths: list[Path] = include_paths or []
        self.prelude_file = prelude_file
        self._prelude_content: str | None = None
        if prelude_file:
            self._prelude_content = prelude_file.read_text(encoding="utf-8")
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
                nodes = MZParser.parse_as_ast(content, str(source_path)).nodes

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
            logger.exception(f"Error reading {source_path}: {e}")
            raise

    def _collect_imports(self, nodes: list[AstNode]) -> list[str]:
        """Collect all import names from AST nodes."""
        from a816.parse.ast.visitor import walk

        return [node.module_name for node in walk(nodes) if isinstance(node, ImportAstNode)]

    def _resolve_module_source(self, module_name: str, base_dir: Path) -> Path | None:
        """Find the source file for a module.

        Args:
            module_name: The module name (e.g., "vwf" or "battle/sram")
            base_dir: The directory of the importing file

        Returns:
            Path to the source file, or None if not found.
        """
        search_paths = [base_dir] + self.module_paths
        module_file = module_name + ".s"

        for search_path in search_paths:
            candidate = search_path / module_file
            if candidate.exists():
                return candidate

        return None

    def _needs_recompilation(self, module_name: str) -> bool:
        """Check if a module needs to be recompiled.

        Args:
            module_name: The module name

        Returns:
            True if the module needs recompilation.
        """
        if module_name not in self.graph.modules:
            return True

        source_path = self.graph.modules[module_name]
        obj_path = self._get_obj_path(module_name)

        if not obj_path.exists():
            return True

        # Check if source is newer than object
        source_mtime = source_path.stat().st_mtime
        obj_mtime = obj_path.stat().st_mtime

        return source_mtime > obj_mtime

    def _get_obj_path(self, module_name: str) -> Path:
        """Get the object file path for a module."""
        # Handle modules with path separators (e.g., "battle/sram")
        obj_name = module_name.replace("/", "_") + ".o"
        return self.output_dir / obj_name

    def _compile_module(self, module_name: str, source_path: Path, obj_path: Path, constants: dict[str, int]) -> None:
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
        result = program.assemble_as_object(str(source_path), obj_path, prelude=self._prelude_content)
        if result != 0:
            raise RuntimeError(f"Failed to compile module '{module_name}'")

    @staticmethod
    def _accumulate_constants(obj: ObjectFile, accumulated: dict[str, int]) -> None:
        from a816.object_file import SymbolSection as ObjSymbolSection
        from a816.object_file import SymbolType as ObjSymbolType

        # ABS_LABEL is a `.label`-declared address — propagate it like a
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
        for module_name in compilation_order:
            source_path = self.graph.modules[module_name]
            obj_path = self._get_obj_path(module_name)
            if self._needs_recompilation(module_name):
                self._compile_module(module_name, source_path, obj_path, accumulated_constants)
            else:
                logger.info(f"Module {module_name} is up to date")
            obj = ObjectFile.from_file(str(obj_path))
            self._accumulate_constants(obj, accumulated_constants)
            object_files.append(obj)

        if len(object_files) == 1:
            return object_files[0]
        logger.info(f"Linking {len(object_files)} modules")
        return Linker(object_files).link(base_address=0x8000)


def _has_position_directives(nodes: list[AstNode]) -> bool:
    """Check if AST contains *= or @= directives (position-dependent code)."""
    from a816.parse.ast.visitor import walk

    return any(isinstance(node, CodePositionAstNode | CodeRelocationAstNode) for node in walk(nodes))


def build_with_imports(
    main_source: str | Path,
    output_file: str | Path,
    output_format: str = "ips",
    module_paths: list[Path] | None = None,
    output_dir: Path | None = None,
    symbols: dict[str, int | str] | None = None,
    copier_header: bool = False,
    include_paths: list[Path] | None = None,
    prelude_file: Path | None = None,
) -> BuildResult:
    """Build a project with automatic import resolution.

    This is the main entry point for building projects that use .import directives.

    For projects with *=directives (position-dependent code like ROM patches),
    use build_with_imports_direct() which assembles the main file directly
    while resolving imported module symbols.

    Args:
        main_source: Path to the main source file.
        output_file: Path to the output file (IPS or SFC).
        output_format: Output format ("ips" or "sfc").
        module_paths: Additional directories to search for modules.
        output_dir: Directory for compiled object files.
        symbols: Predefined symbols for conditional compilation.
        copier_header: Whether to add copier header offset for IPS.
        include_paths: Additional directories to search for .include files.
        prelude_file: Config file prepended to every module compilation.

    Returns:
        BuildResult with exit_code, symbol_map, diagnostics, and program.
    """
    main_source = Path(main_source)
    output_file = Path(output_file)

    # Set up module paths
    paths = module_paths or []
    if main_source.parent not in paths:
        paths = [main_source.parent] + paths

    # Check if the main file uses *= or @= directives (position-dependent code)
    # If so, use the direct assembly approach instead of full object linking
    parse_result = MZParser.parse_as_ast(main_source.read_text(encoding="utf-8"), str(main_source))
    main_nodes = parse_result.nodes
    if _has_position_directives(main_nodes):
        logger.info("Detected position-dependent code (*=), using direct assembly mode")
        return build_with_imports_direct(
            main_source=main_source,
            output_file=output_file,
            output_format=output_format,
            module_paths=paths,
            output_dir=output_dir,
            symbols=symbols,
            copier_header=copier_header,
            include_paths=include_paths,
            prelude_file=prelude_file,
            parsed_main_nodes=main_nodes,
        )

    try:
        builder = ModuleBuilder(
            module_paths=paths,
            output_dir=output_dir,
            symbols=symbols,
        )

        linked = builder.build(main_source, parsed_main_nodes=main_nodes)

        # Output the final file
        from a816.program import Program

        program = Program()
        if output_format == "ips":
            exit_code = program.link_as_patch(linked, output_file, copier_header=copier_header)
        elif output_format == "sfc":
            exit_code = program.link_as_sfc(linked, output_file)
        else:
            logger.error(f"Unknown output format: {output_format}")
            return BuildResult(exit_code=1, diagnostics=[f"Unknown output format: {output_format}"])

        symbol_map = dict(program.resolver.get_all_labels())
        # `.label`-declared names are absolute addresses that should appear in
        # the exported symbol map alongside real labels.
        symbol_map.update(program.resolver.get_all_absolute_labels())
        return BuildResult(exit_code=exit_code, symbol_map=symbol_map, program=program)

    except Exception as e:
        logger.exception(f"Build failed: {e}")
        return BuildResult(exit_code=1, diagnostics=[str(e)])


def _compile_one_module_direct(
    module_name: str,
    source_path: Path,
    obj_path: Path,
    *,
    output_dir: Path,
    paths: list[Path],
    include_paths: list[Path] | None,
    symbols: dict[str, int | str] | None,
    accumulated: dict[str, int],
    prelude_content: str | None,
) -> None:
    from a816.program import Program

    logger.info(f"Compiling module {module_name}: {source_path} -> {obj_path}")
    program = Program()
    program.add_module_path(output_dir)
    for path in paths:
        program.add_module_path(path)
    for inc_path in include_paths or []:
        program.add_include_path(inc_path)
    for name, value in (symbols or {}).items():
        program.resolver.current_scope.add_symbol(name, value)
    for name, value in accumulated.items():
        program.resolver.current_scope.add_symbol(name, value)
    if program.assemble_as_object(str(source_path), obj_path, prelude=prelude_content) != 0:
        raise RuntimeError(f"Failed to compile module '{module_name}'")


def _assemble_main_direct(
    main_source: Path,
    output_file: Path,
    output_format: str,
    *,
    output_dir: Path,
    paths: list[Path],
    include_paths: list[Path] | None,
    symbols: dict[str, int | str] | None,
    copier_header: bool,
    prelude_content: str | None,
    capture_debug: bool = False,
) -> "tuple[int, Program] | BuildResult":  # noqa: F821
    from a816.program import Program

    program = Program()
    if capture_debug:
        program.enable_debug_capture()
    program.add_module_path(output_dir)
    for path in paths:
        program.add_module_path(path)
    for inc_path in include_paths or []:
        program.add_include_path(inc_path)
    for name, value in (symbols or {}).items():
        program.resolver.current_scope.add_symbol(name, value)

    if output_format == "ips":
        exit_code = program.assemble_as_patch(
            str(main_source), output_file, copier_header=copier_header, prelude=prelude_content
        )
    elif output_format == "sfc":
        exit_code = program.assemble(str(main_source), output_file, prelude=prelude_content)
    else:
        logger.error(f"Unknown output format: {output_format}")
        return BuildResult(exit_code=1, diagnostics=[f"Unknown output format: {output_format}"])
    return exit_code, program


def build_with_imports_direct(
    main_source: str | Path,
    output_file: str | Path,
    output_format: str = "ips",
    module_paths: list[Path] | None = None,
    output_dir: Path | None = None,
    symbols: dict[str, int | str] | None = None,
    copier_header: bool = False,
    include_paths: list[Path] | None = None,
    prelude_file: Path | None = None,
    parsed_main_nodes: list[AstNode] | None = None,
    emit_debug_info: bool = True,
) -> BuildResult:
    """Pre-compile imported modules then assemble main directly (position-dependent ROM patches)."""
    main_source = Path(main_source)
    output_file = Path(output_file)
    output_dir = output_dir or Path("build/obj")

    paths = module_paths or []
    if main_source.parent not in paths:
        paths = [main_source.parent] + paths

    prelude_content: str | None = prelude_file.read_text(encoding="utf-8") if prelude_file else None

    try:
        builder = ModuleBuilder(module_paths=paths, output_dir=output_dir, symbols=symbols)
        builder.discover_imports(main_source, parsed_main_nodes)
        modules_to_compile = [m for m in builder.graph.topological_sort() if m != "__main__"]
        output_dir.mkdir(parents=True, exist_ok=True)

        accumulated_constants: dict[str, int] = {}
        for module_name in modules_to_compile:
            source_path = builder.graph.modules[module_name]
            obj_path = builder._get_obj_path(module_name)
            if builder._needs_recompilation(module_name):
                _compile_one_module_direct(
                    module_name,
                    source_path,
                    obj_path,
                    output_dir=output_dir,
                    paths=paths,
                    include_paths=include_paths,
                    symbols=symbols,
                    accumulated=accumulated_constants,
                    prelude_content=prelude_content,
                )
            else:
                logger.info(f"Module {module_name} is up to date")
            ModuleBuilder._accumulate_constants(ObjectFile.from_file(str(obj_path)), accumulated_constants)

        logger.info(f"Assembling main file: {main_source}")
        result = _assemble_main_direct(
            main_source,
            output_file,
            output_format,
            output_dir=output_dir,
            paths=paths,
            include_paths=include_paths,
            symbols=symbols,
            copier_header=copier_header,
            prelude_content=prelude_content,
            capture_debug=emit_debug_info,
        )
        if isinstance(result, BuildResult):
            return result
        exit_code, program = result
        symbol_map = dict(program.resolver.get_all_labels())
        # `.label`-declared names are absolute addresses that should appear in
        # the exported symbol map alongside real labels.
        symbol_map.update(program.resolver.get_all_absolute_labels())

        debug_path: Path | None = None
        if emit_debug_info and exit_code == 0:
            from a816.debug_info import write as write_debug_info

            debug_path = output_file.with_suffix(output_file.suffix + ".adbg")
            write_debug_info(program.build_debug_info(main_source), debug_path)
        return BuildResult(
            exit_code=exit_code,
            symbol_map=symbol_map,
            program=program,
            debug_info_path=debug_path,
        )

    except Exception as e:
        logger.exception(f"Build failed: {e}")
        return BuildResult(exit_code=1, diagnostics=[str(e)])
