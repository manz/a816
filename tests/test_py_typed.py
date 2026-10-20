"""Both packages the wheel ships carry a PEP 561 `py.typed` marker.

Without `script/py.typed`, star_ocean's strict mypy failed on the 1.1.5
wheel (`import-untyped`); an editable install, or mypy run next to the
sources, hid it.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _shipped_packages() -> list[str]:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    packages: list[str] = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["only-include"]
    return packages


@pytest.mark.parametrize("package", _shipped_packages())
def test_every_shipped_package_is_typed(package: str) -> None:
    assert (ROOT / package / "py.typed").is_file()
