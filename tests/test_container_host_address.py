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
    body = response.json()
    hint = body.pop("proxy_hint")
    assert body == {
        "domain": DOMAIN,
        "kind": "port",
        "url": "http://nas-oficina:8081",
        "port": 8081,
    }
    assert hint["target"] == "http://127.0.0.1:8081"


def test_on_a_server_it_is_the_domain_and_https_when_there_is_a_certificate(
    client: TestClient,
) -> None:
    assert client.get(f"/api/apps/{DOMAIN}/address").json() == {
        "domain": DOMAIN,
        "kind": "site",
        "url": f"https://{DOMAIN}",
        "port": 8081,
        "proxy_hint": None,
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


# -- The rule for the proxy of the NAS itself ----------------------------------------------------


def hint_of(client: TestClient, domain: str = DOMAIN) -> dict:
    response = client.get(f"/api/apps/{domain}/address")
    assert response.status_code == 200, response.text
    return response.json()["proxy_hint"]


def test_the_hint_is_the_destination_the_headers_and_whether_it_needs_a_websocket(
    client: TestClient, container_host: None
) -> None:
    hint = hint_of(client)

    assert (hint["target"], hint["scheme"], hint["host"], hint["port"]) == (
        "http://127.0.0.1:8081",
        "http",
        "127.0.0.1",
        8081,
    )
    assert hint["websocket"] is True
    assert hint["read_timeout_seconds"] == 60
    by_name = {header["name"]: header for header in hint["headers"]}
    assert by_name["Host"] == {"name": "Host", "meaning": "public_host", "nginx": "$host"}
    assert by_name["Upgrade"]["meaning"] == "upgrade_request"
    assert by_name["Connection"]["nginx"] == '"upgrade"'
    assert list(by_name) == [
        "Host",
        "X-Real-IP",
        "X-Forwarded-For",
        "X-Forwarded-Proto",
        "X-Forwarded-Host",
        "X-Forwarded-Port",
        "Upgrade",
        "Connection",
    ]


def test_the_headers_are_what_a_site_of_noust_passes_to_the_application() -> None:
    from noust.deployers.helpers.edge import PROXY_HEADERS

    template = (
        Path(__file__).parent.parent / "src/noust/templates/nginx/proxy.conf.j2"
    ).read_text()

    for header in PROXY_HEADERS:
        assert f"proxy_set_header {header.name} {header.nginx};" in template, header


def test_no_hint_where_a_site_serves_it_or_nothing_is_served(
    client: TestClient, store: NoustStore, container_host: None
) -> None:
    store.create_app(App(domain="jobs.example.com", app_type="docker-compose", app_path="/y"))

    assert hint_of(client, "jobs.example.com") is None


def test_a_stack_bound_to_an_address_of_the_machine_is_reached_at_that_address(
    client: TestClient, store: NoustStore, container_host: None, tmp_path: Path
) -> None:
    stack = tmp_path / "stack"
    stack.mkdir()
    (stack / "compose.yaml").write_text(
        "services:\n  web:\n    image: nginx\n    ports:\n      - '192.168.1.160:8090:80'\n"
    )
    store.create_app(
        App(domain="lan.example.com", app_type="docker-compose", app_path=str(stack), port=8090)
    )

    hint = hint_of(client, "lan.example.com")

    assert hint["target"] == "http://192.168.1.160:8090" and hint["host"] == "192.168.1.160"


def test_a_stack_bound_to_loopback_or_to_every_address_is_reached_on_loopback(
    client: TestClient, store: NoustStore, container_host: None, tmp_path: Path
) -> None:
    for index, binding in enumerate(("127.0.0.1:", "0.0.0.0:", "")):
        stack = tmp_path / f"stack{index}"
        stack.mkdir()
        (stack / "compose.yaml").write_text(
            f"services:\n  web:\n    image: nginx\n    ports:\n      - '{binding}8091:80'\n"
        )
        domain = f"s{index}.example.com"
        store.create_app(
            App(domain=domain, app_type="docker-compose", app_path=str(stack), port=8091)
        )

        assert hint_of(client, domain)["target"] == "http://127.0.0.1:8091", binding
