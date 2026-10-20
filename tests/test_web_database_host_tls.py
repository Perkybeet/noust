# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/engines/{engine}/tls``: a host engine that serves a certificate clients can verify
(4.0, checklist C-27).

The routes are a client of :class:`~noust.managers.database.host_tls.HostTls`, which ``noust db tls``
calls too: what is pinned here is the HTTP side. Reading the state and planning need
``databases.read``; giving an engine a certificate, taking it off and refreshing it restart or reload
the engine, need ``databases.manage`` and sudo mode, and are jobs. A restart is refused without
``confirm`` before the job exists, as ``confirmation_required``.
"""

# ruff: noqa: F811

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.exceptions import ValidationError
from noust.web.api.deps import JobAcceptedResponse
from noust.web.permissions import Permission
from noust.web.permissions.routes_database_host_tls import ROUTES
from tests.test_web_databases_api import (  # noqa: F401 - fixtures
    anonymous,
    app,
    client,
    elevate,
    engines,
)

PATH = "/api/databases/engines"
ENABLE = {"hostname": "db.example.com"}


class FakeTls:
    """Stands in for the manager: records what it was asked."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.restart = True
        self.refuse: Exception | None = None
        self.enabled = False

    def status(self) -> dict[str, Any]:
        record = {
            "engine": "postgresql",
            "hostname": "db.example.com",
            "source": "acme",
            "fingerprint": "AB:CD",
            "not_after": "Jan  1 00:00:00 2027 GMT",
        }
        return {
            "engine": "postgresql",
            "enabled": self.enabled,
            "record": record if self.enabled else None,
            "warnings": [],
            "served": "AB:CD" if self.enabled else None,
            "days_left": 80 if self.enabled else None,
            "source_changed": False,
        }

    def plan(self, hostname: str, *, source: str = "acme", certificate: str | None = None) -> dict:
        self.calls.append(("plan", (hostname, source, certificate)))
        if self.refuse:
            raise self.refuse
        return {
            "engine": "postgresql",
            "hostname": hostname,
            "source": source,
            "certificate": certificate,
            "fullchain": "/etc/letsencrypt/live/db.example.com/fullchain.pem",
            "directory": "/etc/postgresql/16/main/noust-tls",
            "fingerprint": "AB:CD",
            "not_after": "Jan  1 00:00:00 2027 GMT",
            "restart": self.restart,
        }

    def needs_restart(self) -> bool:
        return self.restart


@pytest.fixture
def tls(monkeypatch: pytest.MonkeyPatch) -> FakeTls:
    """The fake manager, and a queue that only records the jobs it is handed."""
    fake = FakeTls()
    monkeypatch.setattr("noust.web.api.databases.host_tls._tls", lambda session, engine: fake)
    queued: list[dict[str, Any]] = []

    def queue(session: Any, **kwargs: Any) -> JobAcceptedResponse:
        queued.append(kwargs)
        return JobAcceptedResponse(
            job_id="job-1", status="pending", message=kwargs["message"], job={}
        )

    monkeypatch.setattr("noust.web.api.databases.host_tls.queue", queue)
    fake.queued = queued  # type: ignore[attr-defined]
    return fake


def test_the_routes_declare_their_permission() -> None:
    assert ROUTES[("GET", f"{PATH}/tls")] == Permission.DATABASES_READ
    assert ROUTES[("GET", f"{PATH}/{{engine}}/tls")] == Permission.DATABASES_READ
    assert ROUTES[("POST", f"{PATH}/{{engine}}/tls/plan")] == Permission.DATABASES_READ
    assert ROUTES[("PUT", f"{PATH}/{{engine}}/tls")] == Permission.DATABASES_MANAGE
    assert ROUTES[("DELETE", f"{PATH}/{{engine}}/tls")] == Permission.DATABASES_MANAGE
    assert ROUTES[("POST", f"{PATH}/tls/refresh")] == Permission.DATABASES_MANAGE


def test_every_route_needs_a_session(anonymous: TestClient, engines, tls) -> None:
    assert anonymous.get(f"{PATH}/postgresql/tls").status_code in (401, 403)
    assert anonymous.put(f"{PATH}/postgresql/tls", json=ENABLE).status_code in (401, 403)
    assert anonymous.post(f"{PATH}/tls/refresh").status_code in (401, 403)


def test_the_status_of_an_engine_is_a_read(client: TestClient, engines, tls: FakeTls) -> None:
    tls.enabled = True

    response = client.get(f"{PATH}/postgresql/tls")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is True and body["record"]["hostname"] == "db.example.com"
    assert body["served"] == "AB:CD" and body["days_left"] == 80


