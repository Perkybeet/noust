# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the previews API and for the event dispatch of per-application webhooks.

The API is a client of :mod:`noust.managers.previews` (covered in
``tests/test_previews.py``): pinned here are the shapes, sudo mode on every
mutation, and that a removal is a job. The webhook half pins the 2.2 fix:
``POST /hooks/deploy/{domain}`` reads the event, so a pull request or ping
delivery never updates production any more, and a pull request reaches the
previews instead.
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

from noust.core.forge_events import Forge, PullRequestAction, PullRequestEvent
from noust.core.forge_events import parse_pull_request as pull_request_event
from noust.core.store import App, NoustStore, PreviewRecord, PreviewSettings
from noust.managers import previews
from noust.web.api import hooks as hooks_module
from noust.web.api.hooks import mint_webhook_secret
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.jobs import JobType
from noust.web.server import create_app, get_token_manager

PARENT = "shop.example.com"
BASE = "previews.example.com"
CHILD = f"pr-7-shop-example-com.{BASE}"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A store of the test's own with the application previewed, tracking no branch."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    instance.create_app(
        App(
            domain=PARENT,
            app_type="nodejs",
            source="https://github.com/acme/shop.git",
            branch=None,
            port=3000,
            app_path="/var/www/apps/shop-example-com",
        )
    )
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def no_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the sweep timer off the machine."""
    monkeypatch.setattr(previews, "sync_sweep_timer", lambda **_: True)


@pytest.fixture(autouse=True)
def fresh_delivery_cache() -> None:
    """The replay cache is process-wide."""
    hooks_module._deliveries.clear()


@pytest.fixture
def app(tmp_path: Path, store: NoustStore) -> FastAPI:
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def master_token(app: FastAPI) -> str:
    return get_token_manager().generate_master_token()


@pytest.fixture
def session(app: FastAPI, master_token: str) -> TestClient:
    """A cookie session, signed in but not in sudo mode."""
    client = TestClient(app, client=("testclient", 50000))
    response = client.post("/api/auth/login", json={"token": master_token})
    assert response.status_code == 200, response.text
    client.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


@pytest.fixture
def elevated(session: TestClient, master_token: str) -> TestClient:
    """The same session after confirming it is the operator."""
    response = session.post("/api/auth/elevate", json={"token": master_token})
    assert response.status_code == 200, response.text
    return session


class FakeJob:
    def __init__(self, job_id: str) -> None:
        self.id = job_id
        self.status = type("Status", (), {"value": "pending"})()

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id}


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every job the API, the hook or the manager queues."""
    captured: list[dict[str, Any]] = []

    class Manager:
        def create_job(self, **kwargs: Any) -> FakeJob:
            captured.append(kwargs)
            return FakeJob(f"job-{len(captured)}")

    monkeypatch.setattr(previews, "_jobs", Manager)
    monkeypatch.setattr("noust.web.api.previews.get_job_manager", Manager)
    monkeypatch.setattr("noust.web.api.hooks.get_job_manager", Manager)
    return captured


