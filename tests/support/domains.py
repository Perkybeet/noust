# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_domains.py`` that other tests build on."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.deployers import domains
from noust.deployers.base import BaseDeployer
from noust.deployers.registry import get_deployer
from noust.managers.cert_manager import CertManager
from noust.managers.nginx_manager import NginxManager
from noust.managers.webserver import NGINX_BACKEND

RELEASE_ID = "20260925-120000-abcdef0"

NGINX_REJECTS = "nginx: [emerg] duplicate listen options for 0.0.0.0:443 in /etc/nginx/x:12\n"


@pytest.fixture
def store(tmp_path: Path) -> Any:
    """A store in the test's directory, installed as the process-wide singleton."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
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


def put_certificate(certs: CertManager, domain: str) -> None:
    """Create the files of a live lineage."""
    live = certs.LIVE_DIR / domain
    live.mkdir(parents=True, exist_ok=True)
    for name in ("fullchain.pem", "privkey.pem", "cert.pem", "chain.pem"):
        (live / name).write_text("-----BEGIN CERTIFICATE-----\n")


class Machine:
    """Deploys applications into the temporary tree, and wires their deployers."""

    def __init__(
        self,
        tmp_path: Path,
        store: NoustStore,
        web: NginxManager,
        certs: CertManager,
        runner: FakeRunner,
    ) -> None:
        self.tmp_path = tmp_path
        self.store = store
        self.web = web
        self.certs = certs
        self.runner = runner

    def wire(self, deployer: BaseDeployer) -> BaseDeployer:
        """Point a deployer at this machine's web server and certificates."""
        deployer._webserver_manager = lambda: self.web  # type: ignore[method-assign]
        deployer.cert_manager = self.certs
        return deployer

    def deployer(self, app_type: str, verbose: bool = False) -> Any:
        """Stand-in for the registry's get_deployer, wiring what it returns."""
        deployer = get_deployer(app_type, verbose=verbose)
        if isinstance(deployer, BaseDeployer):
            self.wire(deployer)
        return deployer

    def deploy(
        self,
        domain: str = "example.com",
        *,
        app_type: str = "static",
        layout: str = "inplace",
        tls: bool = False,
    ) -> App:
        """
        Put an application on disk and in the store, with the site a deploy writes.

        Returns:
            Its row.
        """
        root = self.tmp_path / "apps" / domain
        tree = root / "releases" / RELEASE_ID if layout == "releases" else root
        if app_type == "static":
            (tree / "public").mkdir(parents=True)
            (tree / "public/index.html").write_text("<h1>hi</h1>")
        else:
            tree.mkdir(parents=True)
            (tree / "package.json").write_text('{"name": "app"}')
        if layout == "releases":
            (root / "current").symlink_to(Path("releases") / RELEASE_ID)
        app = self.store.create_app(
            App(
                domain=domain,
                app_type=app_type,
                source=str(root),
                app_path=str(root),
                port=None if app_type == "static" else 3000,
                layout=layout,
                ssl_enabled=tls,
            )
        )
        if tls:
            put_certificate(self.certs, domain)
        deployer = self.deployer(app_type)
        deployer.configure(domain, str(root), port=app.port, app_path=root)
        deployer.refresh_site(with_ssl=tls)
        self.runner.calls.clear()
        return app

    def config(self, domain: str = "example.com") -> str:
        """Read an application's site configuration."""
        return self.web.config_path(domain).read_text()

    def domains(self, domain: str = "example.com") -> list[tuple[str, str]]:
        """List an application's domains as (name, kind)."""
        return [(record.domain, record.kind) for record in self.store.list_domains(domain)]

    def reloaded(self) -> bool:
        """Whether nginx was reloaded since the last deploy."""
        return ("systemctl", "reload", "nginx") in self.runner.calls

    def certbot_orders(self) -> list[tuple[str, ...]]:
        """The certbot issuance commands run since the last deploy."""
        return [call for call in self.runner.calls if call[:2] == ("certbot", "certonly")]


@pytest.fixture
def machine(
    tmp_path: Path,
    store: NoustStore,
    web: NginxManager,
    certs: CertManager,
    runner: FakeRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> Machine:
    """A machine whose deployers the domains module builds through the registry."""
    fake = Machine(tmp_path, store, web, certs, runner)
    monkeypatch.setattr(domains, "get_deployer", fake.deployer)
    return fake


def requested_names(order: tuple[str, ...]) -> list[str]:
    """The ``-d`` arguments of a certbot command."""
    return [order[index + 1] for index, arg in enumerate(order) if arg == "-d"]
