"""Shared loader for `a816.toml` project configuration.

Both the LSP and `a816 fluff` need to find the project root, the
include search paths, and the entrypoint. Centralised here so the
schema lives in one place.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from a816.error_codes import (
    E_CONFIG_BAD_MAP_ENTRY,
    E_CONFIG_BAD_MAP_VALUE,
    E_CONFIG_DUPLICATE_MAP,
    E_CONFIG_UNKNOWN_MAPPER,
    ErrorCode,
)
from a816.exceptions import A816ConfigError
from a816.mappers import MAPPERS
from a816.object_file import BusMapping

CONFIG_FILENAME = "a816.toml"
_MAP_REQUIRED_KEYS = ("identifier", "bank_range", "addr_range", "mask")
_MAP_KEYS = frozenset(_MAP_REQUIRED_KEYS + ("writable", "mirror_bank_range"))


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
    # then each `[[map]]` table. Seeded onto every translation unit's bus.
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
    """Turn the `mapper` key and `[[map]]` tables into `BusMapping`s."""

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path

    def parse(self, data: dict[str, object]) -> tuple[str | None, list[BusMapping]]:
        mapper = self._mapper(data.get("mapper"))
        regions = list(MAPPERS[mapper]) if mapper is not None else []
        raw = data.get("map", [])
        if not isinstance(raw, list):
            raise self._error(E_CONFIG_BAD_MAP_ENTRY, "`map` must be an array of tables (`[[map]]`)")
        regions.extend(self._entry(item, index) for index, item in enumerate(raw))
        self._reject_duplicates(regions)
        return mapper, regions

    def _error(self, code: ErrorCode, message: str) -> A816ConfigError:
        return A816ConfigError(code, message, self.config_path)

    def _mapper(self, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or value not in MAPPERS:
            supported = ", ".join(sorted(MAPPERS))
            raise self._error(E_CONFIG_UNKNOWN_MAPPER, f"unknown mapper {value!r} (supported: {supported})")
        return value

    def _entry(self, item: object, index: int) -> BusMapping:
        where = f"[[map]] #{index + 1}"
        if not isinstance(item, dict):
            raise self._error(E_CONFIG_BAD_MAP_ENTRY, f"{where} must be a table")
        unknown = sorted(set(item) - _MAP_KEYS)
        missing = [key for key in _MAP_REQUIRED_KEYS if key not in item]
        if unknown or missing:
            raise self._error(E_CONFIG_BAD_MAP_ENTRY, f"{where}: unknown keys {unknown}, missing keys {missing}")
        mirror = item.get("mirror_bank_range")
        return BusMapping(
            identifier=self._identifier(item["identifier"], where),
            bank_range=self._pair(item["bank_range"], f"{where} bank_range"),
            addr_range=self._pair(item["addr_range"], f"{where} addr_range"),
            mask=self._int(item["mask"], f"{where} mask"),
            writeable=self._bool(item.get("writable", False), f"{where} writable"),
            mirror_bank_range=None if mirror is None else self._pair(mirror, f"{where} mirror_bank_range"),
        )

    def _identifier(self, value: object, where: str) -> str:
        # `.map identifier=N` only takes a number; keep the same keyspace so
        # a source `.map` and a toml region with the same N are comparable.
        return str(self._int(value, f"{where} identifier"))

    def _int(self, value: object, where: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise self._error(E_CONFIG_BAD_MAP_VALUE, f"{where} must be an integer, got {value!r}")
        return value

    def _bool(self, value: object, where: str) -> bool:
        if not isinstance(value, bool):
            raise self._error(E_CONFIG_BAD_MAP_VALUE, f"{where} must be true or false, got {value!r}")
        return value

    def _pair(self, value: object, where: str) -> tuple[int, int]:
        if not isinstance(value, list) or len(value) != 2:
            raise self._error(E_CONFIG_BAD_MAP_VALUE, f"{where} must be a [start, end] pair, got {value!r}")
        return self._int(value[0], where), self._int(value[1], where)

    def _reject_duplicates(self, regions: list[BusMapping]) -> None:
        seen: set[str] = set()
        for region in regions:
            if region.identifier in seen:
                raise self._error(E_CONFIG_DUPLICATE_MAP, f"map identifier {region.identifier!r} is declared twice")
            seen.add(region.identifier)


def load_a816_toml(config_path: Path) -> A816Config | None:
    """Parse the project config. Return None on read / decode errors.

    Raises:
        A816ConfigError: the file decodes but `mapper` / `[[map]]` is invalid.
    """
    try:
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    root = config_path.parent
    entry = data.get("entrypoint")
    entry_path = (root / entry).resolve() if isinstance(entry, str) else None
    raw_experimental = data.get("experimental", {}) or {}
    experimental = {str(k): bool(v) for k, v in raw_experimental.items() if isinstance(v, bool)}
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
