# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/pitr``: recover PostgreSQL to an instant (checklist H-32).

The routes are a client of the manager: what is pinned here is the HTTP side. Reading and
planning need ``databases.read``; every change needs ``databases.manage`` and sudo mode, is a
job, and is refused without ``confirm`` before the job is queued; a recovery is planned first, so
a refusal answers now. The manager's own behaviour is in ``tests/test_database_pitr.py``.
"""

# ruff: noqa: F811

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.exceptions import DatabaseBackupError, ValidationError
from noust.managers.database.pitr import RestorePlan
from noust.managers.database.pitr_archive import BaseBackup
from noust.web.api.deps import JobAcceptedResponse
from noust.web.permissions import Permission
from noust.web.permissions.routes_database_pitr import ROUTES
from tests.test_web_databases_api import (  # noqa: F401 - fixtures
    anonymous,
    app,
    client,
    elevate,
    engines,
)

PATH = "/api/databases/pitr"
BASE = BaseBackup(
    id="base-20261017T120000Z",
    started_at=datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc),
    finished_at=datetime(2026, 10, 17, 12, 0, 9, tzinfo=timezone.utc),
    start_wal_file="000000010000000000000002",
    timeline=1,
    size=100,
    sha256="0" * 64,
    server_version="160004",
    system_id="7430000000000000001",
)


class FakePitr:
    """Stands in for the manager: records what it was asked, answers what a test says."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.restart = True
        self.refuse: Exception | None = None

    def status(self) -> dict[str, Any]:
        return {
            "engine": "postgresql",
            "enabled": True,
            "policy": {"base_every_hours": 168},
            "bases": [BASE.to_dict()],
            "archive": {"path": "/var/lib/postgresql/noust-wal/7430", "files": 3, "bytes": 3000},
            "window": {
                "earliest": "2026-10-17T12:00:09+00:00",
                "latest": "2026-10-17T12:30:00+00:00",
            },
            "archiver": {"archived": 3, "failed": 0},
            "timer": {"installed": True},
            "warnings": [],
        }

    def plan_enable(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("plan_enable", kwargs))
        if self.refuse:
            raise self.refuse
        return {
            "settings": {"archive_mode": "on"},
            "restart": self.restart,
            "archive_path": "/var/lib/postgresql/noust-wal/7430",
            "system_id": "7430000000000000001",
            "data_directory": "/var/lib/postgresql/16/main",
            "wal_level_was": None,
            "base_every_hours": kwargs["base_every_hours"],
            "retention_count": kwargs["retention_count"],
            "retention_days": kwargs["retention_days"],
            "destinations": kwargs["destinations"],
        }

    def plan_restore(self, target: datetime | None, *, base_id: str | None = None) -> RestorePlan:
        self.calls.append(("plan_restore", (target, base_id)))
        if self.refuse:
            raise self.refuse
        return RestorePlan(
            base=BASE,
            target=target,
            data_directory="/var/lib/postgresql/16/main",
            saved_as="/var/lib/postgresql/16/main.noust-before-recovery-20261017T130000Z",
            segments=4,
            last_archived_at=datetime(2026, 10, 17, 12, 30, tzinfo=timezone.utc),
            timeline=1 if target else None,
        )


@pytest.fixture
def pitr(monkeypatch: pytest.MonkeyPatch) -> FakePitr:
    """The fake manager, and a queue that only records the jobs it is handed."""
    fake = FakePitr()
    monkeypatch.setattr("noust.web.api.databases.pitr._pitr", lambda session: fake)
    queued: list[dict[str, Any]] = []

    def queue(session: Any, **kwargs: Any) -> JobAcceptedResponse:
        queued.append(kwargs)
        return JobAcceptedResponse(
            job_id="job-1", status="pending", message=kwargs["message"], job={}
        )

    monkeypatch.setattr("noust.web.api.databases.pitr.queue", queue)
    fake.queued = queued  # type: ignore[attr-defined]
    return fake


def test_the_routes_declare_their_permission() -> None:
    assert ROUTES[("GET", PATH)] == Permission.DATABASES_READ
    assert ROUTES[("POST", f"{PATH}/enable/plan")] == Permission.DATABASES_READ
    assert ROUTES[("POST", f"{PATH}/restore/plan")] == Permission.DATABASES_READ
    for path in ("enable", "disable", "base", "sync", "fetch", "restore"):
        assert ROUTES[("POST", f"{PATH}/{path}")] == Permission.DATABASES_MANAGE


