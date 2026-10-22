# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The resource limits of an engine on the host (checklist C-30).

A systemd drop-in applied without restarting the engine and proved twice: against systemd's own
answer and against the kernel's cgroup files. What is pinned here:

- the drop-in holds the four limits and nothing else, and lives beside the engine's own unit;
- a limit is applied by ``daemon-reload``, never by a restart;
- a limit systemd does not hold as written, a reload it refuses, and an engine that stops
  answering each leave the previous limits in place, with the journal verbatim;
- a limit the kernel does not enforce is said, not hidden;
- a memory limit below what the engine uses is refused until it is confirmed;
- values are checked against what the engine needs and the server has.

A fake systemd and kernel stand in for the real ones: ``daemon-reload`` reads the drop-in the
test's filesystem holds and ``systemctl show`` and the cgroup files answer from what it read.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import ConfirmationRequired, DatabaseEngineError, ValidationError
from noust.core.fs import DryRunFileSystem, set_fs
from noust.core.runner import CommandResult, DryRunRunner, set_runner
from noust.managers.database import flavours
from noust.managers.database import settings as settings_module
from noust.managers.database.host_limits import (
    DROPIN_NAME,
    HostLimits,
    check_tasks,
    parse_span_seconds,
)
from noust.managers.database.mongodb import MongoDBManager
from noust.managers.database.mysql import MySQLManager
from noust.managers.database.postgres import PostgresManager
from noust.managers.database.settings import Resources
from noust.managers.server.host import HostPaths
from tests.test_database_settings import ScriptedRunner, StubConfig, failed, ok, pg_answers

MB = 1024**2
GB = 1024**3
UNIT = "postgresql@16-main"
DEFAULT_TASKS = "4915"
SERVER = Resources(memory_bytes=8 * GB, cpus=4)
JOURNAL = "-- Journal begins --\nFATAL:  the database system is shutting down"


class FakeHost:
    """
    A systemd and a kernel, as far as the limits need them.

    ``daemon-reload`` reads the engine's drop-in from the test's filesystem; ``systemctl show``
    reports what that gave and the cgroup files hold it, unless the test says the kernel does
    not enforce limits.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.dropin = root / f"etc/systemd/system/{UNIT}.service.d/{DROPIN_NAME}"
        self.cgroup = root / f"sys/fs/cgroup/system.slice/{UNIT}.service"
        self.cgroup.mkdir(parents=True)
        self.held: dict[str, str] = {}
        self.in_use = 300 * MB
        self.enforces = True
        self.reload_fails = False
        self.engine_answers = True
        #: A directive another drop-in sets later than Noust's, and so wins.
        self.override: dict[str, str] = {}
        self.reloads = 0
        self._write_kernel()

    def reload(self) -> None:
        """What ``daemon-reload`` does: read the drop-in, apply it."""
        self.reloads += 1
        self.held = {}
        if self.dropin.exists():
            for line in self.dropin.read_text().splitlines():
                name, separator, value = line.partition("=")
                if separator:
                    self.held[name] = value
        self.held.update(self.override)
        if self.enforces:
            self._write_kernel()

    def _write_kernel(self) -> None:
        memory = _bytes(self.held.get("MemoryMax"))
        swap = _bytes(self.held.get("MemorySwapMax"))
        # What systemd gives a service that sets none: a share of the kernel's process limit.
        tasks = self.held.get("TasksMax", DEFAULT_TASKS)
        quota = self.held.get("CPUQuota")
        (self.cgroup / "memory.max").write_text(f"{memory}\n")
        (self.cgroup / "memory.swap.max").write_text(f"{swap}\n")
        (self.cgroup / "pids.max").write_text(f"{tasks}\n")
        (self.cgroup / "memory.current").write_text(f"{self.in_use}\n")
        cpu = "max 100000" if quota is None else f"{int(quota.rstrip('%')) * 1000} 100000"
        (self.cgroup / "cpu.max").write_text(f"{cpu}\n")

    def show(self) -> str:
        """What ``systemctl show`` prints for the unit."""
        memory = self.held.get("MemoryMax")
        swap = self.held.get("MemorySwapMax")
        quota = self.held.get("CPUQuota")
        lines = [
            f"MemoryMax={'infinity' if memory is None else _bytes(memory)}",
            f"MemorySwapMax={'infinity' if swap is None else _bytes(swap)}",
            "CPUQuotaPerSecUSec="
            + ("infinity" if quota is None else f"{int(quota.rstrip('%')) / 100:g}s"),
            f"TasksMax={self.held.get('TasksMax', DEFAULT_TASKS)}",
            f"MemoryCurrent={self.in_use}",
            f"ControlGroup=/system.slice/{UNIT}.service",
        ]
        return "\n".join(lines) + "\n"

    def answer(self, argv: tuple[str, ...], stdin: str | None) -> CommandResult | None:
        """The runner's answer to a command systemd or the engine would give."""
        if argv[:2] == ("systemctl", "daemon-reload"):
            if self.reload_fails:
                return failed("Failed to reload daemon: Access denied")
            self.reload()
            return ok()
        if argv == ("systemctl", "show", "--property=DefaultTasksMax"):
            return ok(f"DefaultTasksMax={DEFAULT_TASKS}\n")
        if argv[:2] == ("systemctl", "show"):
            return ok(self.show())
        if argv[:1] == ("journalctl",):
            return ok(JOURNAL)
        found = pg_answers()(argv, stdin)
        if found is not None and stdin and not self.engine_answers and "pg_settings" not in stdin:
            return failed("psql: connection to server failed")
        return found


