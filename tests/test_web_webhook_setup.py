# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The webhook's guided setup, on the API (backlog 52, spec section 9).

Every delivery an application's webhook receives is kept (a ping, a push that
deployed, a push to another branch, a wrong signature), and the API answers
the questions the console's wizard asks: is the public URL exposed, what is
the exact payload URL, is a secret set, which branch deploys, is anything
wrong. The secret can be shown again in sudo mode, and only there.
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

from noust.core import webhook_deliveries as wd
from noust.core.config import Config
from noust.core.store import App, NoustStore
from noust.integrations.webhook import mint_secret
from noust.web.api import hooks as hooks_module
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app as build_app
from noust.web.server import get_token_manager

DOMAIN = "app.example.com"
PUSH_MAIN = json.dumps({"ref": "refs/heads/main"}).encode()
PUSH_DEV = json.dumps({"ref": "refs/heads/dev"}).encode()


def signed(secret: str, body: bytes, event: str = "push") -> dict[str, str]:
    """
    Build the headers of a GitHub delivery.

    Args:
        secret: The webhook secret in clear.
        body: The raw request body.
        event: ``X-GitHub-Event``.

    Returns:
        The request headers.
    """
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {
        "X-Hub-Signature-256": f"sha256={digest}",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }


@pytest.fixture(autouse=True)
def fresh_delivery_cache() -> None:
    """The replay cache is process-wide; no test may inherit another's ids."""
    hooks_module._deliveries.clear()


@pytest.fixture(autouse=True)
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """
    Give the console an empty configuration of its own.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Yields:
        Nothing.
    """
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    Config.reset_instance()
    try:
        yield
    finally:
        Config.reset_instance()


