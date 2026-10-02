# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the timeline of a stretch (``noust.managers.timeline`` and
``GET /api/timeline``).

The owner selected a CPU peak on a chart and asked what happened. What is
defended:

- **One timeline, in order, from every source**: the journal (warnings and
  worse), the audit trail, deployments, jobs and what the monitor saw (its
  findings, unit failures and recoveries, boots), with the process samples of
  each minute beside it.
- **A source the caller may not read is withheld and says why**, never dropped
  in silence: an operator without ``secrets.reveal`` is told the journal needs
  it, and gets no command lines.
- **An application's stretch is narrowed to it**: its units' journal, the
  audit events that name it, its deployments and jobs, its processes.
- **Bounded**: each source has a ceiling and says when it reached it; the
  process minutes keep the busiest ones.
- **No samples before the collector ran, said**: the answer carries since when
  processes have been sampled.
- **A source that fails is reported with the system's words**, and the others
  are still answered.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.audit.log import AuditLog
from noust.core.exceptions import ValidationError
from noust.core.store import DeploymentRecord, JobRecord
from noust.managers.server.errors import ServerError
from noust.managers.server.journal import Boot, JournalEntry, JournalPage
from noust.managers.timeline import (
    MAX_JOURNAL,
    MAX_PROCESS_MINUTES,
    SOURCES,
    STATE_FAILED,
    STATE_SHOWN,
    STATE_SKIPPED,
    STATE_WITHHELD,
    Timeline,
    TimelineBuilder,
    TimelineReaders,
    parse_boot_time,
)
from noust.monitor.models import ProcessInfo, ProcessObservation
from noust.monitor.observation_store import ObservationStore
from noust.monitor.process_samples import RANK_CPU, RANK_MEMORY, ProcessSample
from noust.monitor.timeseries import EVENT_UNIT_FAILED, MetricsStore
from noust.web.api import audit as audit_api
from noust.web.api import timeline as timeline_api
from noust.web.api.auth import get_current_session
from noust.web.permissions.roles import ADMIN, AUDITOR, OPERATOR

#: The stretch every test reads: the hour before NOW.
NOW = 1_759_400_000
START = NOW - 3_600
END = NOW
SHOP = "shop.example.com"


def iso_utc(seconds: float) -> str:
    """A moment as the journal and the audit log write it."""
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def iso_local(seconds: float) -> str:
    """A moment as the store writes it: naive local time."""
    return datetime.fromtimestamp(seconds).isoformat()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeJournal:
    """The journal: entries per unit (None for the whole machine), and the boots."""

    entries: dict[str | None, list[JournalEntry]] = field(default_factory=dict)
    boot_list: list[Boot] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    error: Exception | None = None

    def read(self, **filters: Any) -> JournalPage:
        self.calls.append(filters)
        if self.error is not None:
            raise self.error
        found = self.entries.get(filters.get("unit"), [])
        lines = int(filters.get("lines", 200))
        return JournalPage(entries=found[-lines:], next_cursor=None, truncated=len(found) > lines)

    def boots(self) -> list[Boot]:
        return list(self.boot_list)


def journal_entry(
    at: float, message: str, *, unit: str = "nginx.service", priority: int = 4
) -> JournalEntry:
    """One journal line."""
    return JournalEntry(
        timestamp=iso_utc(at),
        priority=priority,
        unit=unit,
        message=message,
        pid=42,
        cursor=f"c{at}",
    )


class FakeStore:
    """The store's applications, deployments and jobs."""

    def __init__(self) -> None:
        self.apps = {SHOP: SimpleNamespace(domain=SHOP, app_type="nodejs")}
        self.deployments: list[DeploymentRecord] = []
        self.jobs: list[JobRecord] = []

    def get_app(self, domain: str) -> Any:
        return self.apps.get(domain)

    def list_deployments(
        self, domain: str | None = None, limit: int = 50
    ) -> list[DeploymentRecord]:
        rows = [row for row in self.deployments if domain is None or row.domain == domain]
        return sorted(rows, key=lambda row: row.started_at or "", reverse=True)[:limit]

    def list_jobs(
        self, limit: int = 50, status: str | None = None, domain: str | None = None
    ) -> list[JobRecord]:
        rows = [row for row in self.jobs if domain is None or row.domain == domain]
        return sorted(rows, key=lambda row: row.created_at or "", reverse=True)[:limit]


def write_audit(log: AuditLog, *entries: dict[str, Any]) -> None:
    """Append raw lines to the audit log, oldest first."""
    log.path.parent.mkdir(parents=True, exist_ok=True)
    with open(log.path, "a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")


