# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Bitbucket Cloud: push deploys and pull request previews (checklist A-08, B-27).

The payloads in ``tests/fixtures/bitbucket`` follow Bitbucket Cloud's
documented event payloads (``repo:push``, ``pullrequest:created``,
``pullrequest:updated``, ``pullrequest:fulfilled``, ``pullrequest:rejected``)
and its delivery headers (``X-Event-Key``, ``X-Request-UUID``,
``X-Hub-Signature: sha256=<hmac of the body>``). The endpoint is the one every
forge shares, so what is pinned here is what Bitbucket adds: its signature, the
way it names events and refs, and the four pull request events a preview acts on.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core import webhook_deliveries as deliveries_log
from noust.core.forge_events import (
    Forge,
    PullRequestAction,
    bitbucket_pushed_branches,
    parse_pull_request,
    parse_tag_event,
)
from noust.core.store import App, NoustStore, PreviewRecord
from noust.integrations import webhook as webhook_service
from noust.managers import previews
from noust.web.api import hooks as hooks_module
from noust.web.api.hooks import mint_webhook_secret
from noust.web.auth import SecurityConfig
from noust.web.server import create_app as build_app

FIXTURES = Path(__file__).parent / "fixtures" / "bitbucket"
DOMAIN = "shop.example.com"
BASE = "previews.example.com"


def payload(name: str) -> dict[str, Any]:
    """Read one recorded delivery."""
    return json.loads((FIXTURES / f"{name}.json").read_text())


def body_of(name: str) -> bytes:
    """Read one recorded delivery as the bytes Bitbucket sent."""
    return (FIXTURES / f"{name}.json").read_bytes()


def sign(secret: str, body: bytes) -> str:
    """Sign a body the way Bitbucket Cloud does."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def headers(
    secret: str, body: bytes, event: str, *, request_id: str | None = None
) -> dict[str, str]:
    """Build the headers of a Bitbucket delivery."""
    return {
        "X-Hub-Signature": sign(secret, body),
        "X-Event-Key": event,
        "X-Request-UUID": request_id or str(uuid.uuid4()),
        "X-Attempt-Number": "1",
        "X-Hook-UUID": "7c0b2f1e-0000-4000-8000-000000000001",
        "Content-Type": "application/json",
    }


class TestSignature:
    """Bitbucket's signature is a SHA-256 HMAC in ``X-Hub-Signature``."""

    def test_the_documented_test_vector_verifies(self) -> None:
        # Atlassian's own example: secret "It's a Secret to Everybody", body "Hello World!".
        assert sign("It's a Secret to Everybody", b"Hello World!") == (
            "sha256=a4771c39fbe90f317c7824e83ddef3caae9cb3d976c214ace1f2937e133263c9"
        )


class TestPushPayloads:
    """What a push names."""

    def test_a_branch_push_names_the_branch(self) -> None:
        assert bitbucket_pushed_branches(payload("repo_push_branch")) == ["main"]

    def test_a_push_can_move_several_branches(self) -> None:
        assert bitbucket_pushed_branches(payload("repo_push_branch_and_tag")) == [
            "release",
            "main",
        ]

    def test_a_tag_push_names_no_branch(self) -> None:
        assert bitbucket_pushed_branches(payload("repo_push_tag")) == []

    def test_a_deleted_branch_names_nothing(self) -> None:
        assert bitbucket_pushed_branches(payload("repo_push_branch_deleted")) == []

    @pytest.mark.parametrize("body", [{}, {"push": None}, {"push": {"changes": "x"}}, {"push": []}])
    def test_a_body_that_is_not_a_push_names_nothing(self, body: dict[str, Any]) -> None:
        assert bitbucket_pushed_branches(body) == []
        assert parse_tag_event("bitbucket", "push", body) is None

    def test_a_pushed_tag_is_a_tag_event(self) -> None:
        event = parse_tag_event("bitbucket", "push", payload("repo_push_tag"))
        assert event is not None
        assert (event.forge, event.tag, event.source) == (Forge.BITBUCKET, "v1.4.0", "push")
        assert event.repository == "acme/shop"
        assert event.clone_url == "https://bitbucket.org/acme/shop.git"
        assert event.sha == "9d8e7f6a5b4c3d2e1f0a9b8c7d6e5f4a3b2c1d0e"

    def test_the_tag_of_a_push_of_a_branch_and_a_tag(self) -> None:
        event = parse_tag_event("bitbucket", "push", payload("repo_push_branch_and_tag"))
        assert event is not None and event.tag == "v2.0.0-rc.1"

    def test_a_branch_push_is_not_a_tag_event(self) -> None:
        assert parse_tag_event("bitbucket", "push", payload("repo_push_branch")) is None

    def test_bitbucket_has_no_release_event(self) -> None:
        assert parse_tag_event("bitbucket", "release", payload("repo_push_tag")) is None


