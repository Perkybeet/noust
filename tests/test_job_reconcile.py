# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A job whose work runs in a transient unit is reconciled after a console restart, not lost.

Seen in production on 2026-10-02: ``apt-get upgrade`` ran in its own unit and
finished well at 16:45:13, but the ``noust`` package among the upgrades
restarted ``noust-web`` at 16:45:10 and the new console marked the job
"Interrupted by a panel restart" without asking how the unit ended; the
central's fleet job, tolerating no failure, then skipped the other nodes.

What is pinned here, with a fake runner standing in for systemd: a unit that
ended well completes its job with the whole log; one that failed fails it with
its output; one still running is followed to its end; a job with no unit
recorded is interrupted as before; a job started by an older console is found
again through its own record; and the job records its unit the moment it
starts.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from noust.core.store import JobRecord, NoustStore
from noust.managers.server.updates import RecordStore, UpdateRecord
from noust.managers.transient_unit import (
    UNIT_FAILURE_ID,
    UNIT_SUCCESS_ID,
    JobVerdict,
    UnitEnding,
    UnitState,
    ending_of,
    entries_from_lines,
    parse_journal_entries,
)
from noust.web import job_reconcile
from noust.web.job_reconcile import Followed, UnitJobKind, reconcile_job, unlogged
from noust.web.jobs import INTERRUPTED_REASON, JobManager
from tests.server_support import (  # noqa: F401 - the fixture registers itself
    SequencedRunner,
    no_package_manager_running,
)

UNIT = "noust-os-update-0a1b2c3d"


def _entry(cursor: str, message: str, **extra: Any) -> str:
    return json.dumps({"__CURSOR": cursor, "MESSAGE": message, **extra})


def _journal(*entries: str) -> dict[str, str]:
    return {"stdout": "".join(f"{entry}\n" for entry in entries)}