def test_the_status_is_a_read(client: TestClient, engines, pitr: FakePitr) -> None:
    response = client.get(PATH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is True and body["bases"][0]["id"] == BASE.id
    assert body["window"]["latest"] == "2026-10-17T12:30:00+00:00"


def test_every_route_needs_a_session(anonymous: TestClient, engines, pitr: FakePitr) -> None:
    assert anonymous.get(PATH).status_code in (401, 403)
    assert anonymous.post(f"{PATH}/enable", json={}).status_code in (401, 403)
    assert anonymous.post(f"{PATH}/restore", json={}).status_code in (401, 403)


def test_planning_the_enable_changes_nothing_and_needs_no_sudo_mode(
    client: TestClient, engines, pitr: FakePitr
) -> None:
    response = client.post(f"{PATH}/enable/plan", json={"retention_count": 3})

    assert response.status_code == 200, response.text
    assert response.json()["restart"] is True
    assert response.json()["retention_count"] == 3 and response.json()["retention_days"] == 7
    assert pitr.queued == []  # type: ignore[attr-defined]


def test_turning_it_on_needs_sudo_mode_and_the_restart_confirmed(
    client: TestClient, engines, pitr: FakePitr
) -> None:
    body = {"destinations": [{"name": "nas", "retention_days": 14}]}

    assert client.post(f"{PATH}/enable", json={**body, "confirm": True}).status_code == 403
    elevate(client)
    unconfirmed = client.post(f"{PATH}/enable", json=body)

    assert unconfirmed.status_code == 400, unconfirmed.text
    assert "restarts PostgreSQL" in unconfirmed.text
    assert pitr.queued == []  # type: ignore[attr-defined]

    accepted = client.post(f"{PATH}/enable", json={**body, "confirm": True})

    assert accepted.status_code == 202, accepted.text
    job = pitr.queued[0]  # type: ignore[attr-defined]
    assert job["kwargs"]["action"] == "enable"
    options = job["kwargs"]["options"]
    assert options["destinations"] == [
        {"name": "nas", "retention_count": None, "retention_days": 14}
    ]
    assert options["retention_days"] == 7 and options["base_every_hours"] == 168


def test_no_confirmation_is_needed_when_nothing_restarts(
    client: TestClient, engines, pitr: FakePitr
) -> None:
    pitr.restart = False
    elevate(client)

    assert client.post(f"{PATH}/enable", json={}).status_code == 202


def test_a_policy_out_of_range_is_refused_before_anything_is_planned(
    client: TestClient, engines, pitr: FakePitr
) -> None:
    elevate(client)

    for body in ({"base_every_hours": 0}, {"retention_count": 0}, {"retention_days": 0}):
        assert client.post(f"{PATH}/enable", json=body).status_code == 422
    assert pitr.calls == []


def test_a_refusal_of_the_plan_answers_now_not_from_a_job(
    client: TestClient, engines, pitr: FakePitr
) -> None:
    pitr.refuse = ValidationError("Backup destination 'x' does not exist", field="destination")
    elevate(client)

    response = client.post(f"{PATH}/enable", json={"confirm": True})

    assert response.status_code == 400
    assert pitr.queued == []  # type: ignore[attr-defined]


def test_turning_it_off_must_be_confirmed(client: TestClient, engines, pitr: FakePitr) -> None:
    elevate(client)

    assert client.post(f"{PATH}/disable", json={}).status_code == 400
    accepted = client.post(f"{PATH}/disable", json={"confirm": True, "delete_archive": True})

    assert accepted.status_code == 202, accepted.text
    assert pitr.queued[0]["kwargs"] == {  # type: ignore[attr-defined]
        "action": "disable",
        "options": {"delete_archive": True},
    }


@pytest.mark.parametrize(
    ("path", "body", "action"),
    [
        ("base", {}, "base"),
        ("sync", {}, "sync"),
        ("fetch", {"destination": "nas"}, "fetch"),
    ],
)
def test_a_base_backup_the_sending_and_the_fetching_are_jobs_with_sudo_mode(
    client: TestClient, engines, pitr: FakePitr, path: str, body: dict[str, Any], action: str
) -> None:
    assert client.post(f"{PATH}/{path}", json=body).status_code == 403
    elevate(client)

    accepted = client.post(f"{PATH}/{path}", json=body)

    assert accepted.status_code == 202, accepted.text
    assert pitr.queued[0]["kwargs"]["action"] == action  # type: ignore[attr-defined]
    assert pitr.queued[0]["metadata"] == {"engine": "postgresql", "action": action}  # type: ignore[attr-defined]


def test_planning_a_recovery_names_the_base_and_where_the_data_goes(
    client: TestClient, engines, pitr: FakePitr
) -> None:
    response = client.post(f"{PATH}/restore/plan", json={"time": "2026-10-17 12:15:00"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["base"]["id"] == BASE.id
    assert body["target"] == "2026-10-17T12:15:00+00:00"
    assert body["saved_as"].endswith("noust-before-recovery-20261017T130000Z")
    assert body["restarts_engine"] is True and body["missing"] == []


def test_an_instant_that_is_not_one_is_refused(client: TestClient, engines, pitr: FakePitr) -> None:
    response = client.post(f"{PATH}/restore/plan", json={"time": "yesterday"})

    assert response.status_code == 400
    assert "is not a date and time" in response.text


def test_a_recovery_is_planned_first_so_a_refusal_answers_now(
    client: TestClient, engines, pitr: FakePitr
) -> None:
    pitr.refuse = DatabaseBackupError(
        "The log has a gap", details="Missing: 000000010000000000000005"
    )
    elevate(client)

    response = client.post(f"{PATH}/restore", json={"time": "2026-10-17 12:15:00", "confirm": True})

    assert response.status_code >= 400
    assert "gap" in response.text
    assert pitr.queued == []  # type: ignore[attr-defined]


def test_a_recovery_replaces_the_cluster_only_with_sudo_mode_and_confirmation(
    client: TestClient, engines, pitr: FakePitr
) -> None:
    body = {"time": "2026-10-17 12:15:00", "base": BASE.id}

    assert client.post(f"{PATH}/restore", json={**body, "confirm": True}).status_code == 403
    elevate(client)
    unconfirmed = client.post(f"{PATH}/restore", json=body)

    assert unconfirmed.status_code == 400
    assert "replaces the whole cluster" in unconfirmed.text
    assert pitr.queued == []  # type: ignore[attr-defined]

    accepted = client.post(f"{PATH}/restore", json={**body, "confirm": True})

    assert accepted.status_code == 202, accepted.text
    assert pitr.queued[0]["kwargs"] == {  # type: ignore[attr-defined]
        "action": "restore",
        "options": {"time": "2026-10-17 12:15:00", "base": BASE.id},
    }
    assert (
        "plan_restore",
        (datetime(2026, 10, 17, 12, 15, tzinfo=timezone.utc), BASE.id),
    ) in pitr.calls


# ------------------------------------------------------------------ the job


class RecordingManager:
    """Stands in for the manager inside the job."""

    calls: list[tuple[str, Any]] = []

    def __init__(self, manager: Any) -> None:
        pass

    def restore(self, target: Any, *, confirmed: bool, base_id: Any = None) -> dict[str, Any]:
        self.calls.append(("restore", (target, confirmed, base_id)))
        return {"recovered_to": "x"}

    def disable(self, *, confirmed: bool, delete_archive: bool) -> dict[str, Any]:
        self.calls.append(("disable", (confirmed, delete_archive)))
        return {"disabled": True}

    def sync(self) -> dict[str, Any]:
        self.calls.append(("sync", None))
        return {}


@pytest.fixture
def job_environment(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    """The job's collaborators: a manager that records, over a service that answers PostgreSQL."""
    from noust.managers.database.postgres import PostgresManager
    from noust.managers.database.service import DatabaseService

    RecordingManager.calls = []
    monkeypatch.setattr("noust.managers.database.pitr.PitrManager", RecordingManager)
    monkeypatch.setattr(
        DatabaseService, "manager", lambda self, engine: PostgresManager.__new__(PostgresManager)
    )
    return RecordingManager.calls


class Context:
    """A job context that remembers what it was told."""

    def __init__(self) -> None:
        self.metadata: dict[str, Any] = {}
        self.updates: list[tuple[str, int]] = []

    def set_metadata(self, key: str, value: Any) -> None:
        self.metadata[key] = value

    def update(self, message: str, percent: int) -> None:
        self.updates.append((message, percent))


def test_the_job_recovers_with_the_confirmation_the_route_took(
    job_environment: list[tuple[str, Any]],
) -> None:
    from noust.web.jobs import database_pitr_job

    result = database_pitr_job(
        "restore",
        {"time": "2026-10-17 12:15:00", "base": BASE.id},
        job_context=Context(),  # type: ignore[arg-type]
    )

    assert result["status"] == "completed" and result["result"] == {"recovered_to": "x"}
    assert job_environment == [
        ("restore", (datetime(2026, 10, 17, 12, 15, tzinfo=timezone.utc), True, BASE.id))
    ]


def test_the_job_turns_it_off_and_sends_the_archive(
    job_environment: list[tuple[str, Any]],
) -> None:
    from noust.web.jobs import database_pitr_job

    context = Context()
    database_pitr_job("disable", {"delete_archive": True}, job_context=context)  # type: ignore[arg-type]
    database_pitr_job("sync", None, job_context=context)  # type: ignore[arg-type]

    assert job_environment == [("disable", (True, True)), ("sync", None)]


def test_the_job_refuses_an_action_it_does_not_know(
    job_environment: list[tuple[str, Any]],
) -> None:
    from noust.core.exceptions import DatabaseEngineError
    from noust.web.jobs import database_pitr_job

    with pytest.raises(DatabaseEngineError, match="Unsupported"):
        database_pitr_job("drop", None, job_context=Context())  # type: ignore[arg-type]
