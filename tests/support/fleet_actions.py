# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_fleet_actions.py`` that other tests build on."""

from __future__ import annotations

from typing import Any

from noust.fleet.aggregate import Asker
from noust.web import fleet_jobs
from noust.web.fleet_jobs import FleetJobs, FleetRequest, Runner, plan
from tests.support.fleet_views_support import Central

ADMIN = Asker(actor="ana", scope="admin", role="admin", elevated=True)


class Recorder:
    """A reporter that keeps what it is told."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []
        self.snapshots: list[dict[str, Any]] = []

    def log(self, message: str, level: str = "info") -> None:
        self.lines.append((level, message))

    def publish(self, snapshot: dict[str, Any], done: int, step: str) -> None:
        self.snapshots.append(snapshot)


def _run(
    central: Central, request: FleetRequest, **kw: Any
) -> tuple[dict[str, Any], Recorder, str]:
    the_plan = plan(request, manager=central.manager, asker=ADMIN)
    recorder = Recorder()
    jobs = FleetJobs(central.store)
    job_id = fleet_jobs.new_job_id()
    jobs.create(job_id, the_plan, created_by="ana")
    snapshot = Runner(
        plan=the_plan,
        job_id=job_id,
        manager=central.manager,
        asker=ADMIN,
        reporter=recorder,
        elevated_until=kw.pop("elevated_until", float("inf")),
        jobs=jobs,
        poll=0,
        sleep=lambda seconds: None,
    ).run()
    return snapshot, recorder, job_id


def _states(snapshot: dict[str, Any]) -> dict[str, tuple[str, str | None]]:
    return {node["node"]: (node["state"], node["reason"]) for node in snapshot["nodes"]}
