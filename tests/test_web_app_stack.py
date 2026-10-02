# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for a Compose stack's settings over the API (``/api/apps/{domain}/...``).

``PATCH /backup-before-update`` is ``noust app backup-before-update DOMAIN
on|off`` and ``GET``/``POST /headless`` is ``noust app headless DOMAIN``: each
route is a translation to the one function the command calls
(:func:`~noust.managers.stack_databases.set_backup_before_update`,
:func:`~noust.deployers.docker_compose.headless_check` and
:func:`~noust.deployers.docker_compose.make_headless`), whose rules and audit
are pinned in their own tests. What these own: the routes reach the same
state, removing a site is never implied, both changes need sudo mode, and each
route declares its permission.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from noust.core.runner import FakeRunner, set_runner
from noust.core.store import App, NoustStore, get_store
from noust.deployers import docker_compose
from noust.managers.nginx_manager import NginxManager
from noust.managers.webserver import NGINX_BACKEND
from noust.web.api import app_stack as app_stack_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers, require_elevated
from noust.web.permissions import Permission
from noust.web.permissions.routes_app_stack import ROUTES

DOMAIN = "licitaciones.example.com"
WEB = "shop.example.com"
FIXTURES = Path(__file__).parent / "fixtures" / "compose"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """The process-wide store, with a worker registered by 1.x and a Node web."""
    NoustStore.reset_instance()
    instance = get_store()
    root = tmp_path / "apps" / "licitaciones"
    root.mkdir(parents=True)
    shutil.copy(FIXTURES / "licitaciones.docker-compose.yml", root / "docker-compose.yml")
    instance.create_app(
        App(domain=DOMAIN, app_type="docker-compose", app_path=str(root), port=3000)
    )
    instance.create_app(App(domain=WEB, app_type="nodejs", app_path=str(tmp_path / "s")))
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def web(tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> NginxManager:
    """An nginx manager over a temporary tree, holding the site 1.x wrote for the worker."""
    set_runner(FakeRunner())
    manager = NginxManager(
        backend=replace(
            NGINX_BACKEND,
            sites_available=tmp_path / "nginx/sites-available",
            sites_enabled=tmp_path / "nginx/sites-enabled",
        )
    )
    manager.create_site(DOMAIN, template="proxy", context={"port": 3000})
    manager.enable_site(DOMAIN)
    monkeypatch.setattr(docker_compose, "NginxManager", lambda *a, **k: manager)
    yield manager
    set_runner(None)


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every audit event recorded."""
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "noust.core.audit.record", lambda event, **kwargs: events.append({"event": event, **kwargs})
    )
    return events


def make_client(*, elevated: bool = True) -> TestClient:
    """A client for the router alone, authenticated, elevated or not."""
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(app_stack_api.router, prefix="/api/apps")
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    app.dependency_overrides[get_current_session] = lambda: session

    def elevation() -> dict[str, Any]:
        if not elevated:
            raise HTTPException(status_code=403, detail={"error": "elevation_required"})
        return session

    app.dependency_overrides[require_elevated] = elevation
    return TestClient(app)


class TestBackupBeforeUpdate:
    def test_switching_it_off_and_on_answers_what_it_was(
        self, store: NoustStore, audited: list[dict[str, Any]]
    ) -> None:
        client = make_client()
        url = f"/api/apps/{DOMAIN}/backup-before-update"

        off = client.patch(url, json={"enabled": False})

        assert off.status_code == 200, off.text
        assert off.json() == {"domain": DOMAIN, "backup_before_update": False, "previous": True}
        app = store.get_app(DOMAIN)
        assert app is not None and app.backup_before_update is False
        assert [event["event"] for event in audited] == ["apps.backup_before_update"]

        on = client.patch(url, json={"enabled": True})
        assert on.json() == {"domain": DOMAIN, "backup_before_update": True, "previous": False}

    def test_an_application_that_is_not_a_stack_is_refused_in_the_setter_s_words(
        self, store: NoustStore
    ) -> None:
        response = make_client().patch(
            f"/api/apps/{WEB}/backup-before-update", json={"enabled": False}
        )

        assert response.status_code == 400
        assert "not a Docker Compose application" in response.json()["detail"]

    def test_an_unknown_application_is_404(self, store: NoustStore) -> None:
        response = make_client().patch(
            "/api/apps/nope.example.com/backup-before-update", json={"enabled": False}
        )

        assert response.status_code == 404

    def test_it_needs_sudo_mode(self, store: NoustStore) -> None:
        response = make_client(elevated=False).patch(
            f"/api/apps/{DOMAIN}/backup-before-update", json={"enabled": False}
        )

        assert response.status_code == 403
        app = store.get_app(DOMAIN)
        assert app is not None and app.backup_before_update is True


class TestHeadless:
    def test_the_check_reads_without_changing_anything(
        self, store: NoustStore, web: NginxManager
    ) -> None:
        response = make_client(elevated=False).get(f"/api/apps/{DOMAIN}/headless")

        assert response.status_code == 200, response.text
        assert response.json() == {
            "domain": DOMAIN,
            "headless": True,
            "recorded_port": 3000,
            "site_retirable": True,
        }
        app = store.get_app(DOMAIN)
        assert app is not None and app.port == 3000

    def test_the_site_is_kept_unless_the_request_says_to_remove_it(
        self, store: NoustStore, web: NginxManager, audited: list[dict[str, Any]]
    ) -> None:
        response = make_client().post(f"/api/apps/{DOMAIN}/headless", json={})

        assert response.status_code == 200, response.text
        assert response.json() == {"domain": DOMAIN, "previous_port": 3000, "site": "kept"}
        assert web.site_exists(DOMAIN)
        app = store.get_app(DOMAIN)
        assert app is not None and app.port is None
        assert [event["event"] for event in audited] == ["apps.headless"]

    def test_the_site_is_removed_when_asked(self, store: NoustStore, web: NginxManager) -> None:
        response = make_client().post(f"/api/apps/{DOMAIN}/headless", json={"remove_site": True})

        assert response.json()["site"] == "removed"
        assert not web.site_exists(DOMAIN)

    def test_a_web_keeps_its_port_with_the_reason(
        self, store: NoustStore, web: NginxManager
    ) -> None:
        response = make_client().post(f"/api/apps/{WEB}/headless", json={})

        assert response.status_code == 400
        assert "not a Docker Compose stack" in response.json()["detail"]

    def test_it_needs_sudo_mode(self, store: NoustStore, web: NginxManager) -> None:
        response = make_client(elevated=False).post(f"/api/apps/{DOMAIN}/headless", json={})

        assert response.status_code == 403
        app = store.get_app(DOMAIN)
        assert app is not None and app.port == 3000


def test_every_route_declares_its_permission() -> None:
    declared = {
        (method, f"/api/apps{route.path}")
        for route in app_stack_api.router.routes
        for method in getattr(route, "methods", ())
    }

    assert declared == set(ROUTES)
    assert ROUTES[("GET", "/api/apps/{domain}/headless")] == Permission.APPS_READ
    assert ROUTES[("POST", "/api/apps/{domain}/headless")] == Permission.APPS_MANAGE
    assert ROUTES[("PATCH", "/api/apps/{domain}/backup-before-update")] == Permission.APPS_MANAGE


def test_an_application_says_whether_its_update_copies_its_databases(store: NoustStore) -> None:
    """The console's switch reads its state from the application itself."""
    from noust.web.api import apps as apps_api

    store.set_app_backup_before_update(DOMAIN, False)
    app = store.get_app(DOMAIN)
    assert app is not None

    info = apps_api._to_app_info(
        app, _State(), {}, None, webhook_enabled=False, last_deployment=None
    )

    assert info.backup_before_update is False


class _State:
    """The resolved state, as far as the conversion reads it."""

    label = "running"
    healthy = True
    detail = ""
