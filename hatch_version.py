"""Build-time version source for hatchling.

Reads the `VERSION` env var when set (CI and release pipelines inject the real
tag), otherwise falls back to a dev placeholder so local builds and editable
installs work without exporting anything.
"""

from __future__ import annotations

import os

DEV_VERSION = "0.0.0.dev0"


def get_version() -> str:
    return os.environ.get("VERSION") or DEV_VERSION
