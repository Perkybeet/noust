"""
A push or a pull request an application declines (checklist A-13, A-28).

``[skip ci]`` / ``[skip cd]`` in every commit of a push, or in a pull request's
title or head commit, and watch paths that a push does not touch: the
delivery is acknowledged (never an error, so the forge does not retry it),
nothing is queued, and the application's delivery log says why. The same rules
hold through the per-application webhook and through the GitHub App's endpoint.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core import app_source_store
from noust.core import webhook_deliveries as deliveries_log
from noust.core.forge_events import Forge, PullRequestAction, PullRequestEvent
from noust.core.store import App, NoustStore, PreviewSettings
from noust.managers import previews
from noust.web.api import hooks as hooks_module
from noust.web.api.hooks import mint_webhook_secret
from noust.web.auth import SecurityConfig
from noust.web.server import create_app as build_app

DOMAIN = "shop.example.com"


def commit(message: str, *, modified: tuple[str, ...] = (), added: tuple[str, ...] = ()) -> dict:
    return {
        "id": uuid.uuid4().hex,
        "message": message,
        "added": list(added),
        "modified": list(modified),
        "removed": [],
    }


def push(commits: list[dict]) -> dict[str, Any]:
    return {
        "ref": "refs/heads/main",
        "after": "c0ffee" + "0" * 34,
        "commits": commits,
        "repository": {
            "full_name": "you/app",
            "clone_url": "https://github.com/you/app.git",
            "default_branch": "main",
        },
    }


@pytest.fixture(autouse=True)
def fresh_delivery_cache() -> None:
    hooks_module._deliveries.clear()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def seeded(store: NoustStore, tmp_path: Path) -> App:
    return store.create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            source="https://github.com/you/app.git",
            branch="main",
            port=3000,
            app_path=str(tmp_path / "apps" / "shop"),
            layout="releases",
        )
    )


@pytest.fixture
def secret(seeded: App) -> str:
    return mint_webhook_secret(DOMAIN)


@pytest.fixture
def client(tmp_path: Path, store: NoustStore) -> TestClient:
    app: FastAPI = build_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))
    return TestClient(app, client=("testclient", 50000))


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    class Manager:
        def create_job(self, **kwargs: Any) -> Any:
            captured.append(kwargs)
            return type("Job", (), {"id": f"job-{len(captured)}"})()

        def get_active_jobs(self) -> list[Any]:
            return []

    monkeypatch.setattr("noust.web.api.hooks.get_job_manager", lambda: Manager())
    return captured


def deliver(client: TestClient, secret: str, payload: dict[str, Any], event: str = "push") -> Any:
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        f"/hooks/deploy/{DOMAIN}",
        content=body,
        headers={
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": str(uuid.uuid4()),
            "Content-Type": "application/json",
        },
    )


def last_outcome(store: NoustStore, seeded: App) -> tuple[str, str]:
    assert seeded.id is not None
    row = deliveries_log.list_deliveries(seeded.id, limit=1)[0]
    return row.outcome, row.detail or ""


class TestSkipMarkers:
    def test_a_push_whose_every_commit_is_marked_deploys_nothing(
        self, client, secret, queued, store, seeded
    ) -> None:
        response = deliver(
            client, secret, push([commit("docs [skip ci]"), commit("typo [SKIP CD]")])
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ignored"
        assert response.json()["reason"] == "skip"
        assert queued == []
        outcome, detail = last_outcome(store, seeded)
        assert outcome == deliveries_log.IGNORED_SKIP
        assert "[skip" in detail

    def test_one_unmarked_commit_still_deploys(self, client, secret, queued) -> None:
        response = deliver(client, secret, push([commit("docs [skip ci]"), commit("feat: x")]))
        assert response.status_code == 202
        assert len(queued) == 1

    def test_the_operator_can_turn_the_markers_off(
        self, client, secret, queued, store, seeded
    ) -> None:
        app_source_store.set_options(DOMAIN, skip_markers=False, store=store)
        response = deliver(client, secret, push([commit("docs [skip ci]")]))
        assert response.status_code == 202
        assert len(queued) == 1

    def test_a_push_with_no_commit_list_deploys(self, client, secret, queued) -> None:
        response = deliver(client, secret, push([]))
        assert response.status_code == 202


class TestWatchPaths:
    @pytest.fixture(autouse=True)
    def watched(self, store: NoustStore, seeded: App) -> None:
        app_source_store.set_options(
            DOMAIN, watch_paths=["apps/web/**", "!apps/web/docs/**"], store=store
        )

    def test_a_push_that_touches_a_watched_file_deploys(self, client, secret, queued) -> None:
        response = deliver(client, secret, push([commit("x", modified=("apps/web/src/a.ts",))]))
        assert response.status_code == 202
        assert len(queued) == 1

    def test_a_push_that_touches_nothing_watched_deploys_nothing(
        self, client, secret, queued, store, seeded
    ) -> None:
        response = deliver(
            client,
            secret,
            push([commit("x", modified=("apps/api/src/a.ts", "apps/web/docs/readme.md"))]),
        )
        assert response.status_code == 200
        assert response.json()["reason"] == "paths"
        assert queued == []
        outcome, detail = last_outcome(store, seeded)
        assert outcome == deliveries_log.IGNORED_PATHS
        assert "apps/web/**" in detail

    def test_any_watched_file_among_many_is_enough(self, client, secret, queued) -> None:
        response = deliver(
            client,
            secret,
            push(
                [
                    commit("a", modified=("README.md",)),
                    commit("b", added=("apps/web/new.ts",)),
                ]
            ),
        )
        assert response.status_code == 202

    def test_files_nobody_listed_deploy(self, client, secret, queued) -> None:
        response = deliver(client, secret, push([{"id": "1", "message": "merge"}]))
        assert response.status_code == 202

    def test_a_skip_marker_wins_over_a_watched_path(
        self, client, secret, queued, store, seeded
    ) -> None:
        response = deliver(
            client, secret, push([commit("x [skip ci]", modified=("apps/web/src/a.ts",))])
        )
        assert response.json()["reason"] == "skip"
        assert queued == []


# --------------------------------------------------------------------------- the GitHub App


class TestThroughTheGitHubApp:
    """The App's endpoint asks the same question of every application it would update."""

    @pytest.fixture
    def several(self, store: NoustStore, tmp_path: Path) -> None:
        for name, paths in (
            ("web.example.com", ["apps/web/**"]),
            ("api.example.com", ["apps/api/**"]),
        ):
            store.create_app(
                App(
                    domain=name,
                    app_type="nodejs",
                    source="https://github.com/you/app.git",
                    branch="main",
                    port=3000,
                    app_path=str(tmp_path / name),
                    layout="releases",
                )
            )
            app_source_store.set_options(name, watch_paths=paths, store=store)

    def test_only_the_application_whose_paths_changed_is_updated(
        self, several, store, queued, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.web.api import github_hooks

        monkeypatch.setattr(
            "noust.web.api.github_hooks.queue_push_update", hooks_module.queue_push_update
        )
        payload = push([commit("x", modified=("apps/api/server.ts",))])
        from noust.integrations.github import webhooks

        event = webhooks.parse_push(payload)
        assert event is not None
        jobs, refused, declined = github_hooks._queue_updates(event, "main", "d-1")
        assert [job["domain"] for job in jobs] == ["api.example.com"]
        assert [item["domain"] for item in declined] == ["web.example.com"]
        assert declined[0]["reason"] == "paths"
        assert refused == []

    def test_a_skip_marked_push_updates_none_of_them(self, several, store, queued) -> None:
        from noust.integrations.github import webhooks
        from noust.web.api import github_hooks

        payload = push([commit("x [skip cd]", modified=("apps/api/server.ts",))])
        event = webhooks.parse_push(payload)
        assert event is not None
        jobs, _refused, declined = github_hooks._queue_updates(event, "main", "d-2")
        assert jobs == []
        assert {item["reason"] for item in declined} == {"skip"}


# --------------------------------------------------------------------------- previews


def _pull_request(title: str, head_message: str = "") -> PullRequestEvent:
    return PullRequestEvent(
        forge=Forge.GITLAB,
        action=PullRequestAction.OPENED,
        repository="you/app",
        clone_url="https://gitlab.com/you/app.git",
        number=7,
        title=title,
        branch="feature",
        base_branch="main",
        head_sha="abc123",
        head_message=head_message,
    )


class TestPreviews:
    @pytest.fixture
    def previewing(self, store: NoustStore, seeded: App, monkeypatch: pytest.MonkeyPatch) -> list:
        built: list[str] = []
        settings = PreviewSettings(app_domain=DOMAIN, base_domain="previews.example.com")
        monkeypatch.setattr(
            previews, "previewing_apps", lambda event, app_domain=None: [(seeded, settings)]
        )
        monkeypatch.setattr(previews, "_refusal", lambda event, settings: None)
        monkeypatch.setattr(
            previews, "_queue_build", lambda parent, settings, event: built.append("job") or "job-1"
        )
        return built

    def test_a_title_with_a_marker_builds_no_preview(self, previewing, store, seeded) -> None:
        declined: list[str] = []
        jobs = previews.handle_pull_request(_pull_request("Fix docs [skip ci]"), declined=declined)
        assert jobs == [] and previewing == []
        assert declined and "its title" in declined[0]

    def test_the_head_commit_counts_too(self, previewing, store, seeded) -> None:
        declined: list[str] = []
        previews.handle_pull_request(_pull_request("Fix", "typo [skip cd]"), declined=declined)
        assert declined and "latest commit" in declined[0]

    def test_without_a_sink_the_application_logs_it_itself(self, previewing, store, seeded) -> None:
        previews.handle_pull_request(_pull_request("Fix [skip ci]"))
        assert last_outcome(store, seeded)[0] == deliveries_log.IGNORED_SKIP

    def test_an_ordinary_request_is_built(self, previewing, store, seeded) -> None:
        assert previews.handle_pull_request(_pull_request("Add a feature")) == ["job-1"]

    def test_a_closed_request_is_removed_whatever_it_says(
        self, store, seeded, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = PreviewSettings(app_domain=DOMAIN, base_domain="previews.example.com")
        monkeypatch.setattr(
            previews, "previewing_apps", lambda event, app_domain=None: [(seeded, settings)]
        )
        removed: list[str] = []
        monkeypatch.setattr(
            previews, "_queue_removal", lambda parent, event: removed.append("x") or "job-r"
        )
        event = _pull_request("Fix [skip ci]")
        closed = PullRequestEvent(**{**event.__dict__, "action": PullRequestAction.CLOSED})
        assert previews.handle_pull_request(closed) == ["job-r"]
        assert removed == ["x"]
