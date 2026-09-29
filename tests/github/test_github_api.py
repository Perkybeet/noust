# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``/api/integrations/github``: the console's side of the GitHub App.

Reading needs a session; creating the App, recording an installation and
removing the App change what this server trusts, and need sudo mode.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app as build_app
from noust.web.server import get_token_manager
from tests.github.fakes import (
    APP_ID,
    INSTALLATION_ID,
    FakeGitHub,
    token_route,
)

BASE = "/api/integrations/github"


@pytest.fixture
def app(tmp_path: Path, store: NoustStore) -> FastAPI:
    """
    Args:
        tmp_path: Per-test directory.
        store: The store fixture.

    Returns:
        The console application.
    """
    return build_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """
    Args:
        app: The application.

    Returns:
        A signed-in, not elevated client.
    """
    signed_in = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    response = signed_in.post(
        "/api/auth/login", json={"token": get_token_manager().generate_master_token()}
    )
    assert response.status_code == 200, response.text
    signed_in.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return signed_in


def elevate(client: TestClient) -> None:
    """
    Confirm sudo mode.

    Args:
        client: A signed-in client.
    """
    response = client.post(
        "/api/auth/elevate", json={"token": get_token_manager().generate_master_token()}
    )
    assert response.status_code == 200, response.text


def test_the_status_needs_a_session(app: FastAPI) -> None:
    """Anonymous callers learn nothing."""
    anonymous = TestClient(app, client=("testclient", 50000))
    assert anonymous.get(BASE).status_code == 401


def test_status_without_an_app(client: TestClient) -> None:
    """Not configured, no hooks URL."""
    body = client.get(BASE).json()
    assert body["configured"] is False
    assert body["installations"] == []
    assert body["hooks_url"] is None


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("post", f"{BASE}/manifest", {"origin": "http://localhost:8080"}),
        ("post", f"{BASE}/manifest/conversions", {"code": "c", "state": "s"}),
        ("post", f"{BASE}/installations", {"installation_id": 1}),
        ("delete", BASE, None),
    ],
)
def test_trust_changes_need_sudo_mode(
    client: TestClient, method: str, path: str, json: dict | None
) -> None:
    """Without elevation, the console is asked to confirm it's you."""
    response = getattr(client, method)(path, **({"json": json} if json is not None else {}))
    assert response.status_code == 403
    assert response.json()["error"] == "elevation_required"


def test_the_whole_creation_flow(
    client: TestClient, fake_github: FakeGitHub, openssl: FakeRunner
) -> None:
    """Manifest, conversion, installation: the status then shows all of it."""
    elevate(client)
    started = client.post(f"{BASE}/manifest", json={"origin": "http://localhost:8080"})
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["post_url"].startswith("https://github.com/settings/apps/new?state=")
    assert body["manifest"]["redirect_url"] == "http://localhost:8080/integrations/github/callback"

    fake_github.on(
        "POST",
        "/app-manifests/thecode/conversions",
        {
            "id": APP_ID,
            "slug": "wasm-box",
            "name": "wasm-box",
            "owner": {"login": "you", "type": "User"},
            "html_url": "https://github.com/apps/wasm-box",
            "pem": "-----BEGIN RSA PRIVATE KEY-----\nk\n",
            "webhook_secret": None,
        },
        status=201,
    )
    converted = client.post(
        f"{BASE}/manifest/conversions", json={"code": "thecode", "state": body["state"]}
    )
    assert converted.status_code == 200, converted.text
    assert converted.json()["configured"] is True
    assert converted.json()["install_url"] == "https://github.com/apps/wasm-box/installations/new"

    fake_github.on(
        "GET",
        f"/app/installations/{INSTALLATION_ID}",
        {"id": INSTALLATION_ID, "account": {"login": "you", "type": "User"}},
    )
    installed = client.post(f"{BASE}/installations", json={"installation_id": INSTALLATION_ID})
    assert installed.status_code == 200, installed.text
    assert installed.json()["account"] == "you"
    assert [i["installation_id"] for i in client.get(BASE).json()["installations"]] == [
        INSTALLATION_ID
    ]


