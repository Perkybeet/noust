# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/public`` (checklist C-22).

The contract: planning changes nothing and refuses what could not succeed;
opening needs sudo mode and the operator's acceptance of every warning and
answers with the password once; the password is in no listing; closing needs
sudo mode and undoes it all; a viewer can look and not touch.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.runner import set_runner
from noust.core.store import NoustStore
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager
from tests.test_database_public import PASSWORD, World, build_world
from tests.test_web_databases_api import wire

BODY = {"engine": "postgresql", "database": "appdb"}


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    """A server a database can be made public on, behind the API's registry."""
    built = build_world(tmp_path, monkeypatch)
    wire(monkeypatch, [built.engine])
    try:
        yield built
    finally:
        built.store.close()
        NoustStore.reset_instance()
        set_runner(None)


@pytest.fixture
def app(tmp_path: Path, world: World) -> FastAPI:
    """The console."""
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


def sign_in(app: FastAPI, *, elevate: bool) -> TestClient:
    """
    Sign in with the master token.

    Args:
        app: The console.
        elevate: Confirm sudo mode too.

    Returns:
        A client carrying the CSRF header.
    """
    client = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    login = client.post("/api/auth/login", json={"token": token})
    assert login.status_code == 200, login.text
    client.headers[CSRF_HEADER_NAME] = login.json()["csrf_token"]
    if elevate:
        assert client.post("/api/auth/elevate", json={"token": token}).status_code == 200
    return client


@pytest.fixture
def plain(app: FastAPI) -> TestClient:
    """Signed in, sudo mode not confirmed."""
    return sign_in(app, elevate=False)


@pytest.fixture
def admin(app: FastAPI) -> TestClient:
    """Signed in, in sudo mode."""
    return sign_in(app, elevate=True)


class TestPlan:
    """What opening would do, without doing it."""

    def test_it_changes_nothing_and_says_everything(self, plain: TestClient, world: World) -> None:
        response = plain.post("/api/databases/public/plan", json=BODY)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["port"] == 5433 and body["tls"] == "required"
        assert body["remote_user"] == "appdb_remote"
        assert body["firewall"]["backend"] == "ufw"
        assert any("try the account's password" in line for line in body["warnings"])
        assert world.access.listing() == []
        assert list(world.units.iterdir()) == []

    def test_what_cannot_succeed_is_refused_with_why(
        self, plain: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "noust.managers.database.public_access.security_profile.database_remote_listen_allowed",
            lambda: False,
        )
        response = plain.post("/api/databases/public/plan", json=BODY)
        assert response.status_code == 400
        assert "ENS profile" in response.text

    def test_a_port_out_of_range_is_a_validation_error(self, plain: TestClient) -> None:
        response = plain.post("/api/databases/public/plan", json={**BODY, "port": 70000})
        assert response.status_code == 422

    def test_it_needs_a_session(self, app: FastAPI) -> None:
        anonymous = TestClient(app, client=("testclient", 50001))
        assert anonymous.post("/api/databases/public/plan", json=BODY).status_code in (401, 403)


class TestOpen:
    """Sudo mode, acceptance, then done, once."""

    def test_it_needs_sudo_mode(self, plain: TestClient, world: World) -> None:
        response = plain.post("/api/databases/public", json={**BODY, "confirm": True})
        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"
        assert world.access.listing() == []

    def test_without_the_acceptance_it_answers_with_every_warning_and_changes_nothing(
        self, admin: TestClient, world: World
    ) -> None:
        response = admin.post("/api/databases/public", json=BODY)
        assert response.status_code == 400, response.text
        body = response.json()
        assert body["error"] == "confirmation_required"
        assert any("port 5433" in line for line in body["warnings"])
        assert world.access.listing() == []
        assert list(world.units.iterdir()) == []
        assert world.state["accounts"] == set()

    def test_accepted_it_opens_and_shows_the_password_once(
        self, admin: TestClient, world: World
    ) -> None:
        response = admin.post(
            "/api/databases/public",
            json={**BODY, "confirm": True, "sources": ["203.0.113.7"], "port": 6543},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["exposure"]["port"] == 6543
        assert body["exposure"]["state"] == "exposed"
        assert body["exposure"]["sources"] == ["203.0.113.7/32"]
        assert body["connection"]["password"] == PASSWORD
        assert body["connection"]["options"] == {"sslmode": "require"}
        assert [rule for rule in world.runner.rules if "6543" in rule]

    def test_the_listing_and_the_detail_never_carry_the_password(self, admin: TestClient) -> None:
        admin.post("/api/databases/public", json={**BODY, "confirm": True})
        listing = admin.get("/api/databases/public")
        assert listing.status_code == 200
        assert listing.json()["total"] == 1
        assert listing.json()["exposures"][0]["listening"] is True
        detail = admin.get("/api/databases/public/postgresql/appdb")
        assert detail.json()["public"] is True
        assert detail.json()["connection"]["password"] is None
        assert PASSWORD not in listing.text + detail.text

    def test_a_private_database_says_so(self, plain: TestClient) -> None:
        assert plain.get("/api/databases/public/postgresql/appdb").json() == {
            "public": False,
            "exposure": None,
            "connection": None,
        }

    def test_the_password_is_shown_again_only_in_sudo_mode(
        self, admin: TestClient, plain: TestClient
    ) -> None:
        admin.post("/api/databases/public", json={**BODY, "confirm": True})
        refused = plain.post("/api/databases/public/postgresql/appdb/reveal")
        assert refused.status_code == 403
        shown = admin.post("/api/databases/public/postgresql/appdb/reveal")
        assert shown.status_code == 200, shown.text
        assert shown.json()["password"] == PASSWORD

    def test_a_second_opening_is_refused(self, admin: TestClient) -> None:
        admin.post("/api/databases/public", json={**BODY, "confirm": True})
        again = admin.post("/api/databases/public", json={**BODY, "confirm": True})
        assert again.status_code == 400
        assert "already reachable" in again.text


class TestClose:
    """Sudo mode, then all of it undone."""

    def test_it_needs_sudo_mode(self, admin: TestClient, plain: TestClient, world: World) -> None:
        admin.post("/api/databases/public", json={**BODY, "confirm": True})
        assert plain.delete("/api/databases/public/postgresql/appdb").status_code == 403
        assert world.access.listing()

    def test_it_makes_the_database_private_again(self, admin: TestClient, world: World) -> None:
        admin.post("/api/databases/public", json={**BODY, "confirm": True})
        response = admin.delete("/api/databases/public/postgresql/appdb")
        assert response.status_code == 200, response.text
        assert response.json()["removed"] == ["firewall", "proxy", "tls", "account"]
        assert world.access.listing() == []
        assert world.runner.rules == ["ufw allow 22/tcp"]
        assert list(world.units.iterdir()) == []

    def test_closing_what_is_private_says_so(self, admin: TestClient) -> None:
        response = admin.delete("/api/databases/public/postgresql/appdb")
        assert response.status_code == 200
        assert response.json()["already_private"] is True