def preview(store: NoustStore, number: int = 7) -> PreviewRecord:
    return store.save_preview(
        PreviewRecord(
            parent_domain=PARENT,
            domain=f"pr-{number}-shop-example-com.{BASE}",
            number=number,
            branch="feature/x",
            provider="github",
            expires_at="2026-10-05T12:00:00+00:00",
            head_sha="a" * 40,
            repository="acme/shop",
            status="ready",
        )
    )


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class TestApi:
    def test_listing_says_previews_are_off(self, session: TestClient, store: NoustStore) -> None:
        response = session.get(f"/api/apps/{PARENT}/previews")

        assert response.status_code == 200, response.text
        assert response.json() == {
            "domain": PARENT,
            "enabled": False,
            "settings": None,
            "previews": [],
            "total": 0,
        }

    def test_listing_shows_settings_and_previews(
        self, session: TestClient, store: NoustStore
    ) -> None:
        store.save_preview_settings(PreviewSettings(app_domain=PARENT, base_domain=BASE))
        preview(store)

        body = session.get(f"/api/apps/{PARENT}/previews").json()

        assert body["enabled"] is True
        assert body["settings"]["base_domain"] == BASE
        assert body["settings"]["max_previews"] == 3
        (item,) = body["previews"]
        assert item["domain"] == CHILD
        assert item["url"] == f"https://{CHILD}"
        assert (item["number"], item["status"], item["provider"]) == (7, "ready", "github")
        assert item["expires_at"].endswith("+00:00")

    def test_an_unknown_application_is_404(self, session: TestClient, store: NoustStore) -> None:
        assert session.get("/api/apps/nope.example.com/previews").status_code == 404

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("put", f"/api/apps/{PARENT}/previews/settings"),
            ("delete", f"/api/apps/{PARENT}/previews/settings"),
            ("delete", f"/api/apps/{PARENT}/previews/7"),
        ],
    )
    def test_every_mutation_needs_sudo_mode(
        self,
        session: TestClient,
        store: NoustStore,
        queued: list[dict[str, Any]],
        method: str,
        path: str,
    ) -> None:
        preview(store)
        kwargs: dict[str, Any] = {"json": {"base_domain": BASE}} if method == "put" else {}

        response = session.request(method.upper(), path, **kwargs)

        assert response.status_code == 403, response.text
        assert response.json()["error"] == "elevation_required"
        assert queued == []
        assert store.get_preview(PARENT, 7) is not None

    def test_turning_previews_on(self, elevated: TestClient, store: NoustStore) -> None:
        response = elevated.put(
            f"/api/apps/{PARENT}/previews/settings",
            json={"base_domain": BASE, "max_previews": 5, "ttl_hours": 24},
        )

        assert response.status_code == 200, response.text
        assert response.json()["base_domain"] == BASE
        stored = store.get_preview_settings(PARENT)
        assert stored is not None and (stored.max_previews, stored.ttl_hours) == (5, 24)

    def test_bots_and_excluded_variables_round_trip(
        self, elevated: TestClient, store: NoustStore
    ) -> None:
        response = elevated.put(
            f"/api/apps/{PARENT}/previews/settings",
            json={"base_domain": BASE, "allow_bots": True, "exclude_env": ["STRIPE_KEY", "S3"]},
        )

        assert response.status_code == 200, response.text
        assert (response.json()["allow_bots"], response.json()["exclude_env"]) == (
            True,
            ["STRIPE_KEY", "S3"],
        )
        # Left out, both keep what is stored.
        again = elevated.put(f"/api/apps/{PARENT}/previews/settings", json={"base_domain": BASE})
        assert (again.json()["allow_bots"], again.json()["exclude_env"]) == (
            True,
            ["STRIPE_KEY", "S3"],
        )
        listed = elevated.get(f"/api/apps/{PARENT}/previews").json()["settings"]
        assert (listed["allow_bots"], listed["exclude_env"]) == (True, ["STRIPE_KEY", "S3"])

    def test_new_settings_refuse_bots_and_copy_everything(
        self, elevated: TestClient, store: NoustStore
    ) -> None:
        body = elevated.put(
            f"/api/apps/{PARENT}/previews/settings", json={"base_domain": BASE}
        ).json()

        assert (body["allow_bots"], body["exclude_env"]) == (False, [])

    def test_an_invalid_variable_name_is_refused_beside_its_field(
        self, elevated: TestClient, store: NoustStore
    ) -> None:
        response = elevated.put(
            f"/api/apps/{PARENT}/previews/settings",
            json={"base_domain": BASE, "exclude_env": ["OK", "NOT-A-NAME"]},
        )

        assert response.status_code == 400, response.text
        assert "exclude_env" in response.json().get("fields", {}), response.json()
        assert store.get_preview_settings(PARENT) is None

    @pytest.mark.parametrize(
        "body",
        [
            {"base_domain": BASE, "max_previews": 21},
            {"base_domain": BASE, "ttl_hours": 0},
            {"base_domain": "localhost"},
        ],
    )
    def test_refused_settings_are_400(
        self, elevated: TestClient, store: NoustStore, body: dict[str, Any]
    ) -> None:
        response = elevated.put(f"/api/apps/{PARENT}/previews/settings", json=body)

        assert response.status_code == 400, response.text
        assert store.get_preview_settings(PARENT) is None

    def test_turning_previews_off_queues_one_removal_job(
        self, elevated: TestClient, store: NoustStore, queued: list[dict[str, Any]]
    ) -> None:
        store.save_preview_settings(PreviewSettings(app_domain=PARENT, base_domain=BASE))
        preview(store)

        response = elevated.delete(f"/api/apps/{PARENT}/previews/settings")

        assert response.status_code == 200, response.text
        assert response.json() == {
            "domain": PARENT,
            "enabled": False,
            "removing": [CHILD],
            "job_id": "job-1",
        }
        assert store.get_preview_settings(PARENT) is None
        (job,) = queued
        assert job["func"] is previews.remove_previews_job
        assert job["job_type"] == JobType.DELETE

    def test_turning_previews_off_without_previews_queues_nothing(
        self, elevated: TestClient, store: NoustStore, queued: list[dict[str, Any]]
    ) -> None:
        store.save_preview_settings(PreviewSettings(app_domain=PARENT, base_domain=BASE))

        response = elevated.delete(f"/api/apps/{PARENT}/previews/settings")

        assert response.json()["job_id"] is None
        assert queued == []

    def test_removing_one_preview_is_a_job(
        self, elevated: TestClient, store: NoustStore, queued: list[dict[str, Any]]
    ) -> None:
        preview(store)

        response = elevated.delete(f"/api/apps/{PARENT}/previews/7")

        assert response.status_code == 202, response.text
        assert response.json()["job_id"] == "job-1"
        (job,) = queued
        assert job["func"] is previews.preview_remove_job
        assert job["kwargs"] == {"domain": CHILD, "reason": "removed"}
        record = store.get_preview(PARENT, 7)
        assert record is not None and record.status == "removing"

    def test_removing_an_unknown_preview_is_404(
        self, elevated: TestClient, store: NoustStore, queued: list[dict[str, Any]]
    ) -> None:
        assert elevated.delete(f"/api/apps/{PARENT}/previews/9").status_code == 404
        assert queued == []