def test_a_callback_with_a_foreign_state_is_refused(client: TestClient) -> None:
    """400 with the field named, before GitHub is asked."""
    elevate(client)
    response = client.post(f"{BASE}/manifest/conversions", json={"code": "c", "state": "forged"})
    assert response.status_code == 400
    assert "state" in (response.json().get("fields") or {})


def test_github_refusing_is_a_502_with_its_words(
    client: TestClient, github_configured: NoustStore, fake_github: FakeGitHub, openssl: FakeRunner
) -> None:
    """GitHub's message reaches the console verbatim."""
    elevate(client)
    fake_github.on("GET", "/app/installations/5", {"message": "Not Found"}, 404)
    response = client.post(f"{BASE}/installations", json={"installation_id": 5})
    assert response.status_code == 502
    assert "Not Found" in response.text


def test_repositories_and_branches_for_the_wizard(
    client: TestClient, github_configured: NoustStore, fake_github: FakeGitHub, openssl: FakeRunner
) -> None:
    """The wizard's repository and branch pickers."""
    token_route(fake_github)
    fake_github.on(
        "GET",
        r"/installation/repositories\?.*",
        {"repositories": [{"full_name": "you/app", "private": False, "default_branch": "main"}]},
    )
    fake_github.on(
        "GET", r"/repos/you/app/branches\?.*", [{"name": "main", "commit": {"sha": "a"}}]
    )
    repos = client.get(f"{BASE}/repositories").json()
    assert repos["total"] == 1
    assert repos["items"][0]["source"] == "github:you/app"
    branches = client.get(f"{BASE}/repositories/you/app/branches").json()
    assert branches == {"items": [{"name": "main", "protected": False, "commit": "a"}], "total": 1}


def test_removal_returns_where_to_delete_the_app(
    client: TestClient, github_configured: NoustStore
) -> None:
    """Local credentials go; GitHub's page is returned."""
    elevate(client)
    response = client.delete(BASE)
    assert response.status_code == 200
    assert response.json() == {
        "removed": True,
        "settings_url": "https://github.com/settings/apps/wasm-test",
    }
    assert client.get(BASE).json()["configured"] is False


def audit_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    """
    Args:
        caplog: The log capture.

    Returns:
        What reached the ``noust.audit`` logger.
    """
    return [record.getMessage() for record in caplog.records if record.name == "noust.audit"]


def test_every_trust_change_is_audited(
    client: TestClient,
    fake_github: FakeGitHub,
    openssl: FakeRunner,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Creating, installing, syncing and removing the App each leave a named line."""
    elevate(client)
    with caplog.at_level(logging.INFO, logger="noust.audit"):
        started = client.post(
            f"{BASE}/manifest", json={"origin": "http://localhost:8080", "organization": "acme"}
        ).json()
        fake_github.on(
            "POST",
            "/app-manifests/thecode/conversions",
            {
                "id": APP_ID,
                "slug": "wasm-box",
                "name": "wasm-box",
                "owner": {"login": "you", "type": "User"},
                "html_url": "https://github.com/apps/wasm-box",
                "pem": "-----BEGIN RSA PRIVATE KEY-----\nk\n",
                "webhook_secret": None,
            },
            status=201,
        )
        client.post(
            f"{BASE}/manifest/conversions", json={"code": "thecode", "state": started["state"]}
        )
        fake_github.on(
            "GET",
            f"/app/installations/{INSTALLATION_ID}",
            {"id": INSTALLATION_ID, "account": {"login": "you", "type": "User"}},
        )
        client.post(f"{BASE}/installations", json={"installation_id": INSTALLATION_ID})
        fake_github.on("GET", r"/app/installations(\?.*)?", [])
        client.post(f"{BASE}/installations/sync")
        client.delete(BASE)

    lines = audit_lines(caplog)
    actions = [line.split(" ", 1)[0] for line in lines]
    assert actions == [
        "github_manifest_start",
        "github_app_create",
        "github_app_created",
        "github_installation_add",
        "github_installations_sync",
        "github_app_remove",
    ]
    joined = "\n".join(lines)
    assert "organization=acme" in joined
    assert f"app_id={APP_ID}" in joined
    assert f"installation_id={INSTALLATION_ID}" in joined
    assert all("session=" in line for line in lines)
    assert "thecode" not in joined and "PRIVATE KEY" not in joined
