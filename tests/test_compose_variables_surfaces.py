# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The variables of a compose file through the API and the command line (checklist B-47).

``tests/test_compose_variables.py`` pins how a file is read and judged; here, how it travels:
``GET /api/apps/{domain}/compose/variables`` and ``noust app compose variables`` answer the same
report, never with a value, and only for a stack.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.store import App, NoustStore
from noust.web.api import app_compose
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers

DOMAIN = "stack.example.com"
COMPOSE = """
services:
  web:
    image: nginx
    ports: ['8080:80']
    environment:
      DB_PASSWORD: ${DB_PASSWORD:?set the database password}
      SECRET: ${SECRET_TOKEN:?}
      MODE: ${MODE:-production}
      NOTE: ${LEFT_ALONE}
"""


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    """A store holding one stack with a compose file that asks for variables."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    root = tmp_path / "apps" / "stack-example-com"
    root.mkdir(parents=True)
    (root / "docker-compose.yml").write_text(COMPOSE)
    (root / ".env").write_text("DB_PASSWORD=hunter2-secret\nMODE=staging\n")
    instance.create_app(
        App(domain=DOMAIN, app_type="docker-compose", source="x", app_path=str(root))
    )
    instance.create_app(App(domain="site.example.com", app_type="nodejs", app_path=str(root)))

    class NoUnit:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def get_service_config(self, name: str) -> str | None:
            return None

    monkeypatch.setattr("noust.deployers.docker_compose.ServiceManager", NoUnit)
    # The server's own environment is not the stack's: nothing there answers for it.
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    monkeypatch.delenv("SECRET_TOKEN", raising=False)
    monkeypatch.delenv("MODE", raising=False)
    yield instance
    NoustStore.reset_instance()


def client() -> TestClient:
    """A client for the router alone, authenticated."""
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(app_compose.router, prefix="/api/apps")
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    app.dependency_overrides[get_current_session] = lambda: session
    return TestClient(app)


def test_the_api_lists_what_the_file_asks_for_and_what_the_stack_has(store: NoustStore) -> None:
    body = client().get(f"/api/apps/{DOMAIN}/compose/variables").json()

    assert body["file"] == "docker-compose.yml"
    assert body["missing"] == ["SECRET_TOKEN"]
    assert body["unset"] == ["LEFT_ALONE"]
    states = {variable["name"]: variable for variable in body["variables"]}
    assert states["DB_PASSWORD"]["status"] == "set" and states["DB_PASSWORD"]["source"] == "env"
    assert states["MODE"]["status"] == "set"
    assert states["SECRET_TOKEN"]["status"] == "missing" and states["SECRET_TOKEN"]["required"]
    assert states["DB_PASSWORD"]["message"] == "set the database password"
    assert states["DB_PASSWORD"]["used_in"] == ["web"]


def test_the_api_never_sends_a_value(store: NoustStore) -> None:
    response = client().get(f"/api/apps/{DOMAIN}/compose/variables")

    assert "hunter2-secret" not in response.text and "staging" not in response.text


def test_the_api_answers_404_for_an_unknown_application_and_400_for_one_that_is_not_a_stack(
    store: NoustStore,
) -> None:
    assert client().get("/api/apps/nope.example.com/compose/variables").status_code == 404
    refused = client().get("/api/apps/site.example.com/compose/variables")
    assert refused.status_code == 400 and "not a Docker Compose application" in refused.text


def test_the_route_needs_the_permission_to_read_an_application() -> None:
    from noust.web.permissions import Permission
    from noust.web.permissions.routes_app_compose import ROUTES

    assert ROUTES[("GET", "/api/apps/{domain}/compose/variables")] == Permission.APPS_READ


def test_the_command_says_each_variable_and_what_to_do_about_the_missing_one(
    store: NoustStore,
) -> None:
    result = CliRunner().invoke(root_cli, ["app", "compose", "variables", DOMAIN])

    assert result.exit_code == 0, result.output
    assert "DB_PASSWORD" in result.output and "set; used by web" in result.output
    assert "REQUIRED and missing" in result.output
    assert "LEFT_ALONE" in result.output and "not set, no default" in result.output
    assert f"noust env set {DOMAIN} SECRET_TOKEN=<value>" in result.output
    assert "hunter2-secret" not in result.output


def test_the_command_prints_the_report_as_json(store: NoustStore) -> None:
    result = CliRunner().invoke(root_cli, ["--json", "app", "compose", "variables", DOMAIN])

    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["domain"] == DOMAIN and body["missing"] == ["SECRET_TOKEN"]
    assert "hunter2-secret" not in result.output


def test_the_command_refuses_what_is_not_a_stack(store: NoustStore) -> None:
    result = CliRunner().invoke(root_cli, ["app", "compose", "variables", "site.example.com"])

    assert result.exit_code != 0
    assert "not a Docker Compose application" in result.output
