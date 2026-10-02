# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for deploying by tag (3.2, spec 1.9).

An application can follow the tags that match a pattern instead of a branch.
What is pinned here:

- **Tags are versions.** ``v1.10.0`` is newer than ``v1.9.0``, a release is
  newer than its own release candidate, and a tag that is no version has no
  place in the order at all.
- **The git side goes through SourceManager**: a tag resolves to the commit it
  points at (an annotated tag peeled), is fetched when the clone lacks it, and
  the tags a commit contains are listed by one read-only command.
- **An update by tag deploys exactly that commit** and refuses to go backwards
  past the newest tag already deployed; an app that follows tags and is updated
  with no tag at all deploys the newest tag on the remote, never a branch head.
- **The setting is validated where it is written.**
"""

# The pipeline fixtures are imported rather than replicated, so there stays
# one definition of the fake machine.
# ruff: noqa: F811

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import NoustError, SourceError
from noust.core.runner import CommandResult, FakeRunner, is_read_only
from noust.core.tags import (
    compare_tags,
    newest_tag,
    tag_from_ref,
    tag_matches,
    tag_version,
    why_not_deploy,
)
from noust.managers.source_manager import SourceManager

#: What every git Noust runs starts with.
GIT = ("git", "-c", "protocol.ext.allow=never", "-c", "protocol.file.allow=never")

#: What SourceManager adds to a git command that only reads a checkout.
TRUST = ("-c", "safe.directory=*")

FULL = "0123456789abcdef0123456789abcdef01234567"
OTHER = "fedcba9876543210fedcba9876543210fedcba98"


# ---------------------------------------------------------------------------
# Tags as versions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("newer", "older"),
    [
        ("v1.10.0", "v1.9.0"),
        ("v2.0.0", "v1.99.99"),
        ("1.2.3", "v1.2.2"),
        ("v1.2.3", "v1.2.3-rc.1"),
        ("v1.2.3-rc.2", "v1.2.3-rc.1"),
        ("v1.2.3-rc.10", "v1.2.3-rc.9"),
        ("v1.2.3-rc.1", "v1.2.3-beta.5"),
        ("v1.2.3-rc.1.1", "v1.2.3-rc.1"),
        ("release-1.4.0", "release-1.3.9"),
        ("v1.2.1", "v1.2"),
    ],
)
def test_versions_are_ordered_by_number_not_by_text(newer: str, older: str) -> None:
    """The order is semantic: ten is more than nine, and a release beats its candidates."""
    assert compare_tags(newer, older) == 1
    assert compare_tags(older, newer) == -1


def test_trailing_zeros_make_no_difference() -> None:
    """1.2 and 1.2.0 are one version."""
    assert compare_tags("v1.2", "v1.2.0") == 0
    assert compare_tags("v1.2.0+build5", "v1.2.0") == 0


@pytest.mark.parametrize("tag", ["latest", "stable", "nightly", "release-2024-10-01x", ""])
def test_a_tag_that_is_no_version_has_no_place_in_the_order(tag: str) -> None:
    """Nothing can be said of how 'latest' compares with v1.0.0."""
    assert tag_version(tag) is None
    assert compare_tags(tag, "v1.0.0") is None
    assert compare_tags("v1.0.0", tag) is None


def test_the_newest_tag_ignores_what_is_not_a_version_and_what_does_not_match() -> None:
    tags = ["v1.9.0", "v1.10.0", "latest", "v1.10.0-rc.1", "docs-2.0.0", "v0.1.0"]
    assert newest_tag(tags) == "docs-2.0.0"
    assert newest_tag(tags, "v*") == "v1.10.0"
    assert newest_tag(["latest"], "*") is None
    assert newest_tag([], "v*") is None
    assert newest_tag(["v1.0.0"], "release-*") is None


@pytest.mark.parametrize(
    ("pattern", "tag", "expected"),
    [
        ("v*", "v1.2.3", True),
        ("v*", "1.2.3", False),
        ("v*", "release/v1", False),
        ("release-*", "release-1.0", True),
        ("v[0-9]*", "vnext", False),
        ("*", "anything", True),
        ("V*", "v1", False),
    ],
)
def test_a_pattern_is_a_glob_over_the_whole_tag_name(
    pattern: str, tag: str, expected: bool
) -> None:
    assert tag_matches(pattern, tag) is expected


@pytest.mark.parametrize(
    ("ref", "tag"),
    [
        ("refs/tags/v1.3.0", "v1.3.0"),
        ("refs/tags/release/2.0", "release/2.0"),
        ("refs/heads/main", None),
        ("v1.3.0", None),
        ("refs/tags/", None),
        ("", None),
        (None, None),
        (42, None),
    ],
)
def test_a_tag_is_read_from_a_ref_only_when_it_is_one(ref: object, tag: str | None) -> None:
    assert tag_from_ref(ref) == tag


class TestWhyNotDeploy:
    """The one answer both webhooks give to 'should this tag deploy?'."""

    def test_a_newer_matching_version_deploys(self) -> None:
        assert why_not_deploy("v1.3.0", "v*", "v1.2.0") is None

    def test_the_first_tag_of_an_application_with_nothing_deployed_deploys(self) -> None:
        assert why_not_deploy("v0.0.1", "v*", None) is None

    def test_a_tag_the_pattern_does_not_match_is_named_with_its_pattern(self) -> None:
        reason = why_not_deploy("hotfix-1", "v*", "v1.2.0")
        assert reason is not None
        assert "hotfix-1" in reason and "v*" in reason

    def test_a_tag_below_the_deployed_one_names_both(self) -> None:
        reason = why_not_deploy("v1.2.0", "v*", "v1.3.0")
        assert reason is not None
        assert "v1.2.0" in reason and "v1.3.0" in reason and "older" in reason

    def test_the_deployed_tag_again_is_already_deployed(self) -> None:
        reason = why_not_deploy("v1.3.0", "v*", "v1.3.0")
        assert reason is not None
        assert "already" in reason

    def test_a_tag_that_is_no_version_cannot_be_ordered(self) -> None:
        reason = why_not_deploy("vnext", "v*", "v1.3.0")
        assert reason is not None
        assert "version" in reason

    def test_a_release_candidate_is_below_the_release_it_precedes(self) -> None:
        assert why_not_deploy("v1.3.0-rc.1", "v*", "v1.3.0") is not None
        assert why_not_deploy("v1.3.0", "v*", "v1.3.0-rc.1") is None


# ---------------------------------------------------------------------------
# SourceManager: tags
# ---------------------------------------------------------------------------


def _without_trust(args: tuple[str, ...]) -> tuple[str, ...]:
    return args[len(TRUST) :] if args[: len(TRUST)] == TRUST else args


class GitRunner(FakeRunner):
    """
    A FakeRunner whose git answers come from a function of the arguments.

    Attributes:
        answer: Called with the arguments after ``git`` and its safe config;
            returns ``(exit_code, stdout, stderr)`` or None for the default.
    """

    def __init__(self, answer: Callable[[tuple[str, ...]], tuple[int, str, str] | None]) -> None:
        super().__init__()
        self.answer = answer

    def run(self, argv: Any, **kwargs: Any) -> CommandResult:
        result = super().run(argv, **kwargs)
        if tuple(argv[: len(GIT)]) == GIT:
            custom = self.answer(_without_trust(tuple(argv[len(GIT) :])))
            if custom is not None:
                code, out, err = custom
                return CommandResult(argv=tuple(argv), exit_code=code, stdout=out, stderr=err)
        return result

    def git(self) -> list[tuple[str, ...]]:
        """Every git call, without the common prefix."""
        return [_without_trust(call[len(GIT) :]) for call in self.calls if call[: len(GIT)] == GIT]


class TestResolveTag:
    def test_a_tag_the_clone_has_resolves_to_its_commit_without_the_network(
        self, tmp_path: Path
    ) -> None:
        def answer(args: tuple[str, ...]) -> tuple[int, str, str] | None:
            if args[:2] == ("rev-parse", "--verify"):
                return 0, FULL + "\n", ""
            return None

        runner = GitRunner(answer)
        assert SourceManager(runner=runner).resolve_tag(tmp_path, "v1.3.0") == FULL
        # The peeled commit, so an annotated tag names what it tags and not itself.
        assert ("rev-parse", "--verify", "refs/tags/v1.3.0^{commit}") in runner.git()
        assert not any(call[0] == "fetch" for call in runner.git())

    def test_a_tag_the_clone_lacks_is_fetched_by_name_and_asked_again(self, tmp_path: Path) -> None:
        fetched: list[tuple[str, ...]] = []

        def answer(args: tuple[str, ...]) -> tuple[int, str, str] | None:
            if args[:2] == ("rev-parse", "--verify"):
                return (0, OTHER + "\n", "") if fetched else (128, "", "fatal: bad revision")
            if args[0] == "fetch":
                fetched.append(args)
            return None

        runner = GitRunner(answer)
        assert SourceManager(runner=runner).resolve_tag(tmp_path, "v1.3.0") == OTHER
        assert fetched == [
            ("fetch", "--no-tags", "origin", "+refs/tags/v1.3.0:refs/tags/v1.3.0"),
        ]

    def test_a_tag_the_remote_does_not_have_says_so_and_what_to_do(self, tmp_path: Path) -> None:
        def answer(args: tuple[str, ...]) -> tuple[int, str, str] | None:
            if args[:2] == ("rev-parse", "--verify"):
                return 128, "", "fatal: bad revision"
            if args[0] == "fetch":
                return 128, "", "fatal: couldn't find remote ref refs/tags/v9.9.9"
            return None

        with pytest.raises(SourceError) as raised:
            SourceManager(runner=GitRunner(answer)).resolve_tag(tmp_path, "v9.9.9")
        assert "v9.9.9" in str(raised.value)
        assert raised.value.details and "git tag" in raised.value.details

    @pytest.mark.parametrize("tag", ["-rf", "v1..2", "v 1", "v1;rm", ""])
    def test_a_tag_that_is_not_a_ref_name_never_reaches_git(self, tmp_path: Path, tag: str) -> None:
        runner = GitRunner(lambda _args: None)
        with pytest.raises(SourceError):
            SourceManager(runner=runner).resolve_tag(tmp_path, tag)
        assert runner.git() == []


class TestTagsContainedIn:
    def test_the_tags_a_commit_contains_are_listed_by_one_read_only_command(
        self, tmp_path: Path
    ) -> None:
        def answer(args: tuple[str, ...]) -> tuple[int, str, str] | None:
            if args[:2] == ("tag", "--merged"):
                return 0, "v1.0.0\nv1.2.0\nlatest\n", ""
            return None

        runner = GitRunner(answer)
        tags = SourceManager(runner=runner).tags_merged_into(tmp_path, FULL)
        assert tags == ["v1.0.0", "v1.2.0", "latest"]
        asked = [call for call in runner.calls if "tag" in call]
        assert asked and all(is_read_only(call) for call in asked)

    def test_a_commit_the_clone_does_not_have_has_no_tags_rather_than_an_error(
        self, tmp_path: Path
    ) -> None:
        runner = GitRunner(lambda args: (128, "", "error: malformed object name"))
        assert SourceManager(runner=runner).tags_merged_into(tmp_path, FULL) == []


class TestRemoteTags:
    def test_the_tags_a_remote_has_come_from_one_ls_remote_without_peeled_duplicates(
        self,
    ) -> None:
        listing = (
            f"{FULL}\trefs/tags/v1.0.0\n{OTHER}\trefs/tags/v1.0.0^{{}}\n{FULL}\trefs/tags/v1.1.0\n"
        )

        def answer(args: tuple[str, ...]) -> tuple[int, str, str] | None:
            if args[0] == "ls-remote":
                return 0, listing, ""
            return None

        runner = GitRunner(answer)
        tags = SourceManager(runner=runner).remote_tags("https://github.com/acme/shop.git")
        assert tags == ["v1.0.0", "v1.1.0"]
        asked = [call for call in runner.calls if "ls-remote" in call]
        assert asked and all(is_read_only(call) for call in asked)

    def test_a_remote_with_no_tags_is_an_empty_list(self) -> None:
        runner = GitRunner(lambda args: (2, "", "") if args[0] == "ls-remote" else None)
        assert SourceManager(runner=runner).remote_tags("https://github.com/acme/shop.git") == []

    def test_a_remote_that_cannot_be_read_is_an_error(self) -> None:
        runner = GitRunner(
            lambda args: (128, "", "fatal: unable to access") if args[0] == "ls-remote" else None
        )
        with pytest.raises(SourceError):
            SourceManager(runner=runner).remote_tags("https://github.com/acme/shop.git")


# ---------------------------------------------------------------------------
# update_app with a tag, in place
# ---------------------------------------------------------------------------

import shutil  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from noust.core.exceptions import DeploymentError, ValidationError  # noqa: E402
from noust.core.store import NoustStore  # noqa: E402
from noust.deployers import lifecycle  # noqa: E402
from tests.test_lifecycle import DOMAIN as INPLACE_DOMAIN  # noqa: E402
from tests.test_lifecycle import FakeDeployer, Recorder, make_app  # noqa: E402
from tests.test_lifecycle import store as inplace_store  # noqa: E402,F401


@pytest.fixture
def inplace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inplace_store: NoustStore) -> Any:
    """
    An in-place application at v1.2.0, and every collaborator of its update faked.

    The fake clone knows the tags ``v1.2.0`` (what is live), ``v1.3.0`` (the
    commit ``OTHER``) and ``v1.10.0``, and the remote has one more.
    """
    rec = Recorder()
    app_path = tmp_path / "example-com"
    make_app(inplace_store, app_path)
    known = SimpleNamespace(
        commits={"v1.2.0": FULL, "v1.3.0": OTHER, "v1.10.0": "a" * 40},
        merged=["v1.0.0", "v1.2.0", "latest"],
        remote=["v1.0.0", "v1.2.0", "v1.3.0", "v1.10.0", "v1.9.0", "latest"],
        remote_error=None,
    )

    def resolve_tag(path: Path, tag: str) -> str:
        rec.calls.append(("resolve_tag", tag))
        if tag not in known.commits:
            raise SourceError(f"Tag {tag} does not exist in the repository")
        return known.commits[tag]

    def checkout_commit(path: Path, commit: str) -> str:
        rec.calls.append(("checkout", path, commit))
        return commit

    def tags_merged_into(path: Path, commit: str) -> list[str]:
        rec.calls.append(("merged", commit))
        return known.merged

    def remote_tags(source: str, **kwargs: Any) -> list[str]:
        rec.calls.append(("remote_tags",))
        if known.remote_error is not None:
            raise known.remote_error
        return known.remote

    monkeypatch.setattr(
        lifecycle,
        "RollbackManager",
        lambda verbose=False: SimpleNamespace(
            create_pre_deploy_backup=lambda **kw: rec.calls.append(("backup", kw["domain"]))
        ),
    )
    monkeypatch.setattr(
        lifecycle,
        "SourceManager",
        lambda verbose=False: SimpleNamespace(
            pull=lambda path, branch=None: rec.calls.append(("pull", path, branch)),
            checkout_commit=checkout_commit,
            resolve_tag=resolve_tag,
            tags_merged_into=tags_merged_into,
            remote_tags=remote_tags,
            get_repo_info=lambda path: {"commit": FULL[:7], "branch": "main", "detached": False},
        ),
    )
    monkeypatch.setattr(
        lifecycle,
        "ServiceManager",
        lambda verbose=False: SimpleNamespace(
            get_status=lambda name: {"exists": True, "active": True},
            restart=lambda name: rec.calls.append(("restart", name)),
        ),
    )
    monkeypatch.setattr(lifecycle.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        lifecycle, "get_deployer", lambda app_type, verbose=False: FakeDeployer(rec)
    )
    return SimpleNamespace(
        recorder=rec, app_path=app_path, store=inplace_store, known=known, calls=rec.calls
    )


def _follow(inplace: Any, pattern: str | None = "v*") -> None:
    assert inplace.store.set_app_follow_tags(INPLACE_DOMAIN, pattern)


def test_in_place_a_newer_tag_is_checked_out_instead_of_pulled(inplace: Any) -> None:
    """The tag's commit, detached, then the same rebuild and restart as any update."""
    lifecycle.update_app(INPLACE_DOMAIN, tag="v1.3.0")

    kinds = [call[0] for call in inplace.calls]
    assert "pull" not in kinds
    assert ("checkout", inplace.app_path, OTHER) in inplace.calls
    assert kinds[-3:] == ["checkout", "update", "restart"]
    assert ("resolve_tag", "v1.3.0") in inplace.calls


