# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_docker_compose_sites.py`` that other tests build on."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.deployers import docker_compose
from noust.deployers.docker_compose import DockerComposeDeployer
from noust.managers.cert_manager import CertManager
from noust.managers.nginx_manager import NginxManager
from noust.managers.webserver import NGINX_BACKEND

DOMAIN = "proggest.example.com"
FIXTURES = Path(__file__).parent.parent / "fixtures" / "compose"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    """A store in the test's directory, where every module looks."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    monkeypatch.setattr(docker_compose, "get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def web(tmp_path: Path, runner: FakeRunner, store: NoustStore) -> NginxManager:
    """An nginx manager writing into a temporary configuration tree."""
    return NginxManager(
        backend=replace(
            NGINX_BACKEND,
            sites_available=tmp_path / "nginx/sites-available",
            sites_enabled=tmp_path / "nginx/sites-enabled",
        )
    )


@pytest.fixture
def certs(tmp_path: Path, runner: FakeRunner, store: NoustStore) -> CertManager:
    """A certificate manager whose letsencrypt tree is temporary."""
    manager = CertManager()
    manager.LETSENCRYPT_DIR = tmp_path / "letsencrypt"
    manager.LIVE_DIR = manager.LETSENCRYPT_DIR / "live"
    manager.config = SimpleNamespace(ssl_email="")  # type: ignore[assignment]
    return manager


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Proggest's tree: its production compose file, as committed."""
    path = tmp_path / "apps" / "proggest"
    path.mkdir(parents=True)
    shutil.copy(FIXTURES / "proggest.docker-compose.prod.yml", path / "docker-compose.prod.yml")
    # The one value its file requires (`${PERSONNEL_ENCRYPTION_KEY:?...}`): Proggest's own `.env`
    # has it, and a deploy refuses before building without it.
    (path / ".env").write_text("PERSONNEL_ENCRYPTION_KEY=0123456789abcdef\n")
    return path


@pytest.fixture
def stack(
    root: Path,
    runner: FakeRunner,
    web: NginxManager,
    certs: CertManager,
    store: NoustStore,
    monkeypatch: pytest.MonkeyPatch,
) -> DockerComposeDeployer:
    """The deployer over Proggest's tree, wired to this test's web server and certificates."""
    from noust.deployers.helpers.sandbox import set_compose_exception

    # Proggest's backend mounts the Docker socket, which a new stack needs
    # an operator's recorded reason for.
    set_compose_exception(
        DOMAIN, allowed=True, actor="test", reason="the backend reads its containers", store=store
    )
    deployer = DockerComposeDeployer(runner=runner)
    deployer.configure(DOMAIN, "https://example.com/proggest.git", app_path=root)
    wire(deployer, web, certs)
    # The tree is already in place, and nothing here is about systemd.
    monkeypatch.setattr(deployer, "_fetch_source", lambda: None)
    monkeypatch.setattr(deployer, "_create_systemd_service", lambda: None)
    monkeypatch.setattr(deployer, "_start_and_verify", lambda: None)
    deployer.deploy_target = SimpleNamespace(undo_fetch=lambda fs, log: None)  # type: ignore[assignment]
    return deployer


def wire(deployer: DockerComposeDeployer, web: NginxManager, certs: CertManager) -> None:
    """Point a compose deployer at the test's web server and certificates."""
    deployer.webserver_manager = lambda: web  # type: ignore[method-assign]
    deployer.cert_manager = certs


def deployed_row(store: NoustStore, root: Path, port: int | None = 3001) -> App:
    """The row of the stack as deployed."""
    return store.create_app(
        App(
            domain=DOMAIN,
            app_type="docker-compose",
            app_path=str(root),
            port=port,
            webserver="nginx",
            status="running",
        )
    )
