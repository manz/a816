"""Build-time version source for hatchling.

Reads the `VERSION` env var when set (CI and release pipelines inject the real
tag), otherwise falls back to a dev placeholder so local builds and editable
installs work without exporting anything.
"""

from __future__ import annotations

import os

DEV_VERSION = "0.0.0.dev0"


def get_version() -> str:
    """`VERSION` without a tag's `v`: CI passes the tag (`v1.1.0rc2`), and the
    wheels since the 1.1 alphas carried `Version: v1.1.0rc2` in METADATA,
    which a strict PEP 440 reader rejects."""
    return (os.environ.get("VERSION") or DEV_VERSION).removeprefix("v")