# ---------------------------------------------------------------------------
# Per-application webhook: dispatch on the event
# ---------------------------------------------------------------------------


def github_pull_request(
    action: str = "opened",
    *,
    fork: bool = False,
    number: int = 7,
    login: str = "alice",
    user_type: str = "User",
    association: str = "MEMBER",
) -> dict[str, Any]:
    head_repo = "someone/shop" if fork else "acme/shop"
    return {
        "action": action,
        "number": number,
        "sender": {"login": login, "type": user_type},
        "pull_request": {
            "title": "Add a feature",
            "user": {"login": login, "type": user_type},
            "author_association": association,
            "head": {
                "ref": "feature/x",
                "sha": "a" * 40,
                "repo": {
                    "full_name": head_repo,
                    "clone_url": f"https://github.com/{head_repo}.git",
                },
            },
            "base": {
                "ref": "main",
                "repo": {"full_name": "acme/shop", "clone_url": "https://github.com/acme/shop.git"},
            },
        },
        "repository": {"full_name": "acme/shop", "clone_url": "https://github.com/acme/shop.git"},
        "installation": {"id": 42},
    }


def gitlab_merge_request(action: str = "open", **extra: Any) -> dict[str, Any]:
    return {
        "object_kind": "merge_request",
        "project": {
            "path_with_namespace": "acme/shop",
            "git_http_url": "https://gitlab.com/acme/shop.git",
        },
        "object_attributes": {
            "iid": 7,
            "title": "Add a feature",
            "action": action,
            "source_branch": "feature/x",
            "target_branch": "main",
            "source_project_id": 1,
            "target_project_id": 1,
            "last_commit": {"id": "b" * 40},
            "source": {"git_http_url": "https://gitlab.com/acme/shop.git"},
            **extra,
        },
    }


def signed(secret: str, body: bytes, provider: str, event: str | None) -> dict[str, str]:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json"}
    if provider == "github":
        headers |= {
            "X-Hub-Signature-256": f"sha256={digest}",
            "X-GitHub-Delivery": str(uuid.uuid4()),
        }
        if event:
            headers["X-GitHub-Event"] = event
    elif provider == "gitea":
        headers |= {"X-Gitea-Signature": digest, "X-Gitea-Delivery": str(uuid.uuid4())}
        if event:
            headers["X-Gitea-Event"] = event
    else:
        headers |= {"X-Gitlab-Token": secret, "X-Gitlab-Event-UUID": str(uuid.uuid4())}
        if event:
            headers["X-Gitlab-Event"] = event
    return headers


@pytest.fixture
def secret(store: NoustStore) -> str:
    return mint_webhook_secret(PARENT)


@pytest.fixture
def forge(app: FastAPI) -> TestClient:
    return TestClient(app, client=("testclient", 50000))


