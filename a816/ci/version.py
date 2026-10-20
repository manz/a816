"""`a816 ci version`: the build version and SOURCE_DATE_EPOCH, before the build."""

from __future__ import annotations

import re

from a816.ci.actions import Actions
from a816.ci.process import Run
from a816.version import a816_version

_PULL_REF = re.compile(r"^refs/pull/(\d+)/")


def build_version(actions: Actions) -> str:
    """The tag on a tag push, `pr-<number>` on a pull request, else `nightly-<run>`."""
    if actions.get("GITHUB_REF_TYPE") == "tag":
        return actions.get("GITHUB_REF_NAME")
    pull = _PULL_REF.match(actions.get("GITHUB_REF"))
    if pull:
        return f"pr-{pull.group(1)}"
    return f"nightly-{actions.get('GITHUB_RUN_NUMBER', '0')}"


def run_version(actions: Actions, run: Run) -> int:
    version = build_version(actions)
    actions.output("a816-version", a816_version())
    actions.output("version", version)
    actions.export("A816_BUILD_VERSION", version)
    epoch = run(["git", "log", "-1", "--format=%ct"], None).decode().strip()
    actions.output("source-date-epoch", epoch)
    if not actions.get("SOURCE_DATE_EPOCH"):
        actions.export("SOURCE_DATE_EPOCH", epoch)
    return 0