def audit_line(at: float, action: str, **fields: Any) -> dict[str, Any]:
    """One audit event, the way the log stores it."""
    return {
        "v": 2,
        "ts": iso_utc(at),
        "action": action,
        "result": "ok",
        "cat": "change",
        "sev": 5,
        **fields,
    }


@dataclass
class World:
    """Everything a timeline reads, on fakes and throwaway databases."""

    journal: FakeJournal
    audit: AuditLog
    store: FakeStore
    metrics: MetricsStore
    observations: ObservationStore
    units: dict[str, list[str]]

    def readers(self) -> TimelineReaders:
        return TimelineReaders(
            journal=self.journal,
            audit=self.audit,
            store=self.store,
            metrics=self.metrics,
            observations=self.observations,
            units_of=lambda app: self.units.get(app.domain, []),
            clock=lambda: float(NOW),
        )

    def build(self, **kwargs: Any) -> Timeline:
        return TimelineBuilder(self.readers()).build(START, END, **kwargs)


@pytest.fixture
def world(tmp_path: Path) -> World:
    """A server with one application and nothing recorded yet."""
    return World(
        journal=FakeJournal(),
        audit=AuditLog(tmp_path / "audit" / "audit.log"),
        store=FakeStore(),
        metrics=MetricsStore(tmp_path / "metrics.db", clock=lambda: NOW),
        observations=ObservationStore(tmp_path / "observations.db"),
        units={SHOP: ["shop-example-com"]},
    )


def sample(ts: int, rank: str, position: int, **fields: Any) -> ProcessSample:
    """A stored process sample with defaults."""
    values: dict[str, Any] = {
        "pid": 1000 + position,
        "name": "node",
        "user": "shop",
        "cpu_percent": 10.0,
        "memory_bytes": 50_000_000,
        "memory_percent": 1.0,
        "command": "node server.js",
    }
    values.update(fields)
    return ProcessSample(ts=ts, rank=rank, position=position, **values)


def seed_everything(world: World) -> None:
    """One of each thing, at known moments inside the stretch, and some outside it."""
    world.journal.entries[None] = [
        journal_entry(START + 600, "upstream timed out", unit="nginx.service", priority=3),
    ]
    world.journal.boot_list = [
        Boot(-1, "a" * 32, "Mon 2025-09-01 10:00:00 UTC", "Thu 2025-10-02 09:19:00 UTC"),
        Boot(
            0,
            "b" * 32,
            datetime.fromtimestamp(START + 60, timezone.utc).strftime("%a %Y-%m-%d %H:%M:%S UTC"),
            "now",
        ),
    ]
    write_audit(
        world.audit,
        audit_line(START - 600, "apps.restart", actor="alice", resource=f"app:{SHOP}"),
        audit_line(START + 300, "apps.deploy", actor="alice", resource=f"app:{SHOP}"),
        audit_line(START + 310, "auth.ws_ticket", actor="alice"),
        audit_line(START + 900, "backups.create", actor="cli:root", resource="backup:b1"),
    )
    world.store.deployments = [
        DeploymentRecord(
            id=7,
            domain=SHOP,
            status="failed",
            triggered_by="panel",
            started_at=iso_local(START + 320),
            finished_at=iso_local(START + 420),
            error="build failed",
        ),
        DeploymentRecord(
            id=6,
            domain=SHOP,
            status="success",
            started_at=iso_local(START - 9_000),
            finished_at=iso_local(START - 8_900),
        ),
    ]
    world.store.jobs = [
        JobRecord(
            id="job-1",
            type="backup",
            name="Back up blog.example.com",
            status="completed",
            domain="blog.example.com",
            created_at=iso_local(START + 1_200),
            started_at=iso_local(START + 1_200),
            finished_at=iso_local(START + 1_300),
            actor="bob",
        )
    ]
    world.observations.save(
        ProcessObservation(
            process=ProcessInfo(
                pid=1001,
                name="node",
                user="shop",
                cpu_percent=97.0,
                command="node build.js --token=x",
            ),
            signal="high_cpu",
            severity="warning",
            detail="node used 97% CPU",
            observed_at=datetime.fromtimestamp(START + 1_500),
        )
    )
    world.metrics.record_monitor_event(
        EVENT_UNIT_FAILED, "shop-example-com", detail="failed", reason="failed", ts=START + 1_800
    )
    world.metrics.record_process_samples(
        [
            sample(
                START + 1_500 - 60,
                RANK_CPU,
                1,
                cpu_percent=97.0,
                app=SHOP,
                owner_kind="unit",
                owner="shop-example-com.service",
            ),
            sample(
                START + 1_500 - 60,
                RANK_CPU,
                2,
                pid=812,
                name="postgres",
                cpu_percent=12.0,
                owner_kind="unit",
                owner="postgresql.service",
            ),
            sample(
                START + 1_500 - 60,
                RANK_MEMORY,
                1,
                pid=812,
                name="postgres",
                memory_bytes=900_000_000,
                owner_kind="unit",
                owner="postgresql.service",
            ),
        ]
    )


