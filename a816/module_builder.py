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
    IncludeAstNode,
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

    def discover_imports(self, source_file: Path) -> None:
        """Recursively discover all imports starting from a source file.

        Args:
            source_file: The main source file to start from.
        """
        self._discover_imports_recursive(source_file, "__main__")

    def _discover_imports_recursive(self, source_path: Path, module_name: str) -> None:
        """Recursively discover imports from a source file."""
        if module_name in self._discovered:
            return

        self._discovered.add(module_name)
        self.graph.add_module(module_name, source_path)

        # Parse the file to find imports
        try:
            content = source_path.read_text(encoding="utf-8")
            result = MZParser.parse_as_ast(content, str(source_path))

            # Find all import nodes
            imports = self._collect_imports(result.nodes)

            for import_name in imports:
                self.graph.add_dependency(module_name, import_name)

                # Find the source file for this import
                import_source = self._resolve_module_source(import_name, source_path.parent)
                if import_source:
                    self._discover_imports_recursive(import_source, import_name)
                else:
                    logger.warning(f"Could not find source for module '{import_name}'")

        except OSError as e:
            logger.error(f"Error reading {source_path}: {e}")
            raise

    def _collect_imports(self, nodes: list[AstNode]) -> list[str]:
        """Collect all import names from AST nodes."""
        imports: list[str] = []

        for node in nodes:
            if isinstance(node, ImportAstNode):
                imports.append(node.module_name)

            # Recurse into compound structures
            if hasattr(node, "body") and node.body:
                if hasattr(node.body, "body"):
                    imports.extend(self._collect_imports(node.body.body))
                elif isinstance(node.body, list):
                    imports.extend(self._collect_imports(node.body))

            if hasattr(node, "block") and node.block:
                if hasattr(node.block, "body"):
                    imports.extend(self._collect_imports(node.block.body))

            if hasattr(node, "else_block") and node.else_block:
                if hasattr(node.else_block, "body"):
                    imports.extend(self._collect_imports(node.else_block.body))

            if hasattr(node, "included_nodes") and node.included_nodes:
                imports.extend(self._collect_imports(node.included_nodes))

        return imports

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

    def build(self, main_source: Path) -> ObjectFile:
        """Build all modules and link them.

        Args:
            main_source: The main source file.

        Returns:
            The linked ObjectFile.
        """
        from a816.program import Program

        # Discover all imports
        self.discover_imports(main_source)

        # Get compilation order
        compilation_order = self.graph.topological_sort()
        logger.info(f"Compilation order: {compilation_order}")

        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Compile each module, accumulating DATA constants for downstream modules
        from a816.object_file import SymbolSection as ObjSymbolSection
        from a816.object_file import SymbolType as ObjSymbolType

        object_files: list[ObjectFile] = []
        accumulated_constants: dict[str, int] = {}

        for module_name in compilation_order:
            source_path = self.graph.modules[module_name]
            obj_path = self._get_obj_path(module_name)

            if self._needs_recompilation(module_name):
                logger.info(f"Compiling {module_name}: {source_path} -> {obj_path}")

                program = Program()

                # Add module paths for import resolution
                program.add_module_path(self.output_dir)
                for path in self.module_paths:
                    program.add_module_path(path)
                for inc_path in self.include_paths:
                    program.add_include_path(inc_path)

                # Add predefined symbols
                for name, value in self.symbols.items():
                    program.resolver.current_scope.add_symbol(name, value)

                # Inject accumulated constants from previously compiled modules
                for name, value in accumulated_constants.items():
                    program.resolver.current_scope.add_symbol(name, value)

                result = program.assemble_as_object(str(source_path), obj_path, prelude=self._prelude_content)
                if result != 0:
                    raise RuntimeError(f"Failed to compile module '{module_name}'")
            else:
                logger.info(f"Module {module_name} is up to date")

            # Extract GLOBAL+DATA symbols (constants) for downstream modules
            obj = ObjectFile.read(str(obj_path))
            for name, value, sym_type, section in obj.symbols:
                if sym_type == ObjSymbolType.GLOBAL and section == ObjSymbolSection.DATA:
                    accumulated_constants[name] = value

            object_files.append(obj)

        # Link all object files
        if len(object_files) == 1:
            return object_files[0]

        logger.info(f"Linking {len(object_files)} modules")
        linker = Linker(object_files)
        # Default base address is 0x8000 for SNES LoROM
        return linker.link(base_address=0x8000)