def test_an_engine_noust_cannot_give_a_certificate_to_is_refused(
    client: TestClient, engines, tls: FakeTls
) -> None:
    assert client.get(f"{PATH}/redis/tls").status_code == 400
    assert client.post(f"{PATH}/redis/tls/plan", json=ENABLE).status_code == 400


def test_the_status_of_every_engine_skips_the_ones_that_are_not_there(
    client: TestClient, engines, tls: FakeTls
) -> None:
    response = client.get(f"{PATH}/tls")

    assert response.status_code == 200, response.text
    assert response.json()[0]["engine"] == "postgresql"


def test_planning_checks_the_certificate_and_changes_nothing(
    client: TestClient, engines, tls: FakeTls
) -> None:
    response = client.post(
        f"{PATH}/postgresql/tls/plan",
        json={"hostname": "db.example.com", "source": "lineage", "certificate": "x"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["restart"] is True and response.json()["fingerprint"] == "AB:CD"
    assert tls.calls == [("plan", ("db.example.com", "lineage", "x"))]
    assert tls.queued == []  # type: ignore[attr-defined]


def test_a_certificate_that_fails_its_checks_is_a_400_with_its_words(
    client: TestClient, engines, tls: FakeTls
) -> None:
    tls.refuse = ValidationError("The certificate does not cover db.example.com", field="hostname")

    response = client.post(f"{PATH}/postgresql/tls/plan", json=ENABLE)

    assert response.status_code == 400 and "does not cover" in response.json()["detail"]


def test_giving_a_certificate_needs_sudo_mode_and_the_restart_confirmed(
    client: TestClient, engines, tls: FakeTls
) -> None:
    assert client.put(f"{PATH}/postgresql/tls", json={**ENABLE, "confirm": True}).status_code == 403
    elevate(client)

    unconfirmed = client.put(f"{PATH}/postgresql/tls", json=ENABLE)

    assert unconfirmed.status_code == 400, unconfirmed.text
    assert unconfirmed.json()["error"] == "confirmation_required"
    assert "restarts" in " ".join(unconfirmed.json()["warnings"])
    assert tls.queued == []  # type: ignore[attr-defined]

    accepted = client.put(f"{PATH}/postgresql/tls", json={**ENABLE, "confirm": True})

    assert accepted.status_code == 202, accepted.text
    (job,) = tls.queued  # type: ignore[attr-defined]
    assert job["kwargs"]["action"] == "enable" and job["kwargs"]["engine"] == "postgresql"
    assert job["kwargs"]["hostname"] == "db.example.com" and job["kwargs"]["source"] == "acme"


def test_nothing_is_asked_when_nothing_restarts(client: TestClient, engines, tls: FakeTls) -> None:
    tls.restart = False
    elevate(client)

    assert client.put(f"{PATH}/postgresql/tls", json=ENABLE).status_code == 202


def test_taking_it_off_asks_only_when_it_restarts(
    client: TestClient, engines, tls: FakeTls
) -> None:
    elevate(client)

    refused = client.delete(f"{PATH}/postgresql/tls")
    accepted = client.delete(f"{PATH}/postgresql/tls", params={"confirm": True})

    assert refused.status_code == 400 and refused.json()["error"] == "confirmation_required"
    assert accepted.status_code == 202, accepted.text
    assert tls.queued[-1]["kwargs"]["action"] == "disable"  # type: ignore[attr-defined]


def test_refreshing_is_a_job(client: TestClient, engines, tls: FakeTls) -> None:
    assert client.post(f"{PATH}/tls/refresh").status_code == 403
    elevate(client)

    response = client.post(f"{PATH}/tls/refresh")

    assert response.status_code == 202, response.text
    assert tls.queued[-1]["kwargs"]["action"] == "refresh"  # type: ignore[attr-defined]


def test_the_job_runs_the_manager_and_gives_its_status(
    client: TestClient, engines, tls: FakeTls, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.web.api.databases.host_tls import host_tls_job

    class Enabling(FakeTls):
        def enable(self, hostname: str, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(("enable", (hostname, kwargs)))
            self.enabled = True
            return self.status()

    fake = Enabling()
    monkeypatch.setattr("noust.web.api.databases.host_tls._tls_of", lambda service, engine: fake)

    result = host_tls_job(
        "postgresql", "enable", hostname="db.example.com", source="custom", certificate="up"
    )

    assert result["enabled"] is True
    assert fake.calls == [
        ("enable", ("db.example.com", {"confirmed": True, "source": "custom", "certificate": "up"}))
    ]
