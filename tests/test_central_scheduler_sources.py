# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The two schedules the central of a NAS runs besides its backups: the look for new image versions and the
cleanup of Docker.

Both are one list with a server (the rows of the store and the settings), and a different engine: a timer
there, the central's own scheduler here, running the command the timer would. The look at the registries is
a request to somebody else, so it is off until a person sets it and nothing in a default configuration ever
makes it. The cleanup is the policy a server has already (``noust server cleanup-schedule``), which on a
machine without systemd writes no timer and offers only what can be done there.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.central import scheduler as sched
from noust.central.scheduler import Scheduler
from noust.core.config import Config
from noust.core.exceptions import ConfigError, ValidationError
from noust.core.runner import FakeRunner, set_runner
from noust.core.store import NoustStore
from noust.managers.server.storage_policy import (
    CleanupPolicy,
    CleanupSchedule,
    default_actions,
    schedulable_actions,
)

#: A Thursday.
NOON = datetime(2026, 11, 12, 12, 0, 0)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def fresh_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    monkeypatch.delenv("NOUST_DEPLOY_HERE", raising=False)
    Config.reset_instance()
    yield
    Config.reset_instance()


@pytest.fixture
def container_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
    monkeypatch.setenv("NOUST_DEPLOY_HERE", "containers")
    Config.reset_instance()


def make(
    tmp_path: Path, store: NoustStore, now: datetime = NOON
) -> tuple[Scheduler, FakeRunner, list[datetime]]:
    clock = [now]
    runner = FakeRunner()
    scheduler = Scheduler(
        runner=runner,
        state_path=tmp_path / "scheduler.json",
        say=lambda line: None,
        clock=lambda: clock[0],
        store=store,
    )
    return scheduler, runner, clock


def ran(runner: FakeRunner) -> list[tuple[str, ...]]:
    return [call[3:] for call in runner.calls if call[1:3] == ("-m", "noust")]


# -- The look for new image versions -----------------------------------------------------------


