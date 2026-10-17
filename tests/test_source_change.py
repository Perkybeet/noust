"""
Changing where an application's code comes from (checklist A-20).

What is pinned: that the new address is judged by the rules a deploy applies (no credential,
the allowed origins, a layout that builds releases); that the remote is asked before anything is
written, and not at all when the operator says so; that the plan names what the change does not
carry (the old repository's webhook, history the new one may lack, previews of the old source,
tag following); and that applying it writes the source, the branch and the pin together, audited,
and never while an update runs.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.applock import AppBusyError
from noust.core.exceptions import NoustError, SourceError, ValidationError
from noust.core.runner import FakeRunner, set_runner
from noust.core.store import App, NoustStore
from noust.deployers import source_change
from noust.managers.source_manager import _GIT_SAFE_CONFIG
from tests.test_applock import Holder

DOMAIN = "shop.example.com"
OLD = "https://github.com/example/shop.git"
NEW = "https://git.example.org/team/shop.git"
HEAD = "4f1c2b9d8e7a6f5c4b3a29181716151413121110"
OTHER_HEAD = "0a1b2c3d4e5f60718293a4b5c6d7e8f901234567"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    monkeypatch.setattr(source_change, "get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def remote(runner: FakeRunner) -> Iterator[FakeRunner]:
    """git answering for the new repository: a release branch and a default branch, trunk."""
    ask = ["git", *_GIT_SAFE_CONFIG, "ls-remote"]
    runner.script(
        [*ask, "--exit-code", "--", NEW, "refs/heads/release"],
        stdout=f"{HEAD}\trefs/heads/release\n",
    )
    runner.script([*ask, "--exit-code", "--", NEW, "refs/heads/nope"], exit_code=2)
    runner.script(
        [*ask, "--symref", "--", NEW, "HEAD"],
        stdout=f"ref: refs/heads/trunk\tHEAD\n{OTHER_HEAD}\tHEAD\n",
    )
    set_runner(runner)
    yield runner
    set_runner(None)


def deployed(store: NoustStore, **fields: Any) -> App:
    values: dict[str, Any] = {
        "domain": DOMAIN,
        "app_type": "nodejs",
        "source": OLD,
        "branch": "main",
        "app_path": "/tmp/x",
        "layout": "releases",
    }
    values.update(fields)
    return store.create_app(App(**values))


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "noust.core.audit.record", lambda event, **kw: events.append({"event": event, **kw})
    )
    return events


def asked(runner: FakeRunner) -> list[tuple[str, ...]]:
    return [call for call in runner.calls if "ls-remote" in call]


class TestThePlan:
    def test_it_asks_the_remote_for_the_branch_and_changes_nothing(
        self, store: NoustStore, remote: FakeRunner
    ) -> None:
        deployed(store)

        plan = source_change.plan_change(DOMAIN, NEW, "release")

        assert (plan.target, plan.target_branch, plan.head) == (NEW, "release", HEAD)
        assert plan.checked and plan.changed and not plan.same_repository
        assert len(asked(remote)) == 1
        row = store.get_app(DOMAIN)
        assert row is not None and (row.source, row.branch) == (OLD, "main")

    def test_without_a_branch_it_takes_the_default_one_of_a_new_repository(
        self, store: NoustStore, remote: FakeRunner
    ) -> None:
        deployed(store)

        plan = source_change.plan_change(DOMAIN, NEW)

        assert (plan.target_branch, plan.head) == ("trunk", OTHER_HEAD)

    def test_the_same_repository_at_another_address_keeps_its_branch(
        self, store: NoustStore, runner: FakeRunner
    ) -> None:
        deployed(store)
        ssh = "git@github.com:example/shop.git"
        runner.script(
            ["git", *_GIT_SAFE_CONFIG, "ls-remote", "--exit-code", "--", ssh, "refs/heads/main"],
            stdout=f"{HEAD}\trefs/heads/main\n",
        )
        set_runner(runner)

        plan = source_change.plan_change(DOMAIN, ssh)

        assert plan.same_repository and plan.target_branch == "main"
        assert not any("another repository" in warning for warning in plan.warnings)

    def test_a_remote_that_cannot_be_read_stops_it_before_anything_is_written(
        self, store: NoustStore, runner: FakeRunner
    ) -> None:
        deployed(store)
        runner.script(
            ["git", *_GIT_SAFE_CONFIG, "ls-remote"],
            exit_code=128,
            stderr="fatal: could not read Username for 'https://git.example.org'",
        )
        set_runner(runner)

        with pytest.raises(SourceError):
            source_change.change_source(DOMAIN, NEW, "main")

        row = store.get_app(DOMAIN)
        assert row is not None and (row.source, row.branch) == (OLD, "main")
        set_runner(None)

    def test_a_branch_the_remote_does_not_have_is_refused(
        self, store: NoustStore, remote: FakeRunner
    ) -> None:
        deployed(store)

        with pytest.raises(SourceError, match="nope"):
            source_change.plan_change(DOMAIN, NEW, "nope")

    def test_it_can_skip_the_network_when_the_branch_is_given(
        self, store: NoustStore, remote: FakeRunner
    ) -> None:
        deployed(store)

        plan = source_change.plan_change(DOMAIN, NEW, "release", check=False)

        assert asked(remote) == []
        assert not plan.checked and plan.head is None
        assert any("was not asked" in warning for warning in plan.warnings)

    def test_without_the_network_a_new_repository_needs_its_branch_named(
        self, store: NoustStore, remote: FakeRunner
    ) -> None:
        deployed(store)

        with pytest.raises(ValidationError, match="Name the branch"):
            source_change.plan_change(DOMAIN, NEW, check=False)

    def test_the_same_address_and_branch_is_nothing_to_change(
        self, store: NoustStore, runner: FakeRunner
    ) -> None:
        deployed(store)
        runner.script(
            ["git", *_GIT_SAFE_CONFIG, "ls-remote", "--exit-code", "--", OLD, "refs/heads/main"],
            stdout=f"{HEAD}\trefs/heads/main\n",
        )
        set_runner(runner)

        assert source_change.plan_change(DOMAIN, OLD).changed is False
        with pytest.raises(ValidationError, match="already deploys"):
            source_change.change_source(DOMAIN, OLD)
        set_runner(None)


class TestWhatItDoesNotCarry:
    def test_another_repository_names_the_history_it_may_lack(
        self, store: NoustStore, remote: FakeRunner
    ) -> None:
        deployed(store)

        plan = source_change.plan_change(DOMAIN, NEW, "release")

        [warning] = plan.warnings
        assert "another repository" in warning and "update --commit" in warning

    def test_the_webhook_of_the_old_repository_is_named(
        self, store: NoustStore, remote: FakeRunner
    ) -> None:
        deployed(store)
        store.set_webhook_secret(DOMAIN, "s" * 32)

        plan = source_change.plan_change(DOMAIN, NEW, "release")

        assert any(f"noust webhook show {DOMAIN}" in warning for warning in plan.warnings)

    def test_previews_built_from_the_old_source_are_listed(
        self, store: NoustStore, remote: FakeRunner
    ) -> None:
        deployed(store)
        deployed(
            store, domain="pr-4.shop.example.com", preview_parent=DOMAIN, app_path="/tmp/preview"
        )

        plan = source_change.plan_change(DOMAIN, NEW, "release")

        assert plan.previews == ("pr-4.shop.example.com",)
        assert any("pr-4.shop.example.com" in warning for warning in plan.warnings)

    def test_following_tags_is_named_for_a_repository_and_refused_for_a_directory(
        self, store: NoustStore, remote: FakeRunner, tmp_path: Path
    ) -> None:
        deployed(store, follow_tags="v*")

        plan = source_change.plan_change(DOMAIN, NEW, "release")
        assert any("tags matching v*" in warning for warning in plan.warnings)

        directory = tmp_path / "code"
        directory.mkdir()
        with pytest.raises(ValidationError, match="follows tags"):
            source_change.plan_change(DOMAIN, str(directory))


class TestWhatIsRefused:
    @pytest.mark.parametrize(
        "address",
        [
            "https://user:s3cr3tvalue@git.example.org/team/shop.git",
            "https://oauth2:abcdef123@git.example.org/team/shop.git",
        ],
    )
    def test_an_address_with_a_credential(
        self, store: NoustStore, remote: FakeRunner, address: str
    ) -> None:
        deployed(store)

        with pytest.raises(ValidationError, match="credential") as caught:
            source_change.plan_change(DOMAIN, address)

        shown = f"{caught.value} {caught.value.details}"
        assert "s3cr3tvalue" not in shown and "abcdef123" not in shown

    def test_an_empty_address(self, store: NoustStore) -> None:
        deployed(store)

        with pytest.raises(ValidationError, match="Name the new source"):
            source_change.plan_change(DOMAIN, "   ")

    def test_something_that_is_not_a_source(self, store: NoustStore) -> None:
        deployed(store)

        with pytest.raises(SourceError):
            source_change.plan_change(DOMAIN, "not a source at all")

    def test_a_directory_that_does_not_exist(self, store: NoustStore, tmp_path: Path) -> None:
        deployed(store)

        with pytest.raises(ValidationError, match="not a directory"):
            source_change.plan_change(DOMAIN, str(tmp_path / "missing"))

    def test_a_branch_with_a_directory(self, store: NoustStore, tmp_path: Path) -> None:
        deployed(store)
        directory = tmp_path / "code"
        directory.mkdir()

        with pytest.raises(ValidationError, match="branch goes with a git repository"):
            source_change.plan_change(DOMAIN, str(directory), "main")

    def test_a_push_repository_is_turned_on_for_an_application_not_named(
        self, store: NoustStore
    ) -> None:
        deployed(store)

        with pytest.raises(ValidationError, match="noust push enable") as caught:
            source_change.plan_change(DOMAIN, f"push:{DOMAIN}")

        assert "noust push enable" in str(caught.value.details)

    def test_an_origin_the_server_does_not_allow(
        self, store: NoustStore, remote: FakeRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        deployed(store)
        monkeypatch.setattr(
            "noust.core.ens.origins.allowed_sources", lambda *_a, **_k: ["github.com/example"]
        )

        with pytest.raises(NoustError, match="not allowed"):
            source_change.plan_change(DOMAIN, NEW, "release")

        assert asked(remote) == []

    @pytest.mark.parametrize(
        ("fields", "message"),
        [
            ({"preview_parent": "other.example.com"}, "is a preview"),
            ({"app_type": "container"}, "whose source is its own"),
            ({"layout": "inplace"}, "not on the release layout"),
            ({"source": "push:shop.example.com"}, "deploys from git push"),
        ],
    )
    def test_an_application_whose_source_is_not_this_to_change(
        self,
        store: NoustStore,
        remote: FakeRunner,
        fields: dict[str, Any],
        message: str,
    ) -> None:
        deployed(store, **fields)

        with pytest.raises(ValidationError, match=message):
            source_change.plan_change(DOMAIN, NEW, "release")

    def test_an_application_that_does_not_exist(self, store: NoustStore) -> None:
        with pytest.raises(NoustError, match="Application not found"):
            source_change.plan_change("missing.example.com", NEW, "release")


class TestApplying:
    def test_it_writes_the_source_and_the_branch_and_audits_it(
        self, store: NoustStore, remote: FakeRunner, audited: list[dict[str, Any]]
    ) -> None:
        deployed(store)

        plan = source_change.change_source(DOMAIN, NEW, "release", actor="ana")

        row = store.get_app(DOMAIN)
        assert row is not None and (row.source, row.branch) == (NEW, "release")
        assert plan.changed
        [event] = audited
        assert event["event"] == "apps.source" and event["target"] == f"app:{DOMAIN}"
        assert event["details"] == {
            "source": NEW,
            "previous": OLD,
            "branch": "release",
            "previous_branch": "main",
            "by": "ana",
        }
        # Nothing was fetched or built: only the remote was asked.
        assert {call[0] for call in remote.calls} == {"git"}
        assert all("ls-remote" in call for call in remote.calls)

    def test_a_pinned_branch_follows_the_change_and_an_unpinned_one_stays_unpinned(
        self, store: NoustStore, remote: FakeRunner
    ) -> None:
        deployed(store)
        store.set_branch_pin(DOMAIN, "main")

        source_change.change_source(DOMAIN, NEW, "release")

        assert store.get_branch_pin(DOMAIN) == "release"

        other = "other.example.com"
        deployed(store, domain=other, app_path="/tmp/y")
        assert store.get_branch_pin(other) is None

    def test_a_directory_drops_the_branch_and_the_pin(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        deployed(store)
        store.set_branch_pin(DOMAIN, "main")
        directory = tmp_path / "code"
        directory.mkdir()

        plan = source_change.change_source(DOMAIN, str(directory))

        row = store.get_app(DOMAIN)
        assert row is not None and row.source == str(directory.resolve()) and row.branch is None
        assert store.get_branch_pin(DOMAIN) is None
        assert plan.target_kind == "local"
        assert any("stops deploying" in warning for warning in plan.warnings)

    def test_it_is_refused_while_an_update_runs(
        self, store: NoustStore, remote: FakeRunner
    ) -> None:
        deployed(store)

        with Holder(DOMAIN, "update"), pytest.raises(AppBusyError, match="update started at"):
            source_change.change_source(DOMAIN, NEW, "release")

        row = store.get_app(DOMAIN)
        assert row is not None and row.source == OLD

    def test_a_refusal_leaves_no_audit_record(
        self, store: NoustStore, remote: FakeRunner, audited: list[dict[str, Any]]
    ) -> None:
        deployed(store)

        with pytest.raises(SourceError):
            source_change.change_source(DOMAIN, NEW, "nope")

        assert audited == []
