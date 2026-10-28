# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_project_apply.py`` that other tests build on."""

from __future__ import annotations

from typing import Any

from noust.core.exceptions import ServiceError
from noust.managers.cron_manager import CronJob

DOMAIN = "shop.example.com"


class FakeCron:
    """A cron manager that keeps jobs in memory, shaped like list_jobs answers."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}

    def list_jobs(self) -> list[dict[str, Any]]:
        return sorted(self.jobs.values(), key=lambda job: job["name"])

    def get_job(self, name: str) -> dict[str, Any] | None:
        return self.jobs.get(name)

    def create_job(self, job: CronJob) -> CronJob:
        self.jobs[job.name] = {
            "name": job.name,
            "command": job.command,
            "on_calendar": job.on_calendar,
            "enabled": True,
            "user": job.user or "www-data",
            "working_directory": job.working_directory or "",
            "app_domain": job.app_domain,
        }
        return job

    def disable_job(self, name: str) -> str:
        if name not in self.jobs:
            raise ServiceError(f"No job {name}")
        self.jobs[name]["enabled"] = False
        return f"noust-cron-{name}.timer"

    def delete_job(self, name: str) -> bool:
        return self.jobs.pop(name, None) is not None


class Recorder:
    """What the managers apply was handed, in order."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()

    def of(self, name: str) -> Any:
        def call(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))
            if name in self.fail:
                raise ServiceError(f"{name} refused")

        return call

    def names(self) -> list[str]:
        return [name for name, _args, _kwargs in self.calls]

    def args(self, name: str) -> tuple[Any, ...]:
        return next(args for called, args, _kwargs in self.calls if called == name)

    def kwargs(self, name: str) -> dict[str, Any]:
        return next(kwargs for called, _args, kwargs in self.calls if called == name)
