# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The destinations of logs through the API and the command line (checklist B-73).

``tests/test_log_drains.py`` pins what a destination is and what it is sent, and
``tests/test_log_shipping.py`` how the shipper reads; here, how they travel:
``/api/log-drains`` lists, makes, removes and tests destinations (changes behind sudo
mode, never showing a token), and ``noust logdrain`` is a client of the same manager.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.cli.commands import logdrain as logdrain_cli
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore
from noust.managers import log_drains, log_shipping
from noust.managers.log_drains import LogDrains
from noust.managers.log_transports import DeliveryError
from noust.web.api import log_drains as drains_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers, require_elevated

TOKEN = "glc_eyJrIjoiN2xkc2Zhc2RmIn0"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(App(domain="shop.example.com", app_type="nodejs", app_path="/srv/shop"))
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture
def secrets(tmp_path: Path) -> SecretStore:
    return SecretStore(root=tmp_path / "secrets")


@pytest.fixture
def unit(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stand in for systemd: the unit is always installable, and says what asked."""
    asked: list[str] = []
    monkeypatch.setattr(
        log_shipping, "ensure_service", lambda **_: asked.append("ensure") or "started"
    )
    monkeypatch.setattr(
        log_shipping,
        "service_state",
        lambda **_: {"installed": True, "active": True, "state": "active"},
    )
    monkeypatch.setattr(log_shipping, "remove_service", lambda **_: asked.append("remove") or True)
    return asked


@pytest.fixture
def manager(
    store: NoustStore, secrets: SecretStore, monkeypatch: pytest.MonkeyPatch, unit: list[str]
) -> LogDrains:
    """The manager both clients build, over this test's store and secrets."""
    made = LogDrains(store=store, secrets=secrets)
    monkeypatch.setattr(drains_api, "LogDrains", lambda: made)
    monkeypatch.setattr(logdrain_cli, "LogDrains", lambda: made)
    return made


def client(*, elevated: bool = True) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(drains_api.router, prefix="/api/log-drains")
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    app.dependency_overrides[get_current_session] = lambda: session

    def elevation() -> dict[str, Any]:
        if not elevated:
            raise HTTPException(status_code=403, detail={"error": "elevation_required"})
        return session

    app.dependency_overrides[require_elevated] = elevation
    return TestClient(app)


BODY = {"kind": "loki", "settings": {"url": "https://logs.example.com"}, "secret": TOKEN}


# ---------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------


def test_the_list_is_empty_until_a_destination_is_made(manager: LogDrains) -> None:
    response = client(elevated=False).get("/api/log-drains")

    assert response.status_code == 200
    assert response.json() == {
        "drains": [],
        "total": 0,
        "service": {"installed": True, "active": True, "state": "active"},
    }


def test_a_destination_is_made_with_its_token_and_the_shipper_started(
    manager: LogDrains, unit: list[str]
) -> None:
    response = client().put("/api/log-drains/grafana", json={**BODY, "apps": ["shop.example.com"]})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "grafana" and body["kind"] == "loki"
    assert body["apps"] == ["shop.example.com"] and body["enabled"] is True
    assert body["has_secret"] is True and body["plaintext"] is False
    assert unit == ["ensure"]
    assert manager.secret_of("grafana") == TOKEN


def test_the_token_is_never_in_an_answer(manager: LogDrains) -> None:
    http = client()

    created = http.put("/api/log-drains/grafana", json=BODY)
    listed = http.get("/api/log-drains")

    assert TOKEN not in created.text and TOKEN not in listed.text
    assert listed.json()["drains"][0]["has_secret"] is True


def test_a_destination_that_is_off_does_not_start_the_shipper(
    manager: LogDrains, unit: list[str]
) -> None:
    response = client().put("/api/log-drains/grafana", json={**BODY, "enabled": False})

    assert response.status_code == 200 and response.json()["enabled"] is False
    assert unit == []


def test_editing_can_keep_the_token_it_has(manager: LogDrains) -> None:
    http = client()
    http.put("/api/log-drains/grafana", json=BODY)

    edited = http.put(
        "/api/log-drains/grafana",
        json={
            "kind": "loki",
            "settings": {"url": "https://other.example.com"},
            "keep_secret": True,
        },
    )

    assert edited.status_code == 200 and edited.json()["has_secret"] is True
    assert manager.secret_of("grafana") == TOKEN


def test_plaintext_is_flagged_for_the_console_to_warn_about(manager: LogDrains) -> None:
    response = client().put(
        "/api/log-drains/local",
        json={"kind": "http", "settings": {"url": "http://127.0.0.1:2020"}},
    )

    assert response.status_code == 200 and response.json()["plaintext"] is True


def test_a_setting_a_destination_cannot_have_is_a_400_naming_the_field(
    manager: LogDrains,
) -> None:
    response = client().put(
        "/api/log-drains/grafana", json={"kind": "loki", "settings": {"url": "ftp://x"}}
    )

    assert response.status_code == 400
    assert "url" in response.json()["fields"]


def test_changing_destinations_needs_sudo_mode(manager: LogDrains) -> None:
    http = client(elevated=False)

    for response in (
        http.put("/api/log-drains/grafana", json=BODY),
        http.delete("/api/log-drains/grafana"),
        http.post("/api/log-drains/grafana/test"),
    ):
        assert response.status_code == 403
    assert manager.list_drains() == []


def test_a_destination_is_removed_with_its_token(manager: LogDrains) -> None:
    http = client()
    http.put("/api/log-drains/grafana", json=BODY)

    response = http.delete("/api/log-drains/grafana")

    assert response.status_code == 200 and response.json()["total"] == 0
    assert manager.has_secret("grafana") is False


def test_removing_what_does_not_exist_is_a_400(manager: LogDrains) -> None:
    assert client().delete("/api/log-drains/nothing").status_code == 400


def test_the_test_line_says_what_was_sent(
    manager: LogDrains, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[Any] = []

    class Fake:
        def send(self, events: Any) -> None:
            sent.append(events)

        def close(self) -> None:
            pass

    http = client()
    http.put("/api/log-drains/grafana", json=BODY)
    monkeypatch.setattr(log_drains, "build_transport", lambda drain, secret: Fake())

    response = http.post("/api/log-drains/grafana/test")

    assert response.status_code == 200
    assert response.json()["sent"].startswith("Noust log drain test from ")
    assert len(sent) == 1


def test_a_refused_test_line_carries_what_the_destination_said(
    manager: LogDrains, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Refusing:
        def send(self, events: Any) -> None:
            raise DeliveryError("loki answered HTTP 401", details="invalid token")

        def close(self) -> None:
            pass

    http = client()
    http.put("/api/log-drains/grafana", json=BODY)
    monkeypatch.setattr(log_drains, "build_transport", lambda drain, secret: Refusing())

    response = http.post("/api/log-drains/grafana/test")

    assert response.status_code >= 400
    assert "HTTP 401" in response.text and TOKEN not in response.text


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def invoke(*args: str, input: str | None = None) -> Any:
    return CliRunner().invoke(root_cli, list(args), input=input)


def test_add_reads_the_token_from_standard_input_never_an_argument(
    manager: LogDrains, unit: list[str]
) -> None:
    result = invoke(
        "logdrain",
        "add",
        "grafana",
        "--kind",
        "loki",
        "--url",
        "https://logs.example.com",
        "--tenant",
        "acme",
        "--label",
        "env=prod",
        "--app",
        "shop.example.com",
        "--token-stdin",
        input=TOKEN + "\n",
    )

    assert result.exit_code == 0, result.output
    saved = manager.get("grafana")
    assert saved.settings["tenant"] == "acme" and saved.settings["labels"] == {"env": "prod"}
    assert saved.apps == ("shop.example.com",)
    assert manager.secret_of("grafana") == TOKEN
    assert TOKEN not in result.output
    assert unit == ["ensure"]


def test_add_without_a_token_on_a_pipe_makes_a_destination_without_one(
    manager: LogDrains,
) -> None:
    result = invoke(
        "logdrain", "add", "fluent", "--kind", "http", "--url", "https://f.example.com", input=""
    )

    assert result.exit_code == 0, result.output
    assert manager.has_secret("fluent") is False


def test_add_syslog_takes_its_receiver_not_an_address(manager: LogDrains) -> None:
    result = invoke(
        "logdrain",
        "add",
        "central",
        "--kind",
        "syslog",
        "--host",
        "logs.example.com",
        "--transport",
        "tls",
    )

    assert result.exit_code == 0, result.output
    settings = manager.get("central").settings
    assert settings["host"] == "logs.example.com" and settings["transport"] == "tls"


def test_a_label_that_is_not_a_pair_is_refused(manager: LogDrains) -> None:
    result = invoke(
        "logdrain", "add", "g", "--kind", "loki", "--url", "https://l.example.com", "--label", "env"
    )

    assert result.exit_code != 0
    assert manager.list_drains() == []


def test_disabled_makes_the_destination_without_starting_the_shipper(
    manager: LogDrains, unit: list[str]
) -> None:
    result = invoke(
        "logdrain",
        "add",
        "g",
        "--kind",
        "loki",
        "--url",
        "https://l.example.com",
        "--disabled",
        input="",
    )

    assert result.exit_code == 0, result.output
    assert manager.get("g").enabled is False and unit == []


def test_list_says_how_each_destination_is_doing_as_json(manager: LogDrains) -> None:
    manager.save("grafana", "loki", {"url": "https://logs.example.com"}, secret=TOKEN)

    result = invoke("--json", "logdrain", "list")

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["service"]["active"] is True
    assert data["drains"][0]["name"] == "grafana" and data["drains"][0]["has_secret"] is True
    assert TOKEN not in result.output


def test_list_without_destinations_says_how_to_make_one(manager: LogDrains) -> None:
    result = invoke("logdrain", "list")

    assert result.exit_code == 0 and "noust logdrain add" in result.output


def test_remove_asks_first_and_yes_skips_it(manager: LogDrains) -> None:
    manager.save("grafana", "loki", {"url": "https://logs.example.com"})

    declined = invoke("logdrain", "remove", "grafana", input="n\n")
    assert declined.exit_code != 0 and manager.list_drains()

    removed = invoke("logdrain", "remove", "grafana", "--yes")
    assert removed.exit_code == 0 and manager.list_drains() == []


def test_enable_and_disable_turn_shipping_on_and_off(manager: LogDrains, unit: list[str]) -> None:
    manager.save("grafana", "loki", {"url": "https://logs.example.com"}, enabled=False)

    assert invoke("logdrain", "enable", "grafana").exit_code == 0
    assert manager.get("grafana").enabled is True and unit == ["ensure"]
    assert invoke("logdrain", "disable", "grafana").exit_code == 0
    assert manager.get("grafana").enabled is False


def test_test_reports_what_was_accepted(
    manager: LogDrains, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Fake:
        def send(self, events: Any) -> None:
            pass

        def close(self) -> None:
            pass

    manager.save("grafana", "loki", {"url": "https://logs.example.com"})
    monkeypatch.setattr(log_drains, "build_transport", lambda drain, secret: Fake())

    result = invoke("logdrain", "test", "grafana")

    assert result.exit_code == 0 and "Noust log drain test from" in result.output


def test_run_once_does_one_cycle_and_prints_what_failed(
    manager: LogDrains, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Shipper:
        def cycle(self) -> Any:
            return log_shipping.CycleReport(sent={"a": 3}, failed={"b": "loki answered HTTP 503"})

    monkeypatch.setattr(log_shipping, "LogShipper", Shipper)

    result = invoke("logdrain", "run", "--once")

    assert result.exit_code == 0, result.output
    assert "a: 3 lines sent" in result.output
    assert "b: loki answered HTTP 503" in result.output


def test_the_service_commands_install_remove_and_report(
    manager: LogDrains, unit: list[str]
) -> None:
    assert invoke("logdrain", "service", "install").exit_code == 0
    assert invoke("logdrain", "service", "remove").exit_code == 0
    assert unit == ["ensure", "remove"]

    status = invoke("--json", "logdrain", "service", "status")

    assert json.loads(status.output) == {"installed": True, "active": True, "state": "active"}
