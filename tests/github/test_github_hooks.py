# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``POST /hooks/github``, the GitHub App's one delivery endpoint.

Written as attacks first - no App, no signature, a wrong one, a replay, a
flood of wrong ones - and then as what each event must do: a push updates
exactly the applications that deploy that branch of that repository, a pull
request reaches the previews as a :class:`PullRequestEvent`, and installation
events keep the stored installations in step.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.forge_events import Forge, PullRequestAction, PullRequestEvent
from noust.core.store import App, NoustStore
from noust.integrations.github import app as github_app
from noust.web.api import github_hooks
from noust.web.api import hooks as hooks_module
from tests.github.fakes import INSTALLATION_ID
from tests.support.github_hooks import (  # noqa: F401
    SECRET,
    app,
    client,
    deliver,
    fresh_deliveries,
    push_payload,
)

# A fixture imported from the support module is found by pytest in this module's namespace, and a
# test that asks for it by name looks like a redefinition of an import that is never used.
# ruff: noqa: F811


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """
    Capture the update jobs instead of running them.

    Returns:
        Each job description.
    """
    captured: list[dict[str, Any]] = []

    class Jobs:
        def create_job(self, **kwargs: Any) -> Any:
            captured.append(kwargs)
            return type("Job", (), {"id": f"job-{len(captured)}"})()

    monkeypatch.setattr(hooks_module, "get_job_manager", lambda: Jobs())
    return captured


@pytest.fixture
def previews(monkeypatch: pytest.MonkeyPatch) -> list[PullRequestEvent]:
    """
    Capture what reaches the previews.

    Returns:
        Each event handed over.
    """
    received: list[PullRequestEvent] = []

    def handle(event: PullRequestEvent, *, app_domain: str | None = None) -> list[str]:
        received.append(event)
        return ["job-p"]

    monkeypatch.setattr("noust.managers.previews.handle_pull_request", handle)
    return received


def pr_payload(action: str, *, head_repo: str = "you/app", number: int = 7) -> dict[str, Any]:
    """
    Build a pull_request payload.

    Args:
        action: GitHub's action.
        head_repo: Where the branch lives.
        number: The pull request's number.

    Returns:
        The payload.
    """
    return {
        "action": action,
        "number": number,
        "pull_request": {
            "number": number,
            "title": "Add things",
            "head": {
                "ref": "feature",
                "sha": "beef",
                "repo": {
                    "full_name": head_repo,
                    "clone_url": f"https://github.com/{head_repo}.git",
                },
            },
            "base": {
                "ref": "main",
                "repo": {"full_name": "you/app", "clone_url": "https://github.com/you/app.git"},
            },
        },
        "repository": {"full_name": "you/app"},
        "installation": {"id": INSTALLATION_ID},
    }


# -- Refusals ------------------------------------------------------------------


def test_no_app_answers_404(client: TestClient) -> None:
    """A server without a GitHub App has no such endpoint to speak of."""
    assert deliver(client, "ping", {}).status_code == 404


def test_missing_and_wrong_signatures_are_401(
    client: TestClient, github_configured: NoustStore
) -> None:
    """Neither an unsigned delivery nor one signed with another secret passes."""
    assert deliver(client, "ping", {}, secret=None).status_code == 401
    assert deliver(client, "ping", {}, secret="guess").status_code == 401


def test_repeated_wrong_signatures_lock_out_only_wrong_signatures(
    client: TestClient, github_configured: NoustStore, tmp_path: Path
) -> None:
    """
    A stranger's bad signatures must not stop GitHub's deliveries.

    The signature is checked first: a right one is always accepted, and only
    the wrong ones are counted and, past the limit, answered 429.
    """
    for _ in range(3):
        deliver(client, "ping", {}, secret="guess")
    still_wrong = deliver(client, "ping", {}, secret="guess")
    genuine = deliver(client, "ping", {})
    assert still_wrong.status_code == 429
    assert "Retry-After" in still_wrong.headers
    assert genuine.status_code == 200
    audit = (tmp_path / "state" / "web-audit.log").read_text()
    assert "sha256=" not in audit and SECRET not in audit