def test_in_place_the_tag_is_resolved_and_judged_before_the_backup(inplace: Any) -> None:
    """Nothing is worth backing up for an update that is about to be refused."""
    lifecycle.update_app(INPLACE_DOMAIN, tag="v1.3.0")

    kinds = [call[0] for call in inplace.calls]
    assert kinds.index("resolve_tag") < kinds.index("merged") < kinds.index("backup")


def test_in_place_a_tag_below_the_deployed_one_is_refused_before_anything_changes(
    inplace: Any,
) -> None:
    """v1.0.0 is contained in what serves v1.2.0: an update does not go back."""
    inplace.known.commits["v1.1.0"] = "b" * 40
    with pytest.raises(DeploymentError) as refused:
        lifecycle.update_app(INPLACE_DOMAIN, tag="v1.1.0")

    assert "v1.1.0" in str(refused.value) and "v1.2.0" in str(refused.value)
    assert refused.value.details and "rollback" in refused.value.details
    kinds = [call[0] for call in inplace.calls]
    assert "backup" not in kinds and "checkout" not in kinds and "update" not in kinds


def test_in_place_the_deployed_tag_again_is_rebuilt(inplace: Any) -> None:
    """Asked for by name, it is a rebuild, like --commit of the live commit."""
    lifecycle.update_app(INPLACE_DOMAIN, tag="v1.2.0")

    assert ("checkout", inplace.app_path, FULL) in inplace.calls
    assert ("update",) in inplace.calls


