# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where an application is reached, as the console and the command line say it.

Behind a site an application is reached by its domain, over HTTPS when it has a certificate. On a container
host it has no site, and is reached at ``http://<the machine>:<port>``. ``GET /api/apps/{domain}/address``
says which, and with what, so the console shows the link and the line to paste into the NAS's own proxy
without working out for itself what the machine has; ``noust status`` prints the same.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.config import Config
from noust.core.runner import FakeRunner, set_runner
from noust.core.store import App, NoustStore
from noust.core.teams import ScopedStore
from noust.web.api import app_address
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers

DOMAIN = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    monkeypatch.delenv("NOUST_DEPLOY_HERE", raising=False)
    Config.reset_instance()
    monkeypatch.setattr("noust.web.api.deps.scoped_store", lambda: ScopedStore(store=instance))
    instance.create_app(
        App(domain=DOMAIN, app_type="container", port=8081, app_path="/x", ssl_enabled=True)
    )
    runner = FakeRunner()
    runner.script(["docker", "info", "--format", "{{.Name}}"], stdout="nas-oficina\n")
    set_runner(runner)
    yield instance
    set_runner(None)
    Config.reset_instance()
    NoustStore.reset_instance()


@pytest.fixture
def container_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
    monkeypatch.setenv("NOUST_DEPLOY_HERE", "containers")
    Config.reset_instance()


@pytest.fixture
def client(store: NoustStore) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(app_address.router, prefix="/api/apps")
    app.dependency_overrides[get_current_session] = lambda: {"sid": "s", "scope": "admin"}
    return TestClient(app)


def test_on_a_container_host_it_is_the_port_of_the_machine(
    client: TestClient, container_host: None
) -> None:
    response = client.get(f"/api/apps/{DOMAIN}/address")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "domain": DOMAIN,
        "kind": "port",
        "url": "http://nas-oficina:8081",
        "port": 8081,
    }


def test_on_a_server_it_is_the_domain_and_https_when_there_is_a_certificate(
    client: TestClient,
) -> None:
    assert client.get(f"/api/apps/{DOMAIN}/address").json() == {
        "domain": DOMAIN,
        "kind": "site",
        "url": f"https://{DOMAIN}",
        "port": 8081,
    }


def test_an_application_that_is_not_there_is_a_404(client: TestClient) -> None:
    assert client.get("/api/apps/other.example.com/address").status_code == 404


def test_a_stack_that_serves_no_web_has_no_address(
    client: TestClient, store: NoustStore, container_host: None
) -> None:
    store.create_app(App(domain="jobs.example.com", app_type="docker-compose", app_path="/y"))

    body = client.get("/api/apps/jobs.example.com/address").json()

    assert body["kind"] == "none" and body["url"] is None


def test_the_command_line_prints_it_with_the_status(
    store: NoustStore, container_host: None
) -> None:
    result = CliRunner().invoke(root_cli, ["status", DOMAIN])

    assert result.exit_code == 0, result.output
    assert "http://nas-oficina:8081" in result.output
