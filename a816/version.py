"""The installed a816 version, as `a816 --version` prints it."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

DEV_VERSION = "0.0.0.dev0"


def a816_version() -> str:
    """The version of the installed distribution: the release tag for a
    wheel or the Windows binary (which bundles the metadata), the dev
    placeholder for a source checkout that was never installed."""
    try:
        return version("a816")
    except PackageNotFoundError:
        return DEV_VERSION
