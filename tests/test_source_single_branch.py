# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Another branch of a clone that was made to follow only one.

An in-place application is cloned with ``git clone --depth 1 --branch main``,
which also means ``--single-branch``: ``remote.origin.fetch`` names ``main``
and nothing else. ``noust update DOMAIN --branch X`` used to run ``git fetch
origin X``, which in such a clone fills ``FETCH_HEAD`` and no
``refs/remotes/origin/X``, so the ``git checkout X`` after it failed with
"Failed to checkout branch: X" on every in-place git application. The
release cache and a pinned branch go through the same code.

The fake-runner tests model the clone's refs; the real-git ones build a
shallow single-branch clone in a temporary directory and let
:class:`SourceManager` switch it for real.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import SourceError
from noust.core.runner import CommandResult, FakeRunner, SubprocessRunner
from noust.managers.source_manager import SourceManager

GIT = ("git", "-c", "protocol.ext.allow=never", "-c", "protocol.file.allow=never")
URL = "https://git.example.com/owner/shop.git"


class SingleBranchClone(FakeRunner):
    """
    A FakeRunner whose git behaves like a clone following one branch.

    It keeps what matters to switching branches: the configured fetch
    refspecs, the remote-tracking branches, the local branches and HEAD.
    ``git fetch origin X`` with no destination only fills ``FETCH_HEAD``, as
    real git does when no configured refspec covers ``X``.

    Attributes:
        remote: Branches the remote has.
        refspecs: ``remote.origin.fetch`` values.
        tracking: Branches under ``refs/remotes/origin``.
        local: Local branches.
        head: The branch checked out.
        shallow: Whether the clone is shallow.
    """

    def __init__(
        self,
        *,
        remote: Sequence[str] = ("main", "feature"),
        refspecs: Sequence[str] = ("+refs/heads/main:refs/remotes/origin/main",),
        tracking: Sequence[str] = ("main",),
        shallow: bool = True,
    ) -> None:
        super().__init__()
        self.remote = set(remote)
        self.refspecs = list(refspecs)
        self.tracking = set(tracking)
        self.local = {"main"}
        self.head = "main"
        self.shallow = shallow

    def _covered(self, branch: str) -> bool:
        return any(
            spec.lstrip("+")
            in (
                "refs/heads/*:refs/remotes/origin/*",
                f"refs/heads/{branch}:refs/remotes/origin/{branch}",
            )
            for spec in self.refspecs
        )

    def _answer(self, args: tuple[str, ...]) -> tuple[int, str, str] | None:
        if args == ("config", "--get-all", "remote.origin.fetch"):
            return (
                (0, "".join(f"{spec}\n" for spec in self.refspecs), "")
                if self.refspecs
                else (1, "", "")
            )
        if args == ("rev-parse", "--is-shallow-repository"):
            return 0, "true\n" if self.shallow else "false\n", ""
        if args == ("rev-parse", "--abbrev-ref", "HEAD"):
            return 0, f"{self.head}\n", ""
        if args[:3] == ("remote", "set-branches", "--add"):
            self.refspecs.append(f"+refs/heads/{args[4]}:refs/remotes/origin/{args[4]}")
            return 0, "", ""
        if args[0] == "fetch":
            specs = [a for a in args[1:] if not a.startswith("-") and a not in ("origin", "1")]
            for spec in specs:
                source, _, destination = spec.lstrip("+").partition(":")
                branch = source.removeprefix("refs/heads/")
                if branch not in self.remote:
                    return 128, "", f"fatal: couldn't find remote ref {source}\n"
                if destination or self._covered(branch):
                    self.tracking.add(branch)
            return 0, "", ""
        if args[0] == "checkout":
            return self._checkout(args[1:])
        return None

    def _checkout(self, args: tuple[str, ...]) -> tuple[int, str, str]:
        if "-B" in args:
            branch = args[args.index("-B") + 1]
            start = args[-1].removeprefix("origin/")
            if start not in self.tracking:
                return 128, "", f"fatal: '{args[-1]}' is not a commit\n"
            if "--track" in args and not self._covered(start):
                return (
                    128,
                    "",
                    f"fatal: cannot set up tracking information; '{args[-1]}' is not a branch\n",
                )
            self.local.add(branch)
            self.head = branch
            return 0, "", ""
        branch = args[-1]
        if branch in self.local or branch in self.tracking:
            self.local.add(branch)
            self.head = branch
            return 0, "", ""
        return 1, "", f"error: pathspec '{branch}' did not match any file(s) known to git\n"

    def run(self, argv: Any, **kwargs: Any) -> CommandResult:
        result = super().run(argv, **kwargs)
        if tuple(argv[: len(GIT)]) == GIT:
            custom = self._answer(tuple(argv[len(GIT) :]))
            if custom is not None:
                code, out, err = custom
                return CommandResult(argv=tuple(argv), exit_code=code, stdout=out, stderr=err)
        return result

    def git(self) -> list[tuple[str, ...]]:
        """Every git call, without the common prefix."""
        return [call[len(GIT) :] for call in self.calls if call[: len(GIT)] == GIT]

    def git_envs(self, verb: str) -> list[Mapping[str, str] | None]:
        """The environment of every git call of one verb."""
        return [
            env
            for call, env in zip(self.calls, self.envs, strict=True)
            if call[: len(GIT)] == GIT and call[len(GIT)] == verb
        ]


