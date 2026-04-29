"""Assembly context holding compilation mode and configuration."""

from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from a816.writers import ObjectWriter


class AssemblyMode(Enum):
    """Assembly compilation mode."""

    PARSE = auto()  # Parsing only (tests, LSP, etc.)
    DIRECT = auto()  # Direct assembly (position-dependent code)
    OBJECT = auto()  # Compiling to object file


@dataclass
class AssemblyContext:
    """Holds compilation state that was previously scattered across hasattr checks."""

    mode: AssemblyMode = AssemblyMode.PARSE
    object_writer: "ObjectWriter | None" = None
    module_paths: list[Path] = field(default_factory=list)
    include_paths: list[Path] = field(default_factory=list)
    prelude_file: Path | None = None

    @property
    def is_object_mode(self) -> bool:
        return self.mode == AssemblyMode.OBJECT and self.object_writer is not None

    @property
    def is_direct_mode(self) -> bool:
        return self.mode == AssemblyMode.DIRECT
