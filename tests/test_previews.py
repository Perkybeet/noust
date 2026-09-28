# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for pull request previews (:mod:`wasm.managers.previews`).

The deploy, update and delete of a preview are the ones every application
goes through, covered elsewhere; here they are fakes, and what is pinned is
the bookkeeping around them: naming, the limit, fork refusal, the status a
preview goes through, the comment on the pull request (whose failures never
fail a preview), the sweep and its timer.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from wasm.core.applock import AppBusyError
from wasm.core.exceptions import DeploymentError, IntegrationError, ServiceError, ValidationError
from wasm.core.forge_events import Forge, PullRequestAction, PullRequestEvent
from wasm.core.runner import FakeRunner
from wasm.core.store import App, PreviewRecord, PreviewSettings, WASMStore
from wasm.integrations.github import comments
from wasm.managers import previews
from wasm.web.jobs import JobType

PARENT = "shop.example.com"
BASE = "previews.example.com"
REPO = "acme/shop"
NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)

#: The real one, before the autouse fixture replaces it for every test.
ORIGINAL_SYNC = previews.sync_sweep_timer


@pytest.fixture
def store(tmp_path: Path) -> Iterator[WASMStore]:
    """A store of the test's own, with the application previewed in it."""
    WASMStore.reset_instance()
    instance = WASMStore(tmp_path / "state" / "wasm.db")
    instance.create_app(
        App(
            domain=PARENT,
            app_type="nodejs",
            source="https://github.com/Acme/Shop.git",
            branch="main",
            port=3000,
            app_path=str(tmp_path / "apps" / "shop-example-com"),
            layout="releases",
        )
    )
    yield instance
    instance.close()
    WASMStore.reset_instance()


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    """Stop the clock at NOW; a test moves it by replacing the only item."""
    moment = [NOW]
    monkeypatch.setattr(previews, "_now", lambda: moment[0])
    return moment


@pytest.fixture(autouse=True)
def timer(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Keep the sweep timer off the machine; record each sync instead."""
    synced: list[str] = []
    monkeypatch.setattr(previews, "sync_sweep_timer", lambda **_: synced.append("sync") or True)
    return synced


@pytest.fixture
def posted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture pull request comments; each gets the id ``c-<n>``."""
    calls: list[dict[str, Any]] = []

    def upsert(
        repository: str, number: int, body: str, comment_ref: str | None = None
    ) -> str | None:
        calls.append({"repository": repository, "number": number, "body": body, "ref": comment_ref})
        return comment_ref or f"c-{len(calls)}"

    monkeypatch.setattr(comments, "upsert_pr_comment", upsert)
    return calls


class FakeJob:
    """A queued job that never runs."""

    def __init__(self, job_id: str) -> None:
        self.id = job_id
        self.status = type("Status", (), {"value": "pending"})()

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id}


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture the jobs queued instead of running them."""
    queued: list[dict[str, Any]] = []

    class Manager:
        def create_job(self, **kwargs: Any) -> FakeJob:
            queued.append(kwargs)
            return FakeJob(f"job-{len(queued)}")

    monkeypatch.setattr(previews, "_jobs", Manager)
    return queued


class FakeContext:
    """What the job manager hands a job function."""

    job_id = "job-x"

    def __init__(self) -> None:
        self.logs: list[str] = []
        self.metadata: dict[str, Any] = {}

    def log(self, message: str, level: str = "info") -> None:
        self.logs.append(message)

    def update(self, step: str, progress: int) -> None:
        self.logs.append(step)

    def set_metadata(self, key: str, value: Any) -> None:
        self.metadata[key] = value


def enable(store: WASMStore, *, max_previews: int = 3, ttl_hours: int = 168) -> PreviewSettings:
    """Turn previews on for the parent through the manager."""
    return previews.enable_previews(PARENT, BASE, max_previews=max_previews, ttl_hours=ttl_hours)


def event(
    action: PullRequestAction = PullRequestAction.OPENED,
    *,
    number: int = 7,
    repository: str = REPO,
    from_fork: bool = False,
    head_sha: str = "a" * 40,
    forge: Forge = Forge.GITHUB,
    clone_url: str = "https://github.com/acme/shop.git",
) -> PullRequestEvent:
    """A pull request event against the parent's repository."""
    return PullRequestEvent(
        forge=forge,
        action=action,
        repository=repository,
        clone_url=clone_url,
        number=number,
        title="Add a feature",
        branch="feature/x",
        base_branch="main",
        head_sha=head_sha,
        from_fork=from_fork,
        installation_id=42,
    )