def test_in_place_numbers_are_compared_not_text(inplace: Any) -> None:
    """v1.10.0 is above v1.2.0 although "1.1" sorts below "1.2"."""
    lifecycle.update_app(INPLACE_DOMAIN, tag="v1.10.0")

    assert ("checkout", inplace.app_path, "a" * 40) in inplace.calls


def test_in_place_a_clone_that_knows_no_tag_has_nothing_to_go_back_past(inplace: Any) -> None:
    """The first tagged deploy of a branch application: no baseline, no refusal."""
    inplace.known.merged = []
    lifecycle.update_app(INPLACE_DOMAIN, tag="v1.3.0")

    assert ("checkout", inplace.app_path, OTHER) in inplace.calls


def test_in_place_a_tag_outside_the_followed_pattern_is_not_ordered(inplace: Any) -> None:
    """hotfix-1 is no step of the v* series; the operator named it, so it deploys."""
    _follow(inplace, "v*")
    inplace.known.commits["hotfix-1"] = "c" * 40

    lifecycle.update_app(INPLACE_DOMAIN, tag="hotfix-1")

    assert ("checkout", inplace.app_path, "c" * 40) in inplace.calls


def test_in_place_a_tag_the_remote_lacks_fails_before_the_backup(inplace: Any) -> None:
    with pytest.raises(SourceError, match="does not exist"):
        lifecycle.update_app(INPLACE_DOMAIN, tag="v9.9.9")
    assert "backup" not in [call[0] for call in inplace.calls]


