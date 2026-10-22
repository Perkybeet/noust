# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/engines/{engine}/limits`` (checklist C-30).

The routes are a client of the service: what is pinned here is the HTTP side of it. Reading needs
``databases.read``; changing the limits needs ``databases.manage`` and sudo mode; a memory limit
below the engine's use comes back as a confirmation request with its warning; the engine's journal
reaches the client verbatim when a change is put back.
"""

# ruff: noqa: F811

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.exceptions import ConfirmationRequired, DatabaseEngineError, ValidationError
from noust.managers.database.host_limits import LimitsOutcome, LimitsStatus, LimitValues
from noust.managers.database.service import DatabaseService
from noust.web.permissions import Permission
from noust.web.permissions.routes_databases import ROUTES
from tests.test_web_databases_api import (  # noqa: F401 - fixtures
    anonymous,
    app,
    client,
    elevate,
    engines,
)

PATH = "/api/databases/engines/postgresql/limits"


def status() -> LimitsStatus:
    """PostgreSQL with 2 GB set, which the kernel enforces."""
    return LimitsStatus(
        engine="postgresql",
        display_name="PostgreSQL",
        unit="postgresql@16-main",
        file="/etc/systemd/system/postgresql@16-main.service.d/90-noust-limits.conf",
        configured=LimitValues(memory_mb=2048),
        effective=LimitValues(memory_mb=2048),
        enforced={"memory_mb": True, "cpus": True, "swap_mb": None, "tasks": True},
        memory_in_use_mb=310,
        server_memory_mb=8192,
        cpus=4,
        minimum_memory_mb=256,
    )


def outcome() -> LimitsOutcome:
    """A change of the memory limit."""
    return LimitsOutcome(
        engine="postgresql",
        display_name="PostgreSQL",
        file="/etc/systemd/system/postgresql@16-main.service.d/90-noust-limits.conf",
        changed=["memory_mb"],
        configured=LimitValues(memory_mb=4096),
        warnings=["systemd holds MemoryMax and the kernel does not enforce it"],
    )


def test_the_routes_declare_their_permission() -> None:
    assert ROUTES[("GET", "/api/databases/engines/{engine}/limits")] == Permission.DATABASES_READ
    assert ROUTES[("PUT", "/api/databases/engines/{engine}/limits")] == Permission.DATABASES_MANAGE


def test_reading_the_limits_returns_what_is_set_held_and_enforced(
    client: TestClient, engines, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(DatabaseService, "engine_limits", lambda self, engine: status())

    response = client.get(PATH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["unit"] == "postgresql@16-main"
    assert body["configured"] == {"memory_mb": 2048, "cpus": None, "swap_mb": None, "tasks": None}
    assert body["enforced"] == {"memory_mb": True, "cpus": True, "swap_mb": None, "tasks": True}
    assert body["memory_in_use_mb"] == 310 and body["minimum_memory_mb"] == 256


def test_reading_needs_a_session(anonymous: TestClient, engines) -> None:
    assert anonymous.get(PATH).status_code in (401, 403)


def test_changing_the_limits_needs_sudo_mode(
    client: TestClient, engines, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, dict[str, Any], list[str], bool]] = []

    def change(self, engine, changes=None, *, clear=(), confirm=False):
        calls.append((engine, dict(changes or {}), list(clear), confirm))
        return outcome()

    monkeypatch.setattr(DatabaseService, "change_engine_limits", change)
    body = {"memory_mb": 4096, "cpus": 2.5, "clear": ["tasks"]}

    refused = client.put(PATH, json=body)
    assert refused.status_code == 403, refused.text
    assert calls == []

    elevate(client)
    accepted = client.put(PATH, json=body)

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["changed"] == ["memory_mb"]
    assert accepted.json()["warnings"] == [
        "systemd holds MemoryMax and the kernel does not enforce it"
    ]
    assert calls == [("postgresql", {"memory_mb": 4096, "cpus": 2.5}, ["tasks"], False)]


def test_a_limit_left_out_is_not_sent(
    client: TestClient, engines, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []

    def change(self, engine, changes=None, *, clear=(), confirm=False):
        seen.append(dict(changes or {}))
        return outcome()

    monkeypatch.setattr(DatabaseService, "change_engine_limits", change)
    elevate(client)

    client.put(PATH, json={"swap_mb": 0})

    assert seen == [{"swap_mb": 0}]


@pytest.mark.parametrize(
    "body",
    [
        {"memory_mb": 0},
        {"cpus": -1},
        {"swap_mb": -5},
        {"tasks": 0},
        {"clear": ["a", "b", "c", "d", "e"]},
    ],
)
def test_a_limit_out_of_range_is_refused_before_the_service(
    client: TestClient, engines, monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]
) -> None:
    monkeypatch.setattr(
        DatabaseService, "change_engine_limits", lambda *a, **k: pytest.fail("reached the service")
    )
    elevate(client)

    assert client.put(PATH, json=body).status_code == 422


def test_a_change_to_confirm_comes_back_with_its_warning(
    client: TestClient, engines, monkeypatch: pytest.MonkeyPatch
) -> None:
    def change(self, engine, changes=None, *, clear=(), confirm=False):
        raise ConfirmationRequired(["PostgreSQL uses 900 MB now, more than the 512 MB limit."])

    monkeypatch.setattr(DatabaseService, "change_engine_limits", change)
    elevate(client)

    response = client.put(PATH, json={"memory_mb": 512})

    assert response.status_code == 400, response.text
    assert response.json()["error"] == "confirmation_required"
    assert "uses 900 MB now" in response.json()["warnings"][0]


def test_confirming_reaches_the_service(
    client: TestClient, engines, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[bool] = []

    def change(self, engine, changes=None, *, clear=(), confirm=False):
        seen.append(confirm)
        return outcome()

    monkeypatch.setattr(DatabaseService, "change_engine_limits", change)
    elevate(client)

    client.put(PATH, json={"memory_mb": 512, "confirm": True})

    assert seen == [True]


def test_a_change_that_was_put_back_carries_the_journal(
    client: TestClient, engines, monkeypatch: pytest.MonkeyPatch
) -> None:
    def change(self, engine, changes=None, *, clear=(), confirm=False):
        raise DatabaseEngineError(
            "PostgreSQL stopped answering under the new limits",
            details="The previous limits were put back.",
            output="FATAL:  the database system is shutting down",
        )

    monkeypatch.setattr(DatabaseService, "change_engine_limits", change)
    elevate(client)

    response = client.put(PATH, json={"memory_mb": 1024})

    assert response.status_code >= 400
    assert "stopped answering" in response.text
    assert "the database system is shutting down" in response.text


def test_an_engine_in_a_container_is_refused_with_where_its_limits_are(
    client: TestClient, engines, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(self, engine):
        raise ValidationError(
            "Noust does not configure an engine that runs in a container",
            details="Its image and the project's compose file configure it.",
            field="engine",
        )

    monkeypatch.setattr(DatabaseService, "engine_limits", refuse)

    response = client.get("/api/databases/engines/postgresql@pg16/limits")

    assert response.status_code == 400
