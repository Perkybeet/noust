# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which reference a registry is asked about for what a container runs.

A container usually says the image it was made from, and that is the reference. A container application
that runs an image from a registry does not: Noust names the image after the application and its release
(``noust-<app>:<release>``) and the registry's reference is in the application's settings. One that
builds from a Dockerfile has no registry to ask, and a container that Docker shows by an id (its tag
moved to another image) has no name to ask by.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.container_store import ContainerSettings, ContainerStore
from noust.core.docker.image_sources import registry_references
from noust.core.docker.models import NOBODY, Container, Owner, OwnerKind
from noust.core.store import App, NoustStore

RELEASE = "20261112-101500-abc1234"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    yield NoustStore(tmp_path / "noust.db")
    NoustStore.reset_instance()


def container(name: str, image: str, owner: Owner = NOBODY) -> Container:
    return Container(id=(name.encode().hex() * 16)[:64], name=name, image=image, owner=owner)


def app_with(store: NoustStore, domain: str, **settings: object) -> Owner:
    store.create_app(App(domain=domain, app_type="container", app_path=f"/var/www/apps/{domain}"))
    ContainerStore(store).save(domain, ContainerSettings(**settings), actor=None)  # type: ignore[arg-type]
    return Owner(OwnerKind.APP, domain)


def test_a_container_says_the_image_it_was_made_from(store: NoustStore) -> None:
    found = registry_references([container("cache", "redis:7-alpine")], store)

    assert found == {"cache": "redis:7-alpine"}


def test_an_image_with_no_tag_is_the_latest(store: NoustStore) -> None:
    assert registry_references([container("cache", "redis")], store) == {"cache": "redis:latest"}


def test_an_image_docker_shows_by_id_has_no_name_to_ask_by(store: NoustStore) -> None:
    found = registry_references([container("cache", "1a2b3c4d5e6f")], store)

    assert found == {}


def test_a_container_application_from_a_registry_is_asked_by_its_settings_image(
    store: NoustStore,
) -> None:
    owner = app_with(store, "status.example.com", method="image", image="louislam/uptime-kuma:1")
    running = container("status-1", f"noust-status-example-com:{RELEASE}", owner)

    assert registry_references([running], store) == {"status-1": "louislam/uptime-kuma:1"}


def test_a_container_application_built_from_a_dockerfile_has_no_registry_to_ask(
    store: NoustStore,
) -> None:
    owner = app_with(store, "api.example.com", method="dockerfile")
    running = container("api-1", f"noust-api-example-com:{RELEASE}", owner)

    assert registry_references([running], store) == {}


def test_what_noust_started_for_itself_is_not_asked(store: NoustStore) -> None:
    helper = container("copy", "alpine:3.20", Owner(OwnerKind.NOUST, "helper"))
    central = container("noust", "ghcr.io/perkybeet/noust:4.3", Owner(OwnerKind.NOUST, "central"))

    assert registry_references([helper, central], store) == {}


def test_a_stack_of_an_application_is_asked_by_what_its_services_say(store: NoustStore) -> None:
    store.create_app(
        App(domain="shop.example.com", app_type="docker-compose", app_path="/var/www/apps/shop")
    )
    owner = Owner(OwnerKind.APP, "shop.example.com", "shop")

    found = registry_references(
        [
            container("shop-db-1", "postgres:16", owner),
            container("shop-web-1", "shop-web:latest", owner),
        ],
        store,
    )

    # What Compose builds is named after the project and has no registry; the check finds that out
    # when the image has no digest of a registry, not here.
    assert found == {"shop-db-1": "postgres:16", "shop-web-1": "shop-web:latest"}


def test_an_unreadable_reference_is_left_out(store: NoustStore) -> None:
    found = registry_references([container("odd", "not a reference")], store)

    assert found == {}
