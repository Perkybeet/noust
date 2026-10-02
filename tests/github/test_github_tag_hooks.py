# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for deploying by tag through the GitHub App's ``/hooks/github``.

The App's one endpoint serves every application, so a tag announcement is
matched to the applications of that repository that follow tags, and each is
decided on its own (:func:`noust.web.api.hooks.queue_tag_update`, the same
decision an application's own webhook makes). Applications that follow a
branch are untouched, and an application that follows tags is not updated by
a push to a branch.
"""

# The fixtures of the App's delivery tests are imported rather than replicated.
# ruff: noqa: F811

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from noust.core import webhook_deliveries as deliveries
from noust.core.store import App, NoustStore
from noust.deployers import lifecycle
from noust.web.api import hooks as hooks_module
from tests.github.conftest import github_configured, store  # noqa: F401
from tests.github.test_github_hooks import (  # noqa: F401
    app,
    client,
    deliver,
    fresh_deliveries,
    push_payload,
)

SOURCE = "https://github.com/you/app.git"


class Jobs:
    """A job manager that keeps what it was asked and reports the active ones."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.active: list[Any] = []

    def create_job(self, **kwargs: Any) -> Any:
        self.created.append(kwargs)
        job = SimpleNamespace(
            id=f"job-{len(self.created)}", type=kwargs["job_type"], metadata=kwargs["metadata"]
        )
        self.active.append(job)
        return job

    def get_active_jobs(self) -> list[Any]:
        return list(self.active)


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> Jobs:
    manager = Jobs()
    monkeypatch.setattr(hooks_module, "get_job_manager", lambda: manager)
    return manager


@pytest.fixture
def branch_jobs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture the update jobs a push to a branch queues."""
    captured: list[dict[str, Any]] = []

    class Branch:
        def create_job(self, **kwargs: Any) -> Any:
            captured.append(kwargs)
            return SimpleNamespace(id=f"branch-{len(captured)}")

    monkeypatch.setattr("noust.web.api.github_hooks.get_job_manager", lambda: Branch())
    return captured


@pytest.fixture
def deployed(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(tag="v1.2.0")
    monkeypatch.setattr(lifecycle, "deployed_tag", lambda app, **kw: state.tag)
    return state


def tag_push(tag: str = "v1.3.0", repo: str = "you/app") -> dict[str, Any]:
    payload = push_payload(repo=repo)
    payload["ref"] = f"refs/tags/{tag}"
    return payload


def release(tag: str = "v1.3.0", **flags: Any) -> dict[str, Any]:
    return {
        "action": "published",
        "release": {"tag_name": tag, "prerelease": False, "draft": False, **flags},
        "repository": {"full_name": "you/app", "clone_url": SOURCE},
        "installation": {"id": 1},
    }


def make(store: NoustStore, domain: str, *, follows: str | None) -> App:
    created = store.create_app(App(domain=domain, source=SOURCE, branch="main"))
    if follows is not None:
        store.set_app_follow_tags(domain, follows)
    return created


def log_of(store: NoustStore, domain: str) -> list[deliveries.WebhookDelivery]:
    app = store.get_app(domain)
    assert app is not None and app.id is not None
    return deliveries.list_deliveries(app.id)


@pytest.mark.usefixtures("deployed")
class TestTagsThroughTheApp:
    def test_a_published_release_updates_the_applications_that_follow_tags(
        self, client: Any, github_configured: NoustStore, jobs: Jobs
    ) -> None:
        make(github_configured, "tags.example.com", follows="v*")
        make(github_configured, "branch.example.com", follows=None)

        response = deliver(client, "release", release())

        assert response.status_code == 202, response.text
        assert response.json()["jobs"] == [{"domain": "tags.example.com", "job_id": "job-1"}]
        [job] = jobs.created
        assert job["kwargs"] == {"domain": "tags.example.com", "tag": "v1.3.0"}
        assert job["metadata"]["provider"] == "github-app"
        assert job["metadata"]["tag"] == "v1.3.0"
        [row] = log_of(github_configured, "tags.example.com")
        assert (row.outcome, row.provider, row.event) == (
            deliveries.DEPLOY_STARTED,
            "github-app",
            "release",
        )
        assert log_of(github_configured, "branch.example.com") == []

    def test_a_tag_push_updates_them_too(
        self, client: Any, github_configured: NoustStore, jobs: Jobs
    ) -> None:
        make(github_configured, "tags.example.com", follows="v*")

        response = deliver(client, "push", tag_push())

        assert response.status_code == 202, response.text
        assert jobs.created[0]["kwargs"]["tag"] == "v1.3.0"

    def test_a_prerelease_queues_nothing_and_says_why_in_the_log(
        self, client: Any, github_configured: NoustStore, jobs: Jobs
    ) -> None:
        make(github_configured, "tags.example.com", follows="v*")

        response = deliver(client, "release", release(prerelease=True))

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ignored"
        assert jobs.created == []
        [row] = log_of(github_configured, "tags.example.com")
        assert row.outcome == deliveries.IGNORED_TAG
        assert "pre-release" in (row.detail or "")

    def test_each_application_is_decided_on_its_own(
        self,
        client: Any,
        github_configured: NoustStore,
        jobs: Jobs,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """One is already past the tag, the other follows another pattern."""
        make(github_configured, "past.example.com", follows="v*")
        make(github_configured, "other.example.com", follows="release-*")
        make(github_configured, "new.example.com", follows="v*")
        monkeypatch.setattr(
            lifecycle,
            "deployed_tag",
            lambda app, **kw: "v2.0.0" if app.domain == "past.example.com" else "v1.0.0",
        )

        response = deliver(client, "push", tag_push("v1.3.0"))

        assert response.status_code == 202, response.text
        assert [j["kwargs"]["domain"] for j in jobs.created] == ["new.example.com"]
        assert sorted(item["domain"] for item in response.json()["ignored"]) == [
            "other.example.com",
            "past.example.com",
        ]
        assert log_of(github_configured, "past.example.com")[0].outcome == deliveries.IGNORED_TAG
        assert log_of(github_configured, "other.example.com")[0].outcome == deliveries.IGNORED_TAG

    def test_every_application_ignoring_it_answers_200(
        self, client: Any, github_configured: NoustStore, jobs: Jobs, deployed: SimpleNamespace
    ) -> None:
        make(github_configured, "tags.example.com", follows="v*")
        deployed.tag = "v1.3.0"

        response = deliver(client, "push", tag_push("v1.2.0"))

        assert response.status_code == 200, response.text
        assert response.json()["reason"] == "tag"
        assert jobs.created == []

    def test_another_repository_is_not_this_applications(
        self, client: Any, github_configured: NoustStore, jobs: Jobs
    ) -> None:
        make(github_configured, "tags.example.com", follows="v*")

        response = deliver(client, "push", tag_push(repo="you/other"))

        assert response.status_code == 200, response.text
        assert jobs.created == []

    def test_a_push_and_a_release_of_one_version_are_one_deploy(
        self, client: Any, github_configured: NoustStore, jobs: Jobs
    ) -> None:
        make(github_configured, "tags.example.com", follows="v*")

        deliver(client, "push", tag_push())
        second = deliver(client, "release", release())

        assert len(jobs.created) == 1
        assert second.status_code == 200, second.text


class TestWhatDidNotChange:
    def test_a_tag_push_nobody_follows_is_still_not_a_branch(
        self, client: Any, github_configured: NoustStore, jobs: Jobs
    ) -> None:
        make(github_configured, "branch.example.com", follows=None)

        response = deliver(client, "push", tag_push())

        assert response.json()["reason"] == "not_a_branch"
        assert jobs.created == []

    def test_a_release_nobody_follows_is_still_an_event_that_is_ignored(
        self, client: Any, github_configured: NoustStore, jobs: Jobs
    ) -> None:
        make(github_configured, "branch.example.com", follows=None)

        response = deliver(client, "release", release())

        assert response.status_code == 202
        assert response.json() == {"status": "ignored", "reason": "event"}

    def test_a_push_to_a_branch_updates_the_applications_that_follow_it_and_not_a_tag_follower(
        self, client: Any, github_configured: NoustStore, branch_jobs: list[dict[str, Any]]
    ) -> None:
        make(github_configured, "branch.example.com", follows=None)
        make(github_configured, "tags.example.com", follows="v*")

        response = deliver(client, "push", push_payload())

        assert response.status_code == 202, response.text
        assert [j["kwargs"]["domain"] for j in branch_jobs] == ["branch.example.com"]

    def test_a_push_to_a_branch_with_only_a_tag_follower_has_no_application(
        self, client: Any, github_configured: NoustStore, branch_jobs: list[dict[str, Any]]
    ) -> None:
        make(github_configured, "tags.example.com", follows="v*")

        response = deliver(client, "push", push_payload())

        assert response.json() == {"status": "ignored", "reason": "no_application"}
        assert branch_jobs == []
