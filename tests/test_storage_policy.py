"""
Scheduled cleaning of the disk, and the history of every cleanup (F-34).

The manager that cleans is tested in ``test_server_storage.py``; what is pinned here is what
a timer adds to it: a threshold below which nothing is touched, a policy that is checked
before it is kept, Docker actions that run only when the policy accepted what they take,
a failing action that does not hide the ones that worked, and one history row for every
cleanup, by hand or by timer, with the disk's state before and after.
"""

from __future__ import annotations

import os
import types
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import ValidationError
from noust.core.fs import DryRunFileSystem, RecordingFileSystem
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.managers.server.errors import ServerError
from noust.managers.server.storage import StorageManager
from noust.managers.server.storage_policy import (
    DEFAULT_ACTIONS,
    RUNS_KEPT,
    SCHEDULABLE,
    CleanupHistory,
    CleanupPolicy,
    CleanupRun,
    CleanupSchedule,
)
from tests.server_support import make_host, platform_for


class FakeScheduler:
    """What writes the timer, recording what it was asked."""

    def __init__(self) -> None:
        self.installed: list[tuple[Any, ...]] = []
        self.removed: list[str] = []
        self.next_run: str | None = None

    def install_unit_pair(
        self,
        unit: str,
        prefix: str,
        what: str,
        timer: str,
        service: str,
        calendar: str,
        **values: str,
    ) -> None:
        self.installed.append((unit, prefix, timer, service, calendar))

    def remove_unit_pair(self, unit: str, prefix: str) -> bool:
        self.removed.append(unit)
        return True

    def unit_state(self, unit: str) -> dict[str, Any]:
        return {"installed": bool(self.installed), "next_run": self.next_run, "last_run": None}


class Disk:
    """A disk whose fullness a test moves."""

    def __init__(self, percent: float) -> None:
        self.percent = percent

    def usage(self, path: str) -> types.SimpleNamespace:
        total = 100 * 1024**3
        return types.SimpleNamespace(
            total=total,
            used=int(total * self.percent / 100),
            free=int(total * (100 - self.percent) / 100),
            percent=self.percent,
        )


@pytest.fixture
def disk(monkeypatch: pytest.MonkeyPatch) -> Disk:
    state = Disk(86.0)
    partition = types.SimpleNamespace(device="/dev/sda1", mountpoint="/", fstype="ext4", opts="rw")
    fake = types.SimpleNamespace(
        disk_partitions=lambda all=False: [partition], disk_usage=state.usage
    )
    monkeypatch.setattr("noust.managers.server.storage._psutil", lambda: fake)
    monkeypatch.setattr(
        os, "statvfs", lambda path: types.SimpleNamespace(f_files=1_000_000, f_ffree=900_000)
    )
    return state


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def store(tmp_path: Path) -> NoustStore:
    NoustStore.reset_instance()
    return NoustStore(tmp_path / "noust.db")


@pytest.fixture
def schedule(tmp_path: Path, runner: FakeRunner, store: NoustStore, disk: Disk) -> CleanupSchedule:
    manager = StorageManager(
        runner=runner,
        fs=RecordingFileSystem(),
        host=make_host(tmp_path / "root"),
        platform=platform_for("apt"),
        release_apps=lambda: [],
        backups_usage=lambda: {},
        log_paths=lambda: [],
    )
    return CleanupSchedule(manager, store=store, scheduler=FakeScheduler())


def policy(**changes: Any) -> CleanupPolicy:
    values: dict[str, Any] = {"enabled": True}
    values.update(changes)
    return CleanupPolicy.from_dict(values)


class TestThePolicyIsCheckedBeforeItIsKept:
    def test_the_defaults_clean_what_gives_space_back_and_takes_nothing_wanted(self):
        default = CleanupPolicy()

        assert default.enabled is False
        assert default.actions == DEFAULT_ACTIONS == ("journal", "pkg-cache", "releases")
        assert default.threshold_percent == 80
        assert "docker-image" not in SCHEDULABLE

    @pytest.mark.parametrize("threshold", [-1, 100, "80", 80.5, True, None])
    def test_a_threshold_is_a_whole_percentage_from_0_to_99(self, threshold):
        with pytest.raises(ValidationError, match="threshold"):
            CleanupPolicy.from_dict({"threshold_percent": threshold})

    def test_zero_means_every_time(self):
        assert policy(threshold_percent=0).threshold_percent == 0

    @pytest.mark.parametrize("actions", [[], "journal", ["journal", "journal"], [1], ["rm -rf /"]])
    def test_the_actions_are_a_list_of_known_ones_each_once(self, actions):
        with pytest.raises(ValidationError):
            CleanupPolicy.from_dict({"actions": actions})

    def test_removing_one_image_cannot_be_scheduled(self):
        with pytest.raises(ValidationError) as caught:
            CleanupPolicy.from_dict({"actions": ["docker-image"]})

        assert "docker-image" in caught.value.message
        assert "target" in caught.value.details

    def test_a_docker_action_runs_only_when_the_policy_accepts_what_it_deletes(self):
        with pytest.raises(ValidationError) as caught:
            CleanupPolicy.from_dict({"actions": ["journal", "docker-build-cache"]})

        assert caught.value.field == "allow_docker"
        assert policy(actions=["docker-build-cache"], allow_docker=True).allow_docker is True

    def test_an_unknown_field_is_refused_and_not_ignored(self):
        with pytest.raises(ValidationError, match="Unknown field"):
            CleanupPolicy.from_dict({"run_as_root": True})

    @pytest.mark.parametrize("calendar", ["", "daily\nExecStart=/bin/sh", "x" * 200])
    def test_a_schedule_that_could_become_a_unit_directive_is_refused(self, calendar):
        with pytest.raises(ValidationError):
            CleanupPolicy.from_dict({"schedule": calendar})

    def test_the_journal_size_is_bounded(self):
        with pytest.raises(ValidationError, match="journal"):
            CleanupPolicy.from_dict({"journal_mb": 4})
        assert policy(journal_mb=500).journal_mb == 500


