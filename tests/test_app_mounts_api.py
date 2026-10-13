# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Files and server paths mounted into an application, through the API (checklist B-53).

``tests/test_app_mounts.py`` pins what a mount is; here, how it travels: listing is
reading the application and never shows content, opening a file reveals what may be
a secret, and binding a path of the server into a container is root-equivalent.
"""

# The fixtures of the manager's tests are reused, not copied.
# ruff: noqa: F811

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from noust.core.store import NoustStore
from noust.core.teams import ScopedStore
from noust.managers import app_mounts
from noust.web.api import mounts as mounts_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers, require_elevated
from noust.web.permissions import Permission
from tests.test_app_mounts import (  # noqa: F401  (pytest resolves fixtures by name)
    DOMAIN,
    OwnerFs,
    add_app,
    app,
    root,
    store,
)

ADMIN = [Permission.APPS_READ, Permission.APPS_MANAGE, Permission.SECRETS_REVEAL]


@pytest.fixture(autouse=True)
def wired(store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(mounts_api, "scoped_store", lambda: ScopedStore(store=store))
    monkeypatch.setattr(app_mounts, "get_store", lambda: store)
    monkeypatch.setattr(app_mounts, "get_fs", lambda: OwnerFs())
    yield


def client(*, elevated: bool = True, permissions: list[str] | None = None) -> TestClient:
    api = FastAPI()
    install_error_handlers(api)
    api.include_router(mounts_api.router, prefix="/api/apps")
    session = {
        "type": "session",
        "session_id": "s",
        "scope": "admin",
        "name": "alice",
        "permissions": ADMIN if permissions is None else permissions,
    }
    api.dependency_overrides[get_current_session] = lambda: session

    def elevation() -> dict[str, Any]:
        if not elevated:
            raise HTTPException(status_code=403, detail={"error": "elevation_required"})
        return session

    api.dependency_overrides[require_elevated] = elevation
    return TestClient(api)


def test_a_file_is_kept_listed_and_opened(app: Any, root: Path) -> None:
    put = client().put(
        f"/api/apps/{DOMAIN}/mounts",
        json={"target": "config/a.json", "content": '{"token": "abc"}', "mode": 640},
    )
    listing = client(elevated=False).get(f"/api/apps/{DOMAIN}/mounts").json()
    opened = client().get(f"/api/apps/{DOMAIN}/mounts/content", params={"target": "config/a.json"})

    assert put.status_code == 200, put.text
    assert put.json()["mode"] == "640" and put.json()["exists"] is True
    assert listing["total"] == 1 and listing["container"] is False
    assert "token" not in str(listing)
    assert opened.json()["content"] == '{"token": "abc"}'
    assert (root / "shared" / "config" / "a.json").exists()


def test_opening_a_file_and_changing_a_mount_need_sudo_mode(app: Any) -> None:
    body = {"target": "a.conf", "content": "x"}

    assert client(elevated=False).put(f"/api/apps/{DOMAIN}/mounts", json=body).status_code == 403
    assert (
        client(elevated=False)
        .get(f"/api/apps/{DOMAIN}/mounts/content", params={"target": "a.conf"})
        .status_code
        == 403
    )
    assert (
        client(elevated=False)
        .delete(f"/api/apps/{DOMAIN}/mounts", params={"target": "a.conf"})
        .status_code
        == 403
    )


def test_a_file_mount_needs_its_content_and_a_known_kind(app: Any) -> None:
    no_content = client().put(f"/api/apps/{DOMAIN}/mounts", json={"target": "a.conf"})
    odd_kind = client().put(
        f"/api/apps/{DOMAIN}/mounts", json={"target": "a.conf", "kind": "socket", "content": "x"}
    )

    assert no_content.status_code == 400 and "content" in no_content.json()["fields"]
    assert odd_kind.status_code == 400 and "kind" in odd_kind.json()["fields"]


def test_the_environment_is_refused_naming_the_field(app: Any) -> None:
    response = client().put(
        f"/api/apps/{DOMAIN}/mounts", json={"target": "shared/.env", "content": "A=1"}
    )

    assert response.status_code == 400 and "target" in response.json()["fields"]


def test_binding_a_path_of_the_server_is_root_equivalent(store: NoustStore, tmp_path: Path) -> None:
    add_app(store, tmp_path / "box", app_type="container", identity=None)
    (tmp_path / "assets").mkdir()
    body = {"kind": "path", "source": str(tmp_path / "assets"), "target": "/srv/assets"}

    refused = client().put(f"/api/apps/{DOMAIN}/mounts", json=body)
    allowed = client(permissions=[*ADMIN, Permission.ROOT_EQUIVALENT]).put(
        f"/api/apps/{DOMAIN}/mounts", json=body
    )

    assert refused.status_code == 403
    assert refused.json()["error"] == "permission_denied"
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["read_only"] is True


def test_the_servers_own_state_is_not_bound_even_by_an_administrator(
    store: NoustStore, tmp_path: Path
) -> None:
    add_app(store, tmp_path / "box", app_type="container", identity=None)

    response = client(permissions=[*ADMIN, Permission.ROOT_EQUIVALENT]).put(
        f"/api/apps/{DOMAIN}/mounts",
        json={"kind": "path", "source": "/var/run/docker.sock", "target": "/var/run/docker.sock"},
    )

    assert response.status_code == 400 and "source" in response.json()["fields"]


def test_a_mount_is_removed_and_the_rest_are_returned(app: Any) -> None:
    client().put(f"/api/apps/{DOMAIN}/mounts", json={"target": "a.conf", "content": "x"})
    client().put(f"/api/apps/{DOMAIN}/mounts", json={"target": "b.conf", "content": "y"})

    left = client().delete(f"/api/apps/{DOMAIN}/mounts", params={"target": "a.conf"}).json()

    assert [mount["target"] for mount in left["mounts"]] == ["b.conf"]


def test_an_unknown_application_reads_as_absent() -> None:
    assert client().get("/api/apps/nobody.example.com/mounts").status_code == 404


def test_a_stack_takes_no_mounts(store: NoustStore, tmp_path: Path) -> None:
    add_app(store, tmp_path / "stack", app_type="docker-compose", identity=None)

    response = client().get(f"/api/apps/{DOMAIN}/mounts")

    assert response.status_code == 400 and "takes no mounts" in response.text


def test_the_routes_are_as_public_as_what_they_show() -> None:
    from noust.web.permissions.routes_mounts import ROUTES

    assert ROUTES[("GET", "/api/apps/{domain}/mounts")] == "apps.read"
    assert ROUTES[("GET", "/api/apps/{domain}/mounts/content")] == "secrets.reveal"
    assert ROUTES[("PUT", "/api/apps/{domain}/mounts")] == "apps.manage"