def test_in_place_a_tree_without_history_has_no_tag_to_deploy(inplace: Any) -> None:
    shutil.rmtree(inplace.app_path / ".git")

    with pytest.raises(SourceError, match="not a git checkout"):
        lifecycle.update_app(INPLACE_DOMAIN, tag="v1.3.0")
    assert inplace.calls == []


@pytest.mark.parametrize(
    "extra", [{"commit": "0123abc"}, {"source": "https://github.com/a/b.git"}, {"branch": "main"}]
)
def test_a_tag_with_a_commit_a_source_or_a_branch_is_refused(
    inplace: Any, extra: dict[str, str]
) -> None:
    """A tag says exactly what to build; anything else would be ignored silently."""
    with pytest.raises(ValidationError, match="does not apply"):
        lifecycle.update_app(INPLACE_DOMAIN, tag="v1.3.0", **extra)
    assert inplace.calls == []


@pytest.mark.parametrize("tag", ["", "-rf", "v1..2", "v 1"])
def test_something_that_is_not_a_tag_name_is_refused_before_git(inplace: Any, tag: str) -> None:
    with pytest.raises((ValidationError, SourceError)):
        lifecycle.update_app(INPLACE_DOMAIN, tag=tag)
    assert inplace.calls == []


def test_an_application_that_follows_tags_updates_to_the_newest_one(inplace: Any) -> None:
    """No tag named: never the head of a branch, the newest tag of the pattern."""
    _follow(inplace, "v*")

    lifecycle.update_app(INPLACE_DOMAIN)

    assert ("remote_tags",) in inplace.calls
    assert ("checkout", inplace.app_path, "a" * 40) in inplace.calls
    assert "pull" not in [call[0] for call in inplace.calls]


