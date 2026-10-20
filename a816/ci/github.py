"""`a816 ci pr-comment|nightly|release`: what a build publishes on GitHub, through `gh`."""

from __future__ import annotations

import glob
import json
import re
import shlex
import time
import zipfile
from collections.abc import Sequence
from pathlib import Path

from a816.ci.actions import Actions
from a816.ci.process import CiError, Run

NIGHTLY_TAG = "nightly"
DEFAULT_COMMENT = "Artifact `{artifact}`: [download it from the run]({url}).\n\nBuilt from {sha}."
DEFAULT_NIGHTLY = "Nightly build {version}, from {sha}. Automated and possibly unstable."
# A PEP 440 pre-release tag: v1.2a1, v1.2.0b2, v1.2rc1.
_PRERELEASE = re.compile(r"^v\d+(\.\d+)*[.-]?(a|b|rc)[.-]?\d*$")


class Gh:
    """`gh` calls; with `dry_run`, writes are printed and reads find nothing."""

    def __init__(self, run: Run, *, dry_run: bool = False) -> None:
        self.run = run
        self.dry_run = dry_run

    def write(self, *args: str, stdin: bytes | None = None) -> None:
        if self.dry_run:
            print("gh", shlex.join(args))
            return
        self.run(["gh", *args], stdin)

    def read(self, *args: str) -> bytes | None:
        """The command's stdout, or None when it fails (a missing release)."""
        if self.dry_run:
            return None
        try:
            return self.run(["gh", *args], None)
        except CiError:
            return None


def fill(template: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


def expand(patterns: Sequence[str]) -> list[str]:
    """Each pattern's matches, sorted; a pattern matching nothing is an error."""
    files: list[str] = []
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        if not matches:
            raise CiError(f"{pattern} matches no file")
        files.extend(matches)
    return files


def _marker(marker: str) -> str:
    return marker if marker.startswith("<!--") else f"<!-- {marker} -->"


def _find_comment(gh: Gh, repo: str, number: str, marker: str) -> str | None:
    listed = gh.read("api", "--paginate", f"repos/{repo}/issues/{number}/comments", "--jq", ".[] | {id, body} | tojson")
    for line in (listed or b"").decode().splitlines():
        comment = json.loads(line)
        if marker in (comment.get("body") or ""):
            return str(comment["id"])
    return None


def run_pr_comment(
    actions: Actions, gh: Gh, *, name: str, marker: str, title: str, body: str, sha: str, outcome: str
) -> int:
    """Keep one comment, found by its marker, that links the PR's artifact; delete it on a failure."""
    repo = actions.get("GITHUB_REPOSITORY")
    pull = re.match(r"^refs/pull/(\d+)/", actions.get("GITHUB_REF"))
    if pull is None:
        raise CiError("pr-comment runs on a pull request only")
    number = pull.group(1)
    tag = _marker(marker)
    existing = _find_comment(gh, repo, number, tag)
    if outcome != "success":
        if existing:
            gh.write("api", "-X", "DELETE", f"repos/{repo}/issues/comments/{existing}")
        return 0
    url = f"{actions.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions/runs/{actions.get('GITHUB_RUN_ID')}"
    text = fill(body or DEFAULT_COMMENT, {"artifact": f"{name}-pr-{number}", "url": url, "sha": sha})
    payload = json.dumps({"body": f"{tag}\n**{title or name}**\n\n{text}"}).encode()
    if existing:
        gh.write("api", "-X", "PATCH", f"repos/{repo}/issues/comments/{existing}", "--input", "-", stdin=payload)
    else:
        gh.write("api", "-X", "POST", f"repos/{repo}/issues/{number}/comments", "--input", "-", stdin=payload)
    return 0


def run_nightly(actions: Actions, gh: Gh, *, files: Sequence[str], title: str, body: str) -> int:
    """Replace the one `nightly` prerelease, and its tag, with this push's files."""
    assets = expand(files)
    sha = actions.get("GITHUB_SHA")
    run_number = actions.get("GITHUB_RUN_NUMBER", "0")
    version = actions.get("A816_BUILD_VERSION", f"nightly-{run_number}")
    notes = fill(body or DEFAULT_NIGHTLY, {"run": run_number, "sha": sha, "version": version})
    if gh.read("release", "view", NIGHTLY_TAG) is not None:
        gh.write("release", "delete", NIGHTLY_TAG, "--cleanup-tag", "--yes")
    gh.write(
        "release", "create", NIGHTLY_TAG, "--prerelease", "--target", sha, "--title", title, "--notes", notes, *assets
    )
    return 0


def is_prerelease(tag: str) -> bool:
    return _PRERELEASE.match(tag) is not None


def make_zip(path: Path, files: Sequence[str], epoch: str) -> None:
    """A reproducible zip: entries flattened to their names, stamped with SOURCE_DATE_EPOCH."""
    stamp = time.gmtime(max(int(epoch or 0), 315532800))[:6]  # zip dates start in 1980
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in files:
            entry = zipfile.ZipInfo(Path(file).name, date_time=stamp)
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, Path(file).read_bytes(), zipfile.ZIP_DEFLATED)


def run_release(
    actions: Actions,
    gh: Gh,
    *,
    assets: Sequence[str],
    zip_name: str,
    zip_files: Sequence[str],
    title: str,
    notes_file: str,
) -> int:
    """Publish the tag's release; a re-run uploads the assets again and keeps the notes."""
    tag = actions.get("GITHUB_REF_NAME")
    files = expand(assets)
    if zip_name:
        if not zip_files:
            raise CiError("zip is set but zip-files is empty")
        make_zip(Path(zip_name), expand(zip_files), actions.get("SOURCE_DATE_EPOCH"))
        files.append(zip_name)
    if not files:
        raise CiError("no assets and no zip: nothing to release")
    if gh.read("release", "view", tag) is not None:
        print(f"release {tag} exists: uploading the assets, keeping its notes")
        gh.write("release", "upload", tag, "--clobber", *files)
        return 0
    args = ["--title", title or tag]
    if is_prerelease(tag):
        args.append("--prerelease")
    args += ["--notes-file", notes_file] if notes_file else ["--generate-notes"]
    gh.write("release", "create", tag, "--verify-tag", *args, *files)
    return 0
