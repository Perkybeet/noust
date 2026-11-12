# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the last look at the registries adds to what asks for the operator.

The summary of the Docker zone never asks a registry: it says what the last look found, for the images
that are still in use. A new version that is not a major one is an ``update``, a new major version is a
``major`` (the one a person has to decide), and both go between a stopped container and the disk, which
is where :data:`noust.core.docker.summary.ORDER` puts them.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.docker.gate import DockerGate
from noust.core.docker.owners import Registry
from noust.core.docker_image_checks import ImageVersionStore, VersionRecord
from noust.core.store import NoustStore
from tests.support.docker_machine import DockerMachine

CHECKED = "2026-11-12T10:00:00+00:00"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    yield NoustStore(tmp_path / "noust.db")
    NoustStore.reset_instance()


def record(reference: str, state: str = "newer", change: str = "patch") -> VersionRecord:
    return VersionRecord(
        reference=reference,
        state=state,
        local_digest="sha256:" + "a" * 64,
        remote_digest="sha256:" + "b" * 64 if state == "newer" else "sha256:" + "a" * 64,
        current_version=None,
        latest_version=None,
        latest_created=None,
        change=change if state == "newer" else "",
        floating=reference.endswith(":latest"),
        method="imagetools",
        detail="",
        checked_at=CHECKED,
    )


def machine() -> DockerMachine:
    machine = DockerMachine()
    machine.box("cache", image="redis:7-alpine", policy="unless-stopped")
    machine.box("nextcloud-db-1", image="postgres:latest", policy="unless-stopped")
    machine.box("adminer", image="adminer:latest", policy="unless-stopped")
    machine.box("front", image="nginx:1.27", policy="unless-stopped")
    return machine


def gate(machine: DockerMachine, store: NoustStore) -> DockerGate:
    return DockerGate(
        machine,
        store=store,
        registry=lambda: Registry(),
        disk=lambda path: (1000, 100, 900),
    )


def kinds(found: object) -> list[tuple[str, int]]:
    return [(item.kind, item.count) for item in found.attention]  # type: ignore[attr-defined]


def test_a_new_version_is_an_update_and_a_new_major_is_a_major(store: NoustStore) -> None:
    versions = ImageVersionStore(store)
    versions.save(record("redis:7-alpine", change="patch"))
    versions.save(record("adminer:latest", change="minor"))
    versions.save(record("postgres:latest", change="major"))
    versions.save(record("nginx:1.27", state="current"))

    found = gate(machine(), store).summary()

    assert kinds(found) == [("update", 2), ("major", 1)]
    update, major = found.attention
    assert sorted(update.facts["images"]) == ["adminer:latest", "redis:7-alpine"]
    assert major.facts["images"] == ["postgres:latest"]
    assert update.facts["checked_at"] == CHECKED
    assert update.subject is None


def test_nothing_is_said_when_nobody_looked(store: NoustStore) -> None:
    found = gate(machine(), store).summary()

    assert [item.kind for item in found.attention] == []


def test_what_could_not_be_told_and_what_is_current_ask_for_nothing(store: NoustStore) -> None:
    versions = ImageVersionStore(store)
    versions.save(record("redis:7-alpine", state="unknown"))
    versions.save(record("nginx:1.27", state="current"))

    found = gate(machine(), store).summary()

    assert [item.kind for item in found.attention] == []


def test_a_version_that_already_runs_is_not_new_any_more(store: NoustStore) -> None:
    # The look found redis:7-alpine behind digest b; the application was updated since, and the image
    # that runs now came with digest b.
    ImageVersionStore(store).save(record("redis:7-alpine"))
    box = DockerMachine()
    image = box.image("1", "redis", "7-alpine")
    box.box("cache", image="redis:7-alpine", policy="unless-stopped")
    box.facts(image, repo_digests=["redis@sha256:" + "b" * 64])

    found = gate(box, store).summary()

    assert [item.kind for item in found.attention] == []


def test_an_image_nothing_runs_any_more_is_not_counted(store: NoustStore) -> None:
    ImageVersionStore(store).save(record("mongo:7"))

    found = gate(machine(), store).summary()

    assert [item.kind for item in found.attention] == []


def test_the_versions_come_after_a_stop_and_before_the_disk(store: NoustStore) -> None:
    ImageVersionStore(store).save(record("redis:7-alpine"))
    box = machine()
    box.box(
        "worker",
        image="busybox:1",
        state="exited",
        status="Exited (1) 3 hours ago",
        exit_code=1,
        policy="unless-stopped",
        finished_at="2026-11-12T09:00:00Z",
    )
    low = DockerGate(
        box, store=store, registry=lambda: Registry(), disk=lambda path: (1000, 900, 100)
    )

    found = low.summary()

    assert [item.kind for item in found.attention] == ["stopped", "update", "disk"]


def test_a_container_application_is_counted_by_the_image_of_its_settings(
    store: NoustStore,
) -> None:
    from noust.core.container_store import ContainerSettings, ContainerStore
    from noust.core.docker.owners import AppRecord
    from noust.core.store import App

    store.create_app(
        App(
            domain="status.example.com",
            app_type="container",
            app_path="/var/www/apps/status-example-com",
        )
    )
    ContainerStore(store).save(
        "status.example.com",
        ContainerSettings(method="image", image="louislam/uptime-kuma:1"),
        actor=None,
    )
    ImageVersionStore(store).save(record("louislam/uptime-kuma:1"))
    box = DockerMachine()
    box.box(
        "status-1",
        image="noust-status-example-com:20261112-101500-abc1234",
        project="status-example-com",
        working_dir="/var/www/apps/status-example-com",
    )
    seen = DockerGate(
        box,
        store=store,
        registry=lambda: Registry(
            apps=(AppRecord("status.example.com", "/var/www/apps/status-example-com"),)
        ),
        disk=lambda path: (1000, 100, 900),
    )

    found = seen.summary()

    assert kinds(found) == [("update", 1)]
