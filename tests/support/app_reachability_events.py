# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_app_reachability_events.py`` that other tests build on."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.managers.service_manager import ServiceManager
from noust.monitor.process_monitor import MonitorConfig, ProcessMonitor
from noust.monitor.reachability import AppProbe
from tests.support.monitor_units import FakeEventNotifier, show_block

PROBE = "GET http://127.0.0.1:3004/ -> <urlopen error [Errno 111] Connection refused>"


def make_app(**fields: Any) -> App:
    """An application row as the store would hold it."""
    values: dict[str, Any] = {
        "domain": "shop.example.com",
        "app_type": "nextjs",
        "port": 3004,
        "app_path": "/var/www/apps/shop-example-com",
    }
    values.update(fields)
    return App(**values)


DOMAIN = "shop.example.com"
UNIT = "shop-example-com"
RUNNING = show_block(f"{UNIT}.service")
STOPPED_BY_COMMAND = show_block(f"{UNIT}.service", active="inactive", sub="dead", result="success")


class FakeUnits:
    """Which units serve each application, with the real unit reader on a fake runner."""

    def __init__(self, runner: FakeRunner, units: dict[str, list[str]]) -> None:
        self.units = units
        self.describe_units = ServiceManager(runner=runner).describe_units

    def serving_units(self, app: App) -> list[str]:
        return self.units.get(app.domain, [])

    def managed_units(self) -> list[Any]:
        return []

    def logs(self, name: str, lines: int = 50) -> str:
        return ""


class Prober:
    """What the monitor's probe finds, set by the test; every call is kept."""

    def __init__(self) -> None:
        self.result: AppProbe | Exception | None = AppProbe(ok=True, detail="")
        self.calls: list[str] = []

    def __call__(self, app: App) -> AppProbe | None:
        self.calls.append(app.domain)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def down(self, detail: str = PROBE, *, containers: bool = False) -> None:
        self.result = AppProbe(ok=False, detail=detail, containers=containers)

    def up(self, *, containers: bool = False) -> None:
        self.result = AppProbe(ok=True, detail="", containers=containers)


class Scans:
    """A monitor over fakes, and a clock the test moves one scan at a time."""

    def __init__(self, tmp_path: Path, store: NoustStore, **app_fields: Any) -> None:
        self.runner = FakeRunner()
        self.prober = Prober()
        self.notifier = FakeEventNotifier()
        # The locks and jobs the tests make are stamped with the real clock.
        self.start = time.time()
        self.now = self.start
        self.state_path = tmp_path / "reachability.json"
        self.store = store
        self.tmp_path = tmp_path
        store.create_app(make_app(**app_fields))
        self.monitor = self.build()

    def build(self) -> ProcessMonitor:
        monitor = ProcessMonitor(
            config=MonitorConfig(),
            runner=self.runner,
            event_notifier=self.notifier,
            service_manager=FakeUnits(self.runner, {DOMAIN: [UNIT]}),
            reachability_probe=self.prober,
            reachability_state_path=self.state_path,
            clock=lambda: self.now,
        )
        monitor.global_config.reload = lambda: None  # type: ignore[method-assign]
        return monitor

    def scan(self, unit: str = RUNNING, *, after: float = 60.0) -> None:
        """One scan, ``after`` seconds after the last, with systemd answering ``unit``."""
        self.now += after
        self.runner.script(["systemctl", "show"], stdout=unit)
        self.monitor._check_reachability()

    @property
    def codes(self) -> list[str]:
        return [event.code for event in self.notifier.events]
