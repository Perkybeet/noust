# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``POST /hooks/github`` on a central whose servers deploy the repository.

The App and its webhook are the central's, so the delivery is also written down for the servers
that deploy the repository, and the answer says which. What is pinned: GitHub is answered at once
(the servers are handed the event by the worker, not by the request); a central with no application
of its own (a hub) answers a push to a repository its servers deploy as queued, not as ignored;
what cannot be written down is logged and never changes what GitHub is answered; a replayed
delivery is not forwarded twice; and an event that is not about a branch, a tag or a pull request
is forwarded to nobody.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.exceptions import NoustError
from noust.core.forge_events import (
    PullRequestAction,
    PullRequestEvent,
    PushEvent,
    TagEvent,
    event_kind,
)
from noust.core.store import App, NoustStore
from noust.fleet.sources.worker import set_worker
from tests.support.github_hooks import (  # noqa: F401
    SECRET,
    app,
    client,
    deliver,
    fresh_deliveries,
    pr_payload,
    push_payload,
    queued,
)

# A fixture imported from the support module is found by pytest in this module's namespace, and a
# test that asks for it by name looks like a redefinition of an import that is never used.
# ruff: noqa: F811


@dataclass
class Owed:
    node: str
    id: str


class Forwarder:
    """Stands for the forwarder: writes down nothing, remembers what it was asked."""

    def __init__(self) -> None:
        self.asked: list[dict[str, Any]] = []
        self.nodes: list[str] = []
        self.fail: Exception | None = None

    def enqueue(
        self, event: Any, *, delivery: str, host: str, default_branch: str | None
    ) -> list[Owed]:
        if self.fail is not None:
            raise self.fail
        self.asked.append(
            {
                "kind": event_kind(event),
                "event": event,
                "delivery": delivery,
                "host": host,
                "default_branch": default_branch,
            }
        )
        return [Owed(node, f"f{n}") for n, node in enumerate(self.nodes)]


class Worker:
    def __init__(self, forwarder: Forwarder) -> None:
        self.forwarder = forwarder
        self.woken = 0

    def wake(self) -> None:
        self.woken += 1


@pytest.fixture(autouse=True)
def an_app(github_configured: NoustStore) -> NoustStore:
    """The central has its GitHub App: without one its endpoint answers 404."""
    return github_configured


@pytest.fixture
def forwarder() -> Iterator[Forwarder]:
    forwarding = Forwarder()
    worker = Worker(forwarding)
    forwarding.worker = worker  # type: ignore[attr-defined]
    set_worker(worker)  # type: ignore[arg-type]
    yield forwarding
    set_worker(None)


@pytest.fixture
def previews(monkeypatch: pytest.MonkeyPatch) -> list[PullRequestEvent]:
    received: list[PullRequestEvent] = []

    def handle(event: PullRequestEvent, **_: Any) -> list[str]:
        received.append(event)
        return ["job-p"]

    monkeypatch.setattr("noust.managers.previews.handle_pull_request", handle)
    return received


def a_local_app(store: NoustStore) -> None:
    store.create_app(App(domain="shop.example.com", source="github:you/app", branch="main"))