def _has_position_directives(nodes: list[AstNode]) -> bool:
    """Check if AST contains *= or @= directives (position-dependent code).

    Walks the AST recursively to find CodePositionAstNode or CodeRelocationAstNode
    instances, which indicate position-dependent code that requires direct assembly.
    """
    for node in nodes:
        if isinstance(node, (CodePositionAstNode, CodeRelocationAstNode)):
            return True

        if hasattr(node, "body") and node.body:
            if hasattr(node.body, "body") and isinstance(node.body.body, list):
                if _has_position_directives(node.body.body):
                    return True
            elif isinstance(node.body, list):
                if _has_position_directives(node.body):
                    return True

        if hasattr(node, "block") and node.block:
            if hasattr(node.block, "body") and isinstance(node.block.body, list):
                if _has_position_directives(node.block.body):
                    return True

        if hasattr(node, "else_block") and node.else_block:
            if hasattr(node.else_block, "body") and isinstance(node.else_block.body, list):
                if _has_position_directives(node.else_block.body):
                    return True

        if isinstance(node, IncludeAstNode) and node.included_nodes:
            if _has_position_directives(node.included_nodes):
                return True

    return False


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
    if _has_position_directives(parse_result.nodes):
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
        )

    try:
        builder = ModuleBuilder(
            module_paths=paths,
            output_dir=output_dir,
            symbols=symbols,
        )

        linked = builder.build(main_source)

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

        symbol_map = {name: value for name, value in program.resolver.get_all_labels()}
        return BuildResult(exit_code=exit_code, symbol_map=symbol_map, program=program)

    except Exception as e:
        logger.error(f"Build failed: {e}")
        return BuildResult(exit_code=1, diagnostics=[str(e)])


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
) -> BuildResult:
    """Build a project by directly assembling with import resolution.

    This approach is for projects with position-dependent code (*= directives)
    like ROM patches. Instead of compiling to object files and linking,
    it pre-compiles modules and then assembles the main file directly,
    resolving imported symbols from the compiled modules.

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
    from a816.program import Program

    main_source = Path(main_source)
    output_file = Path(output_file)
    output_dir = output_dir or Path("build/obj")

    # Set up module paths
    paths = module_paths or []
    if main_source.parent not in paths:
        paths = [main_source.parent] + paths

    # Read prelude content once
    prelude_content: str | None = None
    if prelude_file:
        prelude_content = prelude_file.read_text(encoding="utf-8")

    try:
        # 1. Discover imports and compile modules
        builder = ModuleBuilder(
            module_paths=paths,
            output_dir=output_dir,
            symbols=symbols,
        )
        builder.discover_imports(main_source)

        # Get compilation order (excluding main)
        compilation_order = builder.graph.topological_sort()
        modules_to_compile = [m for m in compilation_order if m != "__main__"]

        output_dir.mkdir(parents=True, exist_ok=True)

        # Compile each module to .o file, accumulating DATA constants
        from a816.object_file import SymbolSection as ObjSymbolSection
        from a816.object_file import SymbolType as ObjSymbolType

        accumulated_constants: dict[str, int] = {}

        for module_name in modules_to_compile:
            source_path = builder.graph.modules[module_name]
            obj_path = builder._get_obj_path(module_name)

            if builder._needs_recompilation(module_name):
                logger.info(f"Compiling module {module_name}: {source_path} -> {obj_path}")

                program = Program()
                program.add_module_path(output_dir)
                for path in paths:
                    program.add_module_path(path)
                for inc_path in include_paths or []:
                    program.add_include_path(inc_path)

                for name, value in (symbols or {}).items():
                    program.resolver.current_scope.add_symbol(name, value)

                # Inject accumulated constants from previously compiled modules
                for name, value in accumulated_constants.items():
                    program.resolver.current_scope.add_symbol(name, value)

                compile_result = program.assemble_as_object(str(source_path), obj_path, prelude=prelude_content)
                if compile_result != 0:
                    raise RuntimeError(f"Failed to compile module '{module_name}'")
            else:
                logger.info(f"Module {module_name} is up to date")

            # Extract GLOBAL+DATA symbols (constants) for downstream modules
            obj = ObjectFile.read(str(obj_path))
            for sym_name, sym_value, sym_type, section in obj.symbols:
                if sym_type == ObjSymbolType.GLOBAL and section == ObjSymbolSection.DATA:
                    accumulated_constants[sym_name] = sym_value

        # 2. Assemble main file directly with module paths configured
        logger.info(f"Assembling main file: {main_source}")
        program = Program()

        # Add module paths for import resolution
        program.add_module_path(output_dir)
        for path in paths:
            program.add_module_path(path)
        for inc_path in include_paths or []:
            program.add_include_path(inc_path)

        # Add predefined symbols
        for name, value in (symbols or {}).items():
            program.resolver.current_scope.add_symbol(name, value)

        # Assemble directly to output format
        if output_format == "ips":
            exit_code = program.assemble_as_patch(
                str(main_source), output_file, copier_header=copier_header, prelude=prelude_content
            )
        elif output_format == "sfc":
            exit_code = program.assemble(str(main_source), output_file, prelude=prelude_content)
        else:
            logger.error(f"Unknown output format: {output_format}")
            return BuildResult(exit_code=1, diagnostics=[f"Unknown output format: {output_format}"])

        symbol_map = {name: value for name, value in program.resolver.get_all_labels()}
        return BuildResult(exit_code=exit_code, symbol_map=symbol_map, program=program)

    except Exception as e:
        logger.error(f"Build failed: {e}")
        return BuildResult(exit_code=1, diagnostics=[str(e)])
