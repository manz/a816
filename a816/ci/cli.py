"""`a816 ci <step>` argument parsing and dispatch."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from a816.ci.actions import Actions
from a816.ci.github import Gh, run_nightly, run_pr_comment, run_release
from a816.ci.process import CiError, Run, run_command
from a816.ci.project import run_build, run_lint, run_seal
from a816.ci.rom import PASSPHRASE_VAR, run_decrypt_rom
from a816.ci.version import run_version


def lines(text: str) -> list[str]:
    """A multi-line action input: one item per non-blank line."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="a816 ci", description="The steps behind a816's GitHub actions.")
    steps = parser.add_subparsers(dest="step", required=True)

    steps.add_parser("version", help="Derive the build version and SOURCE_DATE_EPOCH.")

    decrypt = steps.add_parser("decrypt-rom", help=f"Decrypt the base ROM with ${PASSPHRASE_VAR}.")
    decrypt.add_argument("encrypted", type=Path)
    decrypt.add_argument("--to", type=Path, required=True)
    decrypt.add_argument("--sha1", default="")

    lint = steps.add_parser("lint", help="a816 format --diff, then a816 check.")
    lint.add_argument("--paths", type=lines, default=[])
    lint.add_argument("--no-format", dest="check_format", action="store_false")

    build = steps.add_parser("build", help="a816 build to several outputs.")
    build.add_argument("--entry", default="")
    build.add_argument("--outputs", type=lines, required=True)
    build.add_argument("--defines", type=lines, default=[])

    seal = steps.add_parser("seal", help="Seal .bps.unsealed patches against the base ROM.")
    seal.add_argument("--rom", type=Path, required=True)
    seal.add_argument("--patches", type=lines, required=True)

    comment = steps.add_parser("pr-comment", help="Keep the PR's artifact comment up to date.")
    comment.add_argument("--name", required=True)
    comment.add_argument("--marker", required=True)
    comment.add_argument("--title", default="")
    comment.add_argument("--body", default="")
    comment.add_argument("--sha", default="")
    comment.add_argument("--outcome", default="success")

    nightly = steps.add_parser("nightly", help="Replace the nightly prerelease.")
    nightly.add_argument("--files", type=lines, required=True)
    nightly.add_argument("--title", default="Nightly build")
    nightly.add_argument("--body", default="")

    release = steps.add_parser("release", help="Publish the tag's release.")
    release.add_argument("--assets", type=lines, default=[])
    release.add_argument("--zip", dest="zip_name", default="")
    release.add_argument("--zip-files", type=lines, default=[])
    release.add_argument("--title", default="")
    release.add_argument("--notes-file", default="")

    for publishing in (comment, nightly, release):
        publishing.add_argument("--dry-run", action="store_true", help="Print the gh writes instead of running them.")
    return parser


def _fluff(argv: list[str]) -> int:
    from a816.fluff.cli import fluff_main

    return fluff_main(argv)


def _build(argv: list[str]) -> int:
    from a816.cli import build_main

    return build_main(argv)


def _dispatch(args: argparse.Namespace, actions: Actions, run: Run) -> int:
    step = args.step
    if step == "version":
        return run_version(actions, run)
    if step == "decrypt-rom":
        return run_decrypt_rom(actions, run, encrypted=args.encrypted, to=args.to, sha1=args.sha1)
    if step == "lint":
        return run_lint(args.paths, check_format=args.check_format, fluff=_fluff)
    if step == "build":
        return run_build(actions, _build, entry=args.entry, outputs=args.outputs, defines=args.defines)
    if step == "seal":
        return run_seal(actions, rom=args.rom, patches=args.patches)
    gh = Gh(run, dry_run=args.dry_run)
    if step == "pr-comment":
        return run_pr_comment(
            actions,
            gh,
            name=args.name,
            marker=args.marker,
            title=args.title,
            body=args.body,
            sha=args.sha,
            outcome=args.outcome,
        )
    if step == "nightly":
        return run_nightly(actions, gh, files=args.files, title=args.title, body=args.body)
    return run_release(
        actions,
        gh,
        assets=args.assets,
        zip_name=args.zip_name,
        zip_files=args.zip_files,
        title=args.title,
        notes_file=args.notes_file,
    )


def ci_main(argv: Sequence[str], actions: Actions | None = None, run: Run = run_command) -> int:
    args = _parser().parse_args(list(argv))
    try:
        return _dispatch(args, actions or Actions(), run)
    except CiError as error:
        print(f"a816 ci {args.step}: error: {error}", file=sys.stderr)
        return 1
