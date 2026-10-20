# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/replicas``: read replicas of the host's PostgreSQL, MySQL and MariaDB (4.0, C-14).

The routes are a client of the replication managers, which ``noust db replica`` calls too: what is
pinned here is the HTTP side. Reading and planning need ``databases.read``; making room for a
replica, showing its join code again, joining, promoting and removing need ``databases.manage``
and sudo mode. A restart, a replaced data directory or a promotion is refused without ``confirm``
before anything is done, in the words of ``confirmation_required``. The join code holds the
replication password: it is in the answer that made it and the one that shows it again, never in a
job's arguments of record, a plan or a status.
"""

# ruff: noqa: F811

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.exceptions import ValidationError
from noust.managers.database.replication_base import PreparePlan
from noust.web.api.deps import JobAcceptedResponse
from noust.web.permissions import Permission
from noust.web.permissions.routes_database_replicas import ROUTES
from tests.test_web_databases_api import (  # noqa: F401 - fixtures
    anonymous,
    app,
    client,
    elevate,
    engines,
)

PATH = "/api/databases/replicas"
CODE = "noust-replica:v1:SECRET-JOIN-CODE"
PREPARE = {
    "name": "ana",
    "source": "203.0.113.7",
    "host": "db.example.com",
    "listen": "*",
    "open_firewall": True,
}


class FakeJoinPlan:
    """What the manager's plan of a join gives back."""

    def __init__(self, problems: list[str] | None = None) -> None:
        self.problems = problems or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": "ana",
            "primary": "db.example.com:5432",
            "account": "noust_repl_ana",
            "slot": "noust_ana",
            "data_directory": "/var/lib/postgresql/16/main",
            "saved_as": "/var/lib/postgresql/16/main.noust-before-replica",
            "databases": ["shop"],
            "free_bytes": 10**10,
            "needed_bytes": 10**9,
            "capacity": {"max_connections": 100},
            "problems": self.problems,
            "warnings": [],
            "ok": not self.problems,
        }


class FakeReplication:
    """Stands in for a replication manager: records what it was asked."""

    DISPLAY = "PostgreSQL"

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.restart = True
        self.problems: list[str] = []
        self.refuse: Exception | None = None

    def status(self) -> dict[str, Any]:
        return {
            "engine": "postgresql",
            "role": "primary",
            "replicas": [
                {
                    "name": "ana",
                    "state": "streaming",
                    "source": "203.0.113.7",
                    "tls": True,
                    "tls_version": "TLSv1.3",
                    "lag_bytes": 0,
                    "retained_bytes": 0,
                }
            ],
            "follows": None,
            "warnings": [],
        }

    def plan_prepare(self, name: str, **kwargs: Any) -> PreparePlan:
        self.calls.append(("plan_prepare", {"name": name, **kwargs}))
        if self.refuse:
            raise self.refuse
        return PreparePlan(
            name=name,
            account=f"noust_repl_{name}",
            slot=f"noust_{name}",
            host=kwargs["host"],
            port=5432,
            source=kwargs["source"],
            listen=kwargs["listen"],
            restart=self.restart,
            firewall=kwargs["open_firewall"],
            steps=["Create the account"],
            problems=self.problems,
        )

    def prepare(self, name: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("prepare", {"name": name, **kwargs}))
        return {"token": CODE, "plan": {"name": name}, "record": {"name": name}}

    def token(self, name: str) -> str:
        self.calls.append(("token", name))
        return CODE

    def plan_join(self, code: str) -> FakeJoinPlan:
        self.calls.append(("plan_join", code))
        return FakeJoinPlan(self.problems)

    def join(self, code: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("join", {"code": code, **kwargs}))
        return {"name": "ana", "primary": "db.example.com:5432", "saved_as": "/x"}

    def promote(self, *, confirmed: bool) -> dict[str, Any]:
        self.calls.append(("promote", confirmed))
        return {"promoted": True, "note": "Point your applications at this server."}

    def remove(self, name: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("remove", {"name": name, **kwargs}))
        return {"removed": True, "steps": ["Dropped the slot"]}


