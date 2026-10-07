"""Module builder for automatic dependency resolution and compilation.

This module handles the automatic discovery, compilation, and linking of
modules referenced via .import directives.
"""

import gc
import logging
import os
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from a816.object_file import BusMapping
    from a816.parse.codegen.modules import ParsedImport
    from a816.program import Program

from a816.build_cache import BuildCache, BuildSettings, ModuleInputs
from a816.build_inputs import recording_misses
from a816.config import discover_a816_config, merge_build_settings
from a816.exceptions import A816Error
from a816.linker import Linker
from a816.module_loader import resolve_module
from a816.object_file import ObjectFile
from a816.parse.ast.nodes import (
    AstNode,
    ImportAstNode,
)
from a816.parse.mzparser import A816Parser, ParserResult

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
        experimental: list[str] | None = None,
        bus_map: "list[BusMapping] | None" = None,
        use_cache: bool = True,
    ) -> None:
        """Initialize the module builder.

        Args:
            module_paths: Directories to search for modules.
            output_dir: Directory to write compiled .o files.
            symbols: Predefined symbols (e.g., LANG=1) for conditional compilation.
            include_paths: Directories to search for .include files.
            experimental: Experimental feature flags applied to every module compile.
            bus_map: Bus regions seeded onto every module's bus.
            use_cache: Reuse up-to-date objects from `output_dir` (False compiles every module).
        use_a816_toml: Merge the nearest `a816.toml` above `main_source`
            under the arguments above (`merge_build_settings`), so an API
            build matches `a816 build`. Pass False for a bare build.
        """
        self.module_paths = module_paths or []
        self.output_dir = output_dir or Path("build/obj")
        self.symbols: dict[str, int | str] = symbols or {}
        self.include_paths: list[Path] = include_paths or []
        self.experimental: list[str] = sorted(set(experimental or []))
        self.bus_map: list[BusMapping] = list(bus_map or [])
        self.graph = ModuleGraph()
        self._discovered: set[str] = set()
        # Discovery parses every module with the same inputs compile uses,
        # so compile reuses the AST instead of scanning + parsing twice.
        self._parsed: dict[str, ParserResult] = {}
        self._imports: dict[str, list[str]] = {}
        # Every module re-reads the imports of its imports: parse each
        # imported source once per build, not once per importer.
        self._import_asts: dict[str, ParsedImport] = {}
        # Lookups that missed while parsing a module during discovery: the
        # compile reuses that AST, so they belong to the module's inputs.
        self._discovery_misses: dict[str, set[str]] = {}
        settings = BuildSettings(self.symbols, self.experimental, self.bus_map, self.include_paths, self.module_paths)
        self.cache = BuildCache(self.output_dir, settings, enabled=use_cache)

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
            imports = self._module_imports(source_path, module_name, parsed_nodes)
            self._imports[module_name] = imports

            for import_name in imports:
                self.graph.add_dependency(module_name, import_name)

                # Find the source file for this import
                import_source = self._resolve_module_source(import_name)
                if import_source:
                    self._discover_imports_recursive(import_source, import_name)
                else:
                    logger.warning(f"Could not find source for module '{import_name}'")

        except OSError as e:
            logger.error(f"Error reading {source_path}: {e}")  # NOSONAR python:S8572
            logger.debug("Source read traceback", exc_info=True)
            raise

    def _module_imports(self, source_path: Path, module_name: str, parsed_nodes: list[AstNode] | None) -> list[str]:
        """The module's `.import` names: from the `.deps` cache when its files are unchanged, else by parsing."""
        if parsed_nodes is not None:
            return self._collect_imports(parsed_nodes)
        cached = self._cached_imports(module_name)
        if cached is not None:
            return cached
        with recording_misses() as misses:
            parsed = A816Parser.parse_as_ast(
                source_path.read_text(encoding="utf-8"),
                str(source_path),
                include_paths=list(dict.fromkeys(self.include_paths)),
                verbose_errors=True,
            )
        self._parsed[module_name] = parsed
        self._discovery_misses[module_name] = misses
        return self._collect_imports(parsed.nodes)

    def _cached_imports(self, module_name: str) -> list[str] | None:
        if self._needs_recompilation(module_name):
            return None
        return self.cache.imports(self._get_obj_path(module_name))

    def _collect_imports(self, nodes: list[AstNode]) -> list[str]:
        """Collect all import names from AST nodes."""
        from a816.parse.ast.visitor import walk

        return [node.module_name for node in walk(nodes) if isinstance(node, ImportAstNode)]

    def _resolve_module_source(self, module_name: str) -> Path | None:
        """Find the source file for a module via the shared `module_loader`.

        Search order: stdlib `@std/...` then the configured `module_paths`.

        The importing file's own directory is deliberately NOT searched.
        It used to come first, which let a file shadow a project-wide
        module with a same-named neighbour: `.import "items"` from
        `src/ingame/` picked up `src/ingame/items.s` rather than
        `src/items.s`, compiled the wrong file as that module, and
        surfaced as a missing symbol somewhere else entirely. A module
        under a subdirectory is addressed by its path, `ingame/items`.
        """
        return resolve_module(module_name, ".s", self.module_paths)

    def _needs_recompilation(self, module_name: str) -> bool:
        """Whether a module's own inputs changed since its `.o` was built:
        its files, the lookups that missed, the build settings and the object
        format (`BuildCache.inputs_fresh`). Imports are judged by key in
        `build`, once every importee's key is known."""
        if module_name not in self.graph.modules:
            return True
        return not self.cache.inputs_fresh(self._get_obj_path(module_name), self.graph.modules[module_name])

    def _get_obj_path(self, module_name: str) -> Path:
        """Get the object file path for a module."""
        # Handle modules with path separators (e.g., "battle/sram")
        obj_name = module_name.replace("/", "_") + ".o"
        return self.output_dir / obj_name

    def _compile_module(
        self,
        module_name: str,
        source_path: Path,
        obj_path: Path,
        constants: dict[str, int],
        fallback: dict[str, tuple[int, str]],
    ) -> tuple[set[str], set[str], set[str]]:
        """Compile one module to its `.o`; return the asset paths it read
        (absolute `.incbin` / `.table` paths), the lookups that missed and
        the unimported modules whose constants it used."""
        from a816.program import Program

        logger.info(f"Compiling {module_name}: {source_path} -> {obj_path}")
        program = Program()
        apply_experimental_flags(program, self.experimental)
        program.resolver.context.require_placement = True
        program.resolver.context.bus_map = list(self.bus_map)
        program.resolver.context.import_asts = self._import_asts
        program.add_module_path(self.output_dir)
        for path in self.module_paths:
            program.add_module_path(path)
        for inc_path in self.include_paths:
            program.add_include_path(inc_path)
        for name, value in self.symbols.items():
            program.resolver.current_scope.add_symbol(name, value)
        for name, value in constants.items():
            program.resolver.current_scope.add_symbol(name, value)
            # Constants exported by the modules this one imports are seeded
            # into this module's resolver as raw symbols so codegen can read
            # their values, but they're owned by the contributing module's
            # `.o`. Mark them imported so `_export_object_symbols` doesn't
            # re-publish them here - otherwise every downstream `.o` gains
            # a duplicate GLOBAL and the linker rejects the build.
            program.resolver.imported_symbol_names.add(name)
        program.resolver.unimported_constants = dict(fallback)
        with recording_misses() as misses:
            result = program.assemble_as_object(str(source_path), obj_path, parsed=self._parsed.pop(module_name, None))
        if result != 0:
            # The cache key must not outlive the object it described.
            obj_path.with_suffix(".deps").unlink(missing_ok=True)
            raise RuntimeError(f"Failed to compile module '{module_name}'")
        used = program.resolver.used_unimported
        for name, owner in sorted(used.items()):
            logger.warning(
                f"module `{module_name}` uses `{_spelled(name)}` from `{owner}` without importing it; "
                f'add `.import "{owner}"` (this becomes an error in a816 1.1.0)'
            )
        return (
            set(program.resolver.dependency_files),
            misses | self._discovery_misses.pop(module_name, set()),
            set(used.values()),
        )

    def _build_module(
        self,
        module_name: str,
        keys: dict[str, str],
        constants: dict[str, int],
        fallback: dict[str, tuple[int, str]],
    ) -> ObjectFile:
        """Reuse or compile one module; record its key in `keys`.

        Its `.o` bakes in the exported constants of what it imports, so it is
        reused only when its own inputs are unchanged and every import still
        has the key it was compiled against. Compilation order is
        dependencies-first, so every import's key is already known, and a
        change anywhere upstream reaches every importer through the keys.
        """
        source_path = self.graph.modules[module_name]
        obj_path = self._get_obj_path(module_name)
        imports = self._imports.get(module_name, [])
        import_keys = {name: keys[name] for name in imports if name in keys}
        if self.cache.fresh(obj_path, source_path, import_keys):
            logger.info(f"Module {module_name} is up to date")
            keys[module_name] = self.cache.key(obj_path) or ""
            return ObjectFile.from_file(str(obj_path))
        asset_files, misses, owners = self._compile_module(module_name, source_path, obj_path, constants, fallback)
        obj = ObjectFile.from_file(str(obj_path))
        # An unimported owner whose constants were used: its `.o` is an input,
        # so changing those constants rebuilds this module.
        owner_objects = {os.path.abspath(self._get_obj_path(owner)) for owner in owners}
        files = {
            os.path.abspath(str(source_path)),
            *(os.path.abspath(f) for f in obj.files),
            *asset_files,
            *owner_objects,
        }
        keys[module_name] = self.cache.record(obj_path, ModuleInputs(files, misses, imports, import_keys))
        return obj

    @staticmethod
    def _exported_constants(obj: ObjectFile) -> dict[str, int]:
        """The GLOBAL constants a module's `.o` exports.

        ABS_LABEL is a `.label`-declared address: it propagates like a DATA
        constant so importers see the binding without an explicit `.extern`.
        """
        from a816.object_file import SymbolSection as ObjSymbolSection
        from a816.object_file import SymbolType as ObjSymbolType

        constant_sections = (ObjSymbolSection.DATA, ObjSymbolSection.ABS_LABEL)
        return {
            name: value
            for name, value, sym_type, section in obj.symbols
            if sym_type == ObjSymbolType.GLOBAL and section in constant_sections
        }

    def _transitive_imports(self, module_name: str) -> set[str]:
        seen: set[str] = set()
        pending = list(self._imports.get(module_name, []))
        while pending:
            name = pending.pop()
            if name not in seen:
                seen.add(name)
                pending.extend(self._imports.get(name, []))
        return seen

    def _constants_for(
        self, module_name: str, compilation_order: list[str], exported: dict[str, dict[str, int]]
    ) -> tuple[dict[str, int], dict[str, tuple[int, str]]]:
        """Constants a module sees: those of what it imports, directly or
        not, plus the deprecated fallback of every other module built so far
        (resolved with a warning, see `Resolver.unimported_constant`)."""
        reachable = self._transitive_imports(module_name)
        constants: dict[str, int] = {}
        fallback: dict[str, tuple[int, str]] = {}
        for name in compilation_order:
            if name not in exported:
                continue
            if name in reachable:
                constants.update(exported[name])
            else:
                fallback.update({symbol: (value, name) for symbol, value in exported[name].items()})
        return constants, fallback

    def build(self, main_source: Path, parsed_main_nodes: list[AstNode] | None = None) -> ObjectFile:
        """Build all modules in topo order, then link."""
        with _rare_collections():
            return self._build(main_source, parsed_main_nodes)

    def _build(self, main_source: Path, parsed_main_nodes: list[AstNode] | None) -> ObjectFile:
        self.discover_imports(main_source, parsed_main_nodes)
        compilation_order = self.graph.topological_sort()
        logger.info(f"Compilation order: {compilation_order}")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        object_files: list[ObjectFile] = []
        exported: dict[str, dict[str, int]] = {}
        keys: dict[str, str] = {}
        for module_name in compilation_order:
            constants, fallback = self._constants_for(module_name, compilation_order, exported)
            obj = self._build_module(module_name, keys, constants, fallback)
            exported[module_name] = self._exported_constants(obj)
            object_files.append(obj)

        if len(object_files) == 1 and not _object_needs_linking(object_files[0]):
            return object_files[0]
        logger.info(f"Linking {len(object_files)} module(s)")
        return Linker(object_files).link(base_address=0x8000)


