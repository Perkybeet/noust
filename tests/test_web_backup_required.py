# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""``GET``/``PUT /api/apps/{domain}/backup-required``: the backup an update cannot go without (D-17)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.store import App, Database, NoustStore, get_store
from noust.web.permissions import Permission
from noust.web.permissions.routes_app_stack import ROUTES
from tests.test_web_app_stack import make_client

DOMAIN = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """The process-wide store with one application that has a database."""
    NoustStore.reset_instance()
    instance = get_store()
    app = instance.create_app(
        App(domain=DOMAIN, app_type="nodejs", app_path=str(tmp_path / "shop"), port=3000)
    )
    instance.create_database(Database(name="shop", engine="postgresql", app_id=app.id))
    instance.create_app(App(domain="plain.example.com", app_type="nodejs", app_path=str(tmp_path)))
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every audit event recorded."""
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "noust.core.audit.record", lambda event, **kwargs: events.append({"event": event, **kwargs})
    )
    return events


def test_an_application_with_a_database_requires_it_by_default(store: NoustStore) -> None:
    response = make_client().get(f"/api/apps/{DOMAIN}/backup-required")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "domain": DOMAIN,
        "required": True,
        "explicit": False,
        "default_required": True,
        "databases": ["postgresql/shop"],
    }


def test_one_without_a_database_does_not(store: NoustStore) -> None:
    body = make_client().get("/api/apps/plain.example.com/backup-required").json()

    assert body["required"] is False and body["databases"] == []


def test_choosing_it_is_audited_and_null_goes_back_to_the_default(
    store: NoustStore, audited: list[dict[str, Any]]
) -> None:
    client = make_client()
    url = f"/api/apps/{DOMAIN}/backup-required"

    off = client.put(url, json={"required": False})
    back = client.put(url, json={"required": None})

    assert off.json()["required"] is False and off.json()["explicit"] is True
    assert back.json()["required"] is True and back.json()["explicit"] is False
    assert [event["event"] for event in audited] == ["apps.update_backup"] * 2
    assert audited[0]["details"] == {"required": False, "previous": True, "explicit": True}


def test_changing_it_needs_sudo_mode(store: NoustStore) -> None:
    response = make_client(elevated=False).put(
        f"/api/apps/{DOMAIN}/backup-required", json={"required": False}
    )

    assert response.status_code == 403 and "elevation_required" in response.text


def test_an_unknown_application_is_a_404(store: NoustStore) -> None:
    client: TestClient = make_client()

    assert client.get("/api/apps/nobody.example.com/backup-required").status_code == 404
    assert (
        client.put(
            "/api/apps/nobody.example.com/backup-required", json={"required": True}
        ).status_code
        == 404
    )


def test_the_routes_declare_their_permissions() -> None:
    assert ROUTES[("GET", "/api/apps/{domain}/backup-required")] == Permission.APPS_READ
    assert ROUTES[("PUT", "/api/apps/{domain}/backup-required")] == Permission.APPS_MANAGE
