# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``GET /api/apps/{domain}/runs``: how an application that exists runs (item 189).

The application's page says it: a service of the system, a container (built from a Dockerfile or by
Railpack, or an image from a registry) or a Compose stack. It is the one answer of
:func:`noust.deployers.how_it_runs.describe_runs`, read by anyone who may read the application.
"""

from __future__ import annotations

from noust.core.container_store import ContainerSettings
from noust.core.store import App, AppStatus, NoustStore, ReleaseRecord
from noust.web.permissions import Permission
from noust.web.permissions.registry import permission_for_route
from tests.support.web_app_container import (  # noqa: F401
    DOMAIN,
    app,
    client,
    deployed,
    store,
)

# A fixture imported from the support module is found by pytest in this module's namespace, and a
# test that asks for it by name looks like a redefinition of an import that is never used.
# ruff: noqa: F811


def create(store: NoustStore, app_type: str, domain: str = DOMAIN) -> None:
    store.create_app(
        App(
            domain=domain,
            app_type=app_type,
            app_path="/var/www/apps/shop",
            source="https://example.com/shop.git",
            port=3001,
            status=AppStatus.RUNNING,
        )
    )


def test_a_service_runs_directly_on_the_server(client, store):
    create(store, "nodejs")

    body = client.get(f"/api/apps/{DOMAIN}/runs").json()

    assert body == {"domain": DOMAIN, "form": "server", "via": None, "image": None}


def test_a_container_built_from_a_dockerfile_says_which_image_serves(client, store):
    deployed(store, ContainerSettings())
    app = store.get_app(DOMAIN)
    assert app is not None and app.id is not None
    store.record_release(
        ReleaseRecord(
            id="20261112-101500-abc1234",
            app_id=app.id,
            created_at="2026-11-12T10:15:00+00:00",
            status="active",
            path="noust-shop-example-com:20261112-101500-abc1234",
        )
    )

    body = client.get(f"/api/apps/{DOMAIN}/runs").json()

    assert body["form"] == "container"
    assert body["via"] == "dockerfile"
    assert body["image"] == "noust-shop-example-com:20261112-101500-abc1234"


def test_a_container_built_by_railpack_says_so(client, store):
    deployed(store, ContainerSettings(method="railpack"))

    body = client.get(f"/api/apps/{DOMAIN}/runs").json()

    assert (body["form"], body["via"]) == ("container", "railpack")


def test_an_image_of_a_registry_says_which(client, store):
    deployed(store, ContainerSettings(method="image", image="ghcr.io/owner/app:1.4"))

    body = client.get(f"/api/apps/{DOMAIN}/runs").json()

    assert (body["form"], body["via"]) == ("container", "image")
    assert body["image"] == "ghcr.io/owner/app:1.4"


def test_a_container_that_nothing_serves_has_no_image_to_name(client, store):
    deployed(store, ContainerSettings())

    body = client.get(f"/api/apps/{DOMAIN}/runs").json()

    assert body["via"] == "dockerfile"
    assert body["image"] is None


def test_a_compose_stack_is_containers_from_a_compose_file(client, store):
    create(store, "docker-compose")

    body = client.get(f"/api/apps/{DOMAIN}/runs").json()

    assert (body["form"], body["via"], body["image"]) == ("container", "compose", None)


def test_an_application_noust_adopted_has_no_form_of_its_own(client, store):
    create(store, "adopted")

    response = client.get(f"/api/apps/{DOMAIN}/runs")

    assert response.status_code == 400
    assert "adopted" in response.json()["detail"]


def test_an_application_nobody_deployed_is_not_found(client, store):
    assert client.get(f"/api/apps/{DOMAIN}/runs").status_code == 404


def test_reading_it_is_reading_the_application():
    assert permission_for_route("GET", "/api/apps/{domain}/runs") == Permission.APPS_READ
