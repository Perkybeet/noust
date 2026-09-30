"""
Tests for the jobs of ``/api/server`` and for what the console does at start-up.

A job here is a manager call on the job manager's worker, with the tool's output
streamed into its log verbatim and the outcome audited. The tests run the job
functions directly with a real :class:`JobContext`, so what they check is what
the console's job page will show: the log lines, the result, the error carrying
the tool's own words, and the audit record of how it ended.
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import ValidationError
from noust.core.fs import RecordingFileSystem
from noust.core.store import JobRecord, NoustStore
from noust.managers.server.errors import ServerError
from noust.managers.server.power import Returned
from noust.managers.server.storage import Analysis, Candidate
from noust.managers.server.updates import UpdateRecord
from noust.web.api.server import jobs, lifecycle
from noust.web.api.server.common import set_server_context
from noust.web.jobs import INTERRUPTED_REASON, Job, JobContext, JobType
from tests.server_support import (  # noqa: F401 - the fixture registers itself
    RecordingAudit,
    fixture,
    make_machine,
    no_package_manager_running,
)

pytestmark = pytest.mark.usefixtures("no_package_manager_running")


@pytest.fixture
def store(tmp_path: Path):
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "jobs.db", fs=RecordingFileSystem())
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store):
    machine = make_machine(tmp_path, monkeypatch, store)
    audit = RecordingAudit()
    monkeypatch.setattr("noust.core.audit.record", audit.record)
    set_server_context(machine.ctx)
    job = Job(id="job1", type=JobType.OS_UPDATE, name="n", description="d")
    context = JobContext(job, lambda _job: None)
    try:
        yield types.SimpleNamespace(
            machine=machine, audit=audit, job=job, context=context, store=store
        )
    finally:
        set_server_context(None)


def lines(job: Job) -> list[str]:
    return [entry.message for entry in job.logs]


class TestRefresh:
    def test_the_package_managers_output_is_the_jobs_log_and_the_list_is_renewed(self, env) -> None:
        env.machine.runner.script(
            ["apt-get", "update"],
            stdout="Hit:1 http://archive.ubuntu.com noble InRelease\nReading package lists...",
        )

        result = jobs.os_refresh_job(actor="yago", job_context=env.context)

        assert "Hit:1 http://archive.ubuntu.com noble InRelease" in lines(env.job)
        assert result == {"pending": 9, "security": 7}
        fact = env.machine.ctx.cache.peek("updates")
        assert fact is not None and fact.value.pending == 9
        assert env.audit.events() == ["server.refresh"]
        assert env.audit.records[0]["outcome"] == "ok"
        assert env.audit.records[0]["details"]["stage"] == "finished"

    def test_a_mirror_that_fails_fails_the_job_with_the_output_and_the_audit_says_so(
        self, env
    ) -> None:
        env.machine.runner.script(
            ["apt-get", "update"],
            stdout="Err:3 http://mirror.example.com noble InRelease\n  Could not resolve host",
            exit_code=100,
        )

        with pytest.raises(ServerError) as raised:
            jobs.os_refresh_job(actor="yago", job_context=env.context)

        assert "Could not resolve host" in (raised.value.output or "")
        assert env.audit.records[0]["outcome"] == "failure"
        assert env.audit.records[0]["details"]["error"].startswith(
            "Could not refresh the package lists"
        )

    def test_a_job_function_outside_the_job_manager_is_refused(self) -> None:
        with pytest.raises(ValueError, match="job_context"):
            jobs.os_refresh_job()


class TestUpdate:
    def test_without_systemd_it_runs_in_the_process_and_returns_the_record(self, env) -> None:
        env.machine.host.systemd_marker.rmdir()
        env.machine.runner.script(
            ["apt-get", "-y"], stdout="Setting up openssl\nProcessing triggers"
        )

        result = jobs.os_update_job(scope="security", actor="yago", job_context=env.context)

        assert result["status"] == "completed"
        assert result["scope"] == "security"
        assert "openssl" in result["packages"]
        assert result["reboot_required"] is True
        assert "Setting up openssl" in lines(env.job)
        assert env.job.metadata["update_id"] == result["id"]
        assert env.audit.records[-1]["event"] == "server.update"
        assert env.audit.records[-1]["outcome"] == "ok"
        assert env.audit.records[-1]["details"]["update"] == result["id"]

    def test_the_command_that_ran_is_the_security_subset_and_nothing_else(self, env) -> None:
        env.machine.host.systemd_marker.rmdir()

        jobs.os_update_job(scope="security", job_context=env.context)

        argv = next(c for c in env.machine.runner.calls if c[:2] == ("apt-get", "-y"))
        assert "--only-upgrade" in argv
        assert "openssl" in argv and "bash" not in argv

    def test_the_facts_an_update_makes_stale_are_forgotten(self, env) -> None:
        env.machine.host.systemd_marker.rmdir()
        env.machine.ctx.cache.put("updates", "stale")
        env.machine.ctx.cache.put("restart", "stale")

        jobs.os_update_job(scope="all", job_context=env.context)

        assert env.machine.ctx.cache.peek("updates") is None
        assert env.machine.ctx.cache.peek("restart") is None

    def test_a_failed_update_fails_the_job_with_the_last_lines_the_package_manager_printed(
        self, env
    ) -> None:
        env.machine.host.systemd_marker.rmdir()
        env.machine.runner.script(
            ["apt-get", "-y"],
            stdout="E: Sub-process /usr/bin/dpkg returned an error code (1)",
            exit_code=100,
        )

        with pytest.raises(ServerError) as raised:
            jobs.os_update_job(scope="all", job_context=env.context)

        assert "dpkg returned an error code" in (raised.value.output or "")
        assert env.audit.records[-1]["outcome"] == "failure"

    def test_in_a_unit_the_records_failure_becomes_the_jobs_failure(
        self, env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        record = UpdateRecord(
            id="0a1b2c3d",
            scope="all",
            status="failed",
            error="The package manager exited with code 100",
            tail=["E: broken", "E: worse"],
        )
        monkeypatch.setattr(jobs, "start_and_follow", lambda *args, **kwargs: record)

        with pytest.raises(ServerError, match="exited with code 100") as raised:
            jobs.os_update_job(scope="all", job_context=env.context)

        assert raised.value.output == "E: broken\nE: worse"

    def test_following_a_run_that_kept_going_returns_its_record(
        self, env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        record = UpdateRecord(id="0a1b2c3d", scope="all", status="completed", reboot_required=True)
        monkeypatch.setattr(env.machine.ctx.unit, "follow", lambda update_id, on_line: record)

        result = jobs.os_follow_job("0a1b2c3d", job_context=env.context)

        assert result["status"] == "completed"
        assert env.job.metadata["update_id"] == "0a1b2c3d"

    def test_the_repair_runs_dpkg_then_apt(self, env) -> None:
        result = jobs.os_repair_job(job_context=env.context)

        assert result == {"ran": ["dpkg --configure -a", "apt-get -f install -y"]}
        assert env.audit.records[0]["details"]["action"] == "repair"


class TestAutomaticUpdates:
    def test_the_steps_are_logged_and_the_state_afterwards_is_the_result(self, env) -> None:
        result = jobs.auto_updates_job(enabled=True, security_only=True, job_context=env.context)

        assert result["enabled"] is True
        assert result["security_only"] is True
        assert any("APT::Periodic::Unattended-Upgrade" in step for step in result["steps"])
        assert any(step in lines(env.job) for step in result["steps"])
        assert env.machine.ctx.cache.peek("auto") is None
        assert env.audit.records[0]["details"]["action"] == "auto"


class TestStorageJobs:
    def test_the_analysis_is_kept_for_the_page_and_returned(
        self, env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        analysis = Analysis(
            measured_at="2026-09-29T20:00:00+00:00", candidates=[Candidate("backups", 5)]
        )
        monkeypatch.setattr(
            env.machine.ctx.storage, "analyze", lambda step: (step("Backups"), analysis)[1]
        )

        result = jobs.analyze_job(job_context=env.context)

        assert result["measured_at"] == "2026-09-29T20:00:00+00:00"
        fact = env.machine.ctx.cache.peek("storage.analysis")
        assert fact is not None and fact.value is analysis
        assert "Backups" in lines(env.job)

    def test_a_cleanup_runs_the_action_with_its_parameters_and_reports_the_commands(
        self, env
    ) -> None:
        result = jobs.cleanup_job(
            "journal", params={"size_mb": 100}, actor="yago", job_context=env.context
        )

        assert result["commands"] == ["journalctl --rotate", "journalctl --vacuum-size=100M"]
        assert env.machine.runner.ran("journalctl", "--vacuum-size=100M")
        assert env.job.metadata["action"] == "journal"
        assert env.audit.records[0]["event"] == "server.storage"
        assert env.audit.records[0]["details"]["cleanup"] == "journal"

    def test_a_cleanup_with_a_bad_parameter_fails_and_is_audited_as_failed(self, env) -> None:
        with pytest.raises(ValidationError):
            jobs.cleanup_job("journal", params={"size_mb": -1}, job_context=env.context)

        assert env.audit.records[0]["outcome"] == "failure"

    def test_swap_creation_passes_the_size_in_bytes_and_the_default_swappiness(
        self, env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def create(size_bytes: int, *, on_line: Any, swappiness: int | None) -> list[str]:
            seen.update(size=size_bytes, swappiness=swappiness)
            return ["Made /swapfile"]

        monkeypatch.setattr(env.machine.ctx.swap, "create", create)

        result = jobs.swap_job("create", size_mb=2048, job_context=env.context)

        assert seen == {"size": 2048 * 1024**2, "swappiness": 10}
        assert result == {"steps": ["Made /swapfile"]}
        assert "Made /swapfile" in lines(env.job)
        assert env.audit.records[0]["details"]["action"] == "swap-create"

    def test_swap_removal(self, env, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(env.machine.ctx.swap, "remove", lambda on_line: ["Deleted /swapfile"])

        result = jobs.swap_job("remove", job_context=env.context)

        assert result == {"steps": ["Deleted /swapfile"]}
        assert env.audit.records[0]["details"]["action"] == "swap-remove"


class TestStartUp:
    def test_the_sentences_say_who_asked_and_how_long_it_took(self) -> None:
        planned = Returned("reboot", True, "yago", "2026-09-29T21:00:00+00:00", "x", 130)
        quick = Returned("reboot", True, None, None, "x", 45)
        surprise = Returned("reboot", False, None, None, "x", None)
        off = Returned("poweroff", True, "yago", None, "x", None)

        assert (
            lifecycle.describe_return(planned) == "The server restarted, as yago asked (2 min 10 s)"
        )
        assert lifecycle.describe_return(quick) == "The server restarted (45 s)"
        assert "not restarted from Noust" in lifecycle.describe_return(surprise)
        assert lifecycle.describe_return(off) == "The server was powered back on, as yago asked"

    def test_a_server_that_is_back_is_notified_and_audited(
        self, env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.notifications.context import NotificationContext

        sent: list[Any] = []
        monkeypatch.setattr(
            "noust.core.notifier.notify_composed",
            lambda build: sent.append(build(NotificationContext("en", "web-1", ""))),
        )
        returned = Returned(
            "reboot", True, "yago", "2026-09-29T21:00:00+00:00", "2026-09-29T21:02:10+00:00", 130
        )
        monkeypatch.setattr(env.machine.ctx.power, "detect_return", lambda: returned)

        lifecycle.announce_return()

        [notification] = sent
        assert notification.kind == "server_back"
        assert {fact.key: fact.value for fact in notification.facts}["requested_by"] == "yago"
        assert {fact.key: fact.value for fact in notification.facts}["took"] == "2 min 10 s"
        assert env.audit.records[0]["event"] == "server.reboot"
        assert env.audit.records[0]["details"]["action"] == "returned"
        assert env.audit.records[0]["details"]["requested_by"] == "yago"

    def test_a_restart_nobody_asked_for_is_a_warning(
        self, env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.notifications.context import NotificationContext
        from noust.core.notifications.model import State

        sent: list[Any] = []
        monkeypatch.setattr(
            "noust.core.notifier.notify_composed",
            lambda build: sent.append(build(NotificationContext("es", "web-1", ""))),
        )
        returned = Returned("reboot", False, None, None, "2026-09-29T21:02:10+00:00", None)
        monkeypatch.setattr(env.machine.ctx.power, "detect_return", lambda: returned)

        lifecycle.announce_return()

        [notification] = sent
        assert notification.kind == "server_rebooted"
        assert notification.state is State.WARNING
        assert notification.title == "Servidor reiniciado"

    def test_the_same_boot_says_nothing(self, env, monkeypatch: pytest.MonkeyPatch) -> None:
        sent: list[Any] = []
        monkeypatch.setattr("noust.core.notifier.notify_composed", sent.append)
        monkeypatch.setattr(env.machine.ctx.power, "detect_return", lambda: None)

        lifecycle.announce_return()

        assert sent == [] and env.audit.records == []

    def test_an_update_that_kept_running_is_followed_by_a_new_job(
        self, env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env.machine.ctx.records.write(
            UpdateRecord(
                id="0a1b2c3d",
                scope="all",
                status="running",
                unit="noust-os-update-0a1b2c3d",
                actor="yago",
            )
        )
        env.machine.runner.script(
            ["systemctl", "show"], stdout="LoadState=loaded\nActiveState=active\n"
        )
        queued: list[dict[str, Any]] = []
        monkeypatch.setattr(
            lifecycle,
            "get_job_manager",
            lambda: types.SimpleNamespace(
                create_job=lambda **kw: queued.append(kw) or types.SimpleNamespace(id="new1")
            ),
        )
        monkeypatch.setattr(lifecycle, "get_store", lambda: env.store)

        lifecycle.reattach_updates()

        assert len(queued) == 1
        assert queued[0]["func"] is jobs.os_follow_job
        assert queued[0]["kwargs"] == {"update_id": "0a1b2c3d", "actor": "yago"}
        assert queued[0]["job_type"] is JobType.OS_UPDATE

    def _interrupted(self, env, job_id: str = "old1", error: str = INTERRUPTED_REASON) -> None:
        env.store.save_job(
            JobRecord(
                id=job_id,
                type="os_update",
                name="Apply all updates",
                description="d",
                status="failed",
                error=error,
                created_at="2026-09-29T20:00:00",
                started_at="2026-09-29T20:00:01",
                finished_at="2026-09-29T20:05:00",
            )
        )

    def test_a_finished_update_corrects_the_job_a_restart_marked_interrupted(
        self, env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._interrupted(env)
        monkeypatch.setattr(lifecycle, "get_store", lambda: env.store)
        record = UpdateRecord(
            id="0a1b2c3d",
            scope="all",
            status="completed",
            job_id="old1",
            finished_at="2026-09-29T20:09:00+00:00",
        )

        lifecycle._settle_job(record)

        row = env.store.get_job("old1")
        assert row is not None
        assert row.status == "completed"
        assert row.error is None
        assert row.progress == 100
        assert row.finished_at == "2026-09-29T20:09:00+00:00"

    def test_a_failed_update_keeps_the_jobs_failure_but_with_the_real_reason(
        self, env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._interrupted(env)
        monkeypatch.setattr(lifecycle, "get_store", lambda: env.store)

        lifecycle._settle_job(
            UpdateRecord(
                id="0a1b2c3d", scope="all", status="failed", error="dpkg failed", job_id="old1"
            )
        )

        row = env.store.get_job("old1")
        assert row is not None and row.status == "failed" and row.error == "dpkg failed"

    def test_a_job_that_failed_for_another_reason_is_left_alone(
        self, env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._interrupted(env, error="Something else went wrong")
        monkeypatch.setattr(lifecycle, "get_store", lambda: env.store)

        lifecycle._settle_job(
            UpdateRecord(id="0a1b2c3d", scope="all", status="completed", job_id="old1")
        )

        row = env.store.get_job("old1")
        assert (
            row is not None and row.status == "failed" and row.error == "Something else went wrong"
        )

    def test_a_run_still_going_settles_nothing(self, env, monkeypatch: pytest.MonkeyPatch) -> None:
        self._interrupted(env)
        monkeypatch.setattr(lifecycle, "get_store", lambda: env.store)

        lifecycle._settle_job(
            UpdateRecord(id="0a1b2c3d", scope="all", status="running", job_id="old1")
        )

        row = env.store.get_job("old1")
        assert row is not None and row.error == INTERRUPTED_REASON

    def test_one_step_failing_does_not_stop_the_other_nor_the_console(
        self, env, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        ran: list[str] = []

        def failing() -> None:
            raise OSError("state directory is not readable")

        monkeypatch.setattr(lifecycle, "announce_return", failing)
        monkeypatch.setattr(lifecycle, "reattach_updates", lambda: ran.append("reattach"))

        lifecycle.on_console_start()

        assert ran == ["reattach"]
        assert "state directory is not readable" in caplog.text

    def test_a_bug_in_a_step_is_not_swallowed(self, env, monkeypatch: pytest.MonkeyPatch) -> None:
        def buggy() -> None:
            raise AttributeError("no such method")

        monkeypatch.setattr(lifecycle, "announce_return", buggy)
        monkeypatch.setattr(lifecycle, "reattach_updates", lambda: None)

        with pytest.raises(AttributeError):
            lifecycle.on_console_start()