@pytest.fixture
def replicas(monkeypatch: pytest.MonkeyPatch) -> FakeReplication:
    """The fake manager, and a queue that only records the jobs it is handed."""
    fake = FakeReplication()
    monkeypatch.setattr(
        "noust.web.api.databases.replicas._replication", lambda session, engine: fake
    )
    monkeypatch.setattr("noust.web.api.databases.replicas._engine_of", lambda token: "postgresql")
    queued: list[dict[str, Any]] = []

    def queue(session: Any, **kwargs: Any) -> JobAcceptedResponse:
        queued.append(kwargs)
        return JobAcceptedResponse(
            job_id="job-1", status="pending", message=kwargs["message"], job={}
        )

    monkeypatch.setattr("noust.web.api.databases.replicas.queue", queue)
    fake.queued = queued  # type: ignore[attr-defined]
    return fake


def test_the_routes_declare_their_permission() -> None:
    assert ROUTES[("GET", PATH)] == Permission.DATABASES_READ
    assert ROUTES[("GET", f"{PATH}/{{engine}}")] == Permission.DATABASES_READ
    assert ROUTES[("POST", f"{PATH}/{{engine}}/plan")] == Permission.DATABASES_READ
    for method, path in (
        ("POST", f"{PATH}/{{engine}}"),
        ("POST", f"{PATH}/{{engine}}/{{name}}/token"),
        ("POST", f"{PATH}/join/plan"),
        ("POST", f"{PATH}/join"),
        ("POST", f"{PATH}/{{engine}}/promote"),
        ("DELETE", f"{PATH}/{{engine}}/{{name}}"),
    ):
        assert ROUTES[(method, path)] == Permission.DATABASES_MANAGE, (method, path)


def test_every_route_needs_a_session(anonymous: TestClient, engines, replicas) -> None:
    assert anonymous.get(PATH).status_code in (401, 403)
    assert anonymous.post(f"{PATH}/postgresql", json=PREPARE).status_code in (401, 403)
    assert anonymous.post(f"{PATH}/join", json={"token": CODE}).status_code in (401, 403)


def test_the_status_of_one_engine_is_a_read(client: TestClient, engines, replicas) -> None:
    response = client.get(f"{PATH}/postgresql")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["role"] == "primary" and body["replicas"][0]["state"] == "streaming"
    assert CODE not in response.text


def test_an_engine_that_has_no_replicas_is_refused(client: TestClient, engines, replicas) -> None:
    assert client.get(f"{PATH}/redis").status_code == 400
    assert client.post(f"{PATH}/redis/plan", json=PREPARE).status_code == 400


def test_planning_changes_nothing_and_needs_no_sudo_mode(
    client: TestClient, engines, replicas: FakeReplication
) -> None:
    response = client.post(f"{PATH}/postgresql/plan", json=PREPARE)

    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["restart"] is True and plan["slot"] == "noust_ana" and plan["ok"] is True
    assert [call[0] for call in replicas.calls] == ["plan_prepare"]


def test_a_plan_that_has_problems_is_still_an_answer(
    client: TestClient, engines, replicas: FakeReplication
) -> None:
    replicas.problems = ["PostgreSQL's certificate does not cover db.example.com"]

    response = client.post(f"{PATH}/postgresql/plan", json=PREPARE)

    assert response.status_code == 200
    assert response.json()["ok"] is False and response.json()["problems"]


def test_making_room_needs_sudo_mode_and_the_restart_confirmed(
    client: TestClient, engines, replicas: FakeReplication
) -> None:
    assert client.post(f"{PATH}/postgresql", json={**PREPARE, "confirm": True}).status_code == 403
    elevate(client)

    unconfirmed = client.post(f"{PATH}/postgresql", json=PREPARE)

    assert unconfirmed.status_code == 400, unconfirmed.text
    assert unconfirmed.json()["error"] == "confirmation_required"
    assert "restarts PostgreSQL" in " ".join(unconfirmed.json()["warnings"])
    assert not [call for call in replicas.calls if call[0] == "prepare"]

    accepted = client.post(f"{PATH}/postgresql", json={**PREPARE, "confirm": True})

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["token"] == CODE
    prepared = next(call for call in replicas.calls if call[0] == "prepare")[1]
    assert prepared["confirmed"] is True and prepared["open_firewall"] is True


def test_nothing_is_asked_when_nothing_restarts(
    client: TestClient, engines, replicas: FakeReplication
) -> None:
    replicas.restart = False
    elevate(client)

    assert client.post(f"{PATH}/postgresql", json=PREPARE).status_code == 200