def test_an_application_that_follows_tags_with_none_on_the_remote_says_so(
    inplace: Any,
) -> None:
    _follow(inplace, "release-*")

    with pytest.raises(SourceError) as failure:
        lifecycle.update_app(INPLACE_DOMAIN)
    assert "release-*" in str(failure.value)
    assert inplace.calls == [("remote_tags",)]


def test_an_application_that_follows_tags_refuses_a_branch(inplace: Any) -> None:
    """A branch would pin it, and a pinned branch and a followed tag contradict."""
    _follow(inplace, "v*")
    with pytest.raises(ValidationError, match="follows tags"):
        lifecycle.update_app(INPLACE_DOMAIN, branch="main")


def test_an_application_that_follows_a_branch_still_pulls(inplace: Any) -> None:
    lifecycle.update_app(INPLACE_DOMAIN)
    assert ("pull", inplace.app_path, None) in inplace.calls
    assert ("remote_tags",) not in inplace.calls


def test_a_commit_update_of_an_application_that_follows_tags_is_a_commit_update(
    inplace: Any,
) -> None:
    """A rollback rebuilds a commit; it must not turn into the newest tag."""
    _follow(inplace, "v*")

    lifecycle.update_app(INPLACE_DOMAIN, commit="0123abc")

    assert ("remote_tags",) not in inplace.calls
    assert ("checkout", inplace.app_path, "0123abc") in inplace.calls


# ---------------------------------------------------------------------------
# update_app with a tag, on releases
# ---------------------------------------------------------------------------

from noust.deployers.releases import ReleaseManager  # noqa: E402
from tests.test_rebuild_commit import HistoryGit  # noqa: E402
from tests.test_release_activation import two_releases  # noqa: E402,F401
from tests.test_release_pipeline import (  # noqa: E402,F401
    DOMAIN,
    GOOD_SERVER,
    active_id,
    machine,
    node_tree,
    root,
    store,
)


class TaggedGit(HistoryGit):
    """
    HistoryGit that also knows tags.

    Attributes:
        tags: Tag name to the commit it points at.
        remote: Every tag the remote lists.
    """

    def __init__(self) -> None:
        super().__init__()
        self.tags: dict[str, str] = {}
        self.remote: list[str] = []

    def resolve_tag(self, repository: Path, tag: str) -> str:
        self.calls.append(("resolve_tag", tag))
        if tag not in self.tags:
            raise SourceError(f"Tag {tag} does not exist in the repository")
        return self.tags[tag]

    def tags_merged_into(self, repository: Path, commit: str) -> list[str]:
        self.calls.append(("merged", commit))
        order = list(self.commits)
        live = next(c for c in order if c.startswith(commit))
        reachable = set(order[: order.index(live) + 1])
        return [tag for tag, target in self.tags.items() if target in reachable]

    def remote_tags(self, source: str, **kwargs: Any) -> list[str]:
        self.calls.append(("remote_tags", source))
        return self.remote


@pytest.fixture
def git() -> TaggedGit:
    """The fake repository, with history and tags."""
    return TaggedGit()


@pytest.fixture
def tagged(
    tmp_path: Path, store: NoustStore, machine: SimpleNamespace, two_releases: tuple[str, str]
) -> SimpleNamespace:
    """
    Two releases, the second serving; ``v1.0.0`` tags the first commit,
    ``v1.1.0`` the second (live), and ``v1.2.0`` a third not built yet.
    """
    first, second = two_releases
    c1, c2 = list(machine.git.commits)[:2]
    machine.git.publish(node_tree(tmp_path / "v3", server=GOOD_SERVER + "// v3\n"))
    c3 = list(machine.git.commits)[2]
    machine.git.tags.update({"v1.0.0": c1, "v1.1.0": c2, "v1.2.0": c3})
    machine.git.remote = ["v1.0.0", "v1.1.0", "v1.2.0", "latest"]
    return SimpleNamespace(
        first=first, second=second, commits=(c1, c2, c3), machine=machine, store=store
    )


def test_on_releases_a_newer_tag_is_built_as_a_release_of_exactly_its_commit(
    root: Path, tagged: SimpleNamespace
) -> None:
    """The head of the branch is also c3 here, so the export is what proves the commit."""
    c3 = tagged.commits[2]

    lifecycle.update_app(DOMAIN, tag="v1.2.0")

    rebuilt = active_id(root)
    assert rebuilt not in {tagged.first, tagged.second}
    assert rebuilt.split("-")[2] == c3[:7]
    exports = [c for c in tagged.machine.git.calls if c[0] == "export"]
    assert exports[-1][1] == c3


def test_on_releases_a_tag_is_resolved_from_the_repository_cache(
    tagged: SimpleNamespace,
) -> None:
    lifecycle.update_app(DOMAIN, tag="v1.2.0")

    assert ("resolve_tag", "v1.2.0") in tagged.machine.git.calls


def test_on_releases_a_tag_below_the_deployed_one_changes_nothing(
    root: Path, tagged: SimpleNamespace
) -> None:
    """v1.0.0 is on disk as a release, and would be activated: that is a rollback, not an update."""
    exports_before = [c for c in tagged.machine.git.calls if c[0] == "export"]

    with pytest.raises(DeploymentError) as refused:
        lifecycle.update_app(DOMAIN, tag="v1.0.0")

    assert "v1.0.0" in str(refused.value) and "v1.1.0" in str(refused.value)
    assert active_id(root) == tagged.second
    assert [c for c in tagged.machine.git.calls if c[0] == "export"] == exports_before