@pytest.fixture(autouse=True)
def no_github_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Start on a server without a GitHub App.

    Args:
        monkeypatch: Patching helper, scoped to the test.
    """
    from noust.integrations.github import service

    monkeypatch.setattr(service, "status", lambda: service.GitHubStatus(configured=False))


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """
    Give the console a store of its own.

    Args:
        tmp_path: Per-test temporary directory.

    Yields:
        The store.
    """
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def seeded(store: NoustStore) -> App:
    """
    Deploy one application on paper, tracking ``main``.

    Args:
        store: The store fixture.

    Returns:
        The application.
    """
    return store.create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            source="https://github.com/you/app",
            branch="main",
            port=3000,
            layout="releases",
            app_path="/var/www/apps/app-example-com",
        )
    )


@pytest.fixture
def secret(store: NoustStore, seeded: App) -> str:
    """
    Enable the webhook of the seeded application.

    Args:
        store: The store fixture.
        seeded: The application.

    Returns:
        The secret in clear.
    """
    return mint_secret(DOMAIN)


@pytest.fixture
def app(tmp_path: Path, store: NoustStore) -> FastAPI:
    """
    Args:
        tmp_path: Per-test temporary directory.
        store: The store fixture.

    Returns:
        The console.
    """
    return build_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def forge(app: FastAPI) -> TestClient:
    """
    Args:
        app: The console.

    Returns:
        An anonymous client: a forge holds no session.
    """
    return TestClient(app, client=("testclient", 50000))


@pytest.fixture
def operator(app: FastAPI) -> TestClient:
    """
    Args:
        app: The console.

    Returns:
        A signed-in client, in sudo mode.
    """
    client = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    response = client.post("/api/auth/login", json={"token": token})
    assert response.status_code == 200, response.text
    client.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    assert client.post("/api/auth/elevate", json={"token": token}).status_code == 200
    return client


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """
    Capture the update instead of queueing a real job.

    Args:
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The job descriptions that reached the job manager.
    """
    captured: list[dict[str, Any]] = []

    def create_job(**kwargs: Any) -> Any:
        captured.append(kwargs)
        return type(
            "Queued",
            (),
            {"id": "job-1", "status": type("Status", (), {"value": "pending"})()},
        )()

    manager = type("FakeJobs", (), {"create_job": staticmethod(create_job)})()
    monkeypatch.setattr("noust.web.api.hooks.get_job_manager", lambda: manager)
    return captured


def received(operator: TestClient, **params: Any) -> list[dict[str, Any]]:
    """
    Read what the webhook received, through the API.

    Args:
        operator: The signed-in client.
        **params: Query parameters.

    Returns:
        The deliveries, newest first.
    """
    response = operator.get(f"/api/apps/{DOMAIN}/webhook/received", params=params)
    assert response.status_code == 200, response.text
    return response.json()["items"]


# -- every delivery is kept -----------------------------------------------------------


def test_a_ping_is_kept(forge: TestClient, operator: TestClient, secret: str) -> None:
    body = b"{}"

    response = forge.post(
        f"/hooks/deploy/{DOMAIN}", content=body, headers=signed(secret, body, "ping")
    )

    assert response.status_code == 200
    (row,) = received(operator)
    assert (row["outcome"], row["event"], row["provider"]) == ("ping", "ping", "github")
    assert row["received_at"].endswith(("Z", "+00:00")) or "+" in row["received_at"][10:]


def test_a_push_that_deployed_is_kept_with_its_job(
    forge: TestClient, operator: TestClient, secret: str, queued: list[dict[str, Any]]
) -> None:
    response = forge.post(
        f"/hooks/deploy/{DOMAIN}", content=PUSH_MAIN, headers=signed(secret, PUSH_MAIN)
    )

    assert response.status_code == 202
    (row,) = received(operator)
    assert (row["outcome"], row["branch"], row["job_id"]) == ("deploy_started", "main", "job-1")
    assert row["delivery_id"]


def test_a_push_to_another_branch_is_kept_as_ignored(
    forge: TestClient, operator: TestClient, secret: str, queued: list[dict[str, Any]]
) -> None:
    forge.post(f"/hooks/deploy/{DOMAIN}", content=PUSH_DEV, headers=signed(secret, PUSH_DEV))

    (row,) = received(operator)
    assert (row["outcome"], row["branch"], row["job_id"]) == ("ignored_branch", "dev", None)
    assert "main" in row["detail"]
    assert queued == []


def test_a_wrong_signature_is_kept_and_a_burst_is_one_entry(
    forge: TestClient, operator: TestClient, secret: str
) -> None:
    for _ in range(3):
        response = forge.post(
            f"/hooks/deploy/{DOMAIN}", content=PUSH_MAIN, headers=signed("wrong-secret", PUSH_MAIN)
        )
        assert response.status_code in (401, 429)

    (row,) = received(operator)
    assert row["outcome"] == "bad_signature"
    assert row["count"] == 3
    assert row["provider"] is None
    assert secret not in json.dumps(row)


def test_a_replayed_delivery_and_an_event_nothing_acts_on_are_kept(
    forge: TestClient, operator: TestClient, secret: str, queued: list[dict[str, Any]]
) -> None:
    headers = signed(secret, PUSH_MAIN)
    forge.post(f"/hooks/deploy/{DOMAIN}", content=PUSH_MAIN, headers=headers)
    forge.post(f"/hooks/deploy/{DOMAIN}", content=PUSH_MAIN, headers=headers)
    star = signed(secret, b"{}", "star")
    forge.post(f"/hooks/deploy/{DOMAIN}", content=b"{}", headers=star)

    outcomes = [row["outcome"] for row in received(operator)]

    assert outcomes == ["ignored_event", "duplicate", "deploy_started"]


def test_a_domain_without_a_secret_records_nothing_and_answers_the_same_404(
    forge: TestClient, operator: TestClient, seeded: App, store: NoustStore
) -> None:
    response = forge.post(
        f"/hooks/deploy/{DOMAIN}", content=PUSH_MAIN, headers=signed("guess", PUSH_MAIN)
    )

    assert response.status_code == 404
    assert received(operator) == []


def test_an_unknown_domain_records_nothing(forge: TestClient, store: NoustStore) -> None:
    response = forge.post("/hooks/deploy/nothing.example.com", content=b"{}")

    assert response.status_code == 404
    with store._transaction() as cursor:
        cursor.execute("SELECT COUNT(*) FROM webhook_deliveries")
        assert cursor.fetchone()[0] == 0


def test_the_received_list_is_newest_first_and_bounded_by_limit(
    forge: TestClient, operator: TestClient, secret: str
) -> None:
    for _ in range(4):
        forge.post(f"/hooks/deploy/{DOMAIN}", content=b"{}", headers=signed(secret, b"{}", "ping"))

    items = received(operator, limit=2)

    assert len(items) == 2
    assert items[0]["id"] > items[1]["id"]


def test_the_received_list_refuses_a_limit_outside_the_bound(
    operator: TestClient, secret: str
) -> None:
    assert (
        operator.get(f"/api/apps/{DOMAIN}/webhook/received", params={"limit": 0}).status_code == 422
    )
    assert (
        operator.get(
            f"/api/apps/{DOMAIN}/webhook/received", params={"limit": wd.KEEP_PER_APP + 1}
        ).status_code
        == 422
    )


def test_the_older_deliveries_endpoint_still_lists_the_deployments_it_queued(
    operator: TestClient, secret: str
) -> None:
    response = operator.get(f"/api/apps/{DOMAIN}/webhook/deliveries")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}


# -- the state ------------------------------------------------------------------------


def state(operator: TestClient) -> dict[str, Any]:
    """
    Read the guided setup's state through the API.

    Args:
        operator: The signed-in client.

    Returns:
        The state.
    """
    response = operator.get(f"/api/apps/{DOMAIN}/webhook")
    assert response.status_code == 200, response.text
    return response.json()


def test_a_webhook_that_was_never_set_up_says_what_to_do_first(
    operator: TestClient, seeded: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("noust.integrations.hooks_site.public_hooks_url", lambda: None)

    body = state(operator)

    assert body["state"] == "disabled"
    assert body["enabled"] is False
    assert body["hooks"]["exposed"] is False
    assert body["hooks"]["base_url"] is None
    # Not public, but the address the console was opened at is offered.
    assert body["hooks"]["hook_url"].endswith(f"/hooks/deploy/{DOMAIN}")
    assert body["hooks"]["hook_url_public"] is False
    assert body["hooks"]["content_type"] == "application/json"
    assert body["hooks"]["events"] == ["push"]
    assert body["deliveries"] == {
        "total": 0,
        "refused_since_last_verified": 0,
        "last": None,
        "last_verified_at": None,
        "last_push_at": None,
    }


def test_the_state_carries_the_exact_public_payload_url(
    operator: TestClient, secret: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "noust.integrations.hooks_site.public_hooks_url", lambda: "https://hooks.example.com/hooks"
    )

    body = state(operator)

    assert body["state"] == "waiting"
    assert body["hooks"] == {
        "exposed": True,
        "base_url": "https://hooks.example.com/hooks",
        "hook_url": f"https://hooks.example.com/hooks/deploy/{DOMAIN}",
        "hook_url_public": True,
        "content_type": "application/json",
        "events": ["push"],
    }
    assert body["forge"] == {
        "forge": "github",
        "host": "github.com",
        "repository": "you/app",
        "settings_url": "https://github.com/you/app/settings/hooks/new",
    }
    assert body["branch"] == {"tracked": "main", "pinned": True, "any_push_deploys": False}
    assert body["layout"] == "releases"
    assert body["inplace_warning"] is False
    assert body["github_app"]["covers_repository"] is False


def test_the_state_never_carries_the_secret(operator: TestClient, secret: str) -> None:
    response = operator.get(f"/api/apps/{DOMAIN}/webhook")

    assert secret not in response.text
    assert response.json()["enabled"] is True


def test_no_pinned_branch_is_flagged_because_any_push_then_deploys(
    operator: TestClient, store: NoustStore, secret: str
) -> None:
    app = store.get_app(DOMAIN)
    assert app is not None
    app.branch = None
    store.update_app(app)

    body = state(operator)

    assert body["branch"] == {"tracked": None, "pinned": False, "any_push_deploys": True}


def test_auto_deploy_on_an_in_place_application_carries_the_warning(
    operator: TestClient, store: NoustStore, secret: str
) -> None:
    app = store.get_app(DOMAIN)
    assert app is not None
    app.layout = "inplace"
    store.update_app(app)

    body = state(operator)

    assert (body["layout"], body["inplace_warning"]) == ("inplace", True)


def test_the_state_follows_the_deliveries(
    forge: TestClient, operator: TestClient, secret: str, queued: list[dict[str, Any]]
) -> None:
    forge.post(f"/hooks/deploy/{DOMAIN}", content=b"{}", headers=signed(secret, b"{}", "ping"))
    connected = state(operator)
    assert connected["state"] == "connected"
    assert connected["deliveries"]["last"]["outcome"] == "ping"
    assert connected["deliveries"]["last_verified_at"]

    forge.post(f"/hooks/deploy/{DOMAIN}", content=PUSH_MAIN, headers=signed(secret, PUSH_MAIN))
    assert state(operator)["deliveries"]["last_push_at"]

    forge.post(f"/hooks/deploy/{DOMAIN}", content=PUSH_MAIN, headers=signed("nope", PUSH_MAIN))
    problem = state(operator)
    assert problem["state"] == "problem"
    assert problem["deliveries"]["refused_since_last_verified"] == 1


def test_the_github_app_covering_the_repository_is_reported(
    operator: TestClient, secret: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from noust.integrations.github import service

    installation = SimpleNamespace(
        installation_id=9,
        account="you",
        repository_selection="all",
        settings_url="https://github.com/settings/installations/9",
    )
    monkeypatch.setattr(
        service,
        "status",
        lambda: service.GitHubStatus(
            configured=True, hooks_active=True, installations=[installation]
        ),
    )

    body = state(operator)["github_app"]

    assert body == {
        "configured": True,
        "hooks_active": True,
        "covers_repository": True,
        "account": "you",
        "repository_selection": "all",
        "settings_url": "https://github.com/settings/installations/9",
    }


def test_the_state_of_an_unknown_application_is_a_404(
    operator: TestClient, store: NoustStore
) -> None:
    assert operator.get("/api/apps/nothing.example.com/webhook").status_code == 404
    assert operator.get("/api/apps/nothing.example.com/webhook/received").status_code == 404


# -- who may see what -----------------------------------------------------------------


def test_the_state_and_the_deliveries_need_a_session(forge: TestClient, secret: str) -> None:
    assert forge.get(f"/api/apps/{DOMAIN}/webhook").status_code == 401
    assert forge.get(f"/api/apps/{DOMAIN}/webhook/received").status_code == 401
    assert forge.post(f"/api/apps/{DOMAIN}/webhook/reveal").status_code == 401


def test_showing_the_secret_again_needs_sudo_mode(app: FastAPI, secret: str) -> None:
    client = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    login = client.post(
        "/api/auth/login", json={"token": get_token_manager().generate_master_token()}
    )
    client.headers[CSRF_HEADER_NAME] = login.json()["csrf_token"]

    response = client.post(f"/api/apps/{DOMAIN}/webhook/reveal")

    assert response.status_code == 403
    assert response.json()["error"] == "elevation_required"
    assert secret not in response.text


def test_the_secret_is_shown_again_in_sudo_mode_and_audited(
    operator: TestClient, secret: str, tmp_path: Path
) -> None:
    response = operator.post(f"/api/apps/{DOMAIN}/webhook/reveal")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["secret"] == secret
    assert body["domain"] == DOMAIN
    assert body["hook_url"].endswith(f"/hooks/deploy/{DOMAIN}")
    audit = (tmp_path / "state" / "web-audit.log").read_text()
    assert "hooks.secret.reveal" in audit
    assert secret not in audit


def test_showing_a_secret_that_does_not_exist_says_how_to_create_one(
    operator: TestClient, seeded: App
) -> None:
    response = operator.post(f"/api/apps/{DOMAIN}/webhook/reveal")

    assert response.status_code == 404
    assert "no webhook secret" in response.json()["detail"].lower()
    assert "rotate" in response.json()["detail"]


def test_rotating_gives_a_new_secret_that_reveal_then_shows(
    operator: TestClient, secret: str
) -> None:
    rotated = operator.post(f"/api/apps/{DOMAIN}/webhook-secret").json()["secret"]

    assert rotated != secret
    assert operator.post(f"/api/apps/{DOMAIN}/webhook/reveal").json()["secret"] == rotated


def test_disabling_returns_the_state_to_disabled(operator: TestClient, secret: str) -> None:
    assert operator.delete(f"/api/apps/{DOMAIN}/webhook-secret").status_code == 200

    assert state(operator)["state"] == "disabled"