GONE = {"stdout": "LoadState=not-found\nActiveState=inactive\nResult=success\n"}
RUNNING = {"stdout": "LoadState=loaded\nActiveState=active\nSubState=running\n"}
DEACTIVATED = _entry("c9", f"{UNIT}.service: Deactivated successfully.", MESSAGE_ID=UNIT_SUCCESS_ID)
FAILED = _entry(
    "c9",
    f"{UNIT}.service: Failed with result 'exit-code'.",
    MESSAGE_ID=UNIT_FAILURE_ID,
    UNIT_RESULT="exit-code",
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def _job(
    store: NoustStore,
    tmp_path: Path,
    *,
    job_id: str = "job1",
    job_type: str = "custom",
    unit: str | None = UNIT,
    logged: tuple[str, ...] = (),
    status: str = "running",
) -> JobRecord:
    log = tmp_path / f"{job_id}.log"
    log.write_text(
        "".join(f"[2026-10-02 16:45:0{i}] [INFO] {line}\n" for i, line in enumerate(logged))
    )
    store.create_job(
        JobRecord(
            id=job_id,
            type=job_type,
            name="Apply all updates",
            description="d",
            status=status,
            log_path=str(log),
            started_at="2026-10-02T16:44:00",
        )
    )
    if unit is not None:
        store.update_job(job_id, unit=unit)
    record = store.get_job(job_id)
    assert record is not None
    return record


def _log(job: JobRecord) -> list[str]:
    assert job.log_path is not None
    return [line.split("] ", 2)[2] for line in Path(job.log_path).read_text().splitlines()]


def _reconcile(
    store: NoustStore,
    job: JobRecord,
    runner: SequencedRunner,
    kind: UnitJobKind | None = None,
    **kw: Any,
) -> str:
    return reconcile_job(
        store,
        Followed(job=job, unit=job.unit or UNIT, kind=kind or UnitJobKind()),
        runner=runner,
        sleep=lambda seconds: None,
        **kw,
    )


class TestEnding:
    def test_systemd_still_knowing_the_unit_says_how_it_ended(self) -> None:
        state = UnitState(loaded=True, active=False, result="exit-code", exit_status=100)

        assert ending_of(UNIT, state, []) == UnitEnding("failed", "exit-code", 100)

    def test_a_collected_unit_is_read_from_systemds_line_in_its_journal(self) -> None:
        gone = UnitState(loaded=False, active=False, result="success")

        assert ending_of(UNIT, gone, parse_journal_entries(DEACTIVATED)).status == "succeeded"
        ending = ending_of(UNIT, gone, parse_journal_entries(FAILED))
        assert (ending.status, ending.result) == ("failed", "exit-code")

    def test_a_journal_read_as_text_is_read_by_systemds_words(self) -> None:
        gone = UnitState(loaded=False, active=False)
        lines = ["Setting up noust (3.2.0) ...", f"{UNIT}.service: Succeeded."]

        assert ending_of(UNIT, gone, entries_from_lines(lines)).status == "succeeded"

    def test_the_program_saying_succeeded_is_not_systemds_verdict(self) -> None:
        gone = UnitState(loaded=False, active=False)

        ending = ending_of(UNIT, gone, entries_from_lines(["Deactivated successfully."]))

        assert ending.status == "unknown"

    def test_a_unit_running_or_unanswered_has_not_ended(self) -> None:
        assert not ending_of(UNIT, UnitState(loaded=True, active=True), []).ended
        unanswered = UnitState(loaded=True, active=True, known=False)
        assert not ending_of(UNIT, unanswered, []).ended


class TestUnlogged:
    def test_only_what_the_log_does_not_have_yet_is_added(self) -> None:
        logged = ["Job started", "Starting the update", "Reading package lists...", "Unpacking a"]
        output = ["Reading package lists...", "Unpacking a", "Unpacking noust", "Setting up b"]

        assert unlogged(logged, output) == ["Unpacking noust", "Setting up b"]

    def test_a_log_that_never_relayed_the_unit_gets_all_of_it(self) -> None:
        assert unlogged(["Job started"], ["a", "b"]) == ["a", "b"]


class TestReconcile:
    def test_a_unit_that_ended_well_completes_the_job_with_the_whole_log(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        job = _job(store, tmp_path, logged=("Job started", "Reading package lists...", "Unpack a"))
        runner = SequencedRunner()
        runner.sequence(["systemctl", "show"], [GONE])
        runner.sequence(
            ["journalctl"],
            [
                _journal(
                    _entry("c1", "Reading package lists..."),
                    _entry("c2", "Unpack a"),
                    _entry("c3", "Setting up noust (3.2.0) ..."),
                    DEACTIVATED,
                ),
                _journal(),
            ],
        )

        assert _reconcile(store, job, runner) == "completed"

        row = store.get_job("job1")
        assert row is not None and row.status == "completed" and not row.error
        assert row.finished_at is not None
        lines = _log(row)
        assert lines.count("Unpack a") == 1
        assert "Setting up noust (3.2.0) ..." in lines
        assert lines[-1] == "Job completed successfully"

    def test_a_unit_that_failed_fails_the_job_with_its_output(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        job = _job(store, tmp_path)
        runner = SequencedRunner()
        runner.sequence(["systemctl", "show"], [GONE])
        runner.sequence(
            ["journalctl"],
            [
                _journal(
                    _entry("c1", "E: Sub-process /usr/bin/dpkg returned an error code (1)"), FAILED
                ),
                _journal(),
            ],
        )

        assert _reconcile(store, job, runner) == "failed"

        row = store.get_job("job1")
        assert row is not None and row.status == "failed"
        assert row.error is not None
        assert "exit-code" in row.error and "dpkg returned an error code" in row.error
        assert INTERRUPTED_REASON not in row.error

    def test_a_unit_still_running_is_followed_to_its_end(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        job = _job(store, tmp_path, logged=("Job started", "Unpack a"))
        runner = SequencedRunner()
        runner.sequence(["systemctl", "show"], [RUNNING, RUNNING, GONE])
        runner.sequence(
            ["journalctl"],
            [
                _journal(_entry("c1", "Unpack a")),
                _journal(_entry("c2", "Setting up noust (3.2.0) ...")),
                _journal(DEACTIVATED),
                _journal(),
            ],
        )

        assert _reconcile(store, job, runner) == "completed"

        row = store.get_job("job1")
        assert row is not None and row.status == "completed"
        assert "Setting up noust (3.2.0) ..." in _log(row)
        follows = [call for call in runner.calls if call[0] == "journalctl"]
        assert "--after-cursor=c1" in follows[1]

    def test_a_unit_that_never_ends_is_given_up_with_how_to_follow_it(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        job = _job(store, tmp_path)
        runner = SequencedRunner()
        runner.sequence(["systemctl", "show"], [RUNNING])
        ticks = iter(range(0, 10_000, 100))

        status = _reconcile(
            store, job, runner, UnitJobKind(max_seconds=250), clock=lambda: float(next(ticks))
        )

        row = store.get_job("job1")
        assert status == "failed" and row is not None
        assert row.error is not None and f"journalctl -fu {UNIT}" in row.error

    def test_the_kinds_verdict_is_the_jobs_result(self, store: NoustStore, tmp_path: Path) -> None:
        job = _job(store, tmp_path)
        runner = SequencedRunner()
        runner.sequence(["systemctl", "show"], [GONE])
        runner.sequence(["journalctl"], [_journal(DEACTIVATED)])
        seen: list[tuple[str, str]] = []

        def verdict(job: JobRecord, unit: str, ending: UnitEnding, lines: list[str]) -> JobVerdict:
            seen.append((unit, ending.status))
            return JobVerdict(True, result={"packages": ["openssl"]})

        _reconcile(store, job, runner, UnitJobKind(verdict=verdict))

        row = store.get_job("job1")
        assert seen == [(UNIT, "succeeded")]
        assert row is not None and json.loads(row.result_json or "{}") == {"packages": ["openssl"]}

    def test_whoever_listens_hears_the_job_end(self, store: NoustStore, tmp_path: Path) -> None:
        job = _job(store, tmp_path)
        runner = SequencedRunner()
        runner.sequence(["systemctl", "show"], [GONE])
        runner.sequence(["journalctl"], [_journal(DEACTIVATED)])
        published: list[str] = []

        _reconcile(store, job, runner, publish=published.append)

        assert published == ["job1"]


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        job_reconcile, "record_audit", lambda event, **kw: events.append({"event": event, **kw})
    )
    return events


class TestAudit:
    """A job finished from its unit leaves the line its own function would have."""

    def _os_update_kind(self, tmp_path: Path, status: str) -> UnitJobKind:
        records = RecordStore(tmp_path / "updates")
        records.write(
            UpdateRecord(
                id="0a1b2c3d",
                scope="all",
                status=status,
                error="The package manager exited with code 100" if status == "failed" else None,
                unit=UNIT,
                job_id="job1",
            )
        )
        return job_reconcile.job_kinds(update_records=records)["os_update"]

    def test_an_update_that_ended_well_is_audited_as_finished(
        self, store: NoustStore, tmp_path: Path, audited: list[dict[str, Any]]
    ) -> None:
        job = replace(_job(store, tmp_path, job_type="os_update"), actor="cli:yago")
        runner = SequencedRunner()
        runner.sequence(["systemctl", "show"], [GONE])
        runner.sequence(["journalctl"], [_journal(DEACTIVATED)])

        _reconcile(store, job, runner, self._os_update_kind(tmp_path, "completed"))

        [event] = audited
        assert event["event"] == "server.update"
        assert event["target"] == "packages"
        assert event["outcome"] == "ok"
        assert event["details"]["stage"] == "finished"
        assert event["details"]["action"] == "apply"
        assert event["details"]["update"] == "0a1b2c3d"
        assert event["details"]["reconciled"] is True
        assert event["actor"].label == "cli:yago"
        assert event["correlation_id"] == "job-job1"

    def test_an_update_that_failed_is_audited_as_a_failure_with_why(
        self, store: NoustStore, tmp_path: Path, audited: list[dict[str, Any]]
    ) -> None:
        job = _job(store, tmp_path, job_type="os_update")
        runner = SequencedRunner()
        runner.sequence(["systemctl", "show"], [GONE])
        runner.sequence(["journalctl"], [_journal(FAILED)])

        _reconcile(store, job, runner, self._os_update_kind(tmp_path, "failed"))

        [event] = audited
        assert event["event"] == "server.update" and event["outcome"] == "failure"
        assert event["details"]["error"] == "The package manager exited with code 100"

    def test_a_job_that_could_not_be_followed_is_audited_as_a_failure(
        self,
        store: NoustStore,
        tmp_path: Path,
        audited: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        job = _job(store, tmp_path, job_type="os_update")

        def broken(*args: Any, **kwargs: Any) -> str:
            raise RuntimeError("journal unreadable")

        monkeypatch.setattr(job_reconcile, "reconcile_job", broken)
        kind = self._os_update_kind(tmp_path, "completed")

        job_reconcile._follow_safely(store, Followed(job=job, unit=UNIT, kind=kind), None)

        [event] = audited
        assert event["outcome"] == "failure" and "journal unreadable" in event["details"]["error"]

    def test_a_kind_without_an_event_records_nothing(
        self, store: NoustStore, tmp_path: Path, audited: list[dict[str, Any]]
    ) -> None:
        job = _job(store, tmp_path)
        runner = SequencedRunner()
        runner.sequence(["systemctl", "show"], [GONE])
        runner.sequence(["journalctl"], [_journal(DEACTIVATED)])

        _reconcile(store, job, runner)

        assert audited == []

    def test_a_self_update_is_audited_as_noust_updated(self) -> None:
        kind = job_reconcile.job_kinds()["self_update"]

        assert (kind.audit_event, kind.audit_target) == ("system.update", "noust")


class TestStartup:
    def test_a_job_without_a_unit_is_interrupted_as_before(
        self, store: NoustStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _job(store, tmp_path, unit=None)
        followed: list[Any] = []
        monkeypatch.setattr(
            job_reconcile, "reattach", lambda store, jobs, **kw: followed.extend(jobs)
        )

        JobManager.reset_instance()
        JobManager()
        JobManager.reset_instance()

        row = store.get_job("job1")
        assert row is not None and row.status == "failed" and row.error == INTERRUPTED_REASON
        assert followed == []

    def test_a_job_with_a_unit_is_reconciled_instead_of_failed(
        self, store: NoustStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _job(store, tmp_path)
        followed: list[Followed] = []
        monkeypatch.setattr(
            job_reconcile, "reattach", lambda store, jobs, **kw: followed.extend(jobs)
        )

        JobManager.reset_instance()
        JobManager()
        JobManager.reset_instance()

        row = store.get_job("job1")
        assert row is not None and row.status == "running" and not row.error
        assert [(item.job.id, item.unit) for item in followed] == [("job1", UNIT)]

    def test_a_reconciled_job_is_announced_to_the_managers_listeners(
        self, store: NoustStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.web.jobs import Job, JobStatus

        job = _job(store, tmp_path, job_type="os_update")
        store.save_job(replace(job, status="completed", result_json='{"packages": ["a"]}'))
        monkeypatch.setattr(job_reconcile, "reattach", lambda store, jobs, **kw: None)
        JobManager.reset_instance()
        manager = JobManager()
        heard: list[Job] = []
        manager.subscribe_all(heard.append)

        manager._publish_reconciled("job1")
        JobManager.reset_instance()

        (announced,) = heard
        assert announced.status is JobStatus.COMPLETED
        assert announced.result == {"packages": ["a"]}

    def test_a_job_started_by_an_older_console_is_found_through_its_record(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        records = RecordStore(tmp_path / "updates")
        records.write(UpdateRecord(id="0a1b2c3d", scope="all", unit=UNIT, job_id="job1"))
        job = _job(store, tmp_path, job_type="os_update", unit=None)
        kinds = job_reconcile.job_kinds(update_records=records)

        (found,) = job_reconcile.units_to_reconcile([job], kinds)

        assert found.unit == UNIT

    def test_the_job_records_its_unit_the_moment_it_starts(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        from noust.web.jobs import Job, JobContext, JobType

        _job(store, tmp_path, unit=None)
        notified: list[Job] = []
        job = Job(id="job1", type=JobType.OS_UPDATE, name="n", description="d")

        JobContext(job, notified.append).set_unit(UNIT)

        row = store.get_job("job1")
        assert row is not None and row.unit == UNIT
        assert job.metadata["unit"] == UNIT and notified == [job]

    def test_a_snapshot_written_later_keeps_the_unit(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        job = _job(store, tmp_path)

        store.save_job(JobRecord(id=job.id, type=job.type, name=job.name, status="running"))

        row = store.get_job("job1")
        assert row is not None and row.unit == UNIT


class TestOperatingSystemUpdate:
    def test_the_update_record_is_the_jobs_result(self, tmp_path: Path) -> None:
        records = RecordStore(tmp_path / "updates")
        records.write(
            UpdateRecord(
                id="0a1b2c3d",
                scope="all",
                status="completed",
                packages=["openssl", "noust"],
                unit=UNIT,
                job_id="job1",
            )
        )
        kind = job_reconcile.job_kinds(update_records=records)["os_update"]
        job = JobRecord(id="job1", type="os_update")
        assert kind.verdict is not None

        verdict = kind.verdict(job, UNIT, UnitEnding("succeeded", "success", 0), [])

        assert verdict.succeeded
        assert verdict.result is not None and verdict.result["packages"] == ["openssl", "noust"]

    def test_a_failed_update_fails_with_its_own_reason_and_words(self, tmp_path: Path) -> None:
        records = RecordStore(tmp_path / "updates")
        records.write(
            UpdateRecord(
                id="0a1b2c3d",
                scope="all",
                status="failed",
                error="The package manager exited with code 100",
                tail=["E: dpkg was interrupted"],
                unit=UNIT,
                job_id="job1",
            )
        )
        kind = job_reconcile.job_kinds(update_records=records)["os_update"]
        assert kind.verdict is not None

        verdict = kind.verdict(
            JobRecord(id="job1", type="os_update"), UNIT, UnitEnding("failed", "exit-code", 1), []
        )

        assert not verdict.succeeded
        assert verdict.error is not None
        assert verdict.error.startswith("The package manager exited with code 100")
        assert "dpkg was interrupted" in verdict.error

    def test_a_unit_that_ended_without_a_record_is_a_failure_that_says_so(
        self, tmp_path: Path
    ) -> None:
        records = RecordStore(tmp_path / "updates")
        records.write(UpdateRecord(id="0a1b2c3d", scope="all", unit=UNIT, job_id="job1"))
        kind = job_reconcile.job_kinds(update_records=records)["os_update"]
        assert kind.verdict is not None

        verdict = kind.verdict(
            JobRecord(id="job1", type="os_update"), UNIT, UnitEnding("unknown"), ["Killed"]
        )

        assert not verdict.succeeded and verdict.error is not None
        assert "without recording a result" in verdict.error and "Killed" in verdict.error


class TestSelfUpdate:
    def test_the_self_update_record_decides_the_job(self, tmp_path: Path) -> None:
        from noust.core.fs import RealFileSystem
        from noust.core.runner import FakeRunner
        from noust.managers.self_update import SelfUpdate, SelfUpdateRecord

        path = tmp_path / "self-update.json"
        record = SelfUpdateRecord(
            id="abcd1234",
            method="apt",
            from_version="3.1.17",
            argv=["apt-get"],
            unit="noust-self-update-abcd1234",
            started_at="2026-10-02T16:44:00+00:00",
            job_id="job1",
        )
        path.write_text(json.dumps(record.to_dict()))
        runner = FakeRunner().script(
            ["systemctl", "show"], stdout="LoadState=not-found\nActiveState=inactive\n"
        )

        def manager() -> SelfUpdate:
            return SelfUpdate(
                runner=runner,
                fs=RealFileSystem(),
                record_path=path,
                method="apt",
                container=False,
                systemd=True,
                version="3.2.0",
                process_started=2e9,
            )

        kind = job_reconcile.job_kinds(self_update=manager)["self_update"]
        job = JobRecord(id="job1", type="self_update")
        assert kind.find_unit is not None and kind.verdict is not None

        assert kind.find_unit(job) == "noust-self-update-abcd1234"
        verdict = kind.verdict(job, "noust-self-update-abcd1234", UnitEnding("succeeded"), [])

        assert verdict.succeeded
        assert verdict.result is not None and verdict.result["to_version"] == "3.2.0"


def test_the_store_has_a_unit_per_job(store: NoustStore) -> None:
    import sqlite3

    with sqlite3.connect(store.db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}

    assert "unit" in columns


class TestCentral:
    """The central counts a node by how its update ended, not by its console's restart."""

    @pytest.fixture
    def central(self, tmp_path: Path) -> Iterator[Any]:
        from noust.fleet.aggregate import set_aggregator
        from tests.fleet_views_support import build_central

        built = build_central(tmp_path, ("web-1", "web-2"))
        yield built
        set_aggregator(None)
        NoustStore.reset_instance()

    @staticmethod
    def _run(central: Any, request: Any) -> dict[str, Any]:
        from noust.fleet.aggregate import Asker
        from noust.web import fleet_jobs

        asker = Asker(actor="ana", scope="admin", role="admin", elevated=True)
        the_plan = fleet_jobs.plan(request, manager=central.manager, asker=asker)
        jobs = fleet_jobs.FleetJobs(central.store)
        job_id = fleet_jobs.new_job_id()
        jobs.create(job_id, the_plan, created_by="ana")

        class Reporter:
            def log(self, message: str, level: str = "info") -> None:
                pass

            def publish(self, snapshot: dict[str, Any], done: int, step: str) -> None:
                pass

        return fleet_jobs.Runner(
            plan=the_plan,
            job_id=job_id,
            manager=central.manager,
            asker=asker,
            reporter=Reporter(),
            elevated_until=float("inf"),
            jobs=jobs,
            poll=0,
            sleep=lambda seconds: None,
        ).run()

    @staticmethod
    def _pending(node: Any) -> None:
        node.responses["/api/server/summary"] = (200, {"updates": {"pending": 2, "security": 2}})

    def test_a_node_whose_console_restarted_under_its_update_succeeds(self, central) -> None:
        import httpx

        from noust.web.fleet_jobs import FleetRequest

        node = central.nodes["web-1"]
        self._pending(node)
        node.handlers[("POST", "/api/server/updates/apply")] = lambda request: node.queue(
            status="running"
        )
        # Running, then the console restarts under the noust package (nothing
        # answers), then the console that came back reconciled the job.
        answers = iter(["running", "down", "down", "running", "completed"])

        def job(request: httpx.Request) -> httpx.Response:
            state = next(answers, "completed")
            if state == "down":
                raise httpx.ConnectError("Connection refused", request=request)
            return httpx.Response(200, json={"id": "j001", "status": state, "error": None})

        node.handlers[("GET", "/api/jobs/j001")] = job
        node.handlers[("GET", "/api/jobs/j001/log")] = lambda request: httpx.Response(
            200, json={"content": "Setting up noust (3.2.0) ...", "truncated": False}
        )
        node.responses["/api/server/updates/runs"] = (
            200,
            [{"job_id": "j001", "status": "completed", "packages": ["noust", "openssl"]}],
        )

        snapshot = self._run(central, FleetRequest("os_updates", nodes=["web-1"]))

        (web1,) = snapshot["nodes"]
        assert web1["state"] == "succeeded"
        assert web1["items"][0]["packages"] == 2

    def test_an_older_node_that_marked_the_job_interrupted_is_read_from_its_record(
        self, central
    ) -> None:
        import httpx

        from noust.web.fleet_jobs import FleetRequest

        node = central.nodes["web-1"]
        self._pending(node)
        node.handlers[("POST", "/api/server/updates/apply")] = lambda request: node.queue(
            status="failed", error=INTERRUPTED_REASON
        )
        runs = iter(["running", "running", "completed"])
        node.handlers[("GET", "/api/server/updates/runs")] = lambda request: httpx.Response(
            200, json=[{"job_id": "j001", "status": next(runs, "completed"), "packages": ["a"]}]
        )

        snapshot = self._run(central, FleetRequest("os_updates", nodes=["web-1"]))

        assert snapshot["nodes"][0]["state"] == "succeeded"
        assert snapshot["status"] == "succeeded"

    def test_an_update_that_really_failed_is_a_failure(self, central) -> None:
        from noust.web.fleet_jobs import FleetRequest

        node = central.nodes["web-1"]
        self._pending(node)
        node.handlers[("POST", "/api/server/updates/apply")] = lambda request: node.queue(
            status="failed", error="The package manager exited with code 100"
        )
        node.responses["/api/server/updates/runs"] = (
            200,
            [{"job_id": "j001", "status": "failed", "error": "exit 100"}],
        )

        snapshot = self._run(central, FleetRequest("os_updates", nodes=["web-1"]))

        assert snapshot["nodes"][0]["state"] == "failed"

    def test_the_plan_says_which_nodes_also_update_noust(self, central) -> None:
        from noust.fleet.aggregate import Asker
        from noust.web.fleet_jobs import NOUST_PENDING_STEP, FleetRequest, plan

        for name, node in central.nodes.items():
            self._pending(node)
            packages = [{"name": "openssl", "security": True}]
            if name == "web-1":
                packages.append({"name": "noust", "security": True})
            node.responses["/api/server/updates"] = (200, {"packages": packages})

        the_plan = plan(
            FleetRequest("os_updates", nodes=["web-1", "web-2"]),
            manager=central.manager,
            asker=Asker(actor="ana", scope="admin", role="admin", elevated=True),
        )

        steps = {state.node: state.step for state in the_plan.nodes}
        assert steps == {"web-1": NOUST_PENDING_STEP, "web-2": None}
        assert any("Also updates Noust on web-1:" in note for note in the_plan.notes)


@pytest.mark.usefixtures("no_package_manager_running")
class TestNoustLast:
    """A node-side update that includes Noust installs it last, so its restart ends the run."""

    SIMULATION = (
        "Inst noust [3.1.17] (3.2.0 Noust:stable/stable [all])\n"
        "Inst openssl [3.0.1] (3.0.2 Ubuntu:24.04/noble-security [amd64])\n"
    )

    @staticmethod
    def _manager(tmp_path: Path, runner: Any, family: str = "apt") -> Any:
        from noust.core.fs import RecordingFileSystem
        from noust.managers.server.updates import UpdatesManager
        from tests.server_support import make_host, platform_for

        return UpdatesManager(
            platform=platform_for(family),
            runner=runner,
            fs=RecordingFileSystem(),
            host=make_host(tmp_path / "root"),
            records=RecordStore(tmp_path / "os-updates", fs=RecordingFileSystem()),
        )

    def test_everything_else_is_upgraded_first_then_the_plans_own_command(
        self, tmp_path: Path
    ) -> None:
        from noust.core.runner import FakeRunner
        from noust.managers.server.pkg.base import UpdateScope

        runner = FakeRunner()
        runner.script(["apt-get", "-s"], stdout=self.SIMULATION)
        manager = self._manager(tmp_path, runner)

        record = manager.apply(UpdateScope.ALL, on_line=lambda line: None, update_id="0a1b2c3d")

        applied = [call for call in runner.calls if call[:2] == ("apt-get", "-y")]
        assert len(applied) == 2
        assert applied[0][-4:] == ("install", "--only-upgrade", "--no-remove", "openssl")
        assert applied[1][-2:] == ("--with-new-pkgs", "upgrade")
        assert record.status == "completed"

    def test_when_the_first_part_fails_noust_is_not_touched(self, tmp_path: Path) -> None:
        from noust.core.runner import FakeRunner
        from noust.managers.server.errors import ServerError
        from noust.managers.server.pkg.base import UpdateScope

        runner = FakeRunner()
        runner.script(["apt-get", "-s"], stdout=self.SIMULATION)
        runner.script(["apt-get", "-y"], exit_code=100, stdout="E: dpkg was interrupted\n")
        manager = self._manager(tmp_path, runner)

        with pytest.raises(ServerError):
            manager.apply(UpdateScope.ALL, on_line=lambda line: None, update_id="0a1b2c3d")

        assert len([call for call in runner.calls if call[:2] == ("apt-get", "-y")]) == 1

    def test_without_noust_pending_it_is_one_command(self, tmp_path: Path) -> None:
        from noust.core.runner import FakeRunner
        from noust.managers.server.pkg.base import UpdateScope

        runner = FakeRunner()
        runner.script(
            ["apt-get", "-s"],
            stdout="Inst openssl [3.0.1] (3.0.2 Ubuntu:24.04/noble-security [amd64])\n",
        )

        self._manager(tmp_path, runner).apply(
            UpdateScope.ALL, on_line=lambda line: None, update_id="0a1b2c3d"
        )

        assert len([call for call in runner.calls if call[:2] == ("apt-get", "-y")]) == 1
