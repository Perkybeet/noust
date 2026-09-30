# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Retention of Noust's other records (ENS G19) and the audit worker.

Finished jobs and deployment rows, with their logs, go when they are past
their period, and the purge says so in the audit log. A log path read back
from the store is not trusted: only files in the directory Noust keeps them
in are deleted.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from noust.core.audit import get_log
from noust.core.audit import retention as retention_module
from noust.core.audit.log import AuditLog
from noust.core.audit.retention import prune, register_pruner, unregister_pruner
from noust.core.audit.settings import AuditSettings, RetentionSettings
from noust.core.audit.worker import AuditWorker
from noust.core.store import JobRecord, NoustStore


@pytest.fixture
def store(tmp_path: Path):
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "store" / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


def job(store: NoustStore, job_id: str, finished: datetime, log_path: Path | None) -> None:
    store.create_job(
        JobRecord(
            id=job_id,
            type="deploy",
            name=job_id,
            status="completed",
            created_at=finished.isoformat(),
            finished_at=finished.isoformat(),
            log_path=str(log_path) if log_path else None,
        )
    )


SETTINGS = RetentionSettings(
    jobs_days=90, deployments_days=365, sessions_days=30, observations_days=30
)


def test_old_finished_jobs_and_their_logs_go(store: NoustStore, tmp_path: Path) -> None:
    logs = store.db_path.parent / "job-logs"
    logs.mkdir()
    old_log, new_log = logs / "old.log", logs / "new.log"
    old_log.write_text("build output")
    new_log.write_text("build output")
    now = datetime.now()
    job(store, "old", now - timedelta(days=120), old_log)
    job(store, "new", now - timedelta(days=5), new_log)

    report = prune(SETTINGS, now=now)

    assert report.deleted["jobs"] == 1
    assert report.files == 1
    assert not old_log.exists() and new_log.exists()
    assert store.get_job("old") is None and store.get_job("new") is not None
    (event,) = get_log().read(action="retention.prune")
    assert event["details"]["deleted"]["jobs"] == 1
    assert event["who"] == {"kind": "system", "name": "retention"}


def test_a_log_path_outside_noust_s_directory_is_never_deleted(
    store: NoustStore, tmp_path: Path
) -> None:
    victim = tmp_path / "etc-shadow"
    victim.write_text("root:*:")
    job(store, "forged", datetime.now() - timedelta(days=400), victim)

    report = prune(SETTINGS)

    assert report.deleted["jobs"] == 1
    assert report.files == 0
    assert victim.exists()


def test_old_deployments_go(store: NoustStore) -> None:
    deployment = store.record_deployment_start("shop.example.com", "cli")
    store.finish_deployment(deployment, "success")
    later = datetime.now() + timedelta(days=366)

    report = prune(SETTINGS, now=later)

    assert report.deleted["deployments"] == 1


def test_an_application_s_rollback_targets_outlive_the_retention_period(
    store: NoustStore,
) -> None:
    """
    An application not deployed in a year keeps its live deployment.

    Deleting it lost the rollback target and the protection its snapshot
    backup gets (the newest deployments of each application name theirs).
    """
    from noust.core.store import App
    from noust.managers.backup_manager import SNAPSHOT_DEPLOYMENTS_KEPT

    store.create_app(App(domain="shop.example.com", app_type="nodejs"))
    rows = []
    for _ in range(SNAPSHOT_DEPLOYMENTS_KEPT + 2):
        rows.append(store.record_deployment_start("shop.example.com", "cli"))
        store.finish_deployment(rows[-1], "success")

    report = prune(SETTINGS, now=datetime.now() + timedelta(days=400))

    assert report.deleted["deployments"] == 2
    kept = {record.id for record in store.list_deployments("shop.example.com", limit=100)}
    assert kept == set(rows[2:])


def test_the_last_success_is_kept_behind_a_run_of_failures(store: NoustStore) -> None:
    from noust.core.store import App
    from noust.managers.backup_manager import SNAPSHOT_DEPLOYMENTS_KEPT

    store.create_app(App(domain="shop.example.com", app_type="nodejs"))
    good = store.record_deployment_start("shop.example.com", "cli")
    store.finish_deployment(good, "success")
    for _ in range(SNAPSHOT_DEPLOYMENTS_KEPT):
        failed = store.record_deployment_start("shop.example.com", "cli")
        store.finish_deployment(failed, "failed")

    prune(SETTINGS, now=datetime.now() + timedelta(days=400))

    assert store.get_deployment(good) is not None


def test_a_running_deployment_stays(store: NoustStore) -> None:
    store.record_deployment_start("shop.example.com", "cli")
    report = prune(SETTINGS, now=datetime.now() + timedelta(days=366))
    assert report.deleted["deployments"] == 0


def test_registered_record_kinds_are_pruned_too(store: NoustStore) -> None:
    seen: list[datetime] = []

    def sessions(cutoff: datetime) -> int:
        seen.append(cutoff)
        return 3

    register_pruner("sessions", sessions)
    try:
        now = datetime(2026, 9, 29, 12, 0)
        report = prune(SETTINGS, now=now)
    finally:
        unregister_pruner("sessions")
    assert report.deleted["sessions"] == 3
    assert seen == [now - timedelta(days=30)]


def test_nothing_to_prune_records_nothing(store: NoustStore) -> None:
    prune(SETTINGS)
    assert get_log().read(action="retention.prune") == []


def test_a_rehearsal_prunes_nothing(store: NoustStore, tmp_path: Path) -> None:
    from noust.core.fs import DryRunFileSystem, set_fs

    job(store, "old", datetime.now() - timedelta(days=400), None)
    set_fs(DryRunFileSystem())
    try:
        report = prune(SETTINGS)
    finally:
        set_fs(None)
    assert report.deleted == {}
    assert store.get_job("old") is not None


def test_observations_are_not_created_to_be_pruned(
    store: NoustStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "observations.db"
    monkeypatch.setattr("noust.monitor.observation_store.default_db_path", lambda: missing)
    assert retention_module._prune_observations(30) == 0
    assert not missing.exists()


class TestWorker:
    def worker(self, tmp_path: Path, clock: list[float]) -> AuditWorker:
        log = AuditLog(tmp_path / "audit" / "web-audit.log", settings=AuditSettings(journald="off"))
        return AuditWorker(log, clock=lambda: clock[0])

    def test_a_checkpoint_is_written_only_when_something_happened(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        clock = [0.0]
        worker = self.worker(tmp_path, clock)
        worker.log.append("apps.update")
        clock[0] += 5 * 60
        worker.tick()
        clock[0] += 5 * 60
        worker.tick()

        (checkpoint,) = worker.log.read(action="audit.checkpoint")
        assert checkpoint["details"]["seq"] == 2
        assert worker.log.verify().ok

    def test_crossing_the_size_limit_is_reported_once(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        clock = [0.0]
        worker = self.worker(tmp_path, clock)
        worker.log.reload_settings(AuditSettings(journald="off", max_total_mb=0))
        worker.log.append("apps.update")
        worker.check_size()
        worker.check_size()
        assert worker.log.over_limit
        assert len(worker.log.read(action="audit.degraded")) == 1

    def test_start_and_stop_are_on_record(self, tmp_path: Path, store: NoustStore) -> None:
        worker = self.worker(tmp_path, [0.0])
        worker.start()
        worker.stop()
        actions = [entry["action"] for entry in worker.log.read(limit=10)]
        assert actions[:1] == ["system.stop"]
        assert "system.start" in actions