# ---------------------------------------------------------------------------
# One timeline from every source
# ---------------------------------------------------------------------------


def test_every_source_is_merged_in_order(world: World) -> None:
    """The journal, the audit, deployments, jobs and the monitor, oldest first."""
    seed_everything(world)

    timeline = world.build()

    kinds = [(event.kind, event.source) for event in timeline.events]
    assert kinds == [
        ("boot", "monitor"),
        ("audit", "audit"),
        ("deployment", "deployments"),
        ("journal", "journal"),
        ("audit", "audit"),
        ("job", "jobs"),
        ("observation", "monitor"),
        ("unit_failed", "monitor"),
    ]
    assert [event.at for event in timeline.events] == sorted(event.at for event in timeline.events)
    assert {status.source: status.state for status in timeline.sources} == dict.fromkeys(
        SOURCES, STATE_SHOWN
    )


def test_each_event_carries_who_what_and_what_to_open(world: World) -> None:
    """A deployment links to itself, a job to itself, an audit event names its actor."""
    seed_everything(world)

    events = world.build().events

    deployment = next(e for e in events if e.kind == "deployment")
    assert (deployment.ref, deployment.status, deployment.level, deployment.app) == (
        "7",
        "failed",
        "error",
        SHOP,
    )
    assert deployment.details["error"] == "build failed"
    job = next(e for e in events if e.kind == "job")
    assert (job.ref, job.actor, job.text) == ("job-1", "bob", "Back up blog.example.com")
    deploy = next(e for e in events if e.text == "apps.deploy")
    assert (deploy.actor, deploy.details["target"]) == ("alice", f"app:{SHOP}")
    journal = next(e for e in events if e.kind == "journal")
    assert (journal.text, journal.unit, journal.level) == (
        "upstream timed out",
        "nginx.service",
        "error",
    )


def test_the_journal_is_read_from_warnings_up_over_exactly_the_stretch(world: World) -> None:
    """Warnings and worse of every unit, between two exact moments."""
    world.build(sources=["journal"])

    assert world.journal.calls == [
        {"priority": 4, "since": f"@{START}", "until": f"@{END}", "lines": MAX_JOURNAL}
    ]


def test_the_audit_trail_leaves_out_a_console_reading_its_own_stream(world: World) -> None:
    """A WebSocket ticket is not something that happened to the server."""
    seed_everything(world)

    actions = [e.text for e in world.build(sources=["audit"]).events]

    assert actions == ["apps.deploy", "backups.create"]


def test_the_process_samples_come_by_minute_with_since_when(world: World) -> None:
    """Each minute's top processes, and since when there are samples at all."""
    seed_everything(world)

    processes = world.build(show_commands=True).processes

    assert processes is not None
    assert [minute.at for minute in processes.minutes] == [START + 1_440]
    minute = processes.minutes[0]
    assert [row["name"] for row in minute.cpu] == ["node", "postgres"]
    assert [row["name"] for row in minute.memory] == ["postgres"]
    assert minute.cpu[0]["app"] == SHOP
    assert minute.cpu[0]["command"] == "node server.js"
    assert processes.since == START + 1_440


def test_no_samples_before_the_collector_ran_is_said(world: World) -> None:
    """Never sampled: no minutes, and nothing to say they started."""
    processes = world.build().processes

    assert processes is not None
    assert processes.minutes == []
    assert processes.since is None


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


def test_a_source_the_caller_may_not_read_is_withheld_with_its_permission(world: World) -> None:
    """Without secrets.reveal the journal is not read, and the answer says why."""
    seed_everything(world)

    timeline = world.build(withheld={"journal": "secrets.reveal", "audit": "audit.read"})

    statuses = {status.source: status for status in timeline.sources}
    assert (statuses["journal"].state, statuses["journal"].permission) == (
        STATE_WITHHELD,
        "secrets.reveal",
    )
    assert (statuses["audit"].state, statuses["audit"].permission) == (STATE_WITHHELD, "audit.read")
    assert world.journal.calls == []
    assert not any(event.source in ("journal", "audit") for event in timeline.events)