def child_domain(number: int = 7) -> str:
    return f"pr-{number}-shop-example-com.{BASE}"


def stored_preview(
    store: WASMStore, number: int = 7, *, expires_at: str | None = None, status: str = "ready"
) -> PreviewRecord:
    """Put a preview record in the store directly."""
    return store.save_preview(
        PreviewRecord(
            parent_domain=PARENT,
            domain=child_domain(number),
            number=number,
            branch="feature/x",
            provider="github",
            expires_at=expires_at or (NOW + timedelta(days=7)).isoformat(timespec="seconds"),
            head_sha="a" * 40,
            repository=REPO,
            status=status,
        )
    )


def deployed_child(store: WASMStore, number: int = 7, *, parent: str | None = PARENT) -> App:
    """Put a preview's application in the store."""
    app = store.create_app(
        App(domain=child_domain(number), app_type="nodejs", port=3100 + number, app_path="/x")
    )
    if parent is not None:
        store.set_preview_parent(app.domain, parent)
    return app


# ---------------------------------------------------------------------------
# Naming, durations, repositories
# ---------------------------------------------------------------------------


class TestNaming:
    def test_a_preview_is_one_label_under_the_base_domain(self) -> None:
        assert previews.preview_domain_for(PARENT, 12, BASE) == f"pr-12-shop-example-com.{BASE}"

    def test_a_long_name_is_cut_to_fit_and_keeps_a_hash(self) -> None:
        long_parent = "a-very-long-application-name.with-a-subdomain.example.com"
        domain = previews.preview_domain_for(long_parent, 123456, BASE)

        assert len(domain) <= 64
        assert domain.endswith(f".{BASE}")
        label = domain.split(".")[0]
        assert label.startswith("pr-123456-a-very-long")
        assert len(label) <= 63
        assert not label.endswith("-")

    def test_two_long_names_sharing_a_beginning_stay_apart(self) -> None:
        first = previews.preview_domain_for("a-very-long-application-name-one.example.com", 1, BASE)
        second = previews.preview_domain_for(
            "a-very-long-application-name-two.example.com", 1, BASE
        )

        assert first != second

    def test_a_number_that_leaves_no_room_is_refused(self) -> None:
        base = "a-rather-long-base-domain-for-previews.example.com"
        with pytest.raises(ValidationError):
            previews.preview_domain_for(PARENT, 10**9, base)


class TestTtl:
    @pytest.mark.parametrize(
        ("text", "hours"),
        [("7d", 168), ("12h", 12), ("2w", 336), ("1h", 1), ("90d", 2160), ("5", 5)],
    )
    def test_durations(self, text: str, hours: int) -> None:
        assert previews.parse_ttl(text) == hours

    @pytest.mark.parametrize("text", ["91d", "0h", "0", "seven", "7y", "-1d", ""])
    def test_refused(self, text: str) -> None:
        with pytest.raises(ValidationError):
            previews.parse_ttl(text)


