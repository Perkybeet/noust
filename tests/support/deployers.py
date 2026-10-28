# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_deployers.py`` that other tests build on."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore


@pytest.fixture
def store(tmp_path: Path):
    """
    Provide an isolated store, installed as the process-wide singleton.

    Args:
        tmp_path: Per-test temporary directory.

    Yields:
        The store the deployers under test will write to.
    """
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    yield instance
    NoustStore.reset_instance()


class FakeWebServer:
    """Stands in for NginxManager/ApacheManager."""

    def __init__(self) -> None:
        self.sites: dict[str, dict[str, Any]] = {}
        self.reloads = 0
        self.running = True

    def is_running(self) -> bool:
        return self.running

    def site_exists(self, domain: str) -> bool:
        return domain in self.sites

    def site_is_noust(self, domain: str) -> bool:
        # Every site this double holds was written by a deployer.
        return domain in self.sites

    def create_site(self, domain: str, template: str, context: dict[str, Any]) -> bool:
        self.sites[domain] = {"template": template, "context": context}
        return True

    def update_site(self, domain: str, template: str, context: dict[str, Any]) -> bool:
        self.sites[domain] = {"template": template, "context": context}
        return True

    def enable_site(self, domain: str) -> bool:
        return True

    def disable_site(self, domain: str) -> bool:
        return True

    def delete_site(self, domain: str) -> bool:
        self.sites.pop(domain, None)
        return True

    def reload(self) -> bool:
        self.reloads += 1
        return True

    def load_configuration(self) -> str | None:
        # What a deploy asks after writing a site: the web server is running and loads it.
        self.reloads += 1
        return None


class FakeServiceManager:
    """Stands in for ServiceManager."""

    def __init__(self) -> None:
        self.units: dict[str, dict[str, Any]] = {}
        self.started: list[str] = []

    def create_service(self, name: str, **kwargs: Any) -> bool:
        self.units[name] = kwargs
        return True

    def enable(self, name: str) -> bool:
        return True

    def start(self, name: str) -> bool:
        self.started.append(name)
        return True

    def stop(self, name: str) -> bool:
        return True

    def get_status(self, name: str) -> dict[str, Any]:
        return {"exists": name in self.units}

    def delete_service(self, name: str) -> bool:
        self.units.pop(name, None)
        return True


class FakeCertManager:
    """Stands in for CertManager."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.obtained: list[str] = []

    def obtain(self, domain: str, **kwargs: Any) -> bool:
        if self.fail:
            raise DeploymentError("certbot said no")
        self.obtained.append(domain)
        return True

    def cert_exists(self, domain: str) -> bool:
        return domain in self.obtained


def build_deployer(
    deployer_class: type,
    tmp_path: Path,
    *,
    domain: str = "app.example.com",
    ssl: bool = False,
) -> Any:
    """
    Build a deployer wired to fakes for every manager it touches.

    Args:
        deployer_class: The deployer to instantiate.
        tmp_path: Directory the application is deployed into.
        domain: Domain to deploy.
        ssl: Whether to request a certificate.

    Returns:
        The configured deployer, with ``web``, ``services`` and ``certs``
        attributes exposing the fakes for assertions.
    """
    # The pipeline now runs chown/chmod itself, so every deploy() needs a
    # runner that answers instead of the blocked process-wide one.
    deployer = deployer_class(verbose=False, runner=FakeRunner())
    deployer.configure(
        domain,
        "https://github.com/example/app.git",
        port=3000,
        ssl=ssl,
        app_path=tmp_path / "app",
    )

    web = FakeWebServer()
    services = FakeServiceManager()
    certs = FakeCertManager()
    deployer._webserver_manager = lambda: web  # type: ignore[method-assign]
    deployer.service_manager = services
    deployer.cert_manager = certs
    deployer.pre_flight_check = lambda: True  # type: ignore[method-assign]

    deployer.web = web
    deployer.services = services
    deployer.certs = certs
    return deployer