def test_on_releases_the_live_tag_again_rebuilds_it(root: Path, tagged: SimpleNamespace) -> None:
    lifecycle.update_app(DOMAIN, tag="v1.1.0")

    rebuilt = active_id(root)
    assert rebuilt != tagged.second
    assert rebuilt.split("-")[2] == tagged.commits[1][:7]


def test_on_releases_an_application_that_follows_tags_deploys_the_newest_remote_tag(
    root: Path, tagged: SimpleNamespace
) -> None:
    tagged.store.set_app_follow_tags(DOMAIN, "v*")

    lifecycle.update_app(DOMAIN)

    assert active_id(root).split("-")[2] == tagged.commits[2][:7]
    assert ("remote_tags", "https://github.com/example/app.git") in tagged.machine.git.calls


def test_on_releases_a_source_that_is_not_git_has_no_tag_to_deploy(
    tmp_path: Path, tagged: SimpleNamespace
) -> None:
    app = tagged.store.get_app(DOMAIN)
    app.source = str(tmp_path)
    tagged.store.update_app(app)

    with pytest.raises(SourceError, match="not deployed from git"):
        lifecycle.update_app(DOMAIN, tag="v1.2.0")


def test_on_releases_a_failed_tag_resolution_leaves_the_active_release(
    root: Path, tagged: SimpleNamespace
) -> None:
    with pytest.raises(SourceError, match="does not exist"):
        lifecycle.update_app(DOMAIN, tag="v9.9.9")

    assert active_id(root) == tagged.second
    assert len(ReleaseManager(root.parent / root.name).list()) == 2


# ---------------------------------------------------------------------------
# The setting: lifecycle.set_follow_tags
# ---------------------------------------------------------------------------

import json  # noqa: E402
from collections.abc import Iterator  # noqa: E402

from click.testing import CliRunner  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from noust.core.store import App  # noqa: E402
from noust.web.permissions.routes_apps import ROUTES  # noqa: E402

SHOP = "shop.example.com"
SHOP_SOURCE = "https://github.com/example/shop.git"


@pytest.fixture
def settings_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    """A store every module the setting passes through reads."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    monkeypatch.setattr(lifecycle, "get_store", lambda: instance)
    monkeypatch.setattr("noust.cli.commands.app.get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


def shop(store: NoustStore, *, source: str = SHOP_SOURCE, branch: str | None = "main") -> App:
    return store.create_app(
        App(domain=SHOP, app_type="nodejs", source=source, branch=branch, app_path="/tmp/shop")
    )


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "noust.core.audit.record", lambda event, **kw: events.append({"event": event, **kw})
    )
    return events


class TestSettingWhatIsFollowed:
    def test_a_pattern_is_stored_and_audited_with_the_one_before(
        self, settings_store: NoustStore, audited: list[dict[str, Any]]
    ) -> None:
        shop(settings_store)

        change = lifecycle.set_follow_tags(SHOP, "v*")

        assert (change.pattern, change.previous) == ("v*", None)
        assert settings_store.get_app(SHOP).follow_tags == "v*"  # type: ignore[union-attr]
        [event] = audited
        assert event["event"] == "apps.source"
        assert event["target"] == f"app:{SHOP}"
        assert event["details"] == {"follow_tags": "v*", "previous": None}

    def test_none_goes_back_to_following_the_branch(
        self, settings_store: NoustStore, audited: list[dict[str, Any]]
    ) -> None:
        shop(settings_store)
        lifecycle.set_follow_tags(SHOP, "v*")

        change = lifecycle.set_follow_tags(SHOP, None)

        assert (change.pattern, change.previous) == (None, "v*")
        assert settings_store.get_app(SHOP).follow_tags is None  # type: ignore[union-attr]

    @pytest.mark.parametrize("pattern", ["", "v*; rm -rf /", "v* ", "x" * 101, "$(id)"])
    def test_a_pattern_that_is_not_a_tag_glob_is_refused_and_nothing_changes(
        self, settings_store: NoustStore, audited: list[dict[str, Any]], pattern: str
    ) -> None:
        shop(settings_store)

        with pytest.raises(ValidationError):
            lifecycle.set_follow_tags(SHOP, pattern)

        assert settings_store.get_app(SHOP).follow_tags is None  # type: ignore[union-attr]
        assert audited == []

    def test_an_application_not_deployed_from_git_has_no_tags(
        self, settings_store: NoustStore
    ) -> None:
        shop(settings_store, source="/srv/site")

        with pytest.raises(SourceError, match="not deployed from git"):
            lifecycle.set_follow_tags(SHOP, "v*")

    def test_a_pinned_branch_and_followed_tags_contradict(
        self, settings_store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        shop(settings_store)
        settings_store.set_branch_pin(SHOP, "release")

        with pytest.raises(ValidationError, match="branch pinned"):
            lifecycle.set_follow_tags(SHOP, "v*")

    def test_following_tags_refuses_to_pin_a_branch_and_names_the_way_out(
        self, settings_store: NoustStore
    ) -> None:
        shop(settings_store)
        lifecycle.set_follow_tags(SHOP, "v*")

        with pytest.raises(ValidationError) as refused:
            lifecycle.set_branch(SHOP, "release")
        assert "follow-tags" in (refused.value.details or "")

    def test_unpinning_a_branch_is_still_allowed_while_following_tags(
        self, settings_store: NoustStore
    ) -> None:
        shop(settings_store)
        lifecycle.set_follow_tags(SHOP, "v*")

        assert lifecycle.set_branch(SHOP, None).branch is None

    def test_an_unknown_application(self, settings_store: NoustStore) -> None:
        with pytest.raises(NoustError, match="not found"):
            lifecycle.set_follow_tags(SHOP, "v*")

    def test_an_application_that_follows_tags_has_no_branch_head_to_compare(
        self, settings_store: NoustStore
    ) -> None:
        """The console's "nothing new" answer would compare a head the app does not deploy."""
        shop(settings_store)
        lifecycle.set_follow_tags(SHOP, "v*")

        assert lifecycle.check_upstream(SHOP) is None


