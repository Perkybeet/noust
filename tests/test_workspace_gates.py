# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The health gate of an application that runs as several units (a monorepo's workspaces).

One implementation answers "does every workspace answer": the monorepo's own update, the activation
of a release, a rollback and the migration onto releases all ask it, through the same
``restart_and_probe()`` a single unit's gate answers.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.core.exceptions import ServiceError
from noust.core.logger import Logger
from noust.core.store import Service
from noust.deployers.helpers.health_gate import HealthCheck
from noust.deployers.helpers.workspace_gates import WorkspaceGates


class Units:
    """Units that restart, or do not, and are active, or are not."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        self.refuses: set[str] = set()
        self.inactive: set[str] = set()

    def restart(self, name: str) -> None:
        self.events.append(("restart", name))
        if name in self.refuses:
            raise ServiceError(f"Failed to restart {name}")

    def logs(self, name: str, lines: int = 50) -> str:
        return f"{name}: journal"

    def get_status(self, name: str) -> dict[str, Any]:
        return {"exists": True, "active": name not in self.inactive}


def gates(units: Units, services: list[Service], probe: Any, **kwargs: Any) -> WorkspaceGates:
    return WorkspaceGates(
        services,
        check=HealthCheck(),
        services=units,  # type: ignore[arg-type]
        logger=Logger(),
        probe=probe,
        settle=0,
        **kwargs,
    )


WEB = Service(name="app-web", port=3001)
API = Service(name="app-api", port=3002)
WORKER = Service(name="app-worker")


def test_every_unit_restarts_before_any_is_probed() -> None:
    units = Units()
    order: list[str] = []

    def probe(url: str, **kwargs: Any) -> bool:
        order.append(f"probe {url}")
        return True

    healthy, evidence = gates(units, [WEB, API], probe).restart_and_probe()

    assert (healthy, evidence) == (True, "")
    assert units.events == [("restart", "app-web"), ("restart", "app-api")]
    assert order == ["probe http://127.0.0.1:3001/", "probe http://127.0.0.1:3002/"]


def test_one_workspace_that_does_not_answer_fails_the_application() -> None:
    units = Units()

    def probe(url: str, *, on_attempt: Any = None, **kwargs: Any) -> bool:
        if url.endswith(":3002/"):
            on_attempt("Health check attempt 1 failed: HTTP 502")
            return False
        return True

    healthy, evidence = gates(units, [WEB, API], probe).restart_and_probe()

    assert healthy is False
    assert "app-api" in evidence and "HTTP 502" in evidence and "app-api: journal" in evidence
    assert "app-web" not in evidence


def test_a_unit_that_cannot_be_restarted_is_a_failure_and_is_not_probed() -> None:
    units = Units()
    units.refuses.add("app-api")
    probed: list[str] = []

    def probe(url: str, **kwargs: Any) -> bool:
        probed.append(url)
        return True

    outcome = gates(units, [WEB, API], probe).outcome()

    assert outcome.healthy is False
    assert outcome.restarted == ("app-web",)
    assert probed == ["http://127.0.0.1:3001/"]
    assert "Failed to restart app-api" in outcome.evidence


def test_a_worker_has_to_be_running() -> None:
    units = Units()
    units.inactive.add("app-worker")

    outcome = gates(units, [WEB, WORKER], lambda url, **kw: True).outcome()

    assert outcome.healthy is False
    assert "app-worker is not running" in outcome.evidence


@pytest.mark.parametrize("units_list", [[], [WORKER]])
def test_a_rehearsal_restarts_but_probes_nothing(units_list: list[Service]) -> None:
    probed: list[str] = []

    outcome = gates(
        Units(), units_list, lambda url, **kw: probed.append(url) or True, rehearsing=True
    ).outcome()

    assert outcome.healthy is True
    assert probed == []