def _repo(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# An in-place update (and a pinned branch, which is pulled the same way)
# ---------------------------------------------------------------------------


def test_an_update_to_a_branch_the_clone_does_not_track_switches_to_it(tmp_path: Path) -> None:
    runner = SingleBranchClone()

    assert SourceManager(runner=runner).pull(_repo(tmp_path), branch="feature") is True

    calls = runner.git()
    fetch = ("fetch", "--depth", "1", "origin", "+refs/heads/feature:refs/remotes/origin/feature")
    track = ("remote", "set-branches", "--add", "origin", "feature")
    switch = ("checkout", "--track", "-B", "feature", "origin/feature")
    assert calls.index(fetch) < calls.index(track) < calls.index(switch)
    assert calls.index(switch) < calls.index(("pull", "--rebase"))
    assert runner.head == "feature"
    assert "+refs/heads/feature:refs/remotes/origin/feature" in runner.refspecs
    for env in runner.git_envs("fetch"):
        assert env is not None and env["GIT_TERMINAL_PROMPT"] == "0"


def test_a_branch_the_remote_does_not_have_is_named_and_left_untracked(tmp_path: Path) -> None:
    runner = SingleBranchClone()

    with pytest.raises(SourceError) as caught:
        SourceManager(runner=runner).pull(_repo(tmp_path), branch="nope")

    assert "nope" in caught.value.message
    assert "couldn't find remote ref refs/heads/nope" in (caught.value.details or "")
    # A refspec for a branch that does not exist would fail every later fetch.
    assert not any(call[:2] == ("remote", "set-branches") for call in runner.git())
    assert runner.refspecs == ["+refs/heads/main:refs/remotes/origin/main"]
    assert ("pull", "--rebase") not in runner.git()


def test_a_full_clone_fetches_a_new_branch_without_depth_or_new_refspec(tmp_path: Path) -> None:
    """A branch pushed after the last fetch: the wildcard refspec covers it."""
    runner = SingleBranchClone(refspecs=("+refs/heads/*:refs/remotes/origin/*",), shallow=False)

    SourceManager(runner=runner).pull(_repo(tmp_path), branch="feature")

    calls = runner.git()
    assert ("fetch", "origin", "+refs/heads/feature:refs/remotes/origin/feature") in calls
    assert not any(call[:2] == ("remote", "set-branches") for call in calls)
    assert runner.head == "feature"


def test_a_branch_the_clone_already_has_is_checked_out_without_the_network(
    tmp_path: Path,
) -> None:
    runner = SingleBranchClone()

    SourceManager(runner=runner).pull(_repo(tmp_path), branch="main")

    calls = runner.git()
    assert ("checkout", "main") in calls
    assert not any(call[0] == "fetch" for call in calls)


# ---------------------------------------------------------------------------
# The release cache
# ---------------------------------------------------------------------------


def test_the_release_cache_follows_a_branch_its_clone_does_not_track(tmp_path: Path) -> None:
    cache = tmp_path / "repo"
    (cache / ".git").mkdir(parents=True)
    runner = SingleBranchClone()
    runner.script([*GIT, "remote", "get-url", "origin"], stdout=URL + "\n")
    runner.script([*GIT, "rev-parse", "HEAD"], stdout="c" * 40 + "\n")

    assert SourceManager(runner=runner).sync_cache(URL, cache, "feature") == "c" * 40

    calls = runner.git()
    fetch = ("fetch", "--depth", "1", "origin", "+refs/heads/feature:refs/remotes/origin/feature")
    assert calls.index(fetch) < calls.index(
        ("remote", "set-branches", "--add", "origin", "feature")
    )
    assert ("checkout", "--force", "-B", "feature", "origin/feature") in calls
    assert ("reset", "--hard", "origin/feature") in calls


def test_the_release_cache_fetches_a_tracked_branch_as_before(tmp_path: Path) -> None:
    """The branch the clone follows: the same fetch as ever, nothing added."""
    cache = tmp_path / "repo"
    (cache / ".git").mkdir(parents=True)
    runner = SingleBranchClone()
    runner.script([*GIT, "remote", "get-url", "origin"], stdout=URL + "\n")
    runner.script([*GIT, "rev-parse", "HEAD"], stdout="c" * 40 + "\n")

    SourceManager(runner=runner).sync_cache(URL, cache, "main")

    calls = runner.git()
    assert ("fetch", "origin", "+refs/heads/main:refs/remotes/origin/main") in calls
    assert not any(call[:2] == ("remote", "set-branches") for call in calls)


# ---------------------------------------------------------------------------
# Real git
# ---------------------------------------------------------------------------


class _LocalRemoteRunner(SubprocessRunner):
    """
    The real runner, allowing the file protocol.

    Noust passes ``-c protocol.file.allow=never`` to every git it runs; the
    remote of these tests is a directory, so that one guard is lifted here and
    nothing else about the command changes.
    """

    def run(self, argv: Sequence[str], **kwargs: Any) -> CommandResult:  # type: ignore[override]
        args = list(argv)
        if args[:1] == ["git"]:
            while "protocol.file.allow=never" in args:
                at = args.index("protocol.file.allow=never")
                del args[at - 1 : at + 1]
        return super().run(args, **kwargs)


@pytest.fixture
def git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Run real git with a throwaway global configuration and identity."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Noust Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.com")
    runner = SubprocessRunner()

    def run(*args: str, cwd: Path) -> str:
        return runner.run(["git", *args], cwd=cwd, timeout=60, check=True).stdout.strip()

    return run