def test_command_lines_are_only_for_who_may_read_them(world: World) -> None:
    """Process samples and the monitor's findings lose their command line."""
    seed_everything(world)

    timeline = world.build(show_commands=False)

    assert timeline.processes is not None
    assert timeline.processes.commands is False
    assert all(
        row["command"] is None
        for minute in timeline.processes.minutes
        for row in minute.cpu + minute.memory
    )
    finding = next(e for e in timeline.events if e.kind == "observation")
    assert "command" not in finding.details


def test_a_source_not_asked_for_is_skipped(world: World) -> None:
    """``sources`` narrows what is read, and the rest say they were not asked."""
    timeline = world.build(sources=["jobs"])

    assert {s.source: s.state for s in timeline.sources} == {
        **dict.fromkeys(SOURCES, STATE_SKIPPED),
        "jobs": STATE_SHOWN,
    }
    assert timeline.processes is None


# ---------------------------------------------------------------------------
# An application
# ---------------------------------------------------------------------------


def test_an_application_s_stretch_is_narrowed_to_it(world: World) -> None:
    """Its units' journal, every priority; the audit naming it; its deployments, jobs and processes."""
    seed_everything(world)
    world.journal.entries["shop-example-com"] = [
        journal_entry(START + 700, "GET /api 200 12ms", unit="shop-example-com.service", priority=6)
    ]

    timeline = world.build(app=SHOP)

    assert world.journal.calls[0] == {
        "unit": "shop-example-com",
        "since": f"@{START}",
        "until": f"@{END}",
        "lines": MAX_JOURNAL,
    }
    texts = [(e.kind, e.text) for e in timeline.events]
    assert ("journal", "GET /api 200 12ms") in texts
    assert ("audit", "apps.deploy") in texts
    assert ("audit", "backups.create") not in texts
    assert not any(e.kind == "job" for e in timeline.events)
    assert ("unit_failed", "failed") in texts
    assert ("observation", "node used 97% CPU") in texts
    assert timeline.processes is not None
    assert [row["name"] for minute in timeline.processes.minutes for row in minute.cpu] == ["node"]


def test_an_unknown_application_is_refused(world: World) -> None:
    """A typo is an error, not an empty timeline."""
    with pytest.raises(ValidationError):
        world.build(app="nope.example.com")


# ---------------------------------------------------------------------------
# Bounds and failures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("start", "end"), [(END, START), (START, START), (0, END)])
def test_a_stretch_that_cannot_be_answered_is_refused(world: World, start: int, end: int) -> None:
    """Inverted, empty or longer than a month."""
    with pytest.raises(ValidationError):
        TimelineBuilder(world.readers()).build(start, end)


def test_an_unknown_source_is_refused(world: World) -> None:
    """A source name is checked, not ignored."""
    with pytest.raises(ValidationError):
        world.build(sources=["kernel"])


def test_a_source_over_its_ceiling_says_it_was_cut(world: World) -> None:
    """The journal keeps its newest entries and says there were more."""
    world.journal.entries[None] = [
        journal_entry(START + i, f"line {i}") for i in range(MAX_JOURNAL + 5)
    ]

    timeline = world.build(sources=["journal"])

    journal = next(s for s in timeline.sources if s.source == "journal")
    assert (journal.count, journal.truncated) == (MAX_JOURNAL, True)
    assert timeline.events[-1].text == f"line {MAX_JOURNAL + 4}"


def test_more_minutes_than_fit_keep_the_busiest(world: World) -> None:
    """The process minutes keep the busiest ones, in order, and say how many there were."""
    world.metrics.record_process_samples(
        [
            sample(START + minute * 60 - 3_600 * 3, RANK_CPU, 1, cpu_percent=float(minute))
            for minute in range(MAX_PROCESS_MINUTES + 20)
        ]
    )

    timeline = TimelineBuilder(world.readers()).build(START - 3_600 * 3, END, sources=["processes"])

    assert timeline.processes is not None
    kept = timeline.processes.minutes
    assert len(kept) == MAX_PROCESS_MINUTES
    assert timeline.processes.total_minutes == MAX_PROCESS_MINUTES + 20
    assert kept[0].cpu[0]["cpu_percent"] == 20.0
    assert [m.at for m in kept] == sorted(m.at for m in kept)
    status = next(s for s in timeline.sources if s.source == "processes")
    assert status.truncated is True


def test_a_source_that_fails_says_so_and_the_rest_are_answered(world: World) -> None:
    """journalctl failing costs the journal, with its own words, not the timeline."""
    seed_everything(world)
    world.journal.error = ServerError(
        "Could not read the journal", "journalctl failed.", output="Failed to open journal"
    )

    timeline = world.build()

    journal = next(s for s in timeline.sources if s.source == "journal")
    assert (journal.state, journal.message, journal.evidence) == (
        STATE_FAILED,
        "Could not read the journal",
        "Failed to open journal",
    )
    assert any(e.source == "audit" for e in timeline.events)


