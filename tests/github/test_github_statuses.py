# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for GitHub deployment statuses and the preview comment.

The statuses subscriber must decide from the store alone, hand the network to
its own thread, report a deployment's end on the deployment its start
created, and never raise into the deploy. The comment is created once and
edited after.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.core.exceptions import IntegrationError
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.deployers.deploy_events import DeployEvent, DeployEventKind
from noust.integrations.github import statuses
from noust.integrations.github.comments import upsert_pr_comment
from tests.github.fakes import (
    INSTALLATION_ID,
    FakeGitHub,
    token_route,
)


class FakeApp:
    """Records the calls made as an installation; answers deployment ids."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[int, str, str, dict[str, Any] | None]] = []
        self.fail = fail
        self.next_id = 100

    def as_installation(
        self, installation_id: int, method: str, path: str, body: dict[str, Any] | None = None
    ) -> Any:
        if self.fail:
            raise IntegrationError("GitHub could not be reached")
        self.calls.append((installation_id, method, path, body))
        if path.endswith("/deployments"):
            self.next_id += 1
            return {"id": self.next_id}
        return {"id": 1}


def event(kind: DeployEventKind, **extra: Any) -> DeployEvent:
    """
    Build an event of the application ``a.example.com``, deployment 5.

    Args:
        kind: The event's kind.
        **extra: Fields to override.

    Returns:
        The event.
    """
    fields: dict[str, Any] = {
        "domain": "a.example.com",
        "deployment_id": 5,
        "commit": "abc123",
        "branch": "main",
    }
    fields.update(extra)
    return DeployEvent(kind=kind, **fields)


@pytest.fixture
def linked(github_configured: NoustStore) -> NoustStore:
    """
    An application on a covered repository.

    Returns:
        The store.
    """
    github_configured.create_app(
        App(domain="a.example.com", source="github:you/app", branch="main")
    )
    return github_configured


def test_the_target_is_decided_from_the_store(linked: NoustStore) -> None:
    """Repository, installation, environment and address."""
    target = statuses.target_for("a.example.com")
    assert target == statuses.Target(
        repository="you/app",
        installation_id=INSTALLATION_ID,
        environment="production",
        environment_url="https://a.example.com",
    )
    linked.create_app(App(domain="pr-7.example.com", source="github:you/app"))
    linked.set_preview_parent("pr-7.example.com", "a.example.com")
    assert statuses.target_for("pr-7.example.com").environment == "preview"


def test_nothing_is_reported_without_a_github_link(store: NoustStore) -> None:
    """No App, another host, an uncovered owner, or an unknown application."""
    store.create_app(App(domain="a.example.com", source="github:you/app"))
    assert statuses.target_for("a.example.com") is None


def test_other_hosts_and_owners_are_not_reported(github_configured: NoustStore) -> None:
    """Only a github.com repository an installation covers."""
    github_configured.create_app(App(domain="g.example.com", source="https://gitlab.com/you/app"))
    github_configured.create_app(App(domain="s.example.com", source="github:stranger/app"))
    assert statuses.target_for("g.example.com") is None
    assert statuses.target_for("s.example.com") is None
    assert statuses.target_for("missing.example.com") is None


def test_start_then_success_update_one_deployment(linked: NoustStore) -> None:
    """The deployment the start created is the one the end marks."""
    fake = FakeApp()
    reporter = statuses.StatusReporter(load=lambda: fake)
    target = statuses.target_for("a.example.com")
    reporter.report(target, event(DeployEventKind.STARTED))
    reporter.report(target, event(DeployEventKind.SUCCEEDED))

    created = fake.calls[0]
    assert created[1:3] == ("POST", "/repos/you/app/deployments")
    assert created[3]["ref"] == "abc123"
    assert created[3]["environment"] == "production"
    assert created[3]["auto_merge"] is False
    assert created[3]["required_contexts"] == []
    assert [c[2] for c in fake.calls[1:]] == [
        "/repos/you/app/deployments/101/statuses",
        "/repos/you/app/deployments/101/statuses",
    ]
    assert fake.calls[1][3]["state"] == "in_progress"
    assert fake.calls[2][3]["state"] == "success"
    assert fake.calls[2][3]["environment_url"] == "https://a.example.com"


@pytest.mark.parametrize(
    ("kind", "error", "description"),
    [
        (DeployEventKind.FAILED, "Build failed: npm exited 1\nmore", "Build failed: npm exited 1"),
        (DeployEventKind.FAILED, None, "Deploy failed"),
        (DeployEventKind.ROLLED_BACK, "health gate", "rolled back"),
    ],
)
def test_failures_are_reported_as_failure(
    linked: NoustStore, kind: DeployEventKind, error: str | None, description: str
) -> None:
    """A failure, or a rollback, is a failure on GitHub with its reason."""
    fake = FakeApp()
    reporter = statuses.StatusReporter(load=lambda: fake)
    target = statuses.target_for("a.example.com")
    reporter.report(target, event(DeployEventKind.STARTED))
    reporter.report(target, event(kind, error=error))
    final = fake.calls[-1][3]
    assert final["state"] == "failure"
    assert final["description"] == description


def test_an_end_without_a_start_creates_the_deployment(linked: NoustStore) -> None:
    """A restart between start and end still leaves a finished deployment on GitHub."""
    fake = FakeApp()
    reporter = statuses.StatusReporter(load=lambda: fake)
    reporter.report(statuses.target_for("a.example.com"), event(DeployEventKind.SUCCEEDED))
    assert [c[2] for c in fake.calls] == [
        "/repos/you/app/deployments",
        "/repos/you/app/deployments/101/statuses",
    ]


def test_the_subscriber_hands_work_to_a_thread_and_never_raises(
    linked: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GitHub failing is logged on the worker; the deploy's thread returns at once."""
    fake = FakeApp(fail=True)
    reporter = statuses.StatusReporter(load=lambda: fake)
    monkeypatch.setattr(statuses, "reporter", reporter)
    statuses.on_deploy_event(event(DeployEventKind.STARTED))
    statuses.on_deploy_event(event(DeployEventKind.FAILED))
    reporter.drain()
    assert fake.calls == []


