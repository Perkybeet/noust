# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The health check's 4.0 settings through the API and the command line (checklist B-12).

``tests/test_health_check_options.py`` pins what the settings mean; here, how
they travel: ``PATCH /api/apps/{domain}/health`` sets all of it together and
names the field it refuses, ``GET`` shows it, and ``noust app health`` names
only what changes.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.cli.commands import app as app_module
from noust.core import app_health_store
from noust.core.app_health_store import HealthExtras
from noust.core.store import App, NoustStore
from noust.deployers import lifecycle
from noust.web.api import apps as apps_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import require_elevated

DOMAIN = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    """A store of the test's own, where the lifecycle and the command look for it."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    monkeypatch.setattr(lifecycle, "get_store", lambda: instance)
    monkeypatch.setattr(app_module, "get_store", lambda: instance)
    instance.create_app(App(domain=DOMAIN, app_type="nodejs", port=3100, app_path="/srv/shop"))
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def client(store: NoustStore) -> TestClient:
    """The applications router, signed in and elevated."""
    app = FastAPI()
    app.include_router(apps_api.router, prefix="/api/apps")
    session = {"sid": "test", "scope": "admin"}
    app.dependency_overrides[get_current_session] = lambda: session
    app.dependency_overrides[require_elevated] = lambda: session
    return TestClient(app, raise_server_exceptions=False)


def test_the_api_sets_and_shows_a_request_check(client: TestClient) -> None:
    response = client.patch(
        f"/api/apps/{DOMAIN}/health",
        json={
            "path": "/ready",
            "method": "head",
            "scheme": "https",
            "host": "shop.example.com",
            "port": 9443,
            "interval": 3,
            "retries": 4,
            "start_period": 20,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["kind"], body["method"], body["scheme"], body["port"]) == (
        "http",
        "HEAD",
        "https",
        9443,
    )
    assert body["effective_summary"] == "HEAD https://127.0.0.1:9443/ready (Host: shop.example.com)"
    assert client.get(f"/api/apps/{DOMAIN}/health").json() == body


def test_the_api_sets_a_command_check(client: TestClient) -> None:
    response = client.patch(
        f"/api/apps/{DOMAIN}/health",
        json={"kind": "command", "command": "test -s /app/ready", "retries": 5},
    )

    assert response.status_code == 200, response.text
    assert response.json()["effective_summary"] == "command: test -s /app/ready"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"method": "DELETE"}, "method"),
        ({"scheme": "ftp"}, "scheme"),
        ({"host": "a/b"}, "host"),
        ({"port": 0}, "port"),
        ({"interval": 61}, "interval"),
        ({"retries": 0}, "retries"),
        ({"start_period": 601}, "start_period"),
        ({"kind": "command"}, "command"),
        ({"kind": "command", "command": "true", "port": 80}, "port"),
        ({"command": "true"}, "command"),
    ],
)
def test_the_api_refuses_a_bad_setting_and_names_the_field(
    client: TestClient, store: NoustStore, body: dict[str, Any], field: str
) -> None:
    store.set_app_health(DOMAIN, path="/old", expect=None, timeout=None)

    response = client.patch(f"/api/apps/{DOMAIN}/health", json=body)

    assert response.status_code == 400, response.text
    assert field in response.json()["fields"]
    assert store.get_app(DOMAIN).health_path == "/old"  # type: ignore[union-attr]
    assert app_health_store.get_extras(DOMAIN).is_default


def test_the_api_reads_an_unknown_application_as_absent(client: TestClient) -> None:
    assert client.get("/api/apps/nobody.example.com/health").status_code == 404


def test_the_command_line_names_the_extras_and_keeps_the_rest(store: NoustStore) -> None:
    first = CliRunner().invoke(
        root_cli, ["app", "health", DOMAIN, "--method", "HEAD", "--interval", "4"]
    )
    second = CliRunner().invoke(root_cli, ["app", "health", DOMAIN, "--retries", "6"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    got = app_health_store.get_extras(DOMAIN)
    assert (got.method, got.interval, got.retries) == ("HEAD", 4, 6)


def test_the_command_line_turns_the_check_into_a_command_and_back(store: NoustStore) -> None:
    CliRunner().invoke(root_cli, ["app", "health", DOMAIN, "--command", "test -s /ready"])
    assert app_health_store.get_extras(DOMAIN).kind == "command"

    back = CliRunner().invoke(root_cli, ["app", "health", DOMAIN, "--method", "GET"])

    assert back.exit_code == 0, back.output
    got = app_health_store.get_extras(DOMAIN)
    assert (got.kind, got.command) == ("http", None)


def test_the_command_line_reset_forgets_the_extras(store: NoustStore) -> None:
    app_health_store.set_extras(DOMAIN, HealthExtras(retries=4))

    result = CliRunner().invoke(root_cli, ["app", "health", DOMAIN, "--reset"])

    assert result.exit_code == 0, result.output
    assert app_health_store.get_extras(DOMAIN).is_default


def test_the_command_line_refuses_a_bad_value_and_changes_nothing(store: NoustStore) -> None:
    result = CliRunner().invoke(root_cli, ["app", "health", DOMAIN, "--interval", "0"])

    assert result.exit_code != 0
    assert app_health_store.get_extras(DOMAIN).is_default