def _bytes(text: str | None) -> str:
    """Megabytes as the drop-in writes them (``2048M``) to bytes as systemd and the kernel say."""
    if text is None:
        return "max"
    return str(int(text.rstrip("M")) * MB)


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeHost]:
    """A server with PostgreSQL 16 whose systemd and kernel are fakes under a temporary root."""
    monkeypatch.setattr(flavours, "HOST", HostPaths(tmp_path))
    monkeypatch.setattr(settings_module, "_sleep", lambda seconds: None)
    monkeypatch.setattr(settings_module, "WAIT_SECONDS", 3)
    fake = FakeHost(tmp_path)
    runner = ScriptedRunner()
    runner.answer = fake.answer
    set_runner(runner)
    fake.runner = runner  # type: ignore[attr-defined]
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture
def limits(host: FakeHost) -> HostLimits:
    """The limits of PostgreSQL on the fake host."""
    manager = PostgresManager()
    manager.config = StubConfig()
    return HostLimits(manager, SERVER)


def calls(host: FakeHost) -> list[tuple[str, ...]]:
    """Every command the fake host's runner was given."""
    return host.runner.calls  # type: ignore[attr-defined,no-any-return]


# ------------------------------------------------------------------ every engine


@pytest.mark.parametrize(
    ("known", "manager_class", "unit"),
    [
        ("mysql", MySQLManager, "mysql"),
        ("mariadb", MySQLManager, "mariadb"),
        ("mongod", MongoDBManager, "mongod"),
    ],
)
def test_the_drop_in_sits_beside_the_unit_each_engine_runs_as(
    host: FakeHost, known: str, manager_class: Any, unit: str
) -> None:
    host.runner.only_knows(known)  # type: ignore[attr-defined]
    manager = manager_class()
    manager.config = StubConfig()

    limits = HostLimits(manager, SERVER)

    assert limits.dropin() == host.root / f"etc/systemd/system/{unit}.service.d/{DROPIN_NAME}"
    assert limits.unit() == unit


# ------------------------------------------------------------------ reading


def test_an_engine_with_no_limit_reports_none_and_the_kernel_agrees(
    limits: HostLimits, host: FakeHost
) -> None:
    status = limits.status()

    assert status.unit == UNIT
    assert status.file == str(host.dropin)
    assert status.configured.to_dict() == dict.fromkeys(("memory_mb", "cpus", "swap_mb", "tasks"))
    # systemd gives every service a task limit of its own, which nobody set.
    assert status.effective.to_dict() == {**status.configured.to_dict(), "tasks": 4915}
    assert status.elsewhere == []
    assert status.enforced == {"memory_mb": True, "cpus": True, "swap_mb": True, "tasks": True}
    assert status.memory_in_use_mb == 300
    assert status.server_memory_mb == 8192 and status.cpus == 4
    assert status.minimum_memory_mb >= 128


def test_a_task_limit_that_is_not_systemd_s_own_default_is_named(
    limits: HostLimits, host: FakeHost
) -> None:
    host.override = {"TasksMax": "100"}
    host.reload()

    assert limits.status().elsewhere == ["TasksMax"]


def test_a_limit_set_outside_noust_is_named(limits: HostLimits, host: FakeHost) -> None:
    host.override = {"MemoryMax": "1024M"}
    host.reload()

    status = limits.status()

    assert status.configured.memory_mb is None and status.effective.memory_mb == 1024
    assert status.elsewhere == ["MemoryMax"]