@pytest.fixture
def shallow_clone(tmp_path: Path, git: Any) -> tuple[Path, Path]:
    """A remote with main and feature, and a ``--depth 1 --branch main`` clone of it."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git("init", "-q", "-b", "main", cwd=origin)
    (origin / "app.txt").write_text("main\n")
    git("add", "app.txt", cwd=origin)
    git("commit", "-q", "-m", "main", cwd=origin)
    git("checkout", "-q", "-b", "feature", cwd=origin)
    (origin / "app.txt").write_text("feature\n")
    git("commit", "-q", "-am", "feature", cwd=origin)
    git("checkout", "-q", "main", cwd=origin)
    work = tmp_path / "app"
    git(
        "clone",
        "-q",
        "--depth",
        "1",
        "--branch",
        "main",
        f"file://{origin}",
        str(work),
        cwd=tmp_path,
    )
    assert git("config", "--get-all", "remote.origin.fetch", cwd=work) == (
        "+refs/heads/main:refs/remotes/origin/main"
    )
    return origin, work


@pytest.mark.allow_subprocess
def test_real_git_an_update_switches_a_single_branch_shallow_clone(
    shallow_clone: tuple[Path, Path], git: Any
) -> None:
    origin, work = shallow_clone
    manager = SourceManager(runner=_LocalRemoteRunner())

    assert manager.pull(work, branch="feature") is True

    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=work) == "feature"
    assert git("rev-parse", "--abbrev-ref", "feature@{upstream}", cwd=work) == "origin/feature"
    assert git("rev-parse", "--is-shallow-repository", cwd=work) == "true"
    assert (work / "app.txt").read_text() == "feature\n"

    # The clone follows the branch now: the next update brings its new commit.
    git("checkout", "-q", "feature", cwd=origin)
    (origin / "app.txt").write_text("feature 2\n")
    git("commit", "-q", "-am", "feature 2", cwd=origin)
    assert manager.pull(work, branch="feature") is True
    assert (work / "app.txt").read_text() == "feature 2\n"


@pytest.mark.allow_subprocess
def test_real_git_a_missing_branch_fails_and_leaves_the_clone_as_it_was(
    shallow_clone: tuple[Path, Path], git: Any
) -> None:
    _, work = shallow_clone
    manager = SourceManager(runner=_LocalRemoteRunner())

    with pytest.raises(SourceError) as caught:
        manager.pull(work, branch="nope")

    assert "nope" in caught.value.message
    assert git("config", "--get-all", "remote.origin.fetch", cwd=work) == (
        "+refs/heads/main:refs/remotes/origin/main"
    )
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=work) == "main"
    # And an update of the branch it follows still works.
    assert manager.pull(work, branch="main") is True