# A build allocates millions of short-lived nodes that die by refcount; the
# default gen0 threshold scans them about a thousand times for little garbage.
_BUILD_GC_THRESHOLD = (50_000, 20, 20)


@contextmanager
def _rare_collections() -> Iterator[None]:
    """Collect cycles rarely while a build runs, then restore the thresholds
    so a long-lived host (the LSP) keeps its own."""
    saved = gc.get_threshold()
    gc.set_threshold(*_BUILD_GC_THRESHOLD)
    try:
        yield
    finally:
        gc.set_threshold(*saved)


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


def apply_experimental_flags(program: "Program", flags: list[str] | None) -> None:
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


# Public API: peer build scripts pass these by keyword; a grouping object
# would break every caller for no gain.
def _spelled(name: str) -> str:
    """A symbol as source writes it: the internal `NAME.__size` is `sizeof(NAME)`."""
    return f"sizeof({name.removesuffix('.__size')})" if name.endswith(".__size") else name


def build_with_imports(
    main_source: str | Path,  # NOSONAR python:S107 (Sonar anchors it here)
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
    bus_map: "list[BusMapping] | None" = None,
    use_a816_toml: bool = True,
    use_cache: bool = True,
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
        mapping: `-m` ROM type used to translate addresses at link time.
        bus_map: `a816.toml` bus regions seeded onto every module's bus.
        use_cache: Reuse up-to-date objects (False compiles every module: `--no-cache`).

    Returns:
        BuildResult with exit_code, symbol_map, diagnostics, and program.
    """
    main_source = Path(main_source)
    output_file = Path(output_file)

    if use_a816_toml:
        settings = merge_build_settings(
            discover_a816_config(main_source.parent),
            mapping=mapping,
            bus_map=bus_map,
            include_paths=include_paths,
            module_paths=module_paths,
            experimental=experimental,
        )
        mapping, bus_map = settings.mapping, settings.bus_map
        include_paths, module_paths = settings.include_paths, settings.module_paths
        experimental = settings.experimental

    paths = module_paths or []
    if main_source.parent not in paths:
        paths = [main_source.parent] + paths

    try:
        builder = ModuleBuilder(
            module_paths=paths,
            output_dir=output_dir,
            symbols=symbols,
            include_paths=include_paths,
            experimental=experimental,
            bus_map=bus_map,
            use_cache=use_cache,
        )

        linked = builder.build(main_source)

        # Output the final file
        from a816.program import Program

        program = Program(overlap_mode=overlap_mode)
        apply_experimental_flags(program, experimental)
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
        # A816Error subclasses carry a `format()` that renders a source-located,
        # human-readable diagnostic; prefer it over the bare `str()` so the
        # build output is actionable rather than e.g. `Build failed: 252`.
        formatted = e.format() if isinstance(e, A816Error) and hasattr(e, "format") else str(e)
        logger.error(f"Build failed: {formatted}")  # NOSONAR python:S8572
        logger.debug("Build traceback", exc_info=True)
        return BuildResult(exit_code=1, diagnostics=[formatted])
