# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_auto_update.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from noust.core.config import Config
from noust.core.exceptions import NoustError
from noust.core.update_checker import UpdateCheckInProgress, VersionCheck, _location
from noust.managers.auto_update import AutoUpdate, AutoUpdateSettings
from noust.managers.self_update import SelfUpdateRecord, SelfUpdateRefused

RUNNING = "3.4.0"


def check(
    *,
    installable: str | None = None,
    published: str | None = None,
    security: str | None = None,
    method: str = "pip",
) -> VersionCheck:
    return VersionCheck(
        current=RUNNING,
        location=_location(),
        method=method,
        installable=installable,
        published=published or installable,
        checked_at=0.0,
        security=security,
    )


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "etc" / "noust" / "config.yaml"
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", path)
    Config.reset_instance()
    try:
        yield path
    finally:
        Config.reset_instance()


@dataclass
class FakeScheduler:
    installed: list[dict[str, Any]] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    broken: bool = False

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
        if self.broken:
            raise NoustError("Failed to enable timer")
        self.installed.append(
            {
                "unit": unit,
                "prefix": prefix,
                "timer": timer,
                "service": service,
                "calendar": calendar,
                **values,
            }
        )

    def remove_unit_pair(self, unit: str, prefix: str) -> bool:
        self.removed.append(unit)
        return True

    def unit_state(self, unit: str) -> dict[str, Any]:
        return {"installed": bool(self.installed), "next_run": None, "last_run": None}


class FakeUpdater:
    """Stands for SelfUpdate: it records what would have been installed."""

    version = RUNNING

    def __init__(
        self,
        *,
        refusal: SelfUpdateRefused | None = None,
        start_refusal: SelfUpdateRefused | None = None,
        ends: str = "installed",
    ) -> None:
        self._refusal = refusal
        self._start_refusal = start_refusal
        self.ends = ends
        self.started: list[dict[str, Any]] = []

    def refusal(self) -> SelfUpdateRefused | None:
        return self._refusal

    def start(
        self,
        *,
        target_version: str | None = None,
        job_id: str | None = None,
        actor: str | None = None,
        on_line: Any = None,
    ) -> SelfUpdateRecord:
        if self._start_refusal is not None:
            raise self._start_refusal
        self.started.append({"target": target_version, "actor": actor})
        return SelfUpdateRecord(
            id="abc",
            method="pip",
            from_version=RUNNING,
            argv=["pip"],
            target_version=target_version,
            unit="noust-self-update-abc",
        )

    def follow(self, record: SelfUpdateRecord, on_line: Any) -> SelfUpdateRecord:
        record.status = self.ends
        if self.ends == "failed":
            record.error = "pip failed"
            record.tail = ["ERROR: no matching distribution"]
        else:
            record.to_version = record.target_version
        return record


def window_at(text: str) -> datetime:
    """2026-10-14 is a Wednesday; the default window is 03:00-05:00."""
    return datetime.fromisoformat(f"2026-10-{text}")


class Harness:
    def __init__(
        self,
        tmp_path: Path,
        *,
        now: str = "14T03:30",
        found: VersionCheck | None = None,
        busy: bool = False,
        check_enabled: bool = True,
        updater: FakeUpdater | None = None,
    ) -> None:
        self.scheduler = FakeScheduler()
        self.updater = updater or FakeUpdater()
        self.checks = 0
        self.found = found
        self.now = window_at(now)
        self.busy = busy
        self.check_enabled = check_enabled
        self.manager = AutoUpdate(
            scheduler=self.scheduler,  # type: ignore[arg-type]
            self_update=lambda: self.updater,  # type: ignore[arg-type,return-value]
            check=self._check,
            check_enabled=lambda: self.check_enabled,
            busy=lambda: self.busy,
            clock=lambda: self.now,
            state_path=tmp_path / "auto-update.json",
        )

    def _check(self) -> VersionCheck:
        self.checks += 1
        if self.found is None:
            raise UpdateCheckInProgress("reading")
        return self.found

    def turn_on(self, **changes: Any) -> AutoUpdateSettings:
        return self.manager.set_settings(enabled=True, **changes)