class TestRepositoryKey:
    @pytest.mark.parametrize(
        "source",
        [
            "https://github.com/Acme/Shop.git",
            "https://github.com/acme/shop",
            "git@github.com:acme/shop.git",
            "ssh://git@github.com/acme/shop.git",
            "github:acme/shop",
            "https://token@github.com/acme/shop.git#main",
        ],
    )
    def test_every_spelling_names_the_same_repository(self, source: str) -> None:
        assert previews.repository_key(source) == ("github.com", "acme/shop")

    def test_nested_gitlab_groups_keep_their_path(self) -> None:
        assert previews.repository_key("https://gitlab.com/g/sub/proj.git") == (
            "gitlab.com",
            "g/sub/proj",
        )

    @pytest.mark.parametrize("source", ["/srv/app", "./app", "", "https://example.com/a.tar.gz"])
    def test_non_git_sources(self, source: str) -> None:
        assert previews.repository_key(source) is None


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestSettings:
    def test_enable_stores_the_settings_and_installs_the_timer(
        self, store: WASMStore, timer: list[str]
    ) -> None:
        stored = previews.enable_previews(PARENT, "*.Previews.Example.com", max_previews=5)

        assert (stored.base_domain, stored.max_previews, stored.ttl_hours) == (BASE, 5, 168)
        assert store.get_preview_settings(PARENT) is not None
        assert timer == ["sync"]

    @pytest.mark.parametrize("max_previews", [0, 21, -1])
    def test_the_limit_is_one_to_twenty(self, store: WASMStore, max_previews: int) -> None:
        with pytest.raises(ValidationError):
            enable(store, max_previews=max_previews)
        assert store.get_preview_settings(PARENT) is None

    @pytest.mark.parametrize("ttl", [0, 2161])
    def test_the_ttl_is_one_hour_to_ninety_days(self, store: WASMStore, ttl: int) -> None:
        with pytest.raises(ValidationError):
            enable(store, ttl_hours=ttl)

    @pytest.mark.parametrize(
        "base",
        [
            "localhost",
            "10.0.0.1",
            "not a domain",
            "a-rather-long-base-domain-for-the-previews-of.example.com",
        ],
    )
    def test_unusable_base_domains(self, store: WASMStore, base: str) -> None:
        with pytest.raises(ValidationError):
            previews.enable_previews(PARENT, base)

    def test_an_unknown_application_is_refused(self, store: WASMStore) -> None:
        with pytest.raises(ValidationError, match="not found"):
            previews.enable_previews("nope.example.com", BASE)

    def test_an_application_not_deployed_from_git_is_refused(
        self, store: WASMStore, tmp_path: Path
    ) -> None:
        store.create_app(App(domain="local.example.com", app_type="nodejs", source="/srv/code"))
        with pytest.raises(ValidationError, match="git"):
            previews.enable_previews("local.example.com", BASE)

    def test_a_preview_cannot_have_previews(self, store: WASMStore) -> None:
        deployed_child(store)
        with pytest.raises(ValidationError, match="preview of"):
            previews.enable_previews(child_domain(), BASE)

    def test_a_type_without_releases_is_refused(self, store: WASMStore) -> None:
        store.create_app(
            App(
                domain="stack.example.com",
                app_type="docker-compose",
                source="https://github.com/acme/stack",
            )
        )
        with pytest.raises(ValidationError, match="cannot have previews"):
            previews.enable_previews("stack.example.com", BASE)

    def test_a_timer_that_cannot_be_installed_puts_the_settings_back(
        self, store: WASMStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(**_: Any) -> bool:
            raise ServiceError("no systemd")

        monkeypatch.setattr(previews, "sync_sweep_timer", broken)
        with pytest.raises(ServiceError):
            enable(store)
        assert store.get_preview_settings(PARENT) is None


# ---------------------------------------------------------------------------
# Pull request events
# ---------------------------------------------------------------------------


class TestOpened:
    def test_an_opened_pull_request_queues_a_deploy_as_the_webhook(
        self, store: WASMStore, jobs: list[dict[str, Any]], posted: list[dict[str, Any]]
    ) -> None:
        enable(store)

        assert previews.handle_pull_request(event()) == ["job-1"]

        record = store.get_preview(PARENT, 7)
        assert record is not None
        assert record.domain == child_domain()
        assert (record.status, record.branch, record.head_sha) == ("pending", "feature/x", "a" * 40)
        assert record.expires_at == (NOW + timedelta(hours=168)).isoformat(timespec="seconds")
        (job,) = jobs
        assert job["job_type"] == JobType.DEPLOY
        assert job["func"] is previews.preview_deploy_job
        assert job["kwargs"] == {"parent_domain": PARENT, "number": 7}
        assert job["actor"] == "webhook"
        assert job["metadata"]["domain"] == child_domain()

    def test_a_push_to_an_existing_preview_queues_an_update_and_extends_it(
        self, store: WASMStore, jobs: list[dict[str, Any]], clock: list[datetime]
    ) -> None:
        enable(store)
        previews.handle_pull_request(event())
        deployed_child(store)
        clock[0] = NOW + timedelta(days=3)

        previews.handle_pull_request(event(PullRequestAction.UPDATED, head_sha="b" * 40))

        record = store.get_preview(PARENT, 7)
        assert record is not None
        assert record.head_sha == "b" * 40
        assert record.expires_at == (clock[0] + timedelta(hours=168)).isoformat(timespec="seconds")
        assert [job["job_type"] for job in jobs] == [JobType.DEPLOY, JobType.UPDATE]

    def test_nothing_previews_a_repository_without_previews_on(
        self, store: WASMStore, jobs: list[dict[str, Any]]
    ) -> None:
        assert previews.handle_pull_request(event()) == []
        assert jobs == []

    def test_another_repository_is_not_previewed(
        self, store: WASMStore, jobs: list[dict[str, Any]]
    ) -> None:
        enable(store)
        assert previews.handle_pull_request(event(repository="acme/other")) == []
        assert jobs == []

    def test_the_github_app_hook_compares_the_host_too(
        self, store: WASMStore, jobs: list[dict[str, Any]]
    ) -> None:
        enable(store)
        elsewhere = event(clone_url="https://gitea.example.com/acme/shop.git")

        assert previews.handle_pull_request(elsewhere) == []
        # An application's own hook was authenticated with its own secret.
        assert previews.handle_pull_request(elsewhere, app_domain=PARENT) == ["job-1"]

    def test_the_application_restriction_is_honoured(
        self, store: WASMStore, jobs: list[dict[str, Any]]
    ) -> None:
        enable(store)
        store.create_app(
            App(domain="twin.example.com", app_type="nodejs", source="github:acme/shop")
        )
        previews.enable_previews("twin.example.com", BASE)

        assert len(previews.handle_pull_request(event())) == 2
        assert previews.handle_pull_request(event(number=8), app_domain="twin.example.com") == [
            "job-3"
        ]

    def test_the_limit_refuses_a_new_preview_and_says_so_once(
        self,
        store: WASMStore,
        jobs: list[dict[str, Any]],
        posted: list[dict[str, Any]],
    ) -> None:
        enable(store, max_previews=1)
        stored_preview(store, 1)

        assert previews.handle_pull_request(event()) == []
        assert previews.handle_pull_request(event(PullRequestAction.UPDATED)) == []

        assert jobs == []
        assert store.get_preview(PARENT, 7) is None
        assert len(posted) == 1
        assert "limit of 1" in posted[0]["body"]

    def test_the_limit_does_not_stop_an_existing_preview_being_updated(
        self, store: WASMStore, jobs: list[dict[str, Any]]
    ) -> None:
        enable(store, max_previews=1)
        stored_preview(store, 7)

        assert previews.handle_pull_request(event(PullRequestAction.UPDATED)) == ["job-1"]

    def test_a_fork_is_refused_with_one_comment(
        self,
        store: WASMStore,
        jobs: list[dict[str, Any]],
        posted: list[dict[str, Any]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        enable(store)

        assert previews.handle_pull_request(event(from_fork=True)) == []
        assert previews.handle_pull_request(event(PullRequestAction.UPDATED, from_fork=True)) == []

        assert jobs == []
        assert store.list_previews(PARENT) == []
        assert len(posted) == 1
        assert "fork" in posted[0]["body"]
        assert "production secrets" in posted[0]["body"]
        assert "fork" in caplog.text

    def test_no_comment_is_attempted_outside_github(
        self, store: WASMStore, jobs: list[dict[str, Any]], posted: list[dict[str, Any]]
    ) -> None:
        enable(store)
        previews.handle_pull_request(event(from_fork=True, forge=Forge.GITLAB), app_domain=PARENT)

        assert posted == []

    def test_a_failing_comment_does_not_stop_a_refusal(
        self, store: WASMStore, jobs: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(*_: Any, **__: Any) -> str:
            raise IntegrationError("GitHub is down")

        monkeypatch.setattr(comments, "upsert_pr_comment", broken)
        enable(store)

        assert previews.handle_pull_request(event(from_fork=True)) == []

    def test_a_domain_taken_by_a_real_application_is_left_alone(
        self, store: WASMStore, jobs: list[dict[str, Any]]
    ) -> None:
        enable(store)
        deployed_child(store, parent=None)

        assert previews.handle_pull_request(event()) == []
        assert jobs == []


class TestClosed:
    def test_closing_queues_the_removal(self, store: WASMStore, jobs: list[dict[str, Any]]) -> None:
        enable(store)
        stored_preview(store)

        assert previews.handle_pull_request(event(PullRequestAction.CLOSED)) == ["job-1"]

        (job,) = jobs
        assert job["job_type"] == JobType.DELETE
        assert job["func"] is previews.preview_remove_job
        assert job["kwargs"] == {"domain": child_domain(), "reason": "closed"}
        record = store.get_preview(PARENT, 7)
        assert record is not None and record.status == "removing"

    def test_closing_a_pull_request_without_a_preview_does_nothing(
        self, store: WASMStore, jobs: list[dict[str, Any]]
    ) -> None:
        enable(store)
        assert previews.handle_pull_request(event(PullRequestAction.CLOSED)) == []
        assert jobs == []


# ---------------------------------------------------------------------------
# The build job
# ---------------------------------------------------------------------------


@pytest.fixture
def builds(monkeypatch: pytest.MonkeyPatch, store: WASMStore) -> list[tuple[str, Any]]:
    """Replace the deploy and the update with fakes that record what they saw."""
    seen: list[tuple[str, Any]] = []

    def deploy(parent: App, record: PreviewRecord, context: Any) -> dict[str, Any]:
        current = store.get_preview(record.parent_domain, record.number)
        seen.append(("deploy", current.status if current else None))
        store.create_app(App(domain=record.domain, app_type="nodejs", port=3107, app_path="/x"))
        return {"status": "deployed"}

    def update(domain: str, commit: str | None, context: Any) -> dict[str, Any]:
        seen.append(("update", commit))
        return {"status": "updated"}

    monkeypatch.setattr(previews, "_deploy", deploy)
    monkeypatch.setattr(previews, "_update", update)
    return seen


class TestBuildJob:
    def test_a_first_build_creates_the_child_and_marks_it(
        self,
        store: WASMStore,
        builds: list[tuple[str, Any]],
        posted: list[dict[str, Any]],
    ) -> None:
        enable(store)
        store.set_github_installation(PARENT, 42)
        stored_preview(store, status="pending")

        result = previews.preview_deploy_job(PARENT, 7, job_context=FakeContext())

        assert builds == [("deploy", "deploying")]
        assert result["preview"] == child_domain()
        child = store.get_app(child_domain())
        assert child is not None
        assert (child.preview_parent, child.github_installation_id) == (PARENT, 42)
        record = store.get_preview(PARENT, 7)
        assert record is not None
        assert (record.status, record.comment_ref) == ("ready", "c-1")
        # Refreshed, not added: every later comment names the first.
        assert [call["ref"] for call in posted] == [None, "c-1"]
        assert "Ready" in posted[-1]["body"]
        assert f"https://{child_domain()}" in posted[-1]["body"]
        assert "production secrets" in posted[-1]["body"]

    def test_a_later_build_updates_at_the_pushed_commit(
        self, store: WASMStore, builds: list[tuple[str, Any]], posted: list[dict[str, Any]]
    ) -> None:
        enable(store)
        stored_preview(store, status="pending")
        deployed_child(store)

        previews.preview_deploy_job(PARENT, 7, job_context=FakeContext())

        assert builds == [("update", "a" * 40)]
        record = store.get_preview(PARENT, 7)
        assert record is not None and record.status == "ready"

    def test_a_failed_build_is_recorded_and_the_comment_keeps_the_error_out(
        self,
        store: WASMStore,
        monkeypatch: pytest.MonkeyPatch,
        posted: list[dict[str, Any]],
    ) -> None:
        enable(store)
        stored_preview(store, status="pending")

        def deploy(parent: App, record: PreviewRecord, context: Any) -> dict[str, Any]:
            store.create_app(App(domain=record.domain, app_type="nodejs", app_path="/x"))
            raise DeploymentError("Build failed", details="SECRET_KEY=hunter2 printed by npm")

        monkeypatch.setattr(previews, "_deploy", deploy)

        with pytest.raises(DeploymentError):
            previews.preview_deploy_job(PARENT, 7, job_context=FakeContext())

        record = store.get_preview(PARENT, 7)
        assert record is not None
        assert (record.status, record.error) == ("failed", "Build failed")
        # The half-created row is marked, so a removal can take it away.
        child = store.get_app(child_domain())
        assert child is not None and child.preview_parent == PARENT
        assert "Failed" in posted[-1]["body"]
        assert "hunter2" not in posted[-1]["body"]

    def test_a_failing_comment_never_fails_the_build(
        self, store: WASMStore, builds: list[tuple[str, Any]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(*_: Any, **__: Any) -> str:
            raise IntegrationError("GitHub is down")

        monkeypatch.setattr(comments, "upsert_pr_comment", broken)
        enable(store)
        stored_preview(store, status="pending")

        previews.preview_deploy_job(PARENT, 7, job_context=FakeContext())

        record = store.get_preview(PARENT, 7)
        assert record is not None
        assert (record.status, record.comment_ref) == ("ready", None)

    def test_a_push_during_the_build_leaves_the_next_build_to_report(
        self, store: WASMStore, monkeypatch: pytest.MonkeyPatch, posted: list[dict[str, Any]]
    ) -> None:
        enable(store)
        stored_preview(store, status="pending")

        def update(domain: str, commit: str | None, context: Any) -> dict[str, Any]:
            latest = store.get_preview(PARENT, 7)
            assert latest is not None
            store.save_preview(
                PreviewRecord(**{**latest.__dict__, "head_sha": "c" * 40, "status": "pending"})
            )
            return {"status": "updated"}

        monkeypatch.setattr(previews, "_update", update)
        deployed_child(store)

        previews.preview_deploy_job(PARENT, 7, job_context=FakeContext())

        record = store.get_preview(PARENT, 7)
        assert record is not None and record.status == "pending"

    def test_a_preview_removed_before_its_build_is_skipped(
        self, store: WASMStore, builds: list[tuple[str, Any]]
    ) -> None:
        enable(store)
        result = previews.preview_deploy_job(PARENT, 7, job_context=FakeContext())

        assert result["status"] == "skipped"
        assert builds == []

    def test_a_real_application_at_the_domain_is_never_updated(
        self, store: WASMStore, builds: list[tuple[str, Any]]
    ) -> None:
        enable(store)
        stored_preview(store, status="pending")
        deployed_child(store, parent=None)

        with pytest.raises(DeploymentError):
            previews.preview_deploy_job(PARENT, 7, job_context=FakeContext())
        assert builds == []


class TestDeploy:
    def test_the_child_is_created_like_any_application(
        self, store: WASMStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from wasm.web import jobs as jobs_module

        store.create_app(App(domain="other.example.com", port=4000, app_path="/o"))
        monkeypatch.setattr(
            previews,
            "find_available_port",
            lambda start, end: next(p for p in range(start, end) if p >= 4000),
        )
        monkeypatch.setattr(
            previews,
            "_inherited_env",
            lambda parent: {"API_KEY": "k", "DATABASE_URL": "postgres://db"},
        )
        called: dict[str, Any] = {}

        def deploy_app_job(**kwargs: Any) -> dict[str, Any]:
            called.update(kwargs)
            return {"status": "deployed"}

        monkeypatch.setattr(jobs_module, "deploy_app_job", deploy_app_job)
        parent = store.get_app(PARENT)
        assert parent is not None
        record = stored_preview(store)

        previews._deploy(parent, record, FakeContext())

        assert called["domain"] == child_domain()
        assert called["source"] == parent.source
        assert called["branch"] == "feature/x"
        assert called["layout"] == "releases"
        assert (called["memory_max_mb"], called["cpu_quota_percent"]) == (256, 50)
        assert called["ssl"] is True
        assert called["env_vars"] == {"API_KEY": "k", "DATABASE_URL": "postgres://db"}
        # 4000 is the other application's, even though nothing listens on it.
        assert called["port"] == 4001
        assert previews._reserved_ports == set()

    def test_the_parent_environment_is_copied_without_what_the_unit_sets(
        self, store: WASMStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from wasm.deployers.helpers import app_env

        monkeypatch.setattr(
            app_env, "read_app_env", lambda app: {"PORT": "3000", "NODE_ENV": "x", "A": "1"}
        )
        parent = store.get_app(PARENT)
        assert parent is not None

        assert previews._inherited_env(parent) == {"A": "1"}


# ---------------------------------------------------------------------------
# Removal and the sweep
# ---------------------------------------------------------------------------


@pytest.fixture
def deletions(monkeypatch: pytest.MonkeyPatch, store: WASMStore) -> list[str]:
    """Replace the application deletion with a fake that drops the row."""
    deleted: list[str] = []

    def delete(domain: str, on_phase: Any, logger: Any) -> tuple[str, ...]:
        deleted.append(domain)
        store.delete_app(domain)
        return ()

    monkeypatch.setattr(previews, "_delete", delete)
    return deleted


class TestRemoval:
    def test_a_preview_goes_with_its_application_and_says_so(
        self, store: WASMStore, deletions: list[str], posted: list[dict[str, Any]]
    ) -> None:
        stored_preview(store)
        deployed_child(store)

        assert previews.remove_preview(child_domain(), reason="closed") == ()

        assert deletions == [child_domain()]
        assert store.get_preview(PARENT, 7) is None
        assert "closed" in posted[-1]["body"]

    def test_an_application_that_is_not_the_preview_is_never_deleted(
        self, store: WASMStore, deletions: list[str]
    ) -> None:
        stored_preview(store)
        deployed_child(store, parent=None)

        warnings = previews.remove_preview(child_domain())

        assert deletions == []
        assert warnings and "left alone" in warnings[0]
        assert store.get_app(child_domain()) is not None
        assert store.get_preview(PARENT, 7) is None

    def test_something_that_is_not_a_preview_is_refused(
        self, store: WASMStore, deletions: list[str]
    ) -> None:
        with pytest.raises(ValidationError):
            previews.remove_preview(PARENT)
        assert deletions == []

    def test_the_removal_job(self, store: WASMStore, deletions: list[str]) -> None:
        stored_preview(store)
        deployed_child(store)

        result = previews.preview_remove_job(child_domain(), "closed", job_context=FakeContext())

        assert result["status"] == "removed"
        assert deletions == [child_domain()]

    def test_removing_every_preview_of_an_application(
        self, store: WASMStore, deletions: list[str], timer: list[str]
    ) -> None:
        enable(store)
        stored_preview(store, 7)
        deployed_child(store, 7)
        stored_preview(store, 8)
        # A preview whose record was lost is still found.
        deployed_child(store, 9)

        removed = previews.remove_previews_of(PARENT)

        assert sorted(removed) == sorted([child_domain(7), child_domain(8), child_domain(9)])
        assert sorted(deletions) == sorted([child_domain(7), child_domain(9)])
        assert store.get_preview_settings(PARENT) is None
        assert store.list_previews(PARENT) == []
        assert timer[-1] == "sync"

    def test_a_failure_is_reported_after_the_rest_are_removed(
        self, store: WASMStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        enable(store)
        stored_preview(store, 7)
        deployed_child(store, 7)
        stored_preview(store, 8)

        def delete(domain: str, on_phase: Any, logger: Any) -> tuple[str, ...]:
            raise AppBusyError(domain, "deletion", None)

        monkeypatch.setattr(previews, "_delete", delete)

        with pytest.raises(DeploymentError, match="1 preview"):
            previews.remove_previews_of(PARENT)
        assert store.get_preview(PARENT, 8) is None
        assert store.get_preview_settings(PARENT) is None


class TestSweep:
    def test_expired_previews_go_and_the_others_stay(
        self, store: WASMStore, deletions: list[str], clock: list[datetime]
    ) -> None:
        enable(store)
        stored_preview(
            store, 7, expires_at=(NOW - timedelta(hours=1)).isoformat(timespec="seconds")
        )
        deployed_child(store, 7)
        stored_preview(store, 8)

        assert previews.sweep() == [child_domain(7)]
        assert store.get_preview(PARENT, 8) is not None

    def test_previews_of_a_gone_application_go_too(
        self, store: WASMStore, deletions: list[str]
    ) -> None:
        stored_preview(store, 7)
        store.delete_app(PARENT)

        assert previews.sweep() == [child_domain(7)]

    def test_a_preview_application_without_a_record_goes_too(
        self, store: WASMStore, deletions: list[str]
    ) -> None:
        deployed_child(store, 7)

        assert previews.sweep() == [child_domain(7)]
        assert deletions == [child_domain(7)]

    def test_a_real_application_is_never_swept(
        self, store: WASMStore, deletions: list[str]
    ) -> None:
        deployed_child(store, 7, parent=None)

        assert previews.sweep() == []
        assert deletions == []

    def test_a_busy_preview_is_left_for_the_next_run(
        self, store: WASMStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stored_preview(
            store, 7, expires_at=(NOW - timedelta(hours=1)).isoformat(timespec="seconds")
        )
        deployed_child(store, 7)
        stored_preview(
            store, 8, expires_at=(NOW - timedelta(hours=1)).isoformat(timespec="seconds")
        )

        def delete(domain: str, on_phase: Any, logger: Any) -> tuple[str, ...]:
            raise AppBusyError(domain, "deletion", None)

        monkeypatch.setattr(previews, "_delete", delete)

        assert previews.sweep() == [child_domain(8)]
        assert store.get_preview(PARENT, 7) is not None


# ---------------------------------------------------------------------------
# The sweep timer
# ---------------------------------------------------------------------------


class TestSweepTimer:
    @pytest.fixture
    def units(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        directory = tmp_path / "systemd"
        directory.mkdir()
        monkeypatch.setattr(previews, "SYSTEMD_DIR", directory)
        return directory

    def test_install_writes_both_units_and_starts_the_timer(
        self, units: Path, runner: FakeRunner
    ) -> None:
        assert previews.install_sweep_timer() is True

        service = (units / "wasm-previews.service").read_text()
        timer_unit = (units / "wasm-previews.timer").read_text()
        assert "Generated by WASM" in service and "Generated by WASM" in timer_unit
        assert "ExecStart=/usr/bin/wasm preview sweep" in service
        assert "Type=oneshot" in service
        assert "OnCalendar=hourly" in timer_unit
        assert ("systemctl", "daemon-reload") in runner.calls
        assert ("systemctl", "enable", "--now", "wasm-previews.timer") in runner.calls

    def test_installing_again_rewrites_nothing(self, units: Path, runner: FakeRunner) -> None:
        previews.install_sweep_timer()
        runner.calls.clear()

        assert previews.install_sweep_timer() is False
        assert ("systemctl", "daemon-reload") not in runner.calls

    def test_a_unit_wasm_did_not_write_is_not_overwritten(
        self, units: Path, runner: FakeRunner
    ) -> None:
        (units / "wasm-previews.timer").write_text("[Timer]\nOnCalendar=daily\n")

        with pytest.raises(ServiceError):
            previews.install_sweep_timer()
        assert (units / "wasm-previews.timer").read_text() == "[Timer]\nOnCalendar=daily\n"

    def test_remove_stops_and_deletes_them(self, units: Path, runner: FakeRunner) -> None:
        previews.install_sweep_timer()

        assert previews.remove_sweep_timer() is True
        assert not (units / "wasm-previews.timer").exists()
        assert not (units / "wasm-previews.service").exists()
        assert ("systemctl", "disable", "--now", "wasm-previews.timer") in runner.calls
        assert previews.remove_sweep_timer() is False

    def test_a_failed_start_is_an_error(self, units: Path, runner: FakeRunner) -> None:
        runner.script(["systemctl", "enable"], exit_code=1, stderr="Unit not found")

        with pytest.raises(ServiceError, match="Failed to enable"):
            previews.install_sweep_timer()

    def test_sync_follows_what_exists(self, units: Path, runner: FakeRunner) -> None:
        assert ORIGINAL_SYNC(needed=lambda: True) is True
        assert (units / "wasm-previews.timer").exists()
        assert ORIGINAL_SYNC(needed=lambda: False) is False
        assert not (units / "wasm-previews.timer").exists()

    def test_in_use_while_settings_or_previews_exist(self, store: WASMStore) -> None:
        assert previews.previews_in_use() is False
        store.save_preview_settings(PreviewSettings(app_domain=PARENT, base_domain=BASE))
        assert previews.previews_in_use() is True
        store.delete_preview_settings(PARENT)
        stored_preview(store)
        assert previews.previews_in_use() is True


def test_a_preview_without_a_certificate_is_linked_over_http(store: WASMStore) -> None:
    """The deploy goes on without TLS when the certificate fails; the link must open."""
    domain = "pr-3-shop-example-com.previews.example.com"
    assert previews.preview_url(domain) == f"https://{domain}"

    store.create_app(App(domain=domain, app_path="/x", ssl_enabled=False))
    assert previews.preview_url(domain) == f"http://{domain}"
