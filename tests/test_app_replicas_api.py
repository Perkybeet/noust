# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The copies of an application's web process through the API and the command line (checklist B-68).

``tests/test_app_replicas.py`` pins what a copy is and how it is started and routed; here, how
it travels: ``GET /api/apps/{domain}/replicas`` shows how the application is scaled, ``PUT``
queues the change as a job behind sudo mode and refuses what cannot be scaled before queueing,
and ``noust app replicas`` is a client of the same manager.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.cli.commands import app_replicas as replicas_cli
from noust.core.store import App, NoustStore, Service
from noust.core.teams import ScopedStore
from noust.managers.app_replicas import ReplicaChange, ReplicaCopy, ReplicaStatus
from noust.web.api import replicas as replicas_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers, require_elevated

DOMAIN = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    monkeypatch.setattr(replicas_api, "scoped_store", lambda: ScopedStore(store=instance))
    row = instance.create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            port=3100,
            app_path="/var/www/apps/shop-example-com",
            webserver="nginx",
        )
    )
    instance.create_service(
        Service(app_id=row.id, name="shop-example-com", command="/usr/bin/npm run start")
    )
    instance.create_app(
        App(domain="stack.example.com", app_type="docker-compose", app_path="/srv/s")
    )
    yield instance
    NoustStore.reset_instance()


def client(*, elevated: bool) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(replicas_api.router, prefix="/api/apps")
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    app.dependency_overrides[get_current_session] = lambda: session

    def elevation() -> dict[str, Any]:
        if not elevated:
            raise HTTPException(status_code=403, detail={"error": "elevation_required"})
        return session

    app.dependency_overrides[require_elevated] = elevation
    return TestClient(app)


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []

    class Jobs:
        def create_job(self, **kwargs: Any) -> Any:
            jobs.append(kwargs)
            return SimpleNamespace(
                id="j1", status=SimpleNamespace(value="pending"), to_dict=lambda: {"id": "j1"}
            )

    monkeypatch.setattr(replicas_api, "get_job_manager", lambda: Jobs())
    return jobs


STATUS = ReplicaStatus(
    domain=DOMAIN,
    copies=2,
    maximum=8,
    ports=(3101,),
    units=(
        ReplicaCopy(unit="shop-example-com", port=3100, state="active", own=True, serving=True),
        ReplicaCopy(unit="shop-example-com-replica@3101", port=3101, state="active", serving=True),
    ),
)


class Manager:
    """Stands in for the manager both clients build."""

    status_to_give = STATUS
    calls: list[tuple[str, int, str]] = []

    def __init__(self, **kwargs: Any) -> None:
        pass

    def status(self, domain: str) -> ReplicaStatus:
        return Manager.status_to_give

    def set_copies(self, domain: str, copies: int, *, actor: str = "system") -> ReplicaChange:
        Manager.calls.append((domain, copies, actor))
        return ReplicaChange(
            domain=domain, copies=copies, before=2, ports=(3101,), started=("a",), stopped=()
        )


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> type[Manager]:
    Manager.calls = []
    Manager.status_to_give = STATUS
    monkeypatch.setattr(replicas_api, "ReplicaManager", Manager)
    monkeypatch.setattr(replicas_cli, "ReplicaManager", Manager)
    return Manager


# ---------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------


def test_the_api_shows_how_an_application_is_scaled(
    store: NoustStore, manager: type[Manager]
) -> None:
    response = client(elevated=False).get(f"/api/apps/{DOMAIN}/replicas")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["copies"] == 2 and body["maximum"] == 8 and body["eligible"] is True
    assert [unit["port"] for unit in body["units"]] == [3100, 3101]
    assert body["units"][0]["own"] is True


def test_the_api_404s_an_application_that_is_not_deployed(
    store: NoustStore, manager: type[Manager]
) -> None:
    assert client(elevated=False).get("/api/apps/nobody.example.com/replicas").status_code == 404


