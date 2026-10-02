"""
Root's git in a tree another account owns.

An in-place tree belongs to the service account. Whoever runs as that account
(the application itself, after a remote code execution) can write the tree's
``.git/config``, and git executes several keys of it: ``core.fsmonitor`` on
``git status``, ``core.hooksPath``, ``filter.<x>.clean``, ``log.showSignature``
with ``gpg.program``... Run by root with ``safe.directory=*``, that was root
code execution for the account. These tests reproduce the attack against the
chokepoint (``SourceManager._git``) and the two other places that read a
checkout.
"""

from __future__ import annotations

import os
import pwd
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import SourceError
from noust.core.runner import FakeRunner
from noust.managers import source_manager
from noust.managers.source_manager import SourceManager

GIT = ("git", "-c", "protocol.ext.allow=never", "-c", "protocol.file.allow=never")

#: The account tmp_path belongs to: "another account" once Noust runs as uid 0.
OWNER = pwd.getpwuid(os.getuid()).pw_name


@pytest.fixture
def as_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make Noust believe it is root, so the test's own tree is someone else's."""
    monkeypatch.setattr(source_manager, "_own_uid", lambda: 0)


def _checkout(path: Path, config: str = "[core]\n\tbare = false\n") -> Path:
    (path / ".git").mkdir(parents=True)
    (path / ".git" / "config").write_text(config)
    return path


def _git_calls(runner: FakeRunner) -> list[tuple[str, ...]]:
    return [call for call in runner.calls if "git" in call]


def test_reading_a_tree_another_account_owns_runs_git_as_that_account(
    tmp_path: Path, as_root: None
) -> None:
    """The 3.1.5 behaviour (the commit is read) without root trusting the tree."""
    _checkout(tmp_path, "[core]\n\tfsmonitor = /tmp/pwn.sh\n")
    runner = FakeRunner()
    runner.script(["runuser", "-u", OWNER, "--", *GIT], stdout="84c23e0\n")

    info = SourceManager(runner=runner).get_repo_info(tmp_path)

    assert info["commit"] == "84c23e0"
    calls = _git_calls(runner)
    assert calls
    for call in calls:
        assert call[:4] == ("runuser", "-u", OWNER, "--"), call
        assert "safe.directory=*" not in call


def test_root_never_trusts_every_directory(tmp_path: Path) -> None:
    """Not even in its own tree: safe.directory=* is gone."""
    _checkout(tmp_path)
    runner = FakeRunner()
    SourceManager(runner=runner).get_repo_info(tmp_path)
    assert all("safe.directory=*" not in call for call in runner.calls)


def test_a_tree_whose_owner_has_no_account_is_not_read(
    tmp_path: Path, as_root: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nobody to read it as: unknown, rather than root."""
    _checkout(tmp_path)

    def no_account(uid: int) -> Any:
        raise KeyError(uid)

    monkeypatch.setattr(source_manager.pwd, "getpwuid", no_account)
    runner = FakeRunner()
    info = SourceManager(runner=runner).get_repo_info(tmp_path)
    assert info["commit"] is None
    assert _git_calls(runner) == []


@pytest.mark.parametrize(
    "config",
    [
        "[core]\n\tfsmonitor = /tmp/pwn.sh\n",
        "[core]\n\thooksPath = /tmp/hooks\n",
        '[filter "x"]\n\tclean = /tmp/pwn.sh\n',
        "[core] sshCommand = /tmp/pwn.sh\n",
        "[log]\n\tshowSignature = true\n[gpg]\n\tprogram = /tmp/pwn.sh\n",
        '[diff "x"]\n\ttextconv = /tmp/pwn.sh\n',
        "[include]\n\tpath = /tmp/evil.config\n",
        "[core]\n\tworktree = /etc\n",
        '[submodule "x"]\n\tupdate = !/tmp/pwn.sh\n',
    ],
)
def test_root_refuses_to_change_a_tree_whose_config_runs_programs(
    tmp_path: Path, as_root: None, config: str
) -> None:
    """A fetch or a reset must run as root (root's credentials); never with that config."""
    _checkout(tmp_path, config)
    runner = FakeRunner()
    with pytest.raises(SourceError) as caught:
        SourceManager(runner=runner).checkout_commit(tmp_path, "0" * 40)
    assert "configuration" in caught.value.message
    assert not any("rev-parse" in call or "checkout" in call for call in runner.calls)


def test_root_changes_a_clean_foreign_tree_with_hooks_and_fsmonitor_off(
    tmp_path: Path, as_root: None
) -> None:
    """What the account cannot set in the config, it can still drop in .git/hooks."""
    _checkout(tmp_path, '[remote "origin"]\n\turl = https://example.com/r.git\n')
    runner = FakeRunner()
    runner.script([*GIT], stdout="0" * 40 + "\n")
    SourceManager(runner=runner).checkout_commit(tmp_path, "0" * 40)
    # Root's own global configuration (safe.directory) is not the tree's.
    rooted = [call for call in runner.calls if call[: len(GIT)] == GIT and "--global" not in call]
    assert rooted
    for call in rooted:
        assert "core.hooksPath=/dev/null" in call
        assert "core.fsmonitor=false" in call


def test_migration_reads_a_foreign_tree_as_its_owner(tmp_path: Path, as_root: None) -> None:
    """migrate.py's own look at the checkout (status runs core.fsmonitor)."""
    from noust.deployers import migrate

    _checkout(tmp_path, "[core]\n\tfsmonitor = /tmp/pwn.sh\n")
    runner = FakeRunner()
    runner.script(["runuser"], stdout="0" * 40 + "\n")
    assert migrate._head_commit(tmp_path, runner) == "0" * 40
    assert runner.calls[0][:4] == ("runuser", "-u", OWNER, "--")
    assert "safe.directory=*" not in runner.calls[0]


def test_backup_reads_a_foreign_tree_as_its_owner(tmp_path: Path, as_root: None) -> None:
    """The backup's commit and branch, from the same tree."""
    from noust.managers.backup_manager import BackupManager

    _checkout(tmp_path, "[core]\n\tfsmonitor = /tmp/pwn.sh\n")
    runner = FakeRunner()
    runner.script(["runuser"], stdout="abcdef0123456789\n")
    manager = BackupManager.__new__(BackupManager)
    manager._runner = runner
    manager._get_git_info(tmp_path)
    assert runner.calls
    assert all(call[:4] == ("runuser", "-u", OWNER, "--") for call in runner.calls)


def test_the_commit_subject_of_a_foreign_tree_is_read_as_its_owner(
    tmp_path: Path, as_root: None
) -> None:
    """git log runs gpg.program when the tree's config says log.showSignature."""
    from noust.deployers.nodejs import NodeJSDeployer
    from tests.test_deployers import build_deployer

    deployer = build_deployer(NodeJSDeployer, tmp_path)
    deployer.app_path.mkdir(parents=True, exist_ok=True)
    _checkout(deployer.app_path, "[log]\n\tshowSignature = true\n[gpg]\n\tprogram = /tmp/x\n")
    runner = FakeRunner()
    runner.script(["runuser"], stdout="Fix the thing\n")
    deployer._runner = runner
    assert deployer._commit_message_for_recording() == "Fix the thing"
    assert runner.calls[-1][:4] == ("runuser", "-u", OWNER, "--")
