# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/unknown-hosts`` and ``noust site unknown-hosts``.

What the manager decides is pinned in ``tests/test_unknown_hosts.py``. Here: the shapes the
console reads, that changing it asks for sudo mode and looking does not, the status of each
refusal, and that the command line names the same things and reads the same answers.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.cli.app import main
from noust.cli.commands import site_unknown_hosts as command
from noust.core.exceptions import ValidationError
from noust.core.logger import Logger
from noust.core.runner import FakeRunner
from noust.managers import unknown_hosts as module
from noust.managers.nginx_manager import NginxManager
from noust.managers.unknown_hosts import SITE_NAME
from noust.managers.webserver import NGINX_BACKEND
from noust.web.api import unknown_hosts as api
from noust.web.api.deps import install_error_handlers
from noust.web.auth import require_auth

BEARER = {"sid": "token", "scope": "admin", "source": "bearer", "type": "api_token"}
COOKIE = {"sid": "s1", "scope": "admin", "source": "cookie", "type": "session"}


@pytest.fixture
def web(tmp_path: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> NginxManager:
    manager = NginxManager(
        backend=replace(
            NGINX_BACKEND,
            sites_available=tmp_path / "nginx/sites-available",
            sites_enabled=tmp_path / "nginx/sites-enabled",
        )
    )
    manager.backend.sites_available.mkdir(parents=True)
    manager.backend.sites_enabled.mkdir(parents=True)
    runner.script(("nginx", "-v"), stderr="nginx version: nginx/1.24.0\n")
    runner.script(("nginx", "-T"), stdout="")
    monkeypatch.setattr(module, "web_manager", lambda webserver=None: manager)
    monkeypatch.setattr(api, "web_manager", lambda webserver=None: manager)
    return manager


def build(session: dict[str, Any]) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(api.router, prefix="/api/unknown-hosts")
    app.dependency_overrides[require_auth] = lambda: session
    return TestClient(app)


@pytest.fixture
def client(web: NginxManager) -> TestClient:
    return build(BEARER)


def test_nothing_set_is_the_first_site(client: TestClient) -> None:
    response = client.get("/api/unknown-hosts")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is False and body["action"] == ""
    assert body["webserver"] == "nginx" and body["holders"] == []


def test_setting_reads_back_and_resetting_clears_it(client: TestClient, web: NginxManager) -> None:
    put = client.put(
        "/api/unknown-hosts", json={"action": "redirect", "redirect_to": "https://example.org/"}
    )

    assert put.status_code == 200, put.text
    body = put.json()
    assert (body["enabled"], body["action"], body["redirect_to"], body["tls"]) == (
        True,
        "redirect",
        "https://example.org/",
        True,
    )
    assert (web.backend.sites_available / SITE_NAME).exists()
    assert client.get("/api/unknown-hosts").json()["action"] == "redirect"

    deleted = client.delete("/api/unknown-hosts")

    assert deleted.status_code == 200 and deleted.json()["enabled"] is False
    assert not (web.backend.sites_available / SITE_NAME).exists()


def test_a_default_another_server_holds_is_a_400_naming_the_file(
    client: TestClient, runner: FakeRunner
) -> None:
    runner.script(
        ("nginx", "-T"),
        stdout="# configuration file /etc/nginx/conf.d/mine.conf:\nserver {\n    listen 80 default_server;\n}\n",
    )

    response = client.put("/api/unknown-hosts", json={"action": "close"})

    assert response.status_code == 400
    assert "/etc/nginx/conf.d/mine.conf" in response.text
    assert client.get("/api/unknown-hosts").json()["holders"][0]["port"] == 80


def test_an_unknown_action_is_a_400_naming_the_field(client: TestClient) -> None:
    response = client.put("/api/unknown-hosts", json={"action": "explode"})

    assert response.status_code == 400
    assert response.json()["fields"]["action"]


def test_changing_it_asks_for_sudo_mode_and_looking_does_not(web: NginxManager) -> None:
    client = build(COOKIE)

    assert client.get("/api/unknown-hosts").status_code == 200
    assert client.put("/api/unknown-hosts", json={"action": "close"}).status_code == 403
    assert client.delete("/api/unknown-hosts").status_code == 403


# -- the command line ---------------------------------------------------------------


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(Logger, "_write", lambda self, message, newline=True: lines.append(message))
    return lines


def invoke(*args: str) -> Any:
    return CliRunner().invoke(root_cli, ["site", "unknown-hosts", *args])


class Recorder:
    """Stands in for the manager: the command decides nothing, it asks."""

    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __init__(self, manager: Any) -> None:
        self.state = module.UnknownHostsStatus(webserver="nginx", version="1.24.0")

    def status(self) -> Any:
        return self.state

    def set(self, action: str, redirect_to: str = "", **kwargs: Any) -> Any:
        Recorder.calls.append(("set", (action, redirect_to), kwargs))
        return module.UnknownHostsStatus(
            webserver="nginx", enabled=True, action=action, redirect_to=redirect_to, tls=True
        )

    def reset(self) -> Any:
        Recorder.calls.append(("reset", (), {}))
        return self.state


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> type[Recorder]:
    Recorder.calls = []
    monkeypatch.setattr(command, "UnknownHosts", Recorder)
    monkeypatch.setattr(command, "web_manager", lambda webserver=None: None)
    return Recorder


def test_the_command_sets_shows_and_resets(recorder: type[Recorder], log: list[str]) -> None:
    done = invoke("set", "refuse", "--switch-off-default-site", "--json")

    assert done.exit_code == 0, done.output
    assert json.loads(done.output.splitlines()[-1])["action"] == "refuse"
    assert recorder.calls == [("set", ("refuse", ""), {"switch_off_distribution_site": True})]
    assert json.loads(invoke("show", "--json").output)["enabled"] is False
    assert invoke("reset", "--json").exit_code == 0
    assert recorder.calls[-1][0] == "reset"


def test_a_redirect_carries_its_address(recorder: type[Recorder]) -> None:
    invoke("set", "redirect", "--redirect-to", "https://example.org/")

    assert recorder.calls[0][1] == ("redirect", "https://example.org/")


def test_a_redirect_without_an_address_is_a_usage_error(recorder: type[Recorder]) -> None:
    result = invoke("set", "redirect")

    assert result.exit_code == 2
    assert recorder.calls == []


def test_a_refusal_is_exit_one_with_the_reason(
    monkeypatch: pytest.MonkeyPatch, log: list[str]
) -> None:
    class Refusing(Recorder):
        def set(self, action: str, redirect_to: str = "", **kwargs: Any) -> Any:
            raise ValidationError("/etc/nginx/conf.d/mine.conf is already the default server")

    monkeypatch.setattr(command, "UnknownHosts", Refusing)
    monkeypatch.setattr(command, "web_manager", lambda webserver=None: None)

    assert main(["site", "unknown-hosts", "set", "close"]) == 1
    assert any("/etc/nginx/conf.d/mine.conf" in line for line in log)


def test_a_rehearsal_changes_nothing(recorder: type[Recorder]) -> None:
    result = CliRunner().invoke(root_cli, ["--dry-run", "site", "unknown-hosts", "set", "close"])

    assert result.exit_code == 0, result.output
    assert recorder.calls == []