class TestPullRequestPayloads:
    """The four events a preview acts on, and the ones it does not."""

    @pytest.mark.parametrize(
        ("name", "key", "action"),
        [
            ("pullrequest_created", "pullrequest:created", PullRequestAction.OPENED),
            ("pullrequest_updated", "pullrequest:updated", PullRequestAction.UPDATED),
            ("pullrequest_fulfilled", "pullrequest:fulfilled", PullRequestAction.CLOSED),
            ("pullrequest_rejected", "pullrequest:rejected", PullRequestAction.CLOSED),
        ],
    )
    def test_each_event_is_an_action(self, name: str, key: str, action: PullRequestAction) -> None:
        event = parse_pull_request("bitbucket", payload(name), key)
        assert event is not None
        assert event.action is action
        assert event.forge is Forge.BITBUCKET
        assert event.repository == "acme/shop"

    def test_a_created_request_is_described(self) -> None:
        event = parse_pull_request(
            "bitbucket", payload("pullrequest_created"), "pullrequest:created"
        )
        assert event is not None
        assert event.number == 7
        assert event.title == "Add a gift message to the checkout"
        assert event.branch == "feature/gift-message"
        assert event.base_branch == "main"
        assert event.head_sha == "c0ffee1234ab"
        assert event.clone_url == "https://bitbucket.org/acme/shop.git"
        assert event.author == "ada" and event.sender == "ada"
        assert event.from_fork is False
        assert event.bot is False
        assert event.author_association is None

    def test_a_request_from_a_fork_is_a_fork(self) -> None:
        event = parse_pull_request(
            "bitbucket", payload("pullrequest_from_fork"), "pullrequest:created"
        )
        assert event is not None
        assert event.from_fork is True
        assert event.repository == "acme/shop"
        assert event.clone_url == "https://bitbucket.org/mallory/shop.git"

    def test_a_request_whose_fork_is_gone_counts_as_a_fork(self) -> None:
        body = payload("pullrequest_created")
        body["pullrequest"]["source"]["repository"] = None
        event = parse_pull_request("bitbucket", body, "pullrequest:created")
        assert event is not None and event.from_fork is True

    def test_the_account_that_acted_is_the_sender(self) -> None:
        event = parse_pull_request(
            "bitbucket", payload("pullrequest_updated"), "pullrequest:updated"
        )
        assert event is not None
        assert event.author == "ada" and event.sender == "grace"

    @pytest.mark.parametrize("login", ["renovate-bot", "dependabot", "pipelines-bot"])
    def test_a_bot_is_told_by_its_name(self, login: str) -> None:
        body = payload("pullrequest_created")
        body["pullrequest"]["author"]["nickname"] = login
        event = parse_pull_request("bitbucket", body, "pullrequest:created")
        assert event is not None and event.bot is True

    @pytest.mark.parametrize(
        "key", ["pullrequest:approved", "pullrequest:comment_created", "repo:push", "", None]
    )
    def test_an_event_a_preview_does_not_act_on_is_none(self, key: str | None) -> None:
        assert parse_pull_request("bitbucket", payload("pullrequest_created"), key) is None

    def test_an_update_of_a_request_that_is_no_longer_open_is_none(self) -> None:
        body = payload("pullrequest_updated")
        body["pullrequest"]["state"] = "DECLINED"
        assert parse_pull_request("bitbucket", body, "pullrequest:updated") is None

    @pytest.mark.parametrize("field", ["id", "title"])
    def test_a_body_without_what_an_event_needs_is_none(self, field: str) -> None:
        body = payload("pullrequest_created")
        if field == "id":
            del body["pullrequest"]["id"]
            assert parse_pull_request("bitbucket", body, "pullrequest:created") is None
        else:
            del body["pullrequest"]["title"]
            event = parse_pull_request("bitbucket", body, "pullrequest:created")
            assert event is not None and event.title == ""

    def test_a_request_without_a_source_branch_is_none(self) -> None:
        body = payload("pullrequest_created")
        body["pullrequest"]["source"]["branch"] = {}
        assert parse_pull_request("bitbucket", body, "pullrequest:created") is None