def test_scaling_is_queued_as_a_job_by_someone_in_sudo_mode(
    store: NoustStore, queued: list[dict[str, Any]]
) -> None:
    response = client(elevated=True).put(f"/api/apps/{DOMAIN}/replicas", json={"copies": 3})

    assert response.status_code == 202, response.text
    assert response.json()["job_id"] == "j1"
    assert queued[0]["kwargs"]["domain"] == DOMAIN and queued[0]["kwargs"]["copies"] == 3
    assert queued[0]["metadata"] == {"domain": DOMAIN}


def test_scaling_needs_sudo_mode(store: NoustStore, queued: list[dict[str, Any]]) -> None:
    response = client(elevated=False).put(f"/api/apps/{DOMAIN}/replicas", json={"copies": 3})

    assert response.status_code == 403 and queued == []


@pytest.mark.parametrize("copies", [0, 9, -1])
def test_a_count_out_of_range_is_refused_before_anything_is_queued(
    store: NoustStore, queued: list[dict[str, Any]], copies: int
) -> None:
    response = client(elevated=True).put(f"/api/apps/{DOMAIN}/replicas", json={"copies": copies})

    assert response.status_code == 422 and queued == []


def test_an_application_that_cannot_be_scaled_is_refused_with_the_reason(
    store: NoustStore, queued: list[dict[str, Any]]
) -> None:
    store.set_zero_downtime(DOMAIN, True)

    response = client(elevated=True).put(f"/api/apps/{DOMAIN}/replicas", json={"copies": 2})

    assert response.status_code == 400 and queued == []
    assert "zero-downtime" in response.text


def test_going_back_to_one_copy_is_always_allowed(
    store: NoustStore, queued: list[dict[str, Any]]
) -> None:
    store.set_zero_downtime(DOMAIN, True)

    response = client(elevated=True).put(f"/api/apps/{DOMAIN}/replicas", json={"copies": 1})

    assert response.status_code == 202 and len(queued) == 1


def test_the_job_scales_through_the_manager(manager: type[Manager]) -> None:
    result = replicas_api.replicas_job(DOMAIN, 3, "alice")

    assert Manager.calls == [(DOMAIN, 3, "alice")]
    assert result == {
        "domain": DOMAIN,
        "copies": 3,
        "before": 2,
        "ports": [3101],
        "started": ["a"],
        "stopped": [],
    }


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def test_the_command_shows_the_copies_as_json(manager: type[Manager]) -> None:
    result = CliRunner().invoke(root_cli, ["--json", "app", "replicas", DOMAIN])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["copies"] == 2


def test_the_command_shows_the_copies_in_a_table(manager: type[Manager]) -> None:
    result = CliRunner().invoke(root_cli, ["app", "replicas", DOMAIN])

    assert result.exit_code == 0, result.output
    assert "shop-example-com-replica@3101" in result.output and "3101" in result.output


def test_the_command_says_why_an_application_cannot_be_scaled(manager: type[Manager]) -> None:
    Manager.status_to_give = ReplicaStatus(
        domain=DOMAIN,
        copies=1,
        maximum=8,
        units=(),
        eligible=False,
        reason="it is a static site",
    )

    result = CliRunner().invoke(root_cli, ["app", "replicas", DOMAIN])

    assert result.exit_code == 0 and "it is a static site" in result.output


def test_the_command_scales_through_the_manager(manager: type[Manager]) -> None:
    result = CliRunner().invoke(root_cli, ["--json", "app", "replicas", DOMAIN, "3"])

    assert result.exit_code == 0, result.output
    assert Manager.calls[0][:2] == (DOMAIN, 3)
    assert json.loads(result.output)["copies"] == 3


def test_the_command_refuses_a_count_out_of_range(manager: type[Manager]) -> None:
    result = CliRunner().invoke(root_cli, ["app", "replicas", DOMAIN, "9"])

    assert result.exit_code == 2 and Manager.calls == []
