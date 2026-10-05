"""Freshness of cached module objects.

A module's `.o` is reused only when one key over everything that shaped it
still matches: the object format, the build settings (defines, experimental
flags, bus map, search paths), the content of every file it read, the paths
it looked for and did not find, and the keys of the modules it imports.

The key lives in a JSON sidecar next to the object (`<module>.deps`). Each
file entry keeps `(mtime_ns, size, sha256)`: when mtime and size are unchanged
the hash is trusted, otherwise the file is hashed again, so a warm build stats
files instead of reading them, and a file swapped for different bytes is
caught whatever its mtime (`rsync -a`, `cp -p`, `tar x`, an edit inside one
mtime granule).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from a816.object_file import ObjectFile

if TYPE_CHECKING:
    from a816.object_file import BusMapping

SIDECAR_VERSION = 2
# A file whose mtime falls this close to when its hash was recorded may have
# been written again within the same mtime granule: always rehash it.
_RACY_NS = 2_000_000_000
# A sidecar missing any of these (hand-edited, truncated) counts as stale.
_SIDECAR_KEYS = {"identity", "settings", "files", "misses", "imports", "import_keys", "key", "recorded_ns"}


@dataclass(frozen=True)
class BuildSettings:
    """The build-wide inputs every module's object depends on."""

    symbols: dict[str, int | str]
    experimental: list[str]
    bus_map: list[BusMapping]
    include_paths: list[Path]
    module_paths: list[Path]

    def digest(self) -> str:
        return _sha(
            {
                "symbols": sorted((name, repr(value)) for name, value in self.symbols.items()),
                "experimental": sorted(self.experimental),
                "bus_map": [repr(mapping.shape()) for mapping in self.bus_map],
                "include_paths": [os.path.abspath(path) for path in self.include_paths],
                "module_paths": [os.path.abspath(path) for path in self.module_paths],
            }
        )


@dataclass(frozen=True)
class ModuleInputs:
    """What one compile of a module read: its files, the lookups that missed,
    its imports and their keys."""

    files: set[str]
    misses: set[str]
    imports: list[str]
    import_keys: dict[str, str]


class BuildCache:
    """Decides whether a module's cached object can be reused, and records the
    inputs of every object it compiles."""

    def __init__(self, output_dir: Path, settings: BuildSettings, enabled: bool = True) -> None:
        self.output_dir = output_dir
        self.settings_digest = settings.digest()
        self.enabled = enabled

    def sidecar_path(self, obj_path: Path) -> Path:
        return obj_path.with_suffix(".deps")

    def inputs_fresh(self, obj_path: Path, source_path: Path) -> bool:
        """The module's own inputs are unchanged (its imports aside)."""
        data = self._load(obj_path)
        if data is None or os.path.abspath(source_path) not in data["files"]:
            return False
        if data["identity"] != ObjectFile.identity() or data["settings"] != self.settings_digest:
            return False
        header = ObjectFile.read_header(str(obj_path))
        if header is None or header.identity != ObjectFile.identity():
            return False
        recorded_ns = data["recorded_ns"]
        if not all(_file_fresh(path, entry, recorded_ns) for path, entry in data["files"].items()):
            return False
        # Probing recorded lookup misses is the point: a file that now exists
        # shadows an include. The paths are this build's own inputs.
        return not any(os.path.exists(path) for path in data["misses"])  # NOSONAR pythonsecurity:S6549

    def fresh(self, obj_path: Path, source_path: Path, import_keys: dict[str, str]) -> bool:
        """Own inputs unchanged and every import still has the key it had."""
        data = self._load(obj_path)
        return data is not None and data["import_keys"] == import_keys and self.inputs_fresh(obj_path, source_path)

    def imports(self, obj_path: Path) -> list[str] | None:
        data = self._load(obj_path)
        return None if data is None else list(data["imports"])

    def key(self, obj_path: Path) -> str | None:
        data = self._load(obj_path)
        return None if data is None else str(data["key"])

    def record(self, obj_path: Path, inputs: ModuleInputs) -> str:
        """Write the sidecar for a freshly compiled object; return its key."""
        files = {path: _file_entry(path) for path in sorted(inputs.files)}
        key = _sha(
            {
                "identity": ObjectFile.identity(),
                "settings": self.settings_digest,
                "files": [(path, entry[2]) for path, entry in files.items()],
                "misses": sorted(inputs.misses),
                "imports": inputs.imports,
                "import_keys": sorted(inputs.import_keys.items()),
            }
        )
        data = {
            "sidecar": SIDECAR_VERSION,
            "identity": ObjectFile.identity(),
            "settings": self.settings_digest,
            "files": files,
            "misses": sorted(inputs.misses),
            "imports": inputs.imports,
            "import_keys": inputs.import_keys,
            "key": key,
            "recorded_ns": time.time_ns(),
        }
        self.sidecar_path(obj_path).write_text(json.dumps(data, indent=1), encoding="utf-8")
        return key

    def _load(self, obj_path: Path) -> dict[str, Any] | None:
        """The sidecar, or None when caching is off, the object or sidecar is
        missing, or the sidecar predates this format."""
        if not self.enabled or not obj_path.exists():
            return None
        try:
            data = json.loads(self.sidecar_path(obj_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if (
            not isinstance(data, dict)
            or data.get("sidecar") != SIDECAR_VERSION
            or not _SIDECAR_KEYS.issubset(data.keys())
        ):
            return None
        return data


def _sha(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _hash_file(path: str) -> str:
    with open(path, "rb") as fd:
        return hashlib.sha256(fd.read()).hexdigest()


def _file_entry(path: str) -> list[Any]:
    stat = os.stat(path)
    return [stat.st_mtime_ns, stat.st_size, _hash_file(path)]


def _file_fresh(path: str, entry: list[Any], recorded_ns: int) -> bool:
    """The file still holds the bytes it was hashed with."""
    try:
        stat = os.stat(path)
    except OSError:
        return False
    mtime_ns, size, sha = int(entry[0]), int(entry[1]), str(entry[2])  # JSON list: [mtime_ns, size, sha256]
    racy = mtime_ns >= recorded_ns - _RACY_NS
    if not racy and (stat.st_mtime_ns, stat.st_size) == (mtime_ns, size):
        return True
    return _hash_file(path) == sha