# --------------------------------------------------------------------------- the endpoint


@pytest.fixture(autouse=True)
def fresh_delivery_cache() -> None:
    """The replay cache is process-wide; no test may inherit another's ids."""
    hooks_module._deliveries.clear()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """The store the hook reads."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def seeded(store: NoustStore, tmp_path: Path) -> App:
    """An application built from a Bitbucket repository, tracking ``main``."""
    return store.create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            source="https://bitbucket.org/acme/shop.git",
            branch="main",
            port=3000,
            app_path=str(tmp_path / "apps" / "shop-example-com"),
            layout="releases",
        )
    )


@pytest.fixture
def secret(seeded: App) -> str:
    """The webhook secret of the application."""
    return mint_webhook_secret(DOMAIN)


@pytest.fixture
def client(tmp_path: Path, store: NoustStore) -> TestClient:
    """An anonymous client: a code host holds no session."""
    app: FastAPI = build_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))
    return TestClient(app, client=("testclient", 50000))


class FakeJob:
    """A queued job that never runs."""

    def __init__(self, job_id: str) -> None:
        self.id = job_id
        self.status = type("Status", (), {"value": "pending"})()


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture every job the hook and the previews queue."""
    captured: list[dict[str, Any]] = []

    class Manager:
        def create_job(self, **kwargs: Any) -> FakeJob:
            captured.append(kwargs)
            return FakeJob(f"job-{len(captured)}")

        def get_active_jobs(self) -> list[Any]:
            return []

    monkeypatch.setattr("noust.web.api.hooks.get_job_manager", lambda: Manager())
    monkeypatch.setattr(previews, "_jobs", Manager)
    monkeypatch.setattr(previews, "sync_sweep_timer", lambda **_: True)
    return captured


def deliver(
    client: TestClient, secret: str, name: str, event: str, *, request_id: str | None = None
) -> Any:
    """Deliver a recorded payload, signed."""
    body = body_of(name)
    return client.post(
        f"/hooks/deploy/{DOMAIN}",
        content=body,
        headers=headers(secret, body, event, request_id=request_id),
    )