class TestImageCheck:
    def test_it_is_off_in_a_default_configuration(self, tmp_path: Path, store: NoustStore) -> None:
        assert Config().get("central.image_check") == ""
        assert sched.SOURCES["image_check"](store) == []

        scheduler, runner, clock = make(tmp_path, store)
        scheduler.tick()
        clock[0] += timedelta(days=30)
        scheduler.tick()

        assert ran(runner) == []

    def test_it_is_the_command_a_timer_would_run_once_somebody_sets_it(
        self, store: NoustStore
    ) -> None:
        Config().set("central.image_check", "weekly")

        tasks = sched.SOURCES["image_check"](store)

        assert [(t.key, t.kind, t.subject, t.calendar, t.argv) for t in tasks] == [
            ("image_check", "image_check", "", "weekly", ("docker", "images", "check"))
        ]

    def test_it_runs_when_its_calendar_says_and_not_before(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        Config().set("central.image_check", "*-*-* 04:00:00")
        scheduler, runner, clock = make(tmp_path, store)
        scheduler.tick()
        clock[0] += timedelta(hours=3)
        scheduler.tick()
        assert ran(runner) == []

        clock[0] += timedelta(hours=14)
        scheduler.tick()

        assert ran(runner) == [("docker", "images", "check")]

    def test_a_calendar_that_is_not_one_is_refused_where_it_is_set(self) -> None:
        with pytest.raises(ConfigError, match=r"central\.image_check"):
            Config().set("central.image_check", "whenever")

    def test_off_and_nothing_are_the_same(self, store: NoustStore) -> None:
        Config().set("central.image_check", "daily")
        Config().set("central.image_check", "off")

        assert sched.SOURCES["image_check"](store) == []

    def test_what_a_task_is_said_as_in_the_listing(self, tmp_path: Path, store: NoustStore) -> None:
        Config().set("central.image_check", "daily")
        scheduler, _runner, _clock = make(tmp_path, store)

        row = next(s for s in scheduler.status() if s.task.kind == "image_check")

        assert row.next_run is not None and row.problem is None


# -- The cleanup of Docker ---------------------------------------------------------------------


def set_policy(store: NoustStore, **fields: object) -> CleanupPolicy:
    from noust.managers.server.storage import StorageManager

    manager = CleanupSchedule(
        StorageManager(runner=FakeRunner()), store=store, scheduler=_NoTimers()
    )
    policy = CleanupPolicy.from_dict(
        {"enabled": True, "schedule": "daily", "actions": ["releases"], **fields}
    )
    manager.set_policy(policy, actor="test")
    return policy


class _NoTimers:
    """What writes timers, which a test of what the scheduler reads has no need of."""

    def install_unit_pair(self, *args: object, **kwargs: object) -> None:
        return None

    def remove_unit_pair(self, *args: object, **kwargs: object) -> bool:
        return True


class TestCleanup:
    def test_nothing_is_scheduled_while_the_policy_is_off(self, store: NoustStore) -> None:
        assert sched.SOURCES["cleanup"](store) == []
        set_policy(store, enabled=False)
        assert sched.SOURCES["cleanup"](store) == []

    def test_the_policy_is_the_command_its_timer_would_run(self, store: NoustStore) -> None:
        set_policy(store, schedule="weekly")

        tasks = sched.SOURCES["cleanup"](store)

        assert [(t.key, t.kind, t.subject, t.calendar, t.argv) for t in tasks] == [
            (
                "cleanup",
                "cleanup",
                "",
                "weekly",
                (
                    "server",
                    "cleanup-schedule",
                    "run",
                    "--scheduled",
                    "--reason",
                    "Scheduled storage cleanup",
                ),
            )
        ]

    def test_the_command_is_the_one_the_servers_timer_unit_runs(self) -> None:
        from noust.core.paths import STORAGE_CLEAN_UNIT

        template = (
            Path(__file__).parent.parent / "src/noust/templates/systemd/storage-clean-service.j2"
        ).read_text()
        task = sched.Task(
            key="cleanup",
            kind="cleanup",
            subject="",
            unit=STORAGE_CLEAN_UNIT,
            calendar="daily",
            argv=sched.CLEANUP_ARGV,
        )

        assert f"{' '.join(task.argv[:3])} --scheduled" in template
        assert '--reason "Scheduled storage cleanup"' in template

    def test_it_runs_when_due_and_a_policy_set_on_a_container_host_writes_no_timer(
        self, tmp_path: Path, store: NoustStore, container_host: None
    ) -> None:
        from noust.managers.server.storage import StorageManager

        runner = FakeRunner()
        manager = CleanupSchedule(StorageManager(runner=runner), store=store)
        manager.set_policy(
            CleanupPolicy.from_dict(
                {"enabled": True, "schedule": "*-*-* 03:00:00", "actions": ["releases"]}
            ),
            actor="test",
        )
        assert not any(call[0] == "systemctl" for call in runner.calls)

        scheduler, run, clock = make(tmp_path, store)
        scheduler.tick()
        clock[0] += timedelta(days=1)
        scheduler.tick()

        assert ran(run) == [sched.CLEANUP_ARGV]


class TestWhatCanBeCleanedHere:
    def test_a_server_schedules_what_it_always_could(self) -> None:
        assert "journal" in schedulable_actions()
        assert default_actions() == ("journal", "pkg-cache", "releases")

    def test_a_container_host_has_no_journal_and_no_package_cache_to_clean(
        self, container_host: None
    ) -> None:
        assert set(schedulable_actions()) == {
            "releases",
            "docker-build-cache",
            "docker-dangling-images",
        }
        assert default_actions() == ("releases",)

    def test_a_policy_for_what_does_not_exist_there_is_refused_with_what_does(
        self, container_host: None
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            CleanupPolicy.from_dict({"actions": ["journal"], "enabled": True})

        assert "journal" in caught.value.message
        assert "docker-dangling-images" in (caught.value.details or "")

    def test_a_policy_with_no_actions_given_is_the_default_of_the_machine(
        self, container_host: None
    ) -> None:
        assert CleanupPolicy.from_dict({"enabled": True}).actions == ("releases",)


class TestWhatIsOffered:
    def test_the_command_and_the_api_list_only_what_can_be_cleaned_here(
        self, container_host: None, store: NoustStore
    ) -> None:
        import json

        from noust.cli.app import cli as root_cli

        set_runner(FakeRunner())
        try:
            shown = CliRunner().invoke(root_cli, ["server", "cleanup-schedule", "show", "--json"])
        finally:
            set_runner(None)

        assert shown.exit_code == 0, shown.output
        listed = json.loads(shown.output.strip().splitlines()[-1])["schedulable"]
        assert set(listed) == {"releases", "docker-build-cache", "docker-dangling-images"}

    def test_a_server_lists_all_it_could(self, store: NoustStore) -> None:
        import json

        from noust.cli.app import cli as root_cli

        set_runner(FakeRunner())
        try:
            shown = CliRunner().invoke(root_cli, ["server", "cleanup-schedule", "show", "--json"])
        finally:
            set_runner(None)

        assert "journal" in json.loads(shown.output.strip().splitlines()[-1])["schedulable"]


class TestTheCommandsWithoutRoot:
    """The central runs as an unprivileged user: Docker's socket is what it has, not root."""

    @staticmethod
    def invoke(args: list[str]) -> object:
        from noust.cli.app import cli as root_cli

        return CliRunner().invoke(root_cli, args)

    def test_a_container_host_runs_the_scheduled_cleanup_without_being_root(
        self,
        container_host: None,
        store: NoustStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("noust.cli.commands.server.check_root", lambda: False)

        result = self.invoke(["server", "cleanup-schedule", "run", "--scheduled"])

        assert "needs root" not in str(getattr(result, "output", ""))

    def test_a_server_still_needs_root_for_it(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("noust.cli.commands.server.check_root", lambda: False)

        result = self.invoke(["server", "cleanup-schedule", "run", "--scheduled"])

        assert "needs root" in str(getattr(result, "output", "")) + str(
            getattr(result, "exception", "")
        )
