# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Updating, going back and deleting a container application on a central that deploys containers on a NAS.

These are the things the owner asked of Noust on his NAS, and each goes through the one function it always
did (``update_app``, ``activate_release``, ``delete_app``). What is held here is that they work when Docker
and not systemd keeps the application alive and Noust is a container: the update is judged by the health gate
at the address the container answers on, a release that does not answer is put back by the same Docker, going
back to an earlier release recreates it, and deleting an application leaves the machine's web server and
certificates alone (it has none) and no unit behind.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from noust.core.config import Config
from noust.core.container_store import ContainerSettings
from noust.core.exceptions import RolledBackError
from noust.core.store import NoustStore
from noust.deployers import compose_supervisor, docker_compose, lifecycle
from noust.deployers.container import ContainerDeployer
from noust.deployers.helpers.container_compose import COMPOSE_FILE, image_tag
from tests.support.container_support import (
    APP,
    DOMAIN,
    Docker,
    FakeServices,
    install_fakes,
    root_of,
)

IMAGE = ContainerSettings(method="image", image="ghcr.io/owner/app:1.4", container_port=8080)


@pytest.fixture(autouse=True)
def a_container_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
    monkeypatch.setenv("NOUST_DEPLOY_HERE", "containers")
    Config.reset_instance()
    apps = tmp_path / "apps"
    monkeypatch.setattr(Config, "apps_directory", property(lambda _self: apps))
    yield
    Config.reset_instance()


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "store" / "noust.db")
    monkeypatch.setattr(lifecycle, "get_store", lambda: instance)
    monkeypatch.setattr(compose_supervisor, "get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def docker(store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> Iterator[Docker]:
    yield from install_fakes(monkeypatch, store)


@pytest.fixture
def probed(docker: Docker, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Noust is a container of its own, and the stack's container answers on a network they share."""
    urls: list[str] = []

    def probe(url: str, **kwargs: Any) -> bool:
        urls.append(url)
        return docker.healthy

    monkeypatch.setattr(docker_compose, "wait_until_healthy", probe)
    monkeypatch.setattr(compose_supervisor.reach, "own_container_id", lambda: "noustid")
    monkeypatch.setattr(compose_supervisor.reach, "service_address", lambda *a, **k: "172.18.0.3")
    monkeypatch.setattr(compose_supervisor.reach, "container_port", lambda *a, **k: 8080)
    docker.serving = "ghcr.io/owner/app:1.4"
    return urls


@pytest.fixture
def machine(docker: Docker, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """The backup and the source, faked around the real lifecycle; the web server and certbot are not."""
    monkeypatch.setattr(
        lifecycle,
        "RollbackManager",
        lambda verbose=False: SimpleNamespace(create_pre_deploy_backup=lambda **kw: None),
    )
    monkeypatch.setattr(lifecycle, "SourceManager", lambda verbose=False: SimpleNamespace())
    monkeypatch.setattr(lifecycle, "ServiceManager", FakeServices)
    return SimpleNamespace(docker=docker)


@pytest.fixture
def deploy(
    tmp_path: Path, docker: Docker, store: NoustStore, probed: list[str]
) -> Callable[..., ContainerDeployer]:
    def run() -> ContainerDeployer:
        deployer = ContainerDeployer(runner=docker)
        deployer.configure(
            DOMAIN,
            IMAGE.image or "",
            port=None,
            app_path=root_of(tmp_path),
            container=IMAGE,
        )
        deployer.deploy()
        docker.serving = yaml.safe_load((root_of(tmp_path) / COMPOSE_FILE).read_text())["services"][
            "app"
        ]["image"]
        return deployer

    return run


def running(tmp_path: Path) -> str:
    return yaml.safe_load((root_of(tmp_path) / COMPOSE_FILE).read_text())["services"]["app"][
        "image"
    ]


def releases(store: NoustStore) -> list[str]:
    app = store.get_app(DOMAIN)
    assert app is not None and app.id is not None
    return [row.id for row in store.list_releases(app.id)]


def no_systemd(docker: Docker) -> bool:
    return not any(call[0] in ("systemctl", "journalctl") for call in docker.calls)


def test_a_first_deployment_needs_no_unit_no_site_and_no_certificate(
    deploy, machine, docker, store, probed
):
    deploy()

    row = store.get_app(DOMAIN)
    assert row is not None and (row.port, row.webserver, row.ssl_enabled) == (8081, "", False)
    assert probed == ["http://172.18.0.3:8080/"]
    assert FakeServices.started == [] and no_systemd(docker)


def test_an_update_is_judged_at_the_address_the_container_answers_on(
    deploy, machine, docker, store, probed, tmp_path
):
    deploy()
    probed.clear()

    lifecycle.update_app(DOMAIN)

    assert len(releases(store)) == 2
    assert probed and set(probed) == {"http://172.18.0.3:8080/"}
    assert no_systemd(docker) and FakeServices.restarted == []


def test_an_update_that_does_not_answer_leaves_what_served_and_says_so(
    deploy, machine, docker, store, tmp_path
):
    deploy()
    before = running(tmp_path)
    docker.healthy = False
    docker.on_up.append(lambda args: setattr(docker, "healthy", "--no-build" in args))

    with pytest.raises(Exception) as caught:
        lifecycle.update_app(DOMAIN)

    assert "previous ones are back" in str(caught.value) or "back" in str(caught.value)
    assert running(tmp_path) == before
    assert no_systemd(docker)


def test_going_back_to_an_earlier_release_recreates_it_through_docker(
    deploy, machine, docker, store, tmp_path
):
    deploy()
    lifecycle.update_app(DOMAIN)
    first, second = sorted(releases(store))[::-1][1], sorted(releases(store))[::-1][0]
    docker.serving = running(tmp_path)

    outcome = lifecycle.activate_release(DOMAIN, first)

    assert outcome.changed and running(tmp_path) == image_tag(APP, first)
    assert no_systemd(docker)
    assert second != first


def test_a_release_that_does_not_answer_when_going_back_is_replaced_by_the_one_that_served(
    deploy, machine, docker, store, tmp_path
):
    deploy()
    lifecycle.update_app(DOMAIN)
    ordered = [r.id for r in lifecycle.list_releases(DOMAIN)]
    serving = running(tmp_path)
    docker.serving = serving
    recreations: list[int] = []
    docker.healthy = False
    docker.on_up.append(
        lambda args: (recreations.append(1), setattr(docker, "healthy", len(recreations) > 1))
    )

    with pytest.raises(RolledBackError):
        lifecycle.activate_release(DOMAIN, next(i for i in ordered if image_tag(APP, i) != serving))

    assert running(tmp_path) == serving


def test_deleting_it_removes_the_containers_and_leaves_no_unit_site_or_certificate_to_find(
    deploy, machine, docker, store, tmp_path
):
    deploy()
    root = root_of(tmp_path)

    outcome = lifecycle.delete_app(DOMAIN)

    assert outcome.containers_stopped and outcome.certificate_removed is False
    assert any("down" in call for call in docker.compose_calls())
    assert FakeServices.deleted == [] and no_systemd(docker)
    assert not root.exists() and store.get_app(DOMAIN) is None