# ---------------------------------------------------------------------------
# The commands
# ---------------------------------------------------------------------------

from noust.cli.app import cli as root_cli  # noqa: E402
from noust.cli.commands import webapp  # noqa: E402
from tests.test_cli_webapp import UpdateCalls, cli_runner, deployer, update_calls  # noqa: E402,F401


class TestUpdateTag:
    def test_a_tag_reaches_the_lifecycle_without_asking_the_remote_about_a_branch(
        self, cli_runner: CliRunner, update_calls: UpdateCalls
    ) -> None:
        result = cli_runner.invoke(
            webapp.cli.commands["update"], ["example.com", "--tag", "v1.2.3"]
        )

        assert result.exit_code == 0, result.output
        assert update_calls.checks == []
        assert update_calls.updates[0]["tag"] == "v1.2.3"
        assert update_calls.updates[0]["commit"] is None

    def test_a_plain_update_names_no_tag(
        self, cli_runner: CliRunner, update_calls: UpdateCalls
    ) -> None:
        cli_runner.invoke(webapp.cli.commands["update"], ["example.com"])

        assert update_calls.updates[0]["tag"] is None

    def test_the_legacy_entry_point_passes_the_tag_on(self, update_calls: UpdateCalls) -> None:
        from argparse import Namespace

        args = Namespace(domain="example.com", verbose=False, tag="v2.0.0")
        webapp._handle_update(args)

        assert update_calls.updates[0]["tag"] == "v2.0.0"


class TestFollowTagsCommand:
    def test_it_sets_shows_and_clears_the_pattern(self, settings_store: NoustStore) -> None:
        shop(settings_store)

        set_it = CliRunner().invoke(root_cli, ["app", "follow-tags", SHOP, "v*"])
        shown = CliRunner().invoke(root_cli, ["app", "follow-tags", SHOP, "--json"])
        cleared = CliRunner().invoke(root_cli, ["app", "follow-tags", SHOP, "--off"])

        assert set_it.exit_code == 0, set_it.output
        assert "v*" in set_it.output
        assert json.loads(shown.output) == {"domain": SHOP, "follow_tags": "v*", "following": True}
        assert cleared.exit_code == 0, cleared.output
        assert settings_store.get_app(SHOP).follow_tags is None  # type: ignore[union-attr]

    def test_a_pattern_and_off_together_are_a_usage_error(self, settings_store: NoustStore) -> None:
        shop(settings_store)

        result = CliRunner().invoke(root_cli, ["app", "follow-tags", SHOP, "v*", "--off"])

        assert result.exit_code == 2, result.output
        assert settings_store.get_app(SHOP).follow_tags is None  # type: ignore[union-attr]

    def test_an_unknown_application(self, settings_store: NoustStore) -> None:
        result = CliRunner().invoke(root_cli, ["app", "follow-tags", SHOP, "v*"])

        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "not found" in str(result.exception).lower()


