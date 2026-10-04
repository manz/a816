"""Shared loader for `a816.toml` project configuration.

Both the LSP and `a816 fluff` need to find the project root, the
include search paths, and the entrypoint. Centralised here so the
schema lives in one place.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from a816.cpu.mapping import parse_bml_address
from a816.error_codes import (
    E_CONFIG_BAD_EXPERIMENTAL,
    E_CONFIG_BAD_MAP_ENTRY,
    E_CONFIG_BAD_MAP_VALUE,
    E_CONFIG_INVALID,
    E_CONFIG_MAPPER_AND_MAP,
    E_CONFIG_MAPPER_MISMATCH,
    E_CONFIG_UNKNOWN_MAPPER,
    ErrorCode,
)
from a816.exceptions import A816ConfigError
from a816.mappers import CLI_MAPPERS, MAPPER_CLI_FLAGS, MAPPERS
from a816.object_file import BusMapping

CONFIG_FILENAME = "a816.toml"
_MAP_REQUIRED_KEYS = ("address",)
_MAP_KEYS = frozenset(_MAP_REQUIRED_KEYS + ("mask", "base", "writable"))


@dataclass(frozen=True)
class A816Config:
    """Resolved view of a project's `a816.toml` settings."""

    config_path: Path
    entrypoint: Path | None = None
    include_paths: list[Path] = field(default_factory=list)
    module_paths: list[Path] = field(default_factory=list)
    # Opt-in experimental feature flags. Each entry maps a flag name
    # to its boolean value; the CLI surfaces these via
    # `--experimental NAME` (and `--no-experimental NAME` for explicit
    # off). Mirrors the [experimental] table in `a816.toml`.
    experimental: dict[str, bool] = field(default_factory=dict)
    # Cartridge preset named by `mapper = ...`, kept so the CLI can check
    # it against `-m`; its regions are already expanded into `bus_map`.
    mapper: str | None = None
    # Every bus region the project declares: the `mapper` preset first,
    # or the `[map.N]` tables (never both). Seeded onto every translation unit's bus.
    bus_map: list[BusMapping] = field(default_factory=list)

    @property
    def root(self) -> Path:
        return self.config_path.parent


def find_a816_toml(start: Path) -> Path | None:
    """Walk upwards from `start` looking for `a816.toml`. Return its path or None."""
    current = start.resolve()
    if current.is_file():
        current = current.parent
    while True:
        candidate = current / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
        if current.parent == current:
            return None
        current = current.parent


def _resolve_paths(root: Path, raw: list[str]) -> list[Path]:
    return [(root / item).resolve() for item in raw]


