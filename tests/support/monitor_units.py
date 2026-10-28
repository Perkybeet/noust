# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_monitor_units.py`` that other tests build on."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from noust.core.runner import FakeRunner
from noust.managers.service_manager import ServiceManager
from noust.monitor.process_monitor import MonitorConfig, ProcessMonitor


def show_block(
    unit: str,
    *,
    active: str = "active",
    sub: str = "running",
    result: str = "success",
    status: int = 0,
    restarts: int = 0,
    load: str = "loaded",
    names: str | None = None,
) -> str:
    """
    Render what ``systemctl show --property=...`` prints for one unit.

    Args:
        unit: The unit's Id, with its suffix.
        active: ActiveState.
        sub: SubState.
        result: Result of the last run.
        status: ExecMainStatus.
        restarts: NRestarts.
        load: LoadState.
        names: Names, when it differs from the Id (an alias).

    Returns:
        The block, without the blank line that separates blocks.
    """
    return (
        f"Id={unit}\n"
        f"Names={names or unit}\n"
        f"LoadState={load}\n"
        f"ActiveState={active}\n"
        f"SubState={sub}\n"
        "UnitFileState=enabled\n"
        f"Result={result}\n"
        f"ExecMainStatus={status}\n"
        f"NRestarts={restarts}\n"
    )


class FakeServiceManager:
    """
    The real ServiceManager on a fake runner, with the managed set given.

    Which units WASM manages is the service manager's to decide and its own
    tests pin it; here it is an input. Reading their state is the real
    ``describe_units``, so the ``systemctl show`` asserted on is the real one.
    """

    def __init__(
        self, runner: FakeRunner, names: list[str], error: Exception | None = None
    ) -> None:
        self.names = names
        self.error = error
        self.journal_reads: list[str] = []
        self.journal_error: Exception | None = None
        self.describe_units = ServiceManager(runner=runner).describe_units

    def managed_units(self) -> list[SimpleNamespace]:
        if self.error is not None:
            raise self.error
        return [SimpleNamespace(name=name) for name in self.names]

    def logs(self, name: str, lines: int = 50) -> str:
        """The journal of a unit: what the notification's excerpt is made of."""
        self.journal_reads.append(name)
        if self.journal_error is not None:
            raise self.journal_error
        return f"Sep 29 10:45:12 web-1 app[1]: {name} crashed\nSep 29 10:45:13 web-1 app[1]: boom"


class FakeEventNotifier:
    """Records every event instead of delivering it."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    def notify(self, event: Any) -> None:
        self.events.append(event)


def make_monitor(
    runner: FakeRunner,
    managed: list[str],
    *,
    extras: tuple[str, ...] = (),
    services: Any = None,
) -> tuple[ProcessMonitor, FakeEventNotifier]:
    """
    Build a monitor over fakes.

    Args:
        runner: Answers systemctl.
        managed: The units the service manager says WASM manages.
        extras: ``monitor.watch_units``.
        services: A service manager to use instead of one listing ``managed``.

    Returns:
        The monitor and the notifier that records what it announced.
    """
    notifier = FakeEventNotifier()
    monitor = ProcessMonitor(
        config=MonitorConfig(watch_units=extras),
        runner=runner,
        event_notifier=notifier,
        service_manager=services or FakeServiceManager(runner, managed),
    )
    # The notifier path reloads the configuration from disk first; nothing
    # here depends on it.
    monitor.global_config.reload = lambda: None  # type: ignore[method-assign]
    return monitor, notifier


def scan(runner: FakeRunner, monitor: ProcessMonitor, *blocks: str) -> None:
    """
    Run one service check with systemd answering ``blocks``.

    Args:
        runner: The fake runner; later scripts win, so each scan re-scripts.
        monitor: The monitor.
        blocks: One ``show_block`` per unit.
    """
    runner.script(["systemctl", "show"], stdout="\n".join(blocks))
    monitor._report_services()