class TestCreateFollowingTags:
    @pytest.fixture
    def remote(self, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
        """The tags the remote has, and what the command did after the deploy."""
        state = SimpleNamespace(
            tags=["v1.9.0", "v1.10.0", "v1.2.0", "latest"], followed=[], asked=[]
        )

        def remote_tags(self: Any, source: str, **kwargs: Any) -> list[str]:
            state.asked.append(source)
            return state.tags

        monkeypatch.setattr(webapp.SourceManager, "remote_tags", remote_tags)
        monkeypatch.setattr(
            webapp,
            "_follow_tags_after_create",
            lambda domain, pattern, tag: state.followed.append((domain, pattern, tag)),
        )
        return state

    def test_the_newest_matching_tag_is_deployed_and_then_followed(
        self, cli_runner: CliRunner, deployer: Any, remote: SimpleNamespace
    ) -> None:
        result = cli_runner.invoke(
            webapp.cli.commands["create"],
            [
                "-d",
                "example.com",
                "-s",
                "https://github.com/user/repo",
                "-t",
                "nextjs",
                "--follow-tags",
                "v*",
            ],
        )

        assert result.exit_code == 0, result.output
        # The deployer is given the tag as the ref to build: the version order,
        # not the text order, picked it.
        assert deployer.configured["branch"] == "v1.10.0"
        assert remote.followed == [("example.com", "v*", "v1.10.0")]

    def test_a_branch_and_tags_cannot_both_be_asked_for(
        self, cli_runner: CliRunner, deployer: Any, remote: SimpleNamespace
    ) -> None:
        result = cli_runner.invoke(
            webapp.cli.commands["create"],
            [
                "-d",
                "example.com",
                "-s",
                "https://github.com/u/r",
                "-b",
                "main",
                "--follow-tags",
                "v*",
            ],
        )

        assert result.exit_code == 2, result.output
        assert deployer.deployed is False

    def test_no_tag_that_matches_deploys_nothing(
        self, cli_runner: CliRunner, deployer: Any, remote: SimpleNamespace
    ) -> None:
        remote.tags = ["latest"]

        result = cli_runner.invoke(
            webapp.cli.commands["create"],
            ["-d", "example.com", "-s", "https://github.com/u/r", "--follow-tags", "v*"],
        )

        assert result.exit_code != 0
        assert deployer.deployed is False
        assert "v*" in str(result.exception)
        assert remote.followed == []

    def test_a_pattern_that_is_not_a_glob_is_refused_before_the_network(
        self, cli_runner: CliRunner, deployer: Any, remote: SimpleNamespace
    ) -> None:
        result = cli_runner.invoke(
            webapp.cli.commands["create"],
            ["-d", "example.com", "-s", "https://github.com/u/r", "--follow-tags", "v*;id"],
        )

        assert result.exit_code != 0
        assert remote.asked == [] and deployer.deployed is False

    def test_a_failed_deploy_follows_nothing(
        self,
        cli_runner: CliRunner,
        deployer: Any,
        remote: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(deployer, "deploy", lambda: (_ for _ in ()).throw(NoustError("boom")))

        result = cli_runner.invoke(
            webapp.cli.commands["create"],
            [
                "-d",
                "example.com",
                "-s",
                "https://github.com/u/r",
                "-t",
                "nextjs",
                "--follow-tags",
                "v*",
            ],
        )

        assert result.exit_code != 0
        assert remote.followed == []


class TestFollowTagsAfterCreate:
    def test_the_pattern_is_set_and_the_tag_is_no_longer_called_a_branch(
        self, settings_store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The deployer recorded the tag as the ref it built; it is not a branch to go back to."""
        shop(settings_store, branch="v1.10.0")
        monkeypatch.setattr(webapp, "get_store", lambda: settings_store)

        webapp._follow_tags_after_create(SHOP, "v*", "v1.10.0")

        stored = settings_store.get_app(SHOP)
        assert stored is not None
        assert (stored.follow_tags, stored.branch) == ("v*", None)

    def test_a_branch_that_is_not_the_tag_is_left_alone(
        self, settings_store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        shop(settings_store, branch="main")
        monkeypatch.setattr(webapp, "get_store", lambda: settings_store)

        webapp._follow_tags_after_create(SHOP, "v*", "v1.10.0")

        assert settings_store.get_app(SHOP).branch == "main"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------


class TestTheApi:
    @pytest.fixture
    def api(self, settings_store: NoustStore) -> TestClient:
        from noust.web.api import apps as apps_api
        from noust.web.api.auth import get_current_session
        from noust.web.api.deps import install_error_handlers, require_elevated

        server = FastAPI()
        install_error_handlers(server)
        server.include_router(apps_api.router, prefix="/api/apps")
        session = {"sid": "operator", "type": "master"}
        server.dependency_overrides[get_current_session] = lambda: session
        server.dependency_overrides[require_elevated] = lambda: session
        return TestClient(server, raise_server_exceptions=False)

    def test_following_tags_answers_the_pattern_and_the_one_before(
        self, api: TestClient, settings_store: NoustStore
    ) -> None:
        shop(settings_store)

        response = api.patch(f"/api/apps/{SHOP}/follow-tags", json={"pattern": "v*"})

        assert response.status_code == 200, response.text
        assert response.json() == {
            "domain": SHOP,
            "follow_tags": "v*",
            "following": True,
            "previous": None,
        }

    def test_null_goes_back_to_the_branch(
        self, api: TestClient, settings_store: NoustStore
    ) -> None:
        shop(settings_store)
        settings_store.set_app_follow_tags(SHOP, "v*")

        response = api.patch(f"/api/apps/{SHOP}/follow-tags", json={"pattern": None})

        assert response.status_code == 200, response.text
        assert response.json()["following"] is False
        assert settings_store.get_app(SHOP).follow_tags is None  # type: ignore[union-attr]

    def test_a_bad_pattern_is_a_400_that_names_the_field(
        self, api: TestClient, settings_store: NoustStore
    ) -> None:
        shop(settings_store)

        response = api.patch(f"/api/apps/{SHOP}/follow-tags", json={"pattern": "v*; ls"})

        assert response.status_code in (400, 422), response.text
        assert "follow_tags" in response.text

    def test_the_application_reports_what_it_follows(
        self, api: TestClient, settings_store: NoustStore
    ) -> None:
        shop(settings_store)
        settings_store.set_app_follow_tags(SHOP, "v*")
        from noust.web.api import apps as apps_api

        info = apps_api._to_app_info(
            settings_store.get_app(SHOP),  # type: ignore[arg-type]
            SimpleNamespace(label="RUNNING"),
            {},
            None,
            webhook_enabled=False,
            last_deployment=None,
        )

        assert info.follow_tags == "v*"

    def test_the_route_needs_apps_manage(self) -> None:
        assert ROUTES[("PATCH", "/api/apps/{domain}/follow-tags")] == "apps.manage"