class _BusMapParser:
    """Turn the `mapper` key and `[map.N]` tables into `BusMapping`s."""

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path

    def parse(self, data: dict[str, object]) -> tuple[str | None, list[BusMapping]]:
        mapper = self._mapper(data.get("mapper"))
        raw = data.get("map", {})
        if not isinstance(raw, dict):
            raise self._error(E_CONFIG_BAD_MAP_ENTRY, "`map` must be a table of regions (`[map.N]`)")
        if mapper is not None and raw:
            raise self._error(
                E_CONFIG_MAPPER_AND_MAP, "`mapper` and `[map.N]` are mutually exclusive: use one or the other"
            )
        if mapper is not None:
            return mapper, list(MAPPERS[mapper])
        rom_size = self._rom_size(data.get("rom_size"))
        regions = [self._entry(key, item, rom_size) for key, item in raw.items()]
        self._reject_aliased_keys(regions)
        self._require_rom_size(regions, rom_size)
        return None, regions

    def _rom_size(self, value: object) -> int:
        if value is None:
            return 0
        size = self._int(value, "rom_size")
        if size <= 0:
            raise self._error(E_CONFIG_BAD_MAP_VALUE, f"rom_size must be positive, got {size}")
        return size

    def _require_rom_size(self, regions: list[BusMapping], rom_size: int) -> None:
        """bsnes folds a ROM address by the image size, so a read-only region needs it."""
        if rom_size or all(region.writeable for region in regions):
            return
        raise self._error(
            E_CONFIG_BAD_MAP_VALUE,
            "`rom_size` is required when `[map.N]` declares a read-only (ROM) region",
        )

    def _error(self, code: ErrorCode, message: str) -> A816ConfigError:
        return A816ConfigError(code, message, self.config_path)

    def _mapper(self, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or value not in MAPPERS:
            supported = ", ".join(sorted(MAPPERS))
            raise self._error(E_CONFIG_UNKNOWN_MAPPER, f"unknown mapper {value!r} (supported: {supported})")
        return value

    def _entry(self, key: str, item: object, rom_size: int) -> BusMapping:
        where = f"[map.{key}]"
        if not isinstance(item, dict):
            raise self._error(E_CONFIG_BAD_MAP_ENTRY, f"{where} must be a table")
        self._check_keys(item, where)
        return BusMapping.bml(
            identifier=self._identifier(key, where),
            address=self._address(item["address"], f"{where} address"),
            mask=self._int(item.get("mask", 0), f"{where} mask"),
            base=self._int(item.get("base", 0), f"{where} base"),
            rom_size=rom_size,
            writeable=self._bool(item.get("writable", False), f"{where} writable"),
        )

    def _address(self, value: object, where: str) -> str:
        """A BML `map address=` value: `00-7d,80-ff:8000-ffff`."""
        if not isinstance(value, str):
            raise self._error(E_CONFIG_BAD_MAP_VALUE, f'{where} must be a string like "00-3f,80-bf:8000-ffff"')
        try:
            parse_bml_address(value)
        except ValueError as exc:
            raise self._error(E_CONFIG_BAD_MAP_VALUE, f"{where}: {exc}") from None
        return value

    def _check_keys(self, item: dict[str, object], where: str) -> None:
        unknown = sorted(set(item) - _MAP_KEYS)
        missing = [name for name in _MAP_REQUIRED_KEYS if name not in item]
        problems = [
            f"{label} {', '.join(names)}"
            for label, names in (("unknown keys", unknown), ("missing keys", missing))
            if names
        ]
        if problems:
            raise self._error(E_CONFIG_BAD_MAP_ENTRY, f"{where}: {'; '.join(problems)}")

    def _identifier(self, key: str, where: str) -> str:
        """`.map identifier=N` only takes a number; keep the same keyspace so
        a source `.map` and a toml region with the same N are comparable."""
        try:
            return str(int(key, 0))
        except ValueError:
            raise self._error(E_CONFIG_BAD_MAP_VALUE, f"{where}: the region key must be an integer") from None

    def _int(self, value: object, where: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise self._error(E_CONFIG_BAD_MAP_VALUE, f"{where} must be an integer, got {value!r}")
        return value

    def _bool(self, value: object, where: str) -> bool:
        if not isinstance(value, bool):
            raise self._error(E_CONFIG_BAD_MAP_VALUE, f"{where} must be true or false, got {value!r}")
        return value

    def _reject_aliased_keys(self, regions: list[BusMapping]) -> None:
        """TOML rejects a repeated `[map.N]`; this catches spellings of one number (`[map.1]`, `[map.0x1]`)."""
        seen: set[str] = set()
        for region in regions:
            if region.identifier in seen:
                raise self._error(E_CONFIG_BAD_MAP_ENTRY, f"map identifier {region.identifier} is declared twice")
            seen.add(region.identifier)


def _experimental(raw: object, config_path: Path) -> dict[str, bool]:
    """The `[experimental]` table: flag name -> true / false, nothing else."""
    if not isinstance(raw, dict):
        raise A816ConfigError(E_CONFIG_BAD_EXPERIMENTAL, "`experimental` must be a table of flags", config_path)
    for name, value in raw.items():
        if not isinstance(value, bool):
            raise A816ConfigError(
                E_CONFIG_BAD_EXPERIMENTAL,
                f"[experimental] {name} must be true or false, got {value!r}",
                config_path,
            )
    return {str(name): value for name, value in raw.items()}


def load_a816_toml(config_path: Path) -> A816Config | None:
    """Parse the project config. Return None when the file can't be read.

    Raises:
        A816ConfigError: the file is not valid TOML, or `[experimental]` /
            `mapper` / `[map.N]` is invalid.
    """
    try:
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    except OSError:
        return None
    except tomllib.TOMLDecodeError as exc:
        raise A816ConfigError(E_CONFIG_INVALID, f"not valid TOML: {exc}", config_path) from None
    root = config_path.parent
    entry = data.get("entrypoint")
    entry_path = (root / entry).resolve() if isinstance(entry, str) else None
    experimental = _experimental(data.get("experimental", {}), config_path)
    mapper, bus_map = _BusMapParser(config_path).parse(data)
    return A816Config(
        config_path=config_path,
        entrypoint=entry_path,
        include_paths=_resolve_paths(root, data.get("include-paths", []) or []),
        module_paths=_resolve_paths(root, data.get("module-paths", []) or []),
        experimental=experimental,
        mapper=mapper,
        bus_map=bus_map,
    )


def discover_a816_config(start: Path) -> A816Config | None:
    """Find + load the nearest `a816.toml` above `start`, or return None."""
    found = find_a816_toml(start)
    if found is None:
        return None
    return load_a816_toml(found)


@dataclass(frozen=True)
class BuildSettings:
    """Build inputs after merging caller values over `a816.toml`."""

    mapping: str | None
    bus_map: list[BusMapping]
    include_paths: list[Path]
    module_paths: list[Path]
    experimental: list[str]


def merge_build_settings(
    config: A816Config | None,
    *,
    mapping: str | None = None,
    bus_map: list[BusMapping] | None = None,
    include_paths: list[Path] | None = None,
    module_paths: list[Path] | None = None,
    experimental: list[str] | None = None,
) -> BuildSettings:
    """Merge caller-supplied build inputs over a project's `a816.toml`.

    Shared by the CLI and `build_with_imports` so both entry points build
    the same thing. A value the caller gives (non-empty) wins over the
    file; the file fills the rest. Experimental flags are the union of
    both. `mapping` (the `-m` flag) must agree with the toml `mapper`:
    the toml regions are seeded into every object, so letting one side
    silently win would build a bus that matches neither.

    Raises:
        A816ConfigError: `mapping` disagrees with the toml `mapper`.
    """
    flags = list(experimental or [])
    if config is None:
        return BuildSettings(mapping, list(bus_map or []), list(include_paths or []), list(module_paths or []), flags)
    for flag, enabled in config.experimental.items():
        if enabled and flag not in flags:
            flags.append(flag)
    return BuildSettings(
        mapping=_merge_mapping(mapping, config),
        bus_map=list(bus_map) if bus_map else list(config.bus_map),
        include_paths=list(include_paths) if include_paths else list(config.include_paths),
        module_paths=list(module_paths) if module_paths else list(config.module_paths),
        experimental=flags,
    )


def _merge_mapping(mapping: str | None, config: A816Config) -> str | None:
    if config.mapper is None:
        return mapping
    if mapping is None:
        return MAPPER_CLI_FLAGS[config.mapper]
    if CLI_MAPPERS[mapping] != config.mapper:
        raise A816ConfigError(
            E_CONFIG_MAPPER_MISMATCH,
            f"`-m {mapping}` disagrees with `mapper = {config.mapper!r}`; drop one of them",
            config.config_path,
        )
    return mapping
