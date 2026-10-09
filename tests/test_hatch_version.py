"""The package version comes from the release tag, without its `v`.

Bahamut Lagoon (rc3): every wheel since the 1.1 alphas said
`Version: v1.1.0rc2` in its METADATA, so `importlib.metadata.version("a816")`
returned "v1.1.0rc2". PyPI and uv normalize it; a strict PEP 440 reader
doesn't.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location("hatch_version", Path(__file__).parent.parent / "hatch_version.py")
assert _SPEC is not None and _SPEC.loader is not None
hatch_version = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(hatch_version)


@pytest.mark.parametrize(("tag", "version"), [("v1.1.0rc3", "1.1.0rc3"), ("1.1.0", "1.1.0"), ("v1.1.0", "1.1.0")])
def test_the_version_is_the_tag_without_its_v(monkeypatch: pytest.MonkeyPatch, tag: str, version: str) -> None:
    monkeypatch.setenv("VERSION", tag)

    assert hatch_version.get_version() == version


def test_without_a_tag_the_version_is_the_dev_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VERSION", raising=False)

    assert hatch_version.get_version() == "0.0.0.dev0"