def test_a_replayed_body_under_a_new_delivery_id_is_ignored(
    client: TestClient, github_configured: NoustStore, queued: list[dict[str, Any]]
) -> None:
    """The delivery id is not signed: the signed body and signature are what repeat."""
    github_configured.create_app(
        App(domain="a.example.com", source="github:you/app", branch="main")
    )
    first = deliver(client, "push", push_payload(), delivery="d-1")
    again = deliver(client, "push", push_payload(), delivery="d-2")
    assert first.status_code == 202
    assert again.json() == {"status": "ignored", "reason": "duplicate"}
    assert len(queued) == 1


def test_a_replayed_delivery_is_ignored(
    client: TestClient, github_configured: NoustStore, queued: list[dict[str, Any]]
) -> None:
    """One delivery id, one update."""
    github_configured.create_app(
        App(domain="a.example.com", source="github:you/app", branch="main")
    )
    first = deliver(client, "push", push_payload(), delivery="d-1")
    again = deliver(client, "push", push_payload(), delivery="d-1")
    assert first.status_code == 202
    assert again.json() == {"status": "ignored", "reason": "duplicate"}
    assert len(queued) == 1


# -- Events --------------------------------------------------------------------


def test_ping_answers_and_records_the_webhook_as_working(
    client: TestClient, github_configured: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GitHub pings when the webhook is switched on; that is the proof it works."""
    monkeypatch.setattr(
        github_hooks, "github_hooks_url", lambda: "https://h.example.com/hooks/github"
    )
    response = deliver(client, "ping", {"zen": "Keep it logically awesome."})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert github_app.read_meta() == {
        "webhook_active": True,
        "webhook_url": "https://h.example.com/hooks/github",
    }


def test_a_push_updates_exactly_the_applications_that_follow_it(
    client: TestClient, github_configured: NoustStore, queued: list[dict[str, Any]]
) -> None:
    """Same repository and branch (own, or the default when unset); nothing else."""
    store = github_configured
    store.create_app(App(domain="main.example.com", source="github:you/app", branch="main"))
    store.create_app(App(domain="default.example.com", source="https://github.com/you/app.git"))
    store.create_app(App(domain="dev.example.com", source="github:you/app", branch="dev"))
    store.create_app(App(domain="other.example.com", source="github:you/other", branch="main"))
    store.create_app(
        App(domain="ssh.example.com", source="git@github.com:You/App.git", branch="main")
    )
    preview = store.create_app(
        App(domain="pr-1.example.com", source="github:you/app", branch="main")
    )
    store.set_preview_parent(preview.domain, "main.example.com")

    response = deliver(client, "push", push_payload())
    assert response.status_code == 202
    domains = sorted(job["domain"] for job in response.json()["jobs"])
    assert domains == ["default.example.com", "main.example.com", "ssh.example.com"]
    job = queued[0]
    assert job["kwargs"] == {"domain": job["metadata"]["domain"]}
    assert job["metadata"]["trigger"] == "webhook"
    assert job["metadata"]["commit"] == "c0ffee"
    assert job["actor"] == "webhook"
    assert job["func"] is hooks_module.webhook_update_job


def test_a_push_through_the_app_is_kept_in_each_applications_delivery_log(
    client: TestClient, github_configured: NoustStore, queued: list[dict[str, Any]]
) -> None:
    """The webhook setup page of an application shows the App deploying it."""
    from noust.core import webhook_deliveries

    store = github_configured
    main = store.create_app(App(domain="main.example.com", source="github:you/app", branch="main"))
    dev = store.create_app(App(domain="dev.example.com", source="github:you/app", branch="dev"))
    assert main.id is not None and dev.id is not None

    deliver(client, "push", push_payload(), delivery="delivery-7")

    (row,) = webhook_deliveries.list_deliveries(main.id)
    assert (row.outcome, row.provider, row.event, row.branch) == (
        "deploy_started",
        "github-app",
        "push",
        "main",
    )
    assert (row.job_id, row.delivery_id) == ("job-1", "delivery-7")
    assert webhook_deliveries.list_deliveries(dev.id) == []


def test_a_push_nobody_follows_is_ignored(
    client: TestClient, github_configured: NoustStore, queued: list[dict[str, Any]]
) -> None:
    """A branch no application deploys queues nothing."""
    github_configured.create_app(
        App(domain="a.example.com", source="github:you/app", branch="main")
    )
    response = deliver(client, "push", push_payload(branch="feature"))
    assert response.json() == {"status": "ignored", "reason": "no_application"}
    assert queued == []


def test_a_tag_push_is_ignored(
    client: TestClient, github_configured: NoustStore, queued: list[dict[str, Any]]
) -> None:
    """Tags deploy nothing."""
    payload = push_payload()
    payload["ref"] = "refs/tags/v1"
    assert deliver(client, "push", payload).json()["reason"] == "not_a_branch"
    assert queued == []


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("opened", PullRequestAction.OPENED),
        ("reopened", PullRequestAction.OPENED),
        ("ready_for_review", PullRequestAction.OPENED),
        ("synchronize", PullRequestAction.UPDATED),
        ("closed", PullRequestAction.CLOSED),
    ],
)
def test_pull_requests_reach_the_previews(
    client: TestClient,
    github_configured: NoustStore,
    previews: list[PullRequestEvent],
    action: str,
    expected: PullRequestAction,
) -> None:
    """Every action that changes code maps to one of the three the previews know."""
    response = deliver(client, "pull_request", pr_payload(action))
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "jobs": [{"job_id": "job-p"}]}
    assert previews == [
        PullRequestEvent(
            forge=Forge.GITHUB,
            action=expected,
            repository="you/app",
            clone_url="https://github.com/you/app.git",
            number=7,
            title="Add things",
            branch="feature",
            base_branch="main",
            head_sha="beef",
            from_fork=False,
            installation_id=INSTALLATION_ID,
        )
    ]


def test_a_fork_is_marked_as_one(
    client: TestClient, github_configured: NoustStore, previews: list[PullRequestEvent]
) -> None:
    """A branch in another repository is a fork; the previews refuse those."""
    deliver(client, "pull_request", pr_payload("opened", head_repo="mallory/app"))
    assert previews[0].from_fork is True
    assert previews[0].clone_url == "https://github.com/mallory/app.git"


def test_pull_request_actions_that_change_no_code_are_ignored(
    client: TestClient, github_configured: NoustStore, previews: list[PullRequestEvent]
) -> None:
    """A label or an edit rebuilds nothing."""
    response = deliver(client, "pull_request", pr_payload("labeled"))
    assert response.json() == {"status": "ignored", "reason": "action"}
    assert previews == []


def test_installation_events_keep_the_installations(
    client: TestClient, github_configured: NoustStore
) -> None:
    """Created and repository changes are saved; deleted is forgotten."""
    created = {
        "action": "created",
        "installation": {
            "id": 9,
            "account": {"login": "acme", "type": "Organization"},
            "repository_selection": "all",
        },
    }
    assert deliver(client, "installation", created).json() == {
        "status": "ok",
        "installation": "saved",
    }
    changed = {**created, "action": "added"}
    changed["installation"] = {**created["installation"], "repository_selection": "selected"}
    deliver(client, "installation_repositories", changed)
    stored = {i.installation_id: i for i in github_configured.list_github_installations()}
    assert stored[9].repository_selection == "selected"

    deleted = {**created, "action": "deleted"}
    assert deliver(client, "installation", deleted).json()["installation"] == "deleted"
    assert 9 not in {i.installation_id for i in github_configured.list_github_installations()}


def test_unknown_events_are_accepted_and_ignored(
    client: TestClient, github_configured: NoustStore
) -> None:
    """GitHub is told the delivery arrived; nothing happens."""
    response = deliver(client, "star", {"action": "created"})
    assert response.status_code == 202
    assert response.json() == {"status": "ignored", "reason": "event"}
