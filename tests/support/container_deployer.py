# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_container_deployer.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from noust.core.container_store import ContainerSettings
from noust.core.store import NoustStore
from noust.deployers.container import ContainerDeployer
from noust.deployers.docker_compose import stack_deployer
from noust.deployers.helpers.container_compose import COMPOSE_FILE
from noust.managers.cert_manager import CertManager
from noust.managers.nginx_manager import NginxManager
from noust.managers.webserver import NGINX_BACKEND
from tests.support.container_support import DOMAIN, Docker, install_fakes, root_of

IMAGE = ContainerSettings(
    method="image",
    image="ghcr.io/owner/app:1.4",
    container_port=8080,
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "store" / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def docker(store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> Iterator[Docker]:
    yield from install_fakes(monkeypatch, store)


@pytest.fixture
def web(tmp_path: Path, docker: Docker) -> NginxManager:
    return NginxManager(
        backend=replace(
            NGINX_BACKEND,
            sites_available=tmp_path / "nginx/sites-available",
            sites_enabled=tmp_path / "nginx/sites-enabled",
        )
    )


@pytest.fixture
def certs(tmp_path: Path, docker: Docker) -> CertManager:
    manager = CertManager()
    manager.LETSENCRYPT_DIR = tmp_path / "letsencrypt"
    manager.LIVE_DIR = manager.LETSENCRYPT_DIR / "live"
    manager.config = SimpleNamespace(ssl_email="")  # type: ignore[assignment]
    return manager


@pytest.fixture
def make(
    tmp_path: Path, docker: Docker, web: NginxManager, certs: CertManager
) -> Callable[..., ContainerDeployer]:
    """Build a configured deployer, wired to this test's web server."""

    def build(settings: ContainerSettings | None = None, **options) -> ContainerDeployer:
        deployer = ContainerDeployer(runner=docker)
        deployer.configure(
            DOMAIN,
            options.pop("source", "ghcr.io/owner/app:1.4"),
            port=options.pop("port", 3001),
            ssl=False,
            app_path=root_of(tmp_path),
            container=settings,
            **options,
        )
        deployer.webserver_manager = lambda: web  # type: ignore[method-assign]
        deployer.cert_manager = certs
        deployer._create_systemd_service = lambda **kwargs: None  # type: ignore[method-assign]
        return deployer

    return build


def compose_text(tmp_path: Path) -> dict:
    return yaml.safe_load((root_of(tmp_path) / COMPOSE_FILE).read_text())


def running_image(tmp_path: Path) -> str:
    return compose_text(tmp_path)["services"]["app"]["image"]


def releases(store: NoustStore) -> list[tuple[str, str]]:
    """``(id, status)`` of the application's releases, newest first."""
    app = store.get_app(DOMAIN)
    assert app is not None and app.id is not None
    return [(row.id, row.status) for row in store.list_releases(app.id)]


def update(store: NoustStore, docker: Docker, *, commit: str | None = None) -> ContainerDeployer:
    """The deployer the lifecycle builds to update the application; it has not run yet."""
    app = store.get_app(DOMAIN)
    assert app is not None
    deployer = stack_deployer(app, runner=docker)
    assert isinstance(deployer, ContainerDeployer)
    deployer.previous_commit = commit
    return deployer


def serving_now(docker: Docker, tmp_path: Path) -> None:
    """Make the fake report what the compose file on disk runs as the running container."""
    docker.serving = running_image(tmp_path)