def test_auditing_turned_off_is_said(world: World) -> None:
    """No trail is not an empty trail."""
    world.audit.enabled = False

    audit = next(s for s in world.build(sources=["audit"]).sources if s.source == "audit")

    assert audit.state == STATE_FAILED
    assert "turned off" in (audit.message or "")


def test_a_boot_time_is_read_in_utc_or_local_time() -> None:
    """journalctl prints boots in UTC or the machine's zone."""
    assert (
        parse_boot_time("Thu 2025-10-02 10:00:00 UTC")
        == datetime(2025, 10, 2, 10, tzinfo=timezone.utc).timestamp()
    )
    assert parse_boot_time("garbage") is None


# ---------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------


@pytest.fixture
def api(world: World, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[FastAPI, list[str]]]:
    """The timeline router on the fake world, recording audit reads."""
    seed_everything(world)
    monkeypatch.setattr(timeline_api, "build_readers", world.readers)
    reads: list[str] = []
    monkeypatch.setattr(
        audit_api, "_record_read", lambda request, session, what: reads.append(what)
    )
    app = FastAPI()
    app.include_router(timeline_api.router, prefix="/api/timeline")
    yield app, reads


def client_as(app: FastAPI, permissions: frozenset[str]) -> TestClient:
    """A client whose session holds these permissions."""
    app.dependency_overrides[get_current_session] = lambda: {"permissions": sorted(permissions)}
    return TestClient(app)


def test_the_api_answers_the_whole_timeline_for_an_administrator(
    api: tuple[FastAPI, list[str]],
) -> None:
    """An admin reads the journal and command lines; not the audit trail, which is not theirs."""
    app, reads = api

    response = client_as(app, ADMIN).get("/api/timeline", params={"start": START, "end": END})

    assert response.status_code == 200, response.text
    body = response.json()
    sources = {s["source"]: s for s in body["sources"]}
    assert sources["journal"]["state"] == "shown"
    assert (sources["audit"]["state"], sources["audit"]["permission"]) == ("withheld", "audit.read")
    assert body["processes"]["commands"] is True
    assert reads == []


def test_the_api_withholds_the_journal_from_an_operator_and_says_why(
    api: tuple[FastAPI, list[str]],
) -> None:
    """An operator sees the rest, and is told the journal needs secrets.reveal."""
    app, _ = api

    body = client_as(app, OPERATOR).get("/api/timeline", params={"start": START, "end": END}).json()

    sources = {s["source"]: s for s in body["sources"]}
    assert (sources["journal"]["state"], sources["journal"]["permission"]) == (
        "withheld",
        "secrets.reveal",
    )
    assert sources["deployments"]["state"] == "shown"
    assert body["processes"]["commands"] is False
    assert all(
        row["command"] is None for minute in body["processes"]["minutes"] for row in minute["cpu"]
    )


def test_reading_the_audit_trail_through_the_timeline_is_on_the_record(
    api: tuple[FastAPI, list[str]],
) -> None:
    """An auditor reads the trail here as on its own page, and that read is recorded."""
    app, reads = api

    body = client_as(app, AUDITOR).get("/api/timeline", params={"start": START, "end": END}).json()

    assert any(event["source"] == "audit" for event in body["events"])
    assert reads == ["audit log"]


def test_the_api_takes_sources_repeated_or_comma_separated(api: tuple[FastAPI, list[str]]) -> None:
    """``sources=jobs,deployments`` and ``sources=jobs&sources=deployments`` are the same."""
    app, _ = api
    client = client_as(app, ADMIN)

    one = client.get(
        "/api/timeline", params={"start": START, "end": END, "sources": "jobs,deployments"}
    ).json()
    two = client.get(
        "/api/timeline",
        params=[("start", START), ("end", END), ("sources", "jobs"), ("sources", "deployments")],
    ).json()

    shown = lambda body: sorted(s["source"] for s in body["sources"] if s["state"] == "shown")  # noqa: E731
    assert shown(one) == shown(two) == ["deployments", "jobs"]


def test_the_api_refuses_a_bad_stretch_with_the_error_contract(
    api: tuple[FastAPI, list[str]],
) -> None:
    """An inverted stretch is a 400 naming the field."""
    app, _ = api

    response = client_as(app, ADMIN).get("/api/timeline", params={"start": END, "end": START})

    assert response.status_code == 400
    assert "end" in response.text