@pytest.fixture
def handled(monkeypatch: pytest.MonkeyPatch) -> list[tuple[PullRequestEvent, str | None]]:
    """Capture what reaches the previews manager."""
    calls: list[tuple[PullRequestEvent, str | None]] = []

    def handle(event: PullRequestEvent, *, app_domain: str | None = None) -> list[str]:
        calls.append((event, app_domain))
        return ["job-p"]

    monkeypatch.setattr(hooks_module, "handle_pull_request", handle)
    return calls


def deliver(
    forge: TestClient, secret: str, payload: dict[str, Any], provider: str, event: str | None
) -> Any:
    body = json.dumps(payload).encode()
    return forge.post(
        f"/hooks/deploy/{PARENT}", content=body, headers=signed(secret, body, provider, event)
    )


class TestHookDispatch:
    def test_a_pull_request_never_updates_the_application(
        self,
        forge: TestClient,
        secret: str,
        queued: list[dict[str, Any]],
        handled: list[Any],
    ) -> None:
        """The fixed bug: no ref and no pinned branch used to mean "update production"."""
        response = deliver(forge, secret, github_pull_request(), "github", "pull_request")

        assert response.status_code == 202, response.text
        assert response.json() == {"job_ids": ["job-p"], "status": "pending"}
        assert queued == []
        ((event, app_domain),) = handled
        assert app_domain == PARENT
        assert (event.forge, event.action, event.number) == (
            Forge.GITHUB,
            PullRequestAction.OPENED,
            7,
        )

    def test_a_pull_request_through_the_real_manager_queues_no_update(
        self, forge: TestClient, secret: str, store: NoustStore, queued: list[dict[str, Any]]
    ) -> None:
        response = deliver(forge, secret, github_pull_request(), "github", "pull_request")

        # Previews are off: authentic, ignored, and production untouched.
        assert response.status_code == 200, response.text
        assert response.json()["reason"] == "no_preview"
        assert queued == []

    def test_with_previews_on_the_pull_request_builds_a_preview(
        self, forge: TestClient, secret: str, store: NoustStore, queued: list[dict[str, Any]]
    ) -> None:
        store.save_preview_settings(PreviewSettings(app_domain=PARENT, base_domain=BASE))

        response = deliver(forge, secret, github_pull_request(), "github", "pull_request")

        assert response.status_code == 202, response.text
        (job,) = queued
        assert job["func"] is previews.preview_deploy_job
        assert store.get_preview(PARENT, 7) is not None

    @pytest.mark.parametrize(
        "payload",
        [
            github_pull_request(login="dependabot[bot]", user_type="Bot", association="NONE"),
            github_pull_request(login="mallory", association="CONTRIBUTOR"),
        ],
        ids=["bot", "not-a-collaborator"],
    )
    def test_an_untrusted_author_gets_no_preview(
        self,
        forge: TestClient,
        secret: str,
        store: NoustStore,
        queued: list[dict[str, Any]],
        payload: dict[str, Any],
    ) -> None:
        store.save_preview_settings(PreviewSettings(app_domain=PARENT, base_domain=BASE))

        response = deliver(forge, secret, payload, "github", "pull_request")

        assert response.status_code == 200, response.text
        assert response.json()["reason"] == "no_preview"
        assert queued == []
        assert store.get_preview(PARENT, 7) is None

    def test_a_bot_gets_a_preview_when_bots_are_allowed(
        self, forge: TestClient, secret: str, store: NoustStore, queued: list[dict[str, Any]]
    ) -> None:
        store.save_preview_settings(
            PreviewSettings(app_domain=PARENT, base_domain=BASE, allow_bots=True)
        )
        payload = github_pull_request(login="renovate[bot]", user_type="Bot", association="NONE")

        response = deliver(forge, secret, payload, "github", "pull_request")

        assert response.status_code == 202, response.text
        (job,) = queued
        assert job["func"] is previews.preview_deploy_job

    def test_a_ping_is_answered_and_nothing_else(
        self, forge: TestClient, secret: str, queued: list[dict[str, Any]], handled: list[Any]
    ) -> None:
        response = deliver(forge, secret, {"zen": "Keep it simple"}, "github", "ping")

        assert response.status_code == 200, response.text
        assert response.json()["event"] == "ping"
        assert queued == [] and handled == []

    def test_another_event_is_acknowledged_and_ignored(
        self, forge: TestClient, secret: str, queued: list[dict[str, Any]], handled: list[Any]
    ) -> None:
        response = deliver(forge, secret, {"action": "opened"}, "github", "issues")

        assert response.status_code == 202, response.text
        assert response.json() == {"status": "ignored", "reason": "event", "event": "issues"}
        assert queued == [] and handled == []

    @pytest.mark.parametrize(
        ("provider", "event"), [("github", "push"), ("gitea", "push"), ("gitlab", "Push Hook")]
    )
    def test_a_push_still_updates(
        self,
        forge: TestClient,
        secret: str,
        queued: list[dict[str, Any]],
        provider: str,
        event: str,
    ) -> None:
        response = deliver(forge, secret, {"ref": "refs/heads/main"}, provider, event)

        assert response.status_code == 202, response.text
        (job,) = queued
        assert job["job_type"] == JobType.UPDATE
        assert job["kwargs"] == {"domain": PARENT}

    def test_a_delivery_naming_no_event_is_still_a_push(
        self, forge: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        response = deliver(forge, secret, {"ref": "refs/heads/main"}, "github", None)

        assert response.status_code == 202, response.text
        assert len(queued) == 1

    def test_a_gitea_pull_request(
        self, forge: TestClient, secret: str, queued: list[dict[str, Any]], handled: list[Any]
    ) -> None:
        response = deliver(
            forge, secret, github_pull_request("synchronized"), "gitea", "pull_request"
        )

        assert response.status_code == 202, response.text
        ((event, _),) = handled
        assert (event.forge, event.action) == (Forge.GITEA, PullRequestAction.UPDATED)
        assert queued == []

    def test_a_gitlab_merge_request(
        self, forge: TestClient, secret: str, queued: list[dict[str, Any]], handled: list[Any]
    ) -> None:
        response = deliver(forge, secret, gitlab_merge_request(), "gitlab", "Merge Request Hook")

        assert response.status_code == 202, response.text
        ((event, _),) = handled
        assert (event.forge, event.action, event.head_sha) == (
            Forge.GITLAB,
            PullRequestAction.OPENED,
            "b" * 40,
        )
        assert queued == []

    def test_gitlab_is_read_from_object_kind_without_the_header(
        self, forge: TestClient, secret: str, queued: list[dict[str, Any]], handled: list[Any]
    ) -> None:
        response = deliver(forge, secret, gitlab_merge_request("merge"), "gitlab", None)

        assert response.status_code == 202, response.text
        assert handled[0][0].action is PullRequestAction.CLOSED
        assert queued == []

    def test_an_action_previews_ignore_does_nothing(
        self, forge: TestClient, secret: str, queued: list[dict[str, Any]], handled: list[Any]
    ) -> None:
        response = deliver(forge, secret, github_pull_request("labeled"), "github", "pull_request")

        assert response.status_code == 200, response.text
        assert response.json()["reason"] == "action"
        assert queued == [] and handled == []

    def test_a_gitlab_header_cannot_be_claimed_by_a_github_signature(
        self, forge: TestClient, secret: str, queued: list[dict[str, Any]], handled: list[Any]
    ) -> None:
        body = json.dumps({"ref": "refs/heads/main"}).encode()
        headers = signed(secret, body, "github", None) | {"X-Gitlab-Event": "Merge Request Hook"}

        response = forge.post(f"/hooks/deploy/{PARENT}", content=body, headers=headers)

        # Verified as GitHub, which named no event: a push, as before 2.2.
        assert response.status_code == 202, response.text
        assert handled == [] and len(queued) == 1

    def test_signature_checks_still_come_first(
        self, forge: TestClient, secret: str, queued: list[dict[str, Any]], handled: list[Any]
    ) -> None:
        body = json.dumps(github_pull_request()).encode()
        headers = signed("wrong", body, "github", "pull_request")

        response = forge.post(f"/hooks/deploy/{PARENT}", content=body, headers=headers)

        assert response.status_code == 401
        assert handled == [] and queued == []


class TestPullRequestParsing:
    def test_github(self) -> None:
        event = pull_request_event("github", github_pull_request("opened"))

        assert event == PullRequestEvent(
            forge=Forge.GITHUB,
            action=PullRequestAction.OPENED,
            repository="acme/shop",
            clone_url="https://github.com/acme/shop.git",
            number=7,
            title="Add a feature",
            branch="feature/x",
            base_branch="main",
            head_sha="a" * 40,
            from_fork=False,
            installation_id=42,
            author="alice",
            sender="alice",
            bot=False,
            author_association="MEMBER",
        )

    @pytest.mark.parametrize(
        ("login", "user_type", "bot"),
        [
            ("dependabot[bot]", "Bot", True),
            ("renovate[bot]", "", True),
            ("some-app", "Bot", True),
            ("alice", "User", False),
            # On GitHub a name is not a type: a person may be called "abbot".
            ("abbot", "User", False),
        ],
    )
    def test_github_bots(self, login: str, user_type: str, bot: bool) -> None:
        event = pull_request_event("github", github_pull_request(login=login, user_type=user_type))
        assert event is not None and event.bot is bot

    def test_a_bot_pushing_to_a_persons_branch_counts(self) -> None:
        payload = github_pull_request("synchronize")
        payload["sender"] = {"login": "pre-commit-ci[bot]", "type": "Bot"}

        event = pull_request_event("github", payload)

        assert event is not None
        assert (event.author, event.sender, event.bot) == ("alice", "pre-commit-ci[bot]", True)

    @pytest.mark.parametrize(
        ("login", "bot"),
        [
            ("renovate-bot", True),
            ("dependabot", True),
            ("gitea-actions[bot]", True),
            ("bob", False),
        ],
    )
    def test_gitea_bots_are_told_by_name(self, login: str, bot: bool) -> None:
        payload = github_pull_request()
        payload["pull_request"]["user"] = {"login": login}
        payload["sender"] = {"login": login}
        del payload["pull_request"]["author_association"]

        event = pull_request_event("gitea", payload)

        assert event is not None
        assert (event.bot, event.author_association) == (bot, None)

    @pytest.mark.parametrize(
        ("username", "bot"),
        [
            ("renovate-bot", True),
            ("project_12_bot_3f2a9c", True),
            ("group_4_bot", True),
            ("carol", False),
        ],
    )
    def test_gitlab_bots_are_told_by_name(self, username: str, bot: bool) -> None:
        payload = gitlab_merge_request()
        payload["user"] = {"username": username}

        event = pull_request_event("gitlab", payload)

        assert event is not None
        assert (event.author, event.bot, event.author_association) == (username, bot, None)

    @pytest.mark.parametrize(
        ("action", "expected"),
        [
            ("opened", PullRequestAction.OPENED),
            ("reopened", PullRequestAction.OPENED),
            ("ready_for_review", PullRequestAction.OPENED),
            ("synchronize", PullRequestAction.UPDATED),
            ("closed", PullRequestAction.CLOSED),
            ("labeled", None),
            ("edited", None),
        ],
    )
    def test_github_actions(self, action: str, expected: PullRequestAction | None) -> None:
        event = pull_request_event("github", github_pull_request(action))
        assert (event.action if event else None) == expected

    def test_a_fork_is_detected(self) -> None:
        event = pull_request_event("github", github_pull_request(fork=True))

        assert event is not None
        assert event.from_fork is True
        assert event.clone_url == "https://github.com/someone/shop.git"

    def test_a_deleted_fork_counts_as_a_fork(self) -> None:
        payload = github_pull_request()
        payload["pull_request"]["head"]["repo"] = None

        event = pull_request_event("github", payload)
        assert event is not None and event.from_fork is True

    @pytest.mark.parametrize(
        ("action", "extra", "expected"),
        [
            ("open", {}, PullRequestAction.OPENED),
            ("reopen", {}, PullRequestAction.OPENED),
            ("update", {"oldrev": "c" * 40}, PullRequestAction.UPDATED),
            ("update", {}, None),
            ("close", {}, PullRequestAction.CLOSED),
            ("merge", {}, PullRequestAction.CLOSED),
            ("approved", {}, None),
        ],
    )
    def test_gitlab_actions(
        self, action: str, extra: dict[str, Any], expected: PullRequestAction | None
    ) -> None:
        event = pull_request_event("gitlab", gitlab_merge_request(action, **extra))
        assert (event.action if event else None) == expected

    def test_a_gitlab_fork(self) -> None:
        payload = gitlab_merge_request(source_project_id=2)
        event = pull_request_event("gitlab", payload)
        assert event is not None and event.from_fork is True

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"action": "opened"},
            {"action": "opened", "number": "7", "pull_request": {}},
            {"action": "opened", "number": True},
        ],
    )
    def test_malformed_payloads_are_nothing(self, payload: dict[str, Any]) -> None:
        assert pull_request_event("github", payload) is None
        assert pull_request_event("gitlab", payload) is None