class TestThePolicyAndItsTimer:
    def test_no_policy_is_a_disabled_default(self, schedule: CleanupSchedule):
        assert schedule.policy() == CleanupPolicy()

    def test_enabling_keeps_the_policy_and_writes_the_timer(self, schedule: CleanupSchedule):
        schedule.set_policy(policy(schedule="weekly", threshold_percent=70), actor="ana")

        assert schedule.policy().schedule == "weekly"
        assert schedule.policy().threshold_percent == 70
        (installed,) = schedule.scheduler.installed
        unit, prefix, timer, service, calendar = installed
        assert unit == "noust-storage-clean"
        assert (timer, service) == ("storage-clean-timer.j2", "storage-clean-service.j2")
        assert calendar == "Mon *-*-* 02:00:00"

    def test_disabling_removes_the_timer_and_keeps_the_rest_of_the_policy(
        self, schedule: CleanupSchedule
    ):
        schedule.set_policy(policy(actions=["journal"]))

        schedule.set_policy(policy(enabled=False, actions=["journal"]))

        assert schedule.scheduler.removed == ["noust-storage-clean"]
        assert schedule.policy().enabled is False
        assert schedule.policy().actions == ("journal",)

    def test_a_timer_that_cannot_be_written_keeps_the_old_policy(self, schedule: CleanupSchedule):
        schedule.set_policy(policy(threshold_percent=60))

        def refuse(*args: Any, **kwargs: Any) -> None:
            raise ServerError("systemd refused")

        schedule.scheduler.install_unit_pair = refuse
        with pytest.raises(ServerError):
            schedule.set_policy(policy(threshold_percent=90))

        assert schedule.policy().threshold_percent == 60

    def test_the_timer_state_is_what_the_scheduler_says(self, schedule: CleanupSchedule):
        schedule.scheduler.next_run = "Wed 2026-10-14 02:10:00 UTC"
        schedule.set_policy(policy())

        assert schedule.timer_state()["next_run"] == "Wed 2026-10-14 02:10:00 UTC"


