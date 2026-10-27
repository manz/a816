"""Assembly context holding compilation mode and configuration."""

from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from a816.object_file import BusMapping
    from a816.parse.codegen.modules import ParsedImport
    from a816.writers import ObjectWriter


class AssemblyMode(Enum):
    """Assembly compilation mode."""

    PARSE = auto()  # Parsing only (tests, LSP, etc.)
    OBJECT = auto()  # Compiling to object file


@dataclass
class AssemblyContext:
    """Holds compilation state that was previously scattered across hasattr checks."""

    mode: AssemblyMode = AssemblyMode.PARSE
    object_writer: ObjectWriter | None = None
    module_paths: list[Path] = field(default_factory=list)
    include_paths: list[Path] = field(default_factory=list)
    # How the link reacts to overlapping byte writes from
    # different `*=` / `.alloc` / `.relocate` sections. Default is
    # `"error"` since 2026-05: silent last-write-wins on `*=` overlap
    # was the #1 footgun in user code (cross-repo feedback). Flip to
    # `"warn"` to keep building while migrating, or `"off"` to skip
    # the check entirely. CLI: `--overlap-mode={error,warn,off}`.
    overlap_mode: str = "error"
    # Object mode only: reject bytes emitted before any `*=` / outside
    # `.alloc` (E0310) instead of leaving the `.o` relocatable. Set by
    # `ModuleBuilder` for every module of an `a816 build <entry>` link;
    # explicit `--compile-only` objects stay relocatable.
    require_placement: bool = False
    # `mapper` or `[map.N]` regions from `a816.toml`, declared on the bus
    # of every translation unit before its own `.map` lines.
    bus_map: list[BusMapping] = field(default_factory=list)
    # Object-mode `.import` parses shared by every module of one build, keyed
    # by source path. `ModuleBuilder` owns it; None parses every import afresh.
    import_asts: dict[str, ParsedImport] | None = None
    # The include paths as `ModuleBuilder` gives them (unresolved), for every
    # parse of a build: discovery, a module's own compile and its imports' all
    # name an included file the same way, cold or warm. None falls back to
    # `include_paths`.
    parse_include_paths: list[Path] | None = None
    # SPC700 programs `.embed` names, linked by `ModuleBuilder` before any
    # module compiles: name -> its ARAM image, its symbols in a `.json` beside it.
    embedded_programs: dict[str, Path] = field(default_factory=dict)

    @property
    def is_object_mode(self) -> bool:
        return self.mode == AssemblyMode.OBJECT and self.object_writer is not None
