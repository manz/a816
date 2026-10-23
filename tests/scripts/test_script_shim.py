"""`script` forwards to `katsuji.script` until a816 1.3."""

import importlib

import katsuji.script
import katsuji.script.pointers
import pytest


def test_importing_script_warns_it_moved() -> None:
    with pytest.warns(DeprecationWarning, match="katsuji.script"):
        importlib.reload(importlib.import_module("script"))


def test_script_table_is_katsuji_table() -> None:
    with pytest.warns(DeprecationWarning):
        script = importlib.reload(importlib.import_module("script"))
    assert script.Table is katsuji.script.Table


def test_script_pointers_forward_to_katsuji() -> None:
    pointers = importlib.import_module("script.pointers")
    assert pointers.Script is katsuji.script.pointers.Script