class TestPush:
    """A signed ``repo:push`` deploys the tracked branch."""

    def test_a_push_to_the_tracked_branch_queues_the_update(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        response = deliver(client, secret, "repo_push_branch", "repo:push")

        assert response.status_code == 202, response.text
        (job,) = queued
        assert job["kwargs"] == {"domain": DOMAIN}
        assert job["metadata"]["provider"] == "bitbucket"
        assert job["metadata"]["branch"] == "main"

    def test_a_push_of_several_branches_deploys_when_one_is_the_tracked_one(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        response = deliver(client, secret, "repo_push_branch_and_tag", "repo:push")
        assert response.status_code == 202, response.text
        assert queued[0]["metadata"]["branch"] == "main"

    def test_a_push_to_another_branch_deploys_nothing(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]], store: NoustStore
    ) -> None:
        row = store.get_app(DOMAIN)
        assert row is not None
        row.branch = "production"
        store.update_app(row)

        response = deliver(client, secret, "repo_push_branch", "repo:push")

        assert response.status_code == 200
        assert response.json() == {"status": "ignored", "reason": "branch"}
        assert queued == []

    def test_a_tag_push_deploys_nothing_on_an_application_that_follows_a_branch(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        response = deliver(client, secret, "repo_push_tag", "repo:push")
        assert response.status_code == 200
        assert queued == []

    def test_a_deleted_branch_deploys_nothing(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        response = deliver(client, secret, "repo_push_branch_deleted", "repo:push")
        assert response.status_code == 200 and queued == []

    def test_an_application_that_follows_tags_deploys_a_pushed_tag(
        self,
        client: TestClient,
        secret: str,
        queued: list[dict[str, Any]],
        store: NoustStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store.set_app_follow_tags(DOMAIN, "v*")
        monkeypatch.setattr("noust.web.api.hooks.why_tag_is_ignored", lambda app, tag: None)

        response = deliver(client, secret, "repo_push_tag", "repo:push")

        assert response.status_code == 202, response.text
        assert queued[0]["kwargs"] == {"domain": DOMAIN, "tag": "v1.4.0"}
        assert queued[0]["metadata"]["provider"] == "bitbucket"

    def test_the_test_connection_ping_is_answered_and_deploys_nothing(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        response = deliver(client, secret, "repo_push_branch", "diagnostics:ping")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "event": "ping"}
        assert queued == []

    def test_an_event_nobody_acts_on_is_acknowledged_by_its_own_name(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        response = deliver(client, secret, "pullrequest_created", "pullrequest:approved")
        assert response.status_code == 202
        assert response.json()["event"] == "pullrequest:approved"
        assert queued == []

    def test_a_retry_with_the_same_request_id_is_a_duplicate(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        first = deliver(client, secret, "repo_push_branch", "repo:push", request_id="r-1")
        second = deliver(client, secret, "repo_push_branch", "repo:push", request_id="r-1")
        assert first.status_code == 202
        assert second.json() == {"status": "ignored", "reason": "duplicate"}
        assert len(queued) == 1


class TestRefusals:
    """Nothing unsigned, wrongly signed or signed the wrong way deploys."""

    def test_no_signature(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        response = client.post(
            f"/hooks/deploy/{DOMAIN}",
            content=body_of("repo_push_branch"),
            headers={"X-Event-Key": "repo:push"},
        )
        assert response.status_code == 401 and queued == []

    def test_a_wrong_secret(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        body = body_of("repo_push_branch")
        response = client.post(
            f"/hooks/deploy/{DOMAIN}",
            content=body,
            headers=headers("not-the-secret", body, "repo:push"),
        )
        assert response.status_code == 401 and queued == []

    def test_a_body_that_was_changed_after_it_was_signed(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        body = body_of("repo_push_branch")
        response = client.post(
            f"/hooks/deploy/{DOMAIN}",
            content=body.replace(b"main", b"evil"),
            headers=headers(secret, body, "repo:push"),
        )
        assert response.status_code == 401 and queued == []

    def test_only_a_sha256_signature_is_accepted(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        body = body_of("repo_push_branch")
        sha1 = "sha1=" + hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()
        response = client.post(
            f"/hooks/deploy/{DOMAIN}",
            content=body,
            headers={**headers(secret, body, "repo:push"), "X-Hub-Signature": sha1},
        )
        assert response.status_code == 401 and queued == []

    def test_a_signature_without_its_algorithm_is_refused(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        body = body_of("repo_push_branch")
        bare = sign(secret, body).removeprefix("sha256=")
        response = client.post(
            f"/hooks/deploy/{DOMAIN}",
            content=body,
            headers={**headers(secret, body, "repo:push"), "X-Hub-Signature": bare},
        )
        assert response.status_code == 401

    def test_a_domain_without_a_secret_is_a_404(
        self, client: TestClient, seeded: App, queued: list[dict[str, Any]]
    ) -> None:
        body = body_of("repo_push_branch")
        response = client.post(
            f"/hooks/deploy/{DOMAIN}", content=body, headers=headers("x", body, "repo:push")
        )
        assert response.status_code == 404


class TestDeliveryLog:
    """Bitbucket is in the delivery log like every forge."""

    def test_a_push_is_logged_as_bitbucket(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]], seeded: App
    ) -> None:
        deliver(client, secret, "repo_push_branch", "repo:push", request_id="req-42")
        assert seeded.id is not None
        (row,) = deliveries_log.list_deliveries(seeded.id)
        assert row.provider == "bitbucket"
        assert row.outcome == deliveries_log.DEPLOY_STARTED
        assert row.event == "push"
        assert row.branch == "main"
        assert row.delivery_id == "req-42"

    def test_a_ping_is_logged(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]], seeded: App
    ) -> None:
        deliver(client, secret, "repo_push_branch", "diagnostics:ping")
        assert seeded.id is not None
        assert deliveries_log.list_deliveries(seeded.id)[0].outcome == deliveries_log.PING

    def test_a_wrong_signature_is_logged_without_a_provider(
        self, client: TestClient, secret: str, seeded: App
    ) -> None:
        body = body_of("repo_push_branch")
        client.post(
            f"/hooks/deploy/{DOMAIN}", content=body, headers=headers("nope", body, "repo:push")
        )
        assert seeded.id is not None
        row = deliveries_log.list_deliveries(seeded.id)[0]
        assert row.outcome == deliveries_log.BAD_SIGNATURE and row.provider is None


class TestPreviews:
    """Pull requests build and remove previews, for Bitbucket as for the others."""

    @pytest.fixture
    def previewing(self, seeded: App, queued: list[dict[str, Any]]) -> None:
        previews.enable_previews(DOMAIN, BASE)

    def test_a_created_request_queues_a_preview(
        self,
        client: TestClient,
        secret: str,
        queued: list[dict[str, Any]],
        previewing: None,
        store: NoustStore,
    ) -> None:
        response = deliver(client, secret, "pullrequest_created", "pullrequest:created")

        assert response.status_code == 202, response.text
        record = store.get_preview(DOMAIN, 7)
        assert record is not None
        assert (record.provider, record.branch, record.head_sha) == (
            "bitbucket",
            "feature/gift-message",
            "c0ffee1234ab",
        )
        assert queued[0]["metadata"]["provider"] == "bitbucket"
        assert queued[0]["actor"] == "webhook"

    def test_new_commits_rebuild_it(
        self,
        client: TestClient,
        secret: str,
        queued: list[dict[str, Any]],
        previewing: None,
        store: NoustStore,
    ) -> None:
        deliver(client, secret, "pullrequest_created", "pullrequest:created")
        child = store.create_app(
            App(domain=f"pr-7-shop-example-com.{BASE}", app_type="nodejs", port=3107, app_path="/x")
        )
        store.set_preview_parent(child.domain, DOMAIN)
        response = deliver(client, secret, "pullrequest_updated", "pullrequest:updated")

        assert response.status_code == 202, response.text
        assert len(queued) == 2
        record = store.get_preview(DOMAIN, 7)
        assert record is not None and record.head_sha == "feedface5678"

    def test_an_update_that_moved_nothing_rebuilds_nothing(
        self,
        client: TestClient,
        secret: str,
        queued: list[dict[str, Any]],
        previewing: None,
        store: NoustStore,
    ) -> None:
        deliver(client, secret, "pullrequest_created", "pullrequest:created")
        # Bitbucket sends pullrequest:updated for a new title as well.
        body = payload("pullrequest_updated")
        body["pullrequest"]["source"]["commit"]["hash"] = "c0ffee1234ab"
        raw = json.dumps(body).encode()

        response = client.post(
            f"/hooks/deploy/{DOMAIN}",
            content=raw,
            headers=headers(secret, raw, "pullrequest:updated"),
        )

        assert response.status_code == 200
        assert response.json() == {"status": "ignored", "reason": "no_preview"}
        assert len(queued) == 1

    def test_an_update_of_a_failed_preview_is_a_retry_and_rebuilds(
        self,
        client: TestClient,
        secret: str,
        queued: list[dict[str, Any]],
        previewing: None,
        store: NoustStore,
    ) -> None:
        deliver(client, secret, "pullrequest_created", "pullrequest:created")
        record = store.get_preview(DOMAIN, 7)
        assert record is not None
        store.save_preview(
            PreviewRecord(**{**record.__dict__, "status": "failed", "head_sha": "feedface5678"})
        )
        child = store.create_app(
            App(domain=f"pr-7-shop-example-com.{BASE}", app_type="nodejs", port=3107, app_path="/x")
        )
        store.set_preview_parent(child.domain, DOMAIN)

        response = deliver(client, secret, "pullrequest_updated", "pullrequest:updated")

        assert response.status_code == 202
        assert len(queued) == 2

    @pytest.mark.parametrize("name", ["pullrequest_fulfilled", "pullrequest_rejected"])
    def test_merging_or_declining_removes_the_preview(
        self,
        client: TestClient,
        secret: str,
        queued: list[dict[str, Any]],
        previewing: None,
        store: NoustStore,
        name: str,
    ) -> None:
        deliver(client, secret, "pullrequest_created", "pullrequest:created")
        number = payload(name)["pullrequest"]["id"]
        if number != 7:
            store.save_preview(
                PreviewRecord(
                    parent_domain=DOMAIN,
                    domain=f"pr-{number}-shop-example-com.{BASE}",
                    number=number,
                    branch="feature/other",
                    provider="bitbucket",
                    expires_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    status="ready",
                )
            )
        key = "pullrequest:fulfilled" if name.endswith("fulfilled") else "pullrequest:rejected"

        response = deliver(client, secret, name, key)

        assert response.status_code == 202, response.text
        removal = queued[-1]
        assert removal["func"] is previews.preview_remove_job
        assert removal["kwargs"]["reason"] == "closed"
        assert removal["metadata"]["domain"] == f"pr-{number}-shop-example-com.{BASE}"
        assert removal["actor"] == "webhook"

    def test_a_request_from_a_fork_gets_no_preview(
        self,
        client: TestClient,
        secret: str,
        queued: list[dict[str, Any]],
        previewing: None,
        store: NoustStore,
    ) -> None:
        response = deliver(client, secret, "pullrequest_from_fork", "pullrequest:created")

        assert response.status_code == 200
        assert response.json() == {"status": "ignored", "reason": "no_preview"}
        assert store.get_preview(DOMAIN, 9) is None
        assert queued == []

    def test_a_bot_gets_no_preview_unless_bots_are_allowed(
        self,
        client: TestClient,
        secret: str,
        queued: list[dict[str, Any]],
        previewing: None,
        store: NoustStore,
    ) -> None:
        body = payload("pullrequest_created")
        body["pullrequest"]["author"]["nickname"] = "renovate-bot"
        raw = json.dumps(body).encode()
        refused = client.post(
            f"/hooks/deploy/{DOMAIN}",
            content=raw,
            headers=headers(secret, raw, "pullrequest:created"),
        )
        assert refused.status_code == 200 and queued == []

        previews.enable_previews(DOMAIN, None, allow_bots=True)
        allowed = client.post(
            f"/hooks/deploy/{DOMAIN}",
            content=raw,
            headers=headers(secret, raw, "pullrequest:created", request_id="another"),
        )
        assert allowed.status_code == 202

    def test_previews_off_means_no_preview(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        response = deliver(client, secret, "pullrequest_created", "pullrequest:created")
        assert response.status_code == 200 and queued == []

    def test_the_delivery_log_says_a_preview_started(
        self,
        client: TestClient,
        secret: str,
        queued: list[dict[str, Any]],
        previewing: None,
        seeded: App,
    ) -> None:
        deliver(client, secret, "pullrequest_created", "pullrequest:created")
        assert seeded.id is not None
        row = deliveries_log.list_deliveries(seeded.id)[0]
        assert row.outcome == deliveries_log.PREVIEW_STARTED
        assert row.provider == "bitbucket" and row.event == "pull_request"

    def test_no_comment_is_posted_on_a_bitbucket_pull_request(
        self,
        client: TestClient,
        secret: str,
        queued: list[dict[str, Any]],
        previewing: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from noust.integrations.github import comments

        posted: list[Any] = []
        monkeypatch.setattr(comments, "upsert_pr_comment", lambda *a, **k: posted.append(a))
        deliver(client, secret, "pullrequest_from_fork", "pullrequest:created")
        assert posted == []


class TestSetupGuide:
    """The guided setup knows Bitbucket."""

    def test_the_forge_and_its_settings_page(self, seeded: App) -> None:
        state = webhook_service.status(seeded)
        assert state.forge.forge == "bitbucket"
        assert state.forge.host == "bitbucket.org"
        assert state.forge.repository == "acme/shop"
        assert state.forge.settings_url == "https://bitbucket.org/acme/shop/admin/webhooks"

    def test_it_names_bitbucket_s_events_not_github_s(self, seeded: App) -> None:
        assert webhook_service.status(seeded).hooks.events == ["repo:push"]

    def test_with_previews_it_adds_the_four_pull_request_events(
        self, seeded: App, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(previews, "sync_sweep_timer", lambda **_: True)
        previews.enable_previews(DOMAIN, BASE)
        assert webhook_service.status(seeded).hooks.events == [
            "repo:push",
            "pullrequest:created",
            "pullrequest:updated",
            "pullrequest:fulfilled",
            "pullrequest:rejected",
        ]

    @pytest.mark.parametrize(
        "source",
        ["git@bitbucket.org:acme/shop.git", "ssh://git@bitbucket.org/acme/shop.git"],
    )
    def test_an_ssh_source_is_recognised_too(self, store: NoustStore, source: str) -> None:
        app = store.create_app(App(domain="ssh.example.com", app_type="nodejs", source=source))
        state = webhook_service.status(app)
        assert state.forge.forge == "bitbucket"
        assert state.forge.settings_url == "https://bitbucket.org/acme/shop/admin/webhooks"

    def test_other_forges_keep_their_events(self, store: NoustStore) -> None:
        app = store.create_app(
            App(domain="gh.example.com", app_type="nodejs", source="https://github.com/a/b")
        )
        assert webhook_service.status(app).hooks.events == ["push"]