def test_a_plan_that_cannot_go_ahead_is_refused_with_its_problems(
    client: TestClient, engines, replicas: FakeReplication
) -> None:
    replicas.problems = ["The replica's address is the primary's own"]
    elevate(client)

    response = client.post(f"{PATH}/postgresql", json={**PREPARE, "confirm": True})

    assert response.status_code == 400
    assert "The replica's address is the primary's own" in response.text
    assert not [call for call in replicas.calls if call[0] == "prepare"]


def test_the_manager_refusing_is_a_400_with_its_words(
    client: TestClient, engines, replicas: FakeReplication
) -> None:
    replicas.refuse = ValidationError("Replica names are lowercase", field="name")
    elevate(client)

    response = client.post(f"{PATH}/postgresql", json={**PREPARE, "confirm": True})

    assert response.status_code == 400 and "lowercase" in response.json()["detail"]


def test_the_join_code_is_shown_again_with_sudo_mode_and_audited(
    client: TestClient, engines, replicas: FakeReplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    monkeypatch.setattr("noust.core.audit.record", lambda event, **kwargs: events.append(event))
    assert client.post(f"{PATH}/postgresql/ana/token").status_code == 403
    elevate(client)

    response = client.post(f"{PATH}/postgresql/ana/token")

    assert response.status_code == 200, response.text
    assert response.json() == {"name": "ana", "token": CODE}
    assert "db.replica.token" in events


def test_joining_is_a_job_that_must_be_confirmed_and_planned_first(
    client: TestClient, engines, replicas: FakeReplication
) -> None:
    elevate(client)

    unconfirmed = client.post(f"{PATH}/join", json={"token": CODE})

    assert unconfirmed.status_code == 400, unconfirmed.text
    assert unconfirmed.json()["error"] == "confirmation_required"
    assert replicas.queued == []  # type: ignore[attr-defined]

    accepted = client.post(
        f"{PATH}/join", json={"token": CODE, "replace_data": True, "discard_previous": True}
    )

    assert accepted.status_code == 202, accepted.text
    (job,) = replicas.queued  # type: ignore[attr-defined]
    assert job["kwargs"]["token"] == CODE and job["kwargs"]["discard_previous"] is True
    # The code is not in what the job is called or described by.
    assert CODE not in repr({k: v for k, v in job.items() if k != "kwargs"})


def test_a_join_that_cannot_go_ahead_answers_now(
    client: TestClient, engines, replicas: FakeReplication
) -> None:
    replicas.problems = ["There is no room for the copy"]
    elevate(client)

    response = client.post(f"{PATH}/join", json={"token": CODE, "replace_data": True})

    assert response.status_code == 400 and "no room" in response.text
    assert replicas.queued == []  # type: ignore[attr-defined]


def test_the_join_plan_hides_the_password_and_is_not_a_change(
    client: TestClient, engines, replicas: FakeReplication
) -> None:
    response = client.post(f"{PATH}/join/plan", json={"token": CODE})

    assert response.status_code == 200, response.text
    assert response.json()["slot"] == "noust_ana"
    assert CODE not in response.text


def test_promoting_must_be_confirmed(
    client: TestClient, engines, replicas: FakeReplication
) -> None:
    elevate(client)

    unconfirmed = client.post(f"{PATH}/postgresql/promote", json={})
    confirmed = client.post(f"{PATH}/postgresql/promote", json={"confirm": True})

    assert unconfirmed.status_code == 400
    assert unconfirmed.json()["error"] == "confirmation_required"
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["promoted"] is True
    assert ("promote", True) in replicas.calls


def test_removing_must_be_confirmed_and_can_put_the_address_back(
    client: TestClient, engines, replicas: FakeReplication
) -> None:
    elevate(client)

    unconfirmed = client.delete(f"{PATH}/postgresql/ana")
    confirmed = client.delete(
        f"{PATH}/postgresql/ana", params={"confirm": True, "restore_listen": True}
    )

    assert unconfirmed.status_code == 400
    assert confirmed.status_code == 200, confirmed.text
    removed = next(call for call in replicas.calls if call[0] == "remove")[1]
    assert removed == {"name": "ana", "confirmed": True, "restore_listen": True}
