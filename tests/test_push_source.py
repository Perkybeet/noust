"""
Filling an application's repository cache from its push repository (checklist A-23).

What is pinned: that ``push:<domain>`` is a git source everywhere a source is judged and that the
only code that knows where its commits are is :mod:`noust.pushdeploy.source`; that reads of the
repository run as the push account and the one fill of the cache is root's, with the local
transport allowed for that command alone and objects checked as they arrive; that a source
deploys its own application only (whoever typed it into another); and that the allowed origins of
an ENS server can name it (``push``) and nothing else in the list allows it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.ens.origins import OriginNotAllowed, check_origin, describe_origin, matches
from noust.core.exceptions import SourceError
from noust.core.logger import Logger
from noust.managers.source_manager import SourceManager, validate_git_remote_url
from noust.pushdeploy import source as push
from noust.validators.source import (
    is_push_source,
    push_source,
    push_source_domain,
    validate_source,
)
from tests.push_support import DOMAIN, OTHER, PushRig, build_rig


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[PushRig]:
    yield from build_rig(tmp_path, monkeypatch)


@pytest.fixture
def ready(rig: PushRig) -> PushRig:
    rig.set_up()
    rig.app()
    rig.repos.enable(DOMAIN)
    rig.runner.branches[str(rig.world.home / f"{DOMAIN}.git")] = {"main": "a" * 40}
    return rig


def cache_of(rig: PushRig, domain: str = DOMAIN) -> Path:
    app = rig.store.get_app(domain)
    assert app is not None
    return Path(app.app_path) / "repo"


def manager(rig: PushRig) -> SourceManager:
    return SourceManager(runner=rig.runner)


class TestTheSourceString:
    def test_it_is_a_git_source_for_everything_that_judges_one(self) -> None:
        assert validate_source("push:Shop.Example.com") == ("git", "push:shop.example.com")
        assert is_push_source("push:x") and not is_push_source("https://x.example/r.git")
        assert is_push_source(None) is False and is_push_source("") is False
        assert push_source(DOMAIN) == f"push:{DOMAIN}"
        assert push_source_domain(" push:Shop.example.com ") == DOMAIN

    @pytest.mark.parametrize(
        "text",
        [
            "push:",
            "push:not a domain",
            "push:-bad",
            "push:a/b",
            "push:../etc",
            "push:https://x.example/y",
            "push:x\n.y",
        ],
    )
    def test_what_is_not_a_domain_is_refused(self, text: str) -> None:
        with pytest.raises(SourceError):
            validate_source(text)

    def test_it_is_not_a_url_git_can_clone(self) -> None:
        with pytest.raises(SourceError) as caught:
            validate_git_remote_url(push_source(DOMAIN))
        assert "repository on this server" in str(caught.value)

    def test_an_in_place_checkout_is_not_filled_from_it(self, rig: PushRig, tmp_path: Path) -> None:
        with pytest.raises(SourceError) as caught:
            manager(rig).fetch(push_source(DOMAIN), tmp_path / "checkout")
        assert "release layout" in str(caught.value)
        assert not rig.runner.calls


class TestFillingTheCache:
    def test_the_first_time_it_is_cloned_by_root_with_the_local_transport_for_that_command_only(
        self, ready: PushRig
    ) -> None:
        cache = cache_of(ready)
        ready.runner.cache_head = "f" * 40

        commit = manager(ready).sync_cache(push_source(DOMAIN), cache, None)

        assert commit == "f" * 40
        repository = ready.world.home / f"{DOMAIN}.git"
        clone = next(call for call in ready.runner.calls if "clone" in call)
        assert clone[0] == "git" and "runuser" not in clone
        assert "protocol.file.allow=always" in clone
        assert f"safe.directory={repository}" in clone
        assert "transfer.fsckObjects=true" in clone
        assert "core.hooksPath=/dev/null" in clone
        assert "--no-hardlinks" in clone and "--no-recurse-submodules" in clone
        assert clone[clone.index("--branch") + 1] == "main"
        assert clone[-2:] == (str(repository), str(cache))
        # The local transport is allowed for the clone and nowhere else.
        for call in ready.runner.calls:
            if call is not clone and call[0] == "git":
                assert "protocol.file.allow=always" not in call or "fetch" in call

    def test_what_only_reads_runs_as_the_push_account(self, ready: PushRig) -> None:
        manager(ready).sync_cache(push_source(DOMAIN), cache_of(ready), None)

        read = next(
            call for call in ready.runner.calls if "rev-parse" in call and "--verify" in call
        )
        assert read[:4] == ("runuser", "-u", "noust-git", "--")
        assert "protocol.file.allow=never" in read
        index = ready.runner.calls.index(read)
        assert ready.runner.envs[index]["HOME"] == str(ready.world.home)

    def test_the_next_time_it_is_fetched_and_reset_to_the_branch(self, ready: PushRig) -> None:
        cache = cache_of(ready)
        manager(ready).sync_cache(push_source(DOMAIN), cache, None)
        ready.runner.calls.clear()

        manager(ready).sync_cache(push_source(DOMAIN), cache, None)

        verbs = [
            next(w for w in call if w in ("clone", "fetch", "checkout", "reset"))
            for call in ready.runner.calls
            if any(w in call for w in ("clone", "fetch", "checkout", "reset"))
        ]
        assert verbs == ["fetch", "checkout", "reset"]
        fetch = next(call for call in ready.runner.calls if "fetch" in call)
        assert (
            "+refs/heads/*:refs/remotes/origin/*" in fetch and "+refs/tags/*:refs/tags/*" in fetch
        )
        assert "--prune" in fetch and "protocol.file.allow=always" in fetch
        reset = next(call for call in ready.runner.calls if "reset" in call)
        assert reset[-2:] == ("--hard", "origin/main")

    def test_a_cache_that_is_another_sources_clone_is_made_again(self, ready: PushRig) -> None:
        cache = cache_of(ready)
        (cache / ".git").mkdir(parents=True)
        (cache / "stale.txt").write_text("from the old source")
        ready.world.origins[str(cache)] = "https://github.com/acme/shop.git"

        manager(ready).sync_cache(push_source(DOMAIN), cache, None)

        assert any("clone" in call for call in ready.runner.calls)
        assert not (cache / "stale.txt").exists()

    def test_another_branch_than_the_one_deployed_can_be_asked_for(self, ready: PushRig) -> None:
        repository = str(ready.world.home / f"{DOMAIN}.git")
        ready.runner.branches[repository]["release"] = "b" * 40
        manager(ready).sync_cache(push_source(DOMAIN), cache_of(ready), "release")
        clone = next(call for call in ready.runner.calls if "clone" in call)
        assert clone[clone.index("--branch") + 1] == "release"

    def test_nothing_pushed_yet_says_how_to_push(self, ready: PushRig) -> None:
        ready.runner.branches.clear()
        with pytest.raises(SourceError) as caught:
            manager(ready).sync_cache(push_source(DOMAIN), cache_of(ready), None)
        assert "Nothing has been pushed to main of shop.example.com yet" in str(caught.value)
        assert "git push" in caught.value.details
        assert not any("clone" in call for call in ready.runner.calls)

    def test_a_branch_name_that_is_an_option_is_refused(self, ready: PushRig) -> None:
        with pytest.raises(SourceError):
            manager(ready).sync_cache(push_source(DOMAIN), cache_of(ready), "--upload-pack=x")

    def test_a_missing_repository_says_how_to_make_it(self, ready: PushRig) -> None:
        import shutil

        shutil.rmtree(ready.world.home / f"{DOMAIN}.git")
        with pytest.raises(SourceError) as caught:
            manager(ready).sync_cache(push_source(DOMAIN), cache_of(ready), None)
        assert f"noust push enable {DOMAIN}" in caught.value.details

    def test_a_clone_that_fails_says_what_git_said(self, ready: PushRig) -> None:
        ready.runner.script(["git", "-c"], stderr="fatal: bad object", exit_code=128)
        with pytest.raises(SourceError) as caught:
            manager(ready).sync_cache(push_source(DOMAIN), cache_of(ready), None)
        assert "fatal: bad object" in caught.value.details


class TestAnApplicationDeploysItsOwnRepositoryOnly:
    def test_another_applications_cache_is_not_filled_from_it(self, ready: PushRig) -> None:
        ready.app(OTHER)
        ready.runner.calls.clear()
        with pytest.raises(SourceError) as caught:
            manager(ready).sync_cache(push_source(DOMAIN), cache_of(ready, OTHER), None)
        assert "deploys shop.example.com only" in str(caught.value)
        assert not ready.runner.calls

    def test_a_cache_outside_the_applications_directory_is_not_either(
        self, ready: PushRig, tmp_path: Path
    ) -> None:
        with pytest.raises(SourceError, match=r"deploys shop\.example\.com only"):
            manager(ready).sync_cache(push_source(DOMAIN), tmp_path / "elsewhere", None)

    def test_an_application_that_does_not_deploy_from_push_has_no_repository_to_read(
        self, ready: PushRig
    ) -> None:
        ready.app(OTHER)
        with pytest.raises(SourceError) as caught:
            manager(ready).sync_cache(push_source(OTHER), cache_of(ready, OTHER), None)
        assert f"noust push enable {OTHER}" in caught.value.details

    def test_an_application_that_does_not_exist(self, ready: PushRig, tmp_path: Path) -> None:
        with pytest.raises(SourceError):
            manager(ready).sync_cache(push_source("ghost.example.com"), tmp_path / "repo", None)


class TestAskingWithoutFetching:
    def test_the_head_of_the_branch(self, ready: PushRig) -> None:
        head = manager(ready).remote_head(push_source(DOMAIN))
        assert (head.branch, head.commit) == ("main", "a" * 40)

    def test_a_branch_that_was_not_pushed(self, ready: PushRig) -> None:
        with pytest.raises(SourceError, match="Nothing has been pushed to nope"):
            manager(ready).remote_head(push_source(DOMAIN), "nope")

    def test_the_tags_pushed(self, ready: PushRig) -> None:
        repository = str(ready.world.home / f"{DOMAIN}.git")
        ready.runner.tags[repository] = ["v1.0", "v1.1"]
        assert manager(ready).remote_tags(push_source(DOMAIN)) == ["v1.0", "v1.1"]
        call = next(call for call in ready.runner.calls if "for-each-ref" in call)
        assert call[:4] == ("runuser", "-u", "noust-git", "--")

    def test_an_application_without_push_has_nothing_to_ask(self, ready: PushRig) -> None:
        ready.app(OTHER)
        with pytest.raises(SourceError, match="does not deploy from git push"):
            manager(ready).remote_head(push_source(OTHER))
        with pytest.raises(SourceError, match="does not deploy from git push"):
            manager(ready).remote_tags(push_source(OTHER))


def test_the_module_is_what_the_manager_delegates_to(ready: PushRig) -> None:
    commit = push.sync_cache(
        push_source(DOMAIN),
        cache_of(ready),
        None,
        runner=ready.runner,
        fs=ready.repos.fs,
        logger=Logger(),
    )
    assert commit == ready.runner.cache_head


class TestAllowedOrigins:
    def test_no_list_allows_it(self) -> None:
        check_origin(push_source(DOMAIN), allowed=[], kind="push")

    def test_a_list_of_hosts_does_not(self) -> None:
        with pytest.raises(OriginNotAllowed) as caught:
            check_origin(push_source(DOMAIN), allowed=["github.com/acme"], kind="push")
        assert "push:shop.example.com" in str(caught.value)
        assert "github.com/acme, push" in caught.value.details

    def test_local_does_not_allow_it_either(self) -> None:
        with pytest.raises(OriginNotAllowed):
            check_origin(push_source(DOMAIN), allowed=["local"], kind="push")

    def test_the_entry_push_does_and_only_it(self) -> None:
        check_origin(push_source(DOMAIN), allowed=["github.com", "push"], kind="push")
        origin = describe_origin(push_source(DOMAIN))
        assert origin.kind == "push" and origin.host is None
        assert matches("push", origin) and matches("PUSH", origin)
        assert not matches("github.com", origin) and not matches("local", origin)
        # And nothing else is allowed by it.
        assert not matches("push", describe_origin("https://github.com/acme/shop.git"))
        assert not matches("push", describe_origin("/srv/app", "local"))

    def test_the_manager_asks_before_any_git_runs(
        self, ready: PushRig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "noust.core.ens.origins.allowed_sources", lambda config=None: ["github.com"]
        )
        ready.runner.calls.clear()
        with pytest.raises(OriginNotAllowed):
            manager(ready).sync_cache(push_source(DOMAIN), cache_of(ready), None)
        assert not ready.runner.calls