class TestRunning:
    def test_below_the_threshold_nothing_runs_and_the_history_says_why(
        self, schedule: CleanupSchedule, runner: FakeRunner, disk: Disk
    ):
        disk.percent = 42.0
        schedule.set_policy(policy(threshold_percent=80))

        run = schedule.run(trigger="schedule")

        assert run.status == "skipped"
        assert "42% full, below the 80%" in (run.reason or "")
        assert run.actions == []
        assert runner.calls == []
        (recorded,) = schedule.history.list()
        assert recorded.reason == run.reason and recorded.trigger == "schedule"
        assert (recorded.mount, recorded.percent_before) == ("/", 42.0)

    def test_at_the_threshold_the_actions_run_in_order_and_the_run_says_how_full_the_disk_was(
        self, schedule: CleanupSchedule, runner: FakeRunner, disk: Disk
    ):
        disk.percent = 86.0
        schedule.set_policy(policy(actions=["pkg-cache", "journal"], threshold_percent=80))

        run = schedule.run(trigger="schedule")

        assert run.status == "ok"
        assert [a.action for a in run.actions] == ["pkg-cache", "journal"]
        assert runner.calls[0] == ("apt-get", "clean")
        assert (run.mount, run.percent_before, run.percent_after) == ("/", 86.0, 86.0)
        assert schedule.history.list()[0].actions[0].action == "pkg-cache"

    def test_zero_runs_every_time(self, schedule: CleanupSchedule, disk: Disk):
        disk.percent = 3.0
        schedule.set_policy(policy(threshold_percent=0, actions=["pkg-cache"]))

        assert schedule.run(trigger="schedule").status == "ok"

    def test_run_now_ignores_the_threshold_because_the_operator_means_it(
        self, schedule: CleanupSchedule, disk: Disk
    ):
        disk.percent = 10.0
        schedule.set_policy(policy(threshold_percent=90, actions=["pkg-cache"]))

        run = schedule.run(trigger="policy", force=True, actor="ana")

        assert run.status == "ok" and run.actor == "ana"

    def test_a_timer_that_outlived_its_policy_does_nothing_and_says_so(
        self, schedule: CleanupSchedule, runner: FakeRunner
    ):
        schedule.set_policy(policy(enabled=False))

        run = schedule.run(trigger="schedule")

        assert run.status == "skipped" and "switched off" in (run.reason or "")
        assert runner.calls == []

    def test_a_failing_action_is_a_result_beside_the_ones_that_worked(
        self, schedule: CleanupSchedule, runner: FakeRunner
    ):
        runner.script(["apt-get", "clean"], stdout="E: Could not open lock", exit_code=100)
        schedule.set_policy(policy(actions=["pkg-cache", "journal"], threshold_percent=0))

        run = schedule.run(trigger="schedule")

        assert run.status == "partial"
        failed, worked = run.actions
        assert failed.status == "failed" and "Could not open lock" in (failed.error or "")
        assert worked.status == "ok"

    def test_when_every_action_fails_the_run_failed(
        self, schedule: CleanupSchedule, runner: FakeRunner
    ):
        runner.script(["apt-get", "clean"], stdout="E: lock", exit_code=100)
        schedule.set_policy(policy(actions=["pkg-cache"], threshold_percent=0))

        assert schedule.run(trigger="schedule").status == "failed"

    def test_a_docker_action_runs_with_the_policys_yes_and_nothing_else_is_removed(
        self, schedule: CleanupSchedule, runner: FakeRunner
    ):
        schedule.set_policy(
            policy(
                actions=["docker-build-cache", "docker-dangling-images"],
                allow_docker=True,
                threshold_percent=0,
            )
        )

        run = schedule.run(trigger="schedule")

        assert run.status == "ok"
        commands = [" ".join(call) for call in runner.calls]
        assert any(c.startswith("docker builder prune") for c in commands)
        assert "docker image prune -f" in commands
        assert not any(
            "system prune" in c or "image prune -a" in c or "volume" in c for c in commands
        )

    def test_the_journal_is_vacuumed_to_the_policys_size(
        self, schedule: CleanupSchedule, runner: FakeRunner
    ):
        schedule.set_policy(policy(actions=["journal"], journal_mb=300, threshold_percent=0))

        schedule.run(trigger="schedule")

        assert ("journalctl", "--vacuum-size=300M") in runner.calls

    def test_an_unknown_trigger_is_refused(self, schedule: CleanupSchedule):
        with pytest.raises(ValidationError):
            schedule.run(trigger="whenever")


class TestTheHistory:
    def test_a_cleanup_by_hand_leaves_a_row_with_the_disk_before_and_after(
        self, schedule: CleanupSchedule, disk: Disk
    ):
        disk.percent = 91.0

        schedule.storage.cleanup("pkg-cache", actor="ana")

        (row,) = schedule.history.list()
        assert (row.trigger, row.status, row.actor) == ("manual", "ok", "ana")
        assert [a.action for a in row.actions] == ["pkg-cache"]
        assert row.percent_before == 91.0

    def test_a_failed_cleanup_is_written_down_and_still_raises(
        self, schedule: CleanupSchedule, runner: FakeRunner
    ):
        runner.script(["apt-get", "clean"], stdout="E: lock", exit_code=100)

        with pytest.raises(ServerError):
            schedule.storage.cleanup("pkg-cache")

        (row,) = schedule.history.list()
        assert row.status == "failed" and "lock" in (row.actions[0].error or "")

    def test_a_rehearsal_writes_nothing(
        self, tmp_path: Path, runner: FakeRunner, store: NoustStore, disk: Disk
    ):
        manager = StorageManager(
            runner=runner,
            fs=DryRunFileSystem(RecordingFileSystem()),
            host=make_host(tmp_path / "root"),
            platform=platform_for("apt"),
            release_apps=lambda: [],
            backups_usage=lambda: {},
            log_paths=lambda: [],
        )

        manager.cleanup("pkg-cache")

        assert CleanupHistory(store).list() == []

    def test_a_run_of_several_actions_is_one_row_not_one_each(self, schedule: CleanupSchedule):
        schedule.set_policy(
            policy(actions=["pkg-cache", "journal", "releases"], threshold_percent=0)
        )

        schedule.run(trigger="schedule")

        assert len(schedule.history.list()) == 1

    def test_the_newest_come_first_and_only_the_last_few_hundred_are_kept(self, store: NoustStore):
        history = CleanupHistory(store)
        for index in range(RUNS_KEPT + 7):
            history.record(CleanupRun(trigger="manual", reason=f"run {index}"))

        runs = history.list(limit=200)

        assert len(runs) == RUNS_KEPT
        assert runs[0].reason == f"run {RUNS_KEPT + 6}"
        assert len(history.list(limit=5)) == 5
        assert history.list(limit=200)[-1].reason == "run 7"

    def test_a_row_whose_actions_cannot_be_read_still_lists(self, store: NoustStore):
        history = CleanupHistory(store)
        history.record(CleanupRun(trigger="manual"))
        with store._transaction() as cursor:
            cursor.execute("UPDATE storage_cleanup_runs SET actions = 'not json'")

        assert history.list()[0].actions == []