# ------------------------------------------------------------------ setting


def test_the_limits_go_to_a_drop_in_of_noust_s_own_and_are_applied_without_a_restart(
    limits: HostLimits, host: FakeHost
) -> None:
    outcome = limits.apply({"memory_mb": "2g", "cpus": 1.5, "swap_mb": 0, "tasks": 4096})

    text = host.dropin.read_text()
    assert text.startswith("# Generated by Noust\n")
    assert text.splitlines()[-5:] == [
        "[Service]",
        "MemoryMax=2048M",
        "CPUQuota=150%",
        "MemorySwapMax=0M",
        "TasksMax=4096",
    ]
    assert outcome.changed == ["memory_mb", "cpus", "swap_mb", "tasks"]
    assert outcome.warnings == []
    assert ("systemctl", "daemon-reload") in calls(host)
    assert not [
        c for c in calls(host) if c[:2] in (("systemctl", "restart"), ("systemctl", "stop"))
    ]
    status = limits.status()
    assert status.configured.to_dict() == {
        "memory_mb": 2048,
        "cpus": 1.5,
        "swap_mb": 0,
        "tasks": 4096,
    }
    assert status.effective.to_dict() == status.configured.to_dict()
    assert all(status.enforced.values())


def test_a_limit_is_changed_alone_and_the_others_stay(limits: HostLimits, host: FakeHost) -> None:
    limits.apply({"memory_mb": 2048, "cpus": 2})

    outcome = limits.apply({"memory_mb": 4096})

    assert outcome.changed == ["memory_mb"]
    assert limits.configured().to_dict() == {
        "memory_mb": 4096,
        "cpus": 2.0,
        "swap_mb": None,
        "tasks": None,
    }


def test_the_command_line_hands_over_text_and_it_is_read_as_numbers(
    limits: HostLimits, host: FakeHost
) -> None:
    outcome = limits.apply({"memory_mb": "1g", "cpus": "0.5", "swap_mb": "0", "tasks": "1024"})

    assert outcome.changed == ["memory_mb", "cpus", "swap_mb", "tasks"]
    assert limits.configured().to_dict() == {
        "memory_mb": 1024,
        "cpus": 0.5,
        "swap_mb": 0,
        "tasks": 1024,
    }


def test_the_same_limits_again_change_nothing_and_reload_nothing(
    limits: HostLimits, host: FakeHost
) -> None:
    limits.apply({"memory_mb": 2048})
    reloads = host.reloads

    outcome = limits.apply({"memory_mb": 2048})

    assert outcome.changed == [] and host.reloads == reloads


def test_removing_the_last_limit_removes_the_file(limits: HostLimits, host: FakeHost) -> None:
    limits.apply({"memory_mb": 2048, "cpus": 2})

    limits.apply(clear=["memory_mb"])
    assert "MemoryMax" not in host.dropin.read_text()
    outcome = limits.apply(clear=["cpus"])

    assert outcome.changed == ["cpus"]
    assert not host.dropin.exists()
    assert limits.status().effective.memory_mb is None


# ------------------------------------------------------------------ checking


@pytest.mark.parametrize(
    ("key", "value", "why"),
    [
        ("memory_mb", 64, "too little memory"),
        ("memory_mb", "99g", "more memory"),
        ("memory_mb", "lots", "not an amount of memory"),
        ("memory_mb", True, "not an amount of memory"),
        ("cpus", 9, "more than this server has"),
        ("cpus", 0, "not a number of CPUs"),
        ("cpus", "fast", "not a number of CPUs"),
        ("swap_mb", -1, "swap"),
        ("tasks", 10, "not a limit of tasks"),
        ("tasks", "many", "not a limit of tasks"),
        ("tasks", 10**9, "not a limit of tasks"),
    ],
)
def test_a_limit_the_engine_cannot_run_in_or_the_server_cannot_give_is_refused(
    limits: HostLimits, host: FakeHost, key: str, value: Any, why: str
) -> None:
    with pytest.raises(ValidationError, match=why) as excinfo:
        limits.apply({key: value})

    assert excinfo.value.field == key
    assert not host.dropin.exists() and host.reloads == 0


def test_a_limit_noust_does_not_set_is_refused_by_name(limits: HostLimits) -> None:
    with pytest.raises(ValidationError, match="not a limit Noust sets"):
        limits.apply({"shares": 100})
    with pytest.raises(ValidationError, match="not a limit Noust sets"):
        limits.apply(clear=["Environment"])


