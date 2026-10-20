"""`a816 ci pr-comment|nightly|release`: what a build publishes, through a fake `gh`."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from a816.ci import ci_main
from a816.ci.github import Gh, is_prerelease, make_zip
from a816.cli import _dispatch_subcommand
from tests.ci_fakes import FakeRun, actions_in

REPO = "manz/ff4"
PR = {"GITHUB_REPOSITORY": REPO, "GITHUB_REF": "refs/pull/5/merge", "GITHUB_RUN_ID": "99"}
LIST = ("gh", "api", "--paginate")
VIEW = ("gh", "release", "view")


def _comments(*bodies: str) -> bytes:
    return "".join(json.dumps({"id": i + 1, "body": b}) + "\n" for i, b in enumerate(bodies)).encode()


def _comment(tmp_path: Path, run: FakeRun, *extra: str) -> int:
    argv = ["pr-comment", "--name", "ff4", "--marker", "FF4-IPS", "--sha", "abc", *extra]
    return ci_main(argv, actions_in(tmp_path, **PR), run)


def _writes(run: FakeRun) -> list[list[str]]:
    return [argv[3:5] for argv in run.commands() if argv[:3] == ["gh", "api", "-X"]]


def _body(run: FakeRun) -> str:
    sent = next(stdin for argv, stdin in run.calls if argv[3:4] in (["POST"], ["PATCH"]))
    body: str = json.loads(sent or b"")["body"]
    return body


def _fresh() -> FakeRun:
    return FakeRun({LIST: b"", ("gh", "api", "-X"): b""})


def test_a_first_build_creates_the_comment(tmp_path: Path) -> None:
    run = _fresh()
    _comment(tmp_path, run)
    assert _writes(run) == [["POST", f"repos/{REPO}/issues/5/comments"]]


def test_the_comment_starts_with_its_marker(tmp_path: Path) -> None:
    run = _fresh()
    _comment(tmp_path, run)
    assert _body(run).splitlines()[0] == "<!-- FF4-IPS -->"


def test_the_default_keeps_the_game_repos_layout(tmp_path: Path) -> None:
    run = _fresh()
    _comment(tmp_path, run, "--title", "FF4 IPS")
    assert _body(run).splitlines()[1:4] == [
        "🎮 **FF4 IPS Build Ready!**",
        "",
        "Your patch has been built successfully! Download it from the artifacts:",
    ]


def test_the_default_names_the_artifact(tmp_path: Path) -> None:
    run = _fresh()
    _comment(tmp_path, run)
    assert "📦 **Artifact:** `ff4-pr-5`" in _body(run)


def test_the_link_goes_to_the_artifact(tmp_path: Path) -> None:
    run = _fresh()
    _comment(tmp_path, run, "--artifact-url", "https://github.com/manz/ff4/actions/runs/99/artifacts/7")
    assert "(https://github.com/manz/ff4/actions/runs/99/artifacts/7)" in _body(run)


def test_without_an_artifact_url_the_link_goes_to_the_run(tmp_path: Path) -> None:
    run = _fresh()
    _comment(tmp_path, run)
    assert "(https://github.com/manz/ff4/actions/runs/99)" in _body(run)


def test_the_default_says_how_long_and_from_what(tmp_path: Path) -> None:
    run = _fresh()
    _comment(tmp_path, run, "--retention-days", "14")
    assert _body(run).splitlines()[-2:] == ["The artifact will be available for 14 days.", "*Last updated: abc*"]


def test_the_files_are_listed_with_their_sizes(tmp_path: Path) -> None:
    (tmp_path / "ff4.ips").write_bytes(bytes(3 * 1024))
    (tmp_path / "ff4.bps").write_bytes(bytes(10))
    run = _fresh()
    _comment(tmp_path, run, "--files", f"{tmp_path / 'ff4.ips'}\n{tmp_path / 'ff4.bps'}")
    assert "📄 **Files:** `ff4.ips` (3 KiB), `ff4.bps` (10 bytes)" in _body(run)


def test_the_lines_break_as_in_markdown(tmp_path: Path) -> None:
    (tmp_path / "ff4.ips").write_bytes(b"PATCH")
    run = _fresh()
    _comment(tmp_path, run, "--files", str(tmp_path / "ff4.ips"))
    assert "`ff4-pr-5`  \n📄 **Files:** `ff4.ips` (5 bytes)  \n🔗 **Download:**" in _body(run)


def test_no_file_at_hand_leaves_the_list_out(tmp_path: Path) -> None:
    run = _fresh()
    _comment(tmp_path, run, "--files", str(tmp_path / "missing.ips"))
    assert "Files:" not in _body(run)


def test_a_body_fills_its_placeholders(tmp_path: Path) -> None:
    run = _fresh()
    _comment(tmp_path, run, "--body", "{artifact} at {sha}, {retention} days")
    assert _body(run).endswith("ff4-pr-5 at abc, 30 days")


def test_a_later_build_updates_the_marked_comment(tmp_path: Path) -> None:
    run = FakeRun({LIST: _comments("unrelated", "<!-- FF4-IPS -->\nold"), ("gh", "api", "-X"): b""})
    _comment(tmp_path, run)
    assert _writes(run) == [["PATCH", f"repos/{REPO}/issues/comments/2"]]


def test_duplicates_are_deleted_and_the_oldest_kept(tmp_path: Path) -> None:
    # Two runs started together both created a comment.
    run = FakeRun({LIST: _comments("<!-- FF4-IPS -->\na", "<!-- FF4-IPS -->\nb"), ("gh", "api", "-X"): b""})
    _comment(tmp_path, run)
    assert _writes(run) == [
        ["PATCH", f"repos/{REPO}/issues/comments/1"],
        ["DELETE", f"repos/{REPO}/issues/comments/2"],
    ]


def test_a_duplicate_the_other_run_deleted_is_fine(tmp_path: Path) -> None:
    run = FakeRun({LIST: _comments("<!-- FF4-IPS -->\na", "<!-- FF4-IPS -->\nb"), ("gh", "api", "-X", "PATCH"): b""})
    assert _comment(tmp_path, run) == 0


def test_a_failure_deletes_every_marked_comment(tmp_path: Path) -> None:
    run = FakeRun({LIST: _comments("<!-- FF4-IPS -->", "x", "<!-- FF4-IPS -->"), ("gh", "api", "-X"): b""})
    _comment(tmp_path, run, "--outcome", "failure")
    assert _writes(run) == [
        ["DELETE", f"repos/{REPO}/issues/comments/1"],
        ["DELETE", f"repos/{REPO}/issues/comments/3"],
    ]


def test_a_failure_without_a_comment_writes_nothing(tmp_path: Path) -> None:
    run = FakeRun({LIST: b""})
    _comment(tmp_path, run, "--outcome", "failure")
    assert len(run.calls) == 1


def test_pr_comment_needs_a_pull_request(tmp_path: Path) -> None:
    argv = ["pr-comment", "--name", "ff4", "--marker", "M"]
    assert ci_main(argv, actions_in(tmp_path, GITHUB_REF="refs/heads/master"), FakeRun()) == 1


def test_dry_run_prints_the_writes(capsys: pytest.CaptureFixture[str]) -> None:
    Gh(FakeRun(), dry_run=True).write("release", "create", "v1")
    assert capsys.readouterr().out == "gh release create v1\n"


def test_dry_run_reads_nothing() -> None:
    run = FakeRun({VIEW: b"{}"})
    assert (Gh(run, dry_run=True).read("release", "view", "v1"), run.calls) == (None, [])


def _nightly(tmp_path: Path, run: FakeRun, *extra: str) -> int:
    (tmp_path / "ff4.ips").write_bytes(b"PATCH")
    environ = {"GITHUB_SHA": "abc", "GITHUB_RUN_NUMBER": "12", "A816_BUILD_VERSION": "nightly-12"}
    return ci_main(["nightly", "--files", str(tmp_path / "*.ips"), *extra], actions_in(tmp_path, **environ), run)


def test_nightly_replaces_the_nightly_release(tmp_path: Path) -> None:
    run = FakeRun({VIEW: b"{}", ("gh", "release"): b""})
    _nightly(tmp_path, run)
    assert [argv[2] for argv in run.commands()] == ["view", "delete", "create"]


def test_the_first_nightly_deletes_nothing(tmp_path: Path) -> None:
    run = FakeRun({("gh", "release", "create"): b""})
    _nightly(tmp_path, run)
    assert [argv[2] for argv in run.commands()] == ["view", "create"]


def test_nightly_targets_the_commit_with_the_files(tmp_path: Path) -> None:
    run = FakeRun({("gh", "release", "create"): b""})
    _nightly(tmp_path, run, "--body", "{version} {run} {sha}")
    create = run.commands()[-1]
    assert create[3:] == [
        "nightly",
        "--prerelease",
        "--target",
        "abc",
        "--title",
        "Nightly Build 12",
        "--notes",
        "nightly-12 12 abc",
        str(tmp_path / "ff4.ips"),
    ]


def test_the_default_notes_are_the_repos_own(tmp_path: Path) -> None:
    run = FakeRun({("gh", "release", "create"): b""})
    _nightly(tmp_path, run)
    notes = run.commands()[-1][run.commands()[-1].index("--notes") + 1]
    assert notes == (
        "Nightly build #12\n\nBuilt from commit: abc\n\nThis is an automated nightly build and may be unstable."
    )


def test_a_title_fills_its_placeholders(tmp_path: Path) -> None:
    run = FakeRun({("gh", "release", "create"): b""})
    _nightly(tmp_path, run, "--title", "FF4 {version}")
    assert run.commands()[-1][run.commands()[-1].index("--title") + 1] == "FF4 nightly-12"


def test_dry_run_shows_the_comment_it_would_post(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _comment(tmp_path, FakeRun(), "--dry-run")
    assert "Build Ready!" in capsys.readouterr().out


def test_a_pattern_matching_nothing_fails(tmp_path: Path) -> None:
    argv = ["nightly", "--files", str(tmp_path / "*.bps")]
    assert ci_main(argv, actions_in(tmp_path), FakeRun()) == 1


@pytest.mark.parametrize("tag", ["v1.2a1", "v1.2.0b2", "v1.2rc1", "v1.2.0-rc.1"])
def test_pre_release_tags(tag: str) -> None:
    assert is_prerelease(tag)


@pytest.mark.parametrize("tag", ["v1.2", "v1.2.0", "vanilla", "v1.2.0-patch"])
def test_final_tags(tag: str) -> None:
    assert not is_prerelease(tag)


def _release(tmp_path: Path, run: FakeRun, tag: str, *extra: str) -> int:
    (tmp_path / "bl.ips").write_bytes(b"PATCH")
    environ = {"GITHUB_REF_NAME": tag, "SOURCE_DATE_EPOCH": "1700000000"}
    return ci_main(["release", *extra], actions_in(tmp_path, **environ), run)


def test_a_new_release_generates_notes(tmp_path: Path) -> None:
    run = FakeRun({("gh", "release", "create"): b""})
    _release(tmp_path, run, "v1.0", "--assets", str(tmp_path / "bl.ips"))
    assert run.commands()[-1][3:] == [
        "v1.0",
        "--verify-tag",
        "--title",
        "v1.0",
        "--generate-notes",
        str(tmp_path / "bl.ips"),
    ]


def test_a_pre_release_tag_is_marked(tmp_path: Path) -> None:
    run = FakeRun({("gh", "release", "create"): b""})
    _release(tmp_path, run, "v1.0rc1", "--assets", str(tmp_path / "bl.ips"), "--notes-file", "NOTES.md")
    assert run.commands()[-1][5:10] == ["--title", "v1.0rc1", "--prerelease", "--notes-file", "NOTES.md"]


def test_an_existing_release_keeps_its_notes(tmp_path: Path) -> None:
    run = FakeRun({VIEW: b"{}", ("gh", "release", "upload"): b""})
    _release(tmp_path, run, "v1.0", "--assets", str(tmp_path / "bl.ips"))
    assert run.commands()[-1][2:] == ["upload", "v1.0", "--clobber", str(tmp_path / "bl.ips")]


def test_the_zip_is_added(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    run = FakeRun({("gh", "release", "create"): b""})
    _release(tmp_path, run, "v1.0", "--zip", "bl-fr.zip", "--zip-files", "bl.ips")
    assert run.commands()[-1][-1] == "bl-fr.zip"


def test_a_zip_needs_its_files(tmp_path: Path) -> None:
    assert _release(tmp_path, FakeRun(), "v1.0", "--zip", "bl-fr.zip") == 1


def test_nothing_to_release_fails(tmp_path: Path) -> None:
    assert _release(tmp_path, FakeRun(), "v1.0") == 1


def test_the_zip_flattens_its_files(tmp_path: Path) -> None:
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "bl.ips").write_bytes(b"PATCH")
    make_zip(tmp_path / "x.zip", [str(tmp_path / "build" / "bl.ips")], "1700000000")
    assert zipfile.ZipFile(tmp_path / "x.zip").namelist() == ["bl.ips"]


def test_the_zip_is_reproducible(tmp_path: Path) -> None:
    (tmp_path / "bl.ips").write_bytes(b"PATCH")
    make_zip(tmp_path / "a.zip", [str(tmp_path / "bl.ips")], "1700000000")
    make_zip(tmp_path / "b.zip", [str(tmp_path / "bl.ips")], "1700000000")
    assert (tmp_path / "a.zip").read_bytes() == (tmp_path / "b.zip").read_bytes()


def test_the_zip_dates_start_in_1980(tmp_path: Path) -> None:
    (tmp_path / "bl.ips").write_bytes(b"PATCH")
    make_zip(tmp_path / "x.zip", [str(tmp_path / "bl.ips")], "")
    assert zipfile.ZipFile(tmp_path / "x.zip").infolist()[0].date_time == (1980, 1, 1, 0, 0, 0)


def test_a816_dispatches_ci(tmp_path: Path) -> None:
    assert _dispatch_subcommand(["ci", "nightly", "--dry-run", f"--files={tmp_path}/*.none"]) == 1