def test_the_end_of_a_first_deploy_is_reported_after_its_records_are_gone(
    linked: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A first deploy that fails forgets the application before FAILED is
    published; the target resolved at STARTED must still carry the end, or
    the GitHub deployment stays in progress forever.
    """
    fake = FakeApp()
    reporter = statuses.StatusReporter(load=lambda: fake)
    monkeypatch.setattr(statuses, "reporter", reporter)

    statuses.on_deploy_event(event(DeployEventKind.STARTED))
    linked.delete_app("a.example.com")
    assert statuses.target_for("a.example.com") is None
    statuses.on_deploy_event(event(DeployEventKind.FAILED, error="npm exited 1"))
    assert reporter.drain(timeout=5)

    assert [c[3]["state"] for c in fake.calls if c[2].endswith("/statuses")] == [
        "in_progress",
        "failure",
    ]
    # Once ended, the deployment's target is forgotten.
    assert reporter.cached_targets() == 0


def test_the_reporter_is_drained_when_the_process_exits(
    linked: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A CLI deploy's statuses are sent before the interpreter goes away."""
    from noust.core import background

    monkeypatch.setattr(background, "_queues", [])
    monkeypatch.setattr(background, "_atexit_registered", False)
    registered: list[Any] = []
    monkeypatch.setattr("atexit.register", registered.append)
    fake = FakeApp()
    reporter = statuses.StatusReporter(load=lambda: fake)
    monkeypatch.setattr(statuses, "reporter", reporter)

    statuses.on_deploy_event(event(DeployEventKind.STARTED))

    assert registered == [background.drain_all]
    assert background.drain_all(timeout=5)
    assert len(fake.calls) == 2


def test_the_subscriber_does_nothing_for_unlinked_apps(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No thread, no queue, for an application GitHub does not know."""
    submitted: list[Any] = []
    monkeypatch.setattr(statuses.reporter, "submit", lambda *a: submitted.append(a))
    statuses.on_deploy_event(event(DeployEventKind.STARTED))
    assert submitted == []


def test_it_is_a_default_subscriber() -> None:
    """The recorder loads it by name."""
    from noust.deployers.deploy_events import DEFAULT_SUBSCRIBERS

    assert "noust.integrations.github.statuses" in DEFAULT_SUBSCRIBERS
    assert callable(statuses.on_deploy_event)


# -- Comments ------------------------------------------------------------------


def test_a_comment_is_created_then_edited(
    github_configured: NoustStore, fake_github: FakeGitHub, openssl: FakeRunner
) -> None:
    """First call creates; a call with the id edits that comment."""
    token_route(fake_github)
    fake_github.on("POST", "/repos/you/app/issues/7/comments", {"id": 555}, 201)
    fake_github.on("PATCH", "/repos/you/app/issues/comments/555", {"id": 555})
    assert upsert_pr_comment("you/app", 7, "Preview building") == "555"
    assert upsert_pr_comment("you/app", 7, "Preview ready", "555") == "555"
    bodies = [(r.method, r.body) for r in fake_github.requests if "comments" in r.path]
    assert bodies == [("POST", {"body": "Preview building"}), ("PATCH", {"body": "Preview ready"})]


def test_a_deleted_comment_is_created_again(
    github_configured: NoustStore, fake_github: FakeGitHub, openssl: FakeRunner
) -> None:
    """Someone deleted it on GitHub: a new one says the same."""
    token_route(fake_github)
    fake_github.on("PATCH", "/repos/you/app/issues/comments/555", {"message": "Not Found"}, 404)
    fake_github.on("POST", "/repos/you/app/issues/7/comments", {"id": 556}, 201)
    assert upsert_pr_comment("you/app", 7, "Preview ready", "555") == "556"


def test_no_comment_without_a_covering_installation(github_configured: NoustStore) -> None:
    """Nothing is sent for a repository the App does not reach."""
    assert upsert_pr_comment("stranger/app", 7, "x") is None
    assert upsert_pr_comment("not a repo", 7, "x") is None


def test_no_comment_without_an_app(store: NoustStore) -> None:
    """A server with no App comments nowhere."""
    assert upsert_pr_comment("you/app", 7, "x") is None