def test_a_limit_cannot_be_set_and_removed_in_one_change(limits: HostLimits) -> None:
    with pytest.raises(ValidationError, match="set and removed"):
        limits.apply({"cpus": 2}, clear=["cpus"])


def test_the_value_of_a_drop_in_is_a_number_and_never_text_that_reaches_the_file(
    limits: HostLimits, host: FakeHost
) -> None:
    with pytest.raises(ValidationError):
        limits.apply({"memory_mb": "2g\nExecStart=/bin/sh"})
    with pytest.raises(ValidationError):
        limits.apply({"tasks": "4096\nEnvironment=X=1"})

    assert not host.dropin.exists()


def test_tasks_and_time_spans_are_read_the_way_systemd_prints_them() -> None:
    assert check_tasks("4096") == 4096
    assert parse_span_seconds("1.500s") == 1.5
    assert parse_span_seconds("100ms") == pytest.approx(0.1)
    assert parse_span_seconds("1min 30s") == 90
    assert parse_span_seconds("infinity") is None
    assert parse_span_seconds("") is None


# ------------------------------------------------------------------ the cost


def test_a_memory_limit_below_what_the_engine_uses_asks_for_a_confirmation(
    limits: HostLimits, host: FakeHost
) -> None:
    host.in_use = 900 * MB
    host.reload()

    with pytest.raises(ConfirmationRequired) as excinfo:
        limits.apply({"memory_mb": 512})

    assert "uses 900 MB now" in excinfo.value.warnings[0]
    assert not host.dropin.exists()

    outcome = limits.apply({"memory_mb": 512}, confirm=True)

    assert outcome.changed == ["memory_mb"]


def test_a_limit_above_what_the_engine_uses_needs_no_confirmation(
    limits: HostLimits, host: FakeHost
) -> None:
    assert limits.apply({"memory_mb": 4096}).changed == ["memory_mb"]


# ------------------------------------------------------------------ the proof


def test_a_limit_the_kernel_does_not_enforce_is_said(limits: HostLimits, host: FakeHost) -> None:
    host.enforces = False

    outcome = limits.apply({"memory_mb": 2048})

    assert any("kernel does not enforce" in warning for warning in outcome.warnings)
    assert limits.status().enforced["memory_mb"] is False
    assert host.dropin.exists()


def test_a_limit_systemd_does_not_hold_as_written_is_put_back_with_a_reason(
    limits: HostLimits, host: FakeHost
) -> None:
    limits.apply({"memory_mb": 2048})
    before = host.dropin.read_text()
    host.override = {"MemoryMax": "512M"}

    with pytest.raises(DatabaseEngineError, match="does not hold MemoryMax as written") as excinfo:
        limits.apply({"memory_mb": 4096})

    assert host.dropin.read_text() == before
    assert "systemctl cat" in (excinfo.value.details or "")
    assert "The previous limits were put back" in (excinfo.value.details or "")


def test_systemd_refusing_the_reload_leaves_the_engine_as_it_was(
    limits: HostLimits, host: FakeHost
) -> None:
    host.reload_fails = True

    with pytest.raises(DatabaseEngineError, match="did not take the new limits") as excinfo:
        limits.apply({"memory_mb": 2048})

    assert not host.dropin.exists()
    assert "Access denied" in (excinfo.value.output or "")
    assert JOURNAL in (excinfo.value.output or "")


def test_an_engine_that_stops_answering_gets_its_previous_limits_back(
    limits: HostLimits, host: FakeHost
) -> None:
    limits.apply({"memory_mb": 4096})
    before = host.dropin.read_text()
    host.engine_answers = False

    with pytest.raises(DatabaseEngineError, match="stopped answering") as excinfo:
        limits.apply({"memory_mb": 1024})

    assert host.dropin.read_text() == before
    assert host.held["MemoryMax"] == "4096M"
    assert excinfo.value.output is not None and JOURNAL in excinfo.value.output


def test_an_engine_that_stops_answering_with_no_limit_before_has_none_after(
    limits: HostLimits, host: FakeHost
) -> None:
    host.engine_answers = False

    with pytest.raises(DatabaseEngineError, match="stopped answering"):
        limits.apply({"cpus": 1})

    assert not host.dropin.exists() and "CPUQuota" not in host.held


