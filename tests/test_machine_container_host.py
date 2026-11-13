# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the header of the console counts on the central of a NAS.

The header and the count of the top bar are the tally of the units Noust manages, read from systemd. The
central of a NAS has no systemd, so it said "0 services" and every application stopped. Where Docker keeps
what is deployed alive the count is its containers (the ones of Noust's applications): running, restarting,
and stopped without anybody having stopped them. What is read from systemd on a server is exactly what it
was.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.config import Config
from noust.core.runner import FakeRunner, set_runner
from noust.core.store import App, NoustStore
from noust.managers.service_manager import ServiceManager
from noust.web import machine
from tests.support.docker_machine import BLOG, SHOP, DockerMachine

SHOP_DOMAIN = "shop.example.com"
BLOG_DOMAIN = "blog.example.com"
CALM_DOMAIN = "calm.example.com"
OFF_DOMAIN = "off.example.com"
EMPTY_DOMAIN = "empty.example.com"
CALM = "/var/www/apps/calm-example-com"
OFF = "/var/www/apps/off-example-com"


@pytest.fixture(autouse=True)
def fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    monkeypatch.delenv("NOUST_DEPLOY_HERE", raising=False)
    Config.reset_instance()
    machine._container_units_cache.clear()
    yield
    machine._container_units_cache.clear()
    set_runner(None)
    Config.reset_instance()


@pytest.fixture
def host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
    monkeypatch.setenv("NOUST_DEPLOY_HERE", "containers")
    Config.reset_instance()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    for domain, path in (
        (SHOP_DOMAIN, SHOP),
        (BLOG_DOMAIN, BLOG),
        (CALM_DOMAIN, CALM),
        (OFF_DOMAIN, OFF),
        (EMPTY_DOMAIN, "/var/www/apps/empty-example-com"),
    ):
        instance.create_app(App(domain=domain, app_type="docker-compose", app_path=path))
    yield instance
    NoustStore.reset_instance()


def nas() -> DockerMachine:
    box = DockerMachine()
    box.box("shop-web-1", project="shop-example-com", working_dir=SHOP, policy="unless-stopped")
    box.box("shop-db-1", project="shop-example-com", working_dir=SHOP, policy="unless-stopped")
    box.box(
        "shop-worker-1",
        project="shop-example-com",
        working_dir=SHOP,
        state="restarting",
        status="Restarting (1) 5 seconds ago",
        policy="always",
    )
    # A job that did its work is not a service that stopped.
    box.box(
        "shop-migrate-1",
        project="shop-example-com",
        working_dir=SHOP,
        state="exited",
        status="Exited (0) 2 days ago",
        exit_code=0,
        policy="no",
    )
    box.box(
        "blog-web-1",
        project="blog-example-com",
        working_dir=BLOG,
        state="exited",
        status="Exited (1) 2 hours ago",
        exit_code=1,
        policy="unless-stopped",
    )
    box.box(
        "calm-web-1",
        project="calm-example-com",
        working_dir=CALM,
        policy="unless-stopped",
    )
    box.box(
        "off-web-1",
        project="off-example-com",
        working_dir=OFF,
        state="exited",
        status="Exited (143) 1 hour ago",
        exit_code=143,
        policy="unless-stopped",
    )
    # What nobody of Noust made is not a service Noust manages, and neither is what Compose ran for a
    # hook.
    box.box("somebody-elses", project="theirs", working_dir="/srv/theirs", policy="always")
    box.box(
        "shop-hook-run-1",
        project="shop-example-com",
        working_dir=SHOP,
        oneoff=True,
        policy="no",
    )
    return box


def test_the_units_are_the_containers_of_the_applications_docker_keeps_alive(
    host: None, store: NoustStore
) -> None:
    box = nas()
    set_runner(box)

    rows = machine.fetch_service_states()
    tally = machine._count_units(rows)

    assert {row["name"] for row in rows} == {
        "shop-web-1",
        "shop-db-1",
        "shop-worker-1",
        "blog-web-1",
        "calm-web-1",
        "off-web-1",
    }
    # Running: shop-web, shop-db, calm-web. Failed: blog-web (it ended with 1). Stopped: shop-worker
    # (Docker keeps restarting it) and off-web (somebody stopped it).
    assert (tally.running, tally.failed, tally.stopped, tally.scheduled) == (3, 1, 2, 0)


def test_an_application_is_as_healthy_as_its_worst_container(host: None, store: NoustStore) -> None:
    set_runner(nas())

    states = machine.classify_apps(machine.fetch_service_states())

    assert states == {
        SHOP_DOMAIN: machine.APP_RESTARTING,
        BLOG_DOMAIN: machine.APP_FAILED,
        CALM_DOMAIN: machine.APP_RUNNING,
        OFF_DOMAIN: machine.APP_STOPPED,
        EMPTY_DOMAIN: machine.APP_STOPPED,
    }
    tally = machine._count_apps(machine.fetch_service_states())
    assert (tally.running, tally.failed, tally.stopped, tally.unmanaged) == (1, 1, 3, 0)


def test_nothing_asks_systemd_and_no_stack_is_looked_for_outside_its_unit(
    host: None, store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = nas()
    set_runner(box)
    monkeypatch.setattr(
        ServiceManager,
        "list_services",
        lambda self: pytest.fail("a machine without systemd has no units to list"),
    )
    monkeypatch.setattr(
        machine,
        "_running_outside",
        lambda apps: pytest.fail("a stack's unit is not the thing that keeps it here"),
    )

    machine.classify_apps(machine.fetch_service_states())

    assert not any(call[0] in ("systemctl", "journalctl") for call in box.calls)


def test_a_server_still_reads_its_units_from_systemd(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner()
    set_runner(runner)
    monkeypatch.setattr(
        ServiceManager,
        "list_services",
        lambda self: [{"name": "shop-example-com", "active": "active", "sub": "running"}],
    )

    rows = machine.fetch_service_states()

    assert rows == [{"name": "shop-example-com", "active": "active", "sub": "running"}]
    assert not any(call[0] == "docker" for call in runner.calls)


def test_a_hub_that_was_not_told_to_deploy_here_reads_nothing_from_docker(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
    Config.reset_instance()
    box = nas()
    set_runner(box)
    monkeypatch.setattr(ServiceManager, "list_services", lambda self: [])

    assert machine.fetch_service_states() == []
    assert box.listings == 0


def test_a_docker_that_does_not_answer_is_no_units_and_not_an_error(
    host: None, store: NoustStore
) -> None:
    box = nas()
    box.down = True
    set_runner(box)

    assert machine.fetch_service_states() == []


def test_the_answer_is_reused_for_a_moment_because_the_header_is_read_every_few_seconds(
    host: None, store: NoustStore
) -> None:
    box = nas()
    set_runner(box)

    machine.fetch_service_states()
    machine.fetch_service_states()
    machine.fetch_service_states()

    assert box.listings == 1


def test_the_answer_is_of_the_instance_whoever_asks(host: None, store: NoustStore) -> None:
    from noust.core.teams.scope import Scope, bind

    set_runner(nas())

    with bind(Scope.nothing()):
        rows = machine.fetch_service_states()

    assert len(rows) == 6
