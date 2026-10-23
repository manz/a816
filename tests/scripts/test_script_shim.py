"""`script` forwards to `katsuji.script` until a816 1.3."""

import importlib
import warnings
from types import ModuleType

import katsuji.script
import katsuji.script.pointers
import pytest


def _script() -> ModuleType:
    """The `script` module, its own import warning silenced: tests reload it to see the warning."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return importlib.import_module("script")


def test_importing_script_warns_it_moved() -> None:
    script = _script()
    with pytest.warns(DeprecationWarning, match="katsuji.script"):
        importlib.reload(script)


def test_script_table_is_katsuji_table() -> None:
    assert _script().Table is katsuji.script.Table


def test_script_pointers_forward_to_katsuji() -> None:
    _script()
    assert importlib.import_module("script.pointers").Script is katsuji.script.pointers.Script