def test_an_interruption_puts_the_previous_limits_back_too(
    limits: HostLimits, host: FakeHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupted() -> bool:
        raise KeyboardInterrupt

    monkeypatch.setattr(limits.settings, "_wait", interrupted)

    with pytest.raises(KeyboardInterrupt):
        limits.apply({"memory_mb": 2048})

    assert not host.dropin.exists()


# ------------------------------------------------------------------ safety


def test_the_drop_in_is_never_written_through_a_link(
    limits: HostLimits, host: FakeHost, tmp_path: Path
) -> None:
    host.dropin.parent.mkdir(parents=True)
    target = tmp_path / "elsewhere"
    target.write_text("keep")
    host.dropin.symlink_to(target)

    with pytest.raises(DatabaseEngineError):
        limits.apply({"memory_mb": 2048})

    assert target.read_text() == "keep"


def test_a_rehearsal_writes_and_runs_nothing(host: FakeHost) -> None:
    inner = ScriptedRunner()
    inner.answer = host.answer
    rehearsal = DryRunRunner(inner)
    set_runner(rehearsal)
    set_fs(DryRunFileSystem())
    try:
        manager = PostgresManager()
        manager.config = StubConfig()
        outcome = HostLimits(manager, SERVER).apply({"memory_mb": 2048})
    finally:
        set_runner(None)
        set_fs(None)

    assert outcome.changed == ["memory_mb"]
    assert not host.dropin.exists() and host.reloads == 0
    assert ("systemctl", "daemon-reload") in rehearsal.skipped


def test_an_engine_in_a_container_is_refused(host: FakeHost) -> None:
    manager = PostgresManager()
    manager.config = StubConfig()
    manager.instance = _container()

    with pytest.raises(DatabaseEngineError, match="limit"):
        HostLimits(manager, SERVER)


def _container() -> Any:
    """An instance record that is a container Noust runs."""
    from types import SimpleNamespace

    return SimpleNamespace(
        managed="pg16", container="noust-db-pg16", unit=None, image="postgres:16"
    )


# ------------------------------------------------------------------ the service


class _Running(PostgresManager):
    """A PostgreSQL manager reported installed and running."""

    def is_installed(self) -> bool:
        return True

    def is_running(self) -> bool:
        return True


@pytest.fixture
def service(host: FakeHost, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A database service over PostgreSQL on the fake host."""
    from noust.managers.database import host_limits
    from noust.managers.database.service import DatabaseService

    monkeypatch.setattr(host_limits, "server_resources", lambda: SERVER)

    def resolve(name: str) -> _Running:
        engine = _Running()
        engine.config = StubConfig()
        return engine

    return DatabaseService(resolve=resolve, engines=lambda: ["postgresql"])


def test_the_service_audits_a_change_with_the_limits_and_nothing_secret(
    service: Any, host: FakeHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core import audit

    recorded: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(audit, "record", lambda event, **kwargs: recorded.append((event, kwargs)))

    outcome = service.change_engine_limits("postgresql", {"memory_mb": 4096, "cpus": 2})

    assert outcome.changed == ["memory_mb", "cpus"]
    [(event, kwargs)] = recorded
    assert event == "db.limits.change"
    assert kwargs["target"] == "db:postgresql"
    assert kwargs["details"]["keys"] == ["memory_mb", "cpus"]
    assert kwargs["details"]["limits"]["memory_mb"] == 4096


def test_the_service_audits_a_failure_and_a_no_op_leaves_no_trace(
    service: Any, host: FakeHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core import audit

    recorded: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(audit, "record", lambda event, **kwargs: recorded.append((event, kwargs)))
    service.change_engine_limits("postgresql", {"memory_mb": 4096})
    recorded.clear()

    service.change_engine_limits("postgresql", {"memory_mb": 4096})
    assert recorded == []

    host.reload_fails = True
    with pytest.raises(DatabaseEngineError):
        service.change_engine_limits("postgresql", {"cpus": 2})

    [(event, kwargs)] = recorded
    assert event == "db.limits.change" and kwargs["outcome"] == "failure"
    assert kwargs["details"]["keys"] == ["cpus"]


def test_the_service_reads_the_limits_and_never_those_of_an_engine_in_a_container(
    service: Any, host: FakeHost
) -> None:
    assert service.engine_limits("postgresql").unit == UNIT
    with pytest.raises(ValidationError, match="in a container"):
        service.engine_limits("postgresql@proggest.db")
    with pytest.raises(ValidationError, match="in a container"):
        service.change_engine_limits("postgresql@proggest.db", {"memory_mb": 1024})


def test_the_limits_change_event_is_declared() -> None:
    from noust.core.audit.catalog import EVENTS

    assert "db.limits.change" in EVENTS
