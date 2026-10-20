"""`a816 ci version` and `a816 ci decrypt-rom`, the steps before a build."""

from __future__ import annotations

import gzip
import hashlib
from pathlib import Path

import pytest

from a816.ci import ci_main
from a816.ci.actions import Actions
from a816.ci.process import CiError, run_command
from a816.ci.rom import decrypt_rom
from a816.ci.version import build_version
from tests.ci_fakes import FakeRun, actions_in, written

GIT_LOG = ("git", "log")


def test_a_tag_push_builds_the_tag() -> None:
    assert build_version(Actions({"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v1.2"})) == "v1.2"


def test_a_pull_request_builds_pr_number() -> None:
    assert build_version(Actions({"GITHUB_REF_TYPE": "branch", "GITHUB_REF": "refs/pull/42/merge"})) == "pr-42"


def test_a_master_push_builds_nightly_run() -> None:
    environ = {"GITHUB_REF_TYPE": "branch", "GITHUB_REF": "refs/heads/master", "GITHUB_RUN_NUMBER": "7"}
    assert build_version(Actions(environ)) == "nightly-7"


def test_version_exports_the_build_version(tmp_path: Path) -> None:
    actions = actions_in(tmp_path, GITHUB_REF_TYPE="tag", GITHUB_REF_NAME="v1.2")
    ci_main(["version"], actions, FakeRun({GIT_LOG: b"1700000000\n"}))
    assert written(tmp_path, "env") == "A816_BUILD_VERSION=v1.2\nSOURCE_DATE_EPOCH=1700000000\n"


def test_version_keeps_a_job_s_source_date_epoch(tmp_path: Path) -> None:
    actions = actions_in(tmp_path, GITHUB_REF_TYPE="tag", GITHUB_REF_NAME="v1.2", SOURCE_DATE_EPOCH="1")
    ci_main(["version"], actions, FakeRun({GIT_LOG: b"1700000000\n"}))
    assert "SOURCE_DATE_EPOCH" not in written(tmp_path, "env")


def test_version_outputs_the_commit_time(tmp_path: Path) -> None:
    ci_main(["version"], actions_in(tmp_path), FakeRun({GIT_LOG: b"1700000000\n"}))
    assert "source-date-epoch=1700000000\n" in written(tmp_path, "output")


def test_no_passphrase_decrypts_nothing(tmp_path: Path) -> None:
    assert not decrypt_rom(tmp_path / "x.sfc.gpg", tmp_path / "x.sfc", passphrase="", sha1="", run=FakeRun())


def test_no_passphrase_outputs_unavailable(tmp_path: Path) -> None:
    ci_main(["decrypt-rom", "x.sfc.gpg", "--to", str(tmp_path / "x.sfc")], actions_in(tmp_path), FakeRun())
    assert written(tmp_path, "output") == "available=false\n"


def test_the_passphrase_goes_on_stdin(tmp_path: Path) -> None:
    run = FakeRun({("gpg",): b"rom"})
    decrypt_rom(tmp_path / "x.sfc.gpg", tmp_path / "x.sfc", passphrase="secret", sha1="", run=run)
    assert run.calls[0][1] == b"secret"


def test_the_passphrase_stays_out_of_argv(tmp_path: Path) -> None:
    run = FakeRun({("gpg",): b"rom"})
    decrypt_rom(tmp_path / "x.sfc.gpg", tmp_path / "x.sfc", passphrase="secret", sha1="", run=run)
    assert "secret" not in " ".join(run.commands()[0])


def test_a_gz_gpg_rom_is_gunzipped(tmp_path: Path) -> None:
    run = FakeRun({("gpg",): gzip.compress(b"rom")})
    decrypt_rom(tmp_path / "x.sfc.gz.gpg", tmp_path / "build" / "x.sfc", passphrase="p", sha1="", run=run)
    assert (tmp_path / "build" / "x.sfc").read_bytes() == b"rom"


def test_a_matching_sha1_passes(tmp_path: Path) -> None:
    sha1 = hashlib.sha1(b"rom", usedforsecurity=False).hexdigest().upper()  # NOSONAR - the dump's published hash
    assert decrypt_rom(
        tmp_path / "x.gpg", tmp_path / "x.sfc", passphrase="p", sha1=sha1, run=FakeRun({("gpg",): b"rom"})
    )


def test_a_wrong_sha1_fails(tmp_path: Path) -> None:
    run = FakeRun({("gpg",): b"rom"})
    with pytest.raises(CiError, match="expected 00"):
        decrypt_rom(tmp_path / "x.gpg", tmp_path / "x.sfc", passphrase="p", sha1="00", run=run)


def test_a_failing_step_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    actions = actions_in(tmp_path, A816_ROM_PASSPHRASE="p")
    status = ci_main(["decrypt-rom", "x.gpg", "--to", str(tmp_path / "x.sfc")], actions, FakeRun())
    assert (status, capsys.readouterr().err) == (1, "a816 ci decrypt-rom: error: gpg failed\n")


def test_outputs_print_outside_actions(capsys: pytest.CaptureFixture[str]) -> None:
    Actions({}).output("version", "v1")
    assert capsys.readouterr().out == "version=v1\n"


def test_multi_line_outputs_use_a_delimiter(tmp_path: Path) -> None:
    actions_in(tmp_path).output("files", "a\nb")
    lines = written(tmp_path, "output").splitlines()
    assert (lines[0].split("<<")[0], lines[1:3], lines[3]) == ("files", ["a", "b"], lines[0].split("<<")[1])


def test_run_command_returns_stdout() -> None:
    assert run_command(["echo", "hi"]) == b"hi\n"


def test_run_command_raises_on_failure() -> None:
    with pytest.raises(CiError, match="false failed with exit code 1"):
        run_command(["false"])


def test_run_command_raises_on_a_missing_tool() -> None:
    with pytest.raises(CiError, match="is not installed"):
        run_command(["a816-no-such-tool"])
