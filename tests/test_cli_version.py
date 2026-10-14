"""`a816 --version` prints the installed version.

There was no way to ask: `--version` failed on the missing input files, so
a peer checking which a816 it built with had to ask pip instead.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError

import pytest

from a816 import version as version_module
from a816.cli import _dispatch_subcommand


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_the_flag_prints_the_version(flag: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert _dispatch_subcommand([flag]) == 0
    assert capsys.readouterr().out == f"a816 {version_module.a816_version()}\n"


def test_build_takes_the_flag_too(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        _dispatch_subcommand(["build", "--version"])

    assert (exit_info.value.code, capsys.readouterr().out.startswith("a816 ")) == (0, True)


def test_the_version_comes_from_the_installed_distribution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(version_module, "version", lambda name: "1.1.9")

    assert version_module.a816_version() == "1.1.9"


def test_an_uninstalled_checkout_reports_the_dev_version(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(version_module, "version", missing)

    assert version_module.a816_version() == version_module.DEV_VERSION