class TestAPush:
    def test_it_is_written_down_for_the_servers_that_deploy_it_and_the_answer_lists_them(
        self, client: TestClient, forwarder: Forwarder, queued: list[dict[str, Any]]
    ) -> None:
        forwarder.nodes = ["web-1", "web-2"]

        response = deliver(client, "push", push_payload("you/app", "main"), delivery="d-9")

        assert response.status_code == 202, response.text
        assert response.json()["forwarded"] == [
            {"node": "web-1", "id": "f0"},
            {"node": "web-2", "id": "f1"},
        ]
        (asked,) = forwarder.asked
        assert (asked["kind"], asked["delivery"], asked["host"], asked["default_branch"]) == (
            "push",
            "d-9",
            "github.com",
            "main",
        )
        assert isinstance(asked["event"], PushEvent) and asked["event"].branch == "main"

    def test_the_worker_is_woken_so_that_the_servers_are_not_kept_waiting(
        self, client: TestClient, forwarder: Forwarder, queued: list[dict[str, Any]]
    ) -> None:
        forwarder.nodes = ["web-1"]

        deliver(client, "push", push_payload())

        assert forwarder.worker.woken == 1  # type: ignore[attr-defined]

    def test_a_central_with_no_application_of_its_own_answers_queued_when_its_servers_deploy_it(
        self, client: TestClient, forwarder: Forwarder, queued: list[dict[str, Any]]
    ) -> None:
        forwarder.nodes = ["web-1"]

        response = deliver(client, "push", push_payload())

        assert response.status_code == 202
        assert response.json()["status"] == "queued" and response.json()["jobs"] == []

    def test_its_own_applications_are_still_updated_beside_the_servers(
        self,
        client: TestClient,
        forwarder: Forwarder,
        queued: list[dict[str, Any]],
        github_configured: NoustStore,
    ) -> None:
        a_local_app(github_configured)
        forwarder.nodes = ["web-1"]

        body = deliver(client, "push", push_payload()).json()

        assert [j["domain"] for j in body["jobs"]] == ["shop.example.com"]
        assert body["forwarded"] == [{"node": "web-1", "id": "f0"}]

    def test_nobody_deploying_it_is_ignored_as_before(
        self, client: TestClient, forwarder: Forwarder, queued: list[dict[str, Any]]
    ) -> None:
        response = deliver(client, "push", push_payload())

        assert response.status_code == 200
        assert response.json() == {"status": "ignored", "reason": "no_application"}
        assert forwarder.worker.woken == 0  # type: ignore[attr-defined]

    def test_a_replayed_delivery_is_not_forwarded_twice(
        self, client: TestClient, forwarder: Forwarder, queued: list[dict[str, Any]]
    ) -> None:
        forwarder.nodes = ["web-1"]

        deliver(client, "push", push_payload(), delivery="same")
        again = deliver(client, "push", push_payload(), delivery="same")

        assert again.json() == {"status": "ignored", "reason": "duplicate"}
        assert len(forwarder.asked) == 1

    def test_what_cannot_be_written_down_never_changes_what_github_is_answered(
        self,
        client: TestClient,
        forwarder: Forwarder,
        queued: list[dict[str, Any]],
        github_configured: NoustStore,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        a_local_app(github_configured)
        forwarder.fail = NoustError("the registry of servers cannot be read")

        response = deliver(client, "push", push_payload())

        assert response.status_code == 202
        assert [j["domain"] for j in response.json()["jobs"]] == ["shop.example.com"]
        assert "forwarded" not in response.json()
        assert "Could not forward push of you/app" in caplog.text

    def test_a_signature_that_is_wrong_is_forwarded_to_nobody(
        self, client: TestClient, forwarder: Forwarder
    ) -> None:
        forwarder.nodes = ["web-1"]

        response = deliver(client, "push", push_payload(), secret="not-the-secret")

        assert response.status_code == 401 and forwarder.asked == []

    def test_a_ping_and_an_event_nobody_handles_are_forwarded_to_nobody(
        self, client: TestClient, forwarder: Forwarder
    ) -> None:
        forwarder.nodes = ["web-1"]

        assert deliver(client, "ping", {"zen": "x"}).status_code == 200
        assert deliver(client, "star", {"action": "created"}).status_code == 202
        assert forwarder.asked == []


class TestATag:
    def tag_payload(self) -> dict[str, Any]:
        return {
            "action": "published",
            "release": {"tag_name": "v1.0.0", "draft": False, "prerelease": False},
            "repository": {"full_name": "you/app", "clone_url": "https://github.com/you/app.git"},
            "installation": {"id": 77},
        }

    def test_a_published_release_is_written_down_for_the_servers(
        self, client: TestClient, forwarder: Forwarder, queued: list[dict[str, Any]]
    ) -> None:
        forwarder.nodes = ["web-1"]

        response = deliver(client, "release", self.tag_payload())

        assert response.status_code == 202, response.text
        assert response.json()["forwarded"] == [{"node": "web-1", "id": "f0"}]
        (asked,) = forwarder.asked
        assert asked["kind"] == "tag" and isinstance(asked["event"], TagEvent)
        assert asked["event"].tag == "v1.0.0"


class TestAPullRequest:
    def test_it_goes_to_the_previews_here_and_to_the_servers(
        self, client: TestClient, forwarder: Forwarder, previews: list[PullRequestEvent]
    ) -> None:
        forwarder.nodes = ["web-1"]

        response = deliver(client, "pull_request", pr_payload("opened"))

        assert response.status_code == 202, response.text
        assert response.json()["jobs"] == [{"job_id": "job-p"}]
        assert response.json()["forwarded"] == [{"node": "web-1", "id": "f0"}]
        (asked,) = forwarder.asked
        assert asked["kind"] == "pull_request"
        assert asked["event"].action is PullRequestAction.OPENED

    def test_an_action_no_preview_acts_on_is_forwarded_to_nobody(
        self, client: TestClient, forwarder: Forwarder, previews: list[PullRequestEvent]
    ) -> None:
        forwarder.nodes = ["web-1"]

        response = deliver(client, "pull_request", pr_payload("labeled"))

        assert response.json() == {"status": "ignored", "reason": "action"}
        assert forwarder.asked == []
