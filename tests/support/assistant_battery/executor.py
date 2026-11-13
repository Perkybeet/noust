# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The false executor: everything the machine would have been asked to do, written down (item 171).

What a proposal comes to is measured where the server hands an order to the machine, not where the
assistant says it did something. Four places take an order, and each leaves a receipt here:

- an action on an application's service (restart, start, stop);
- a change of an application's resource limits;
- the queue of background jobs (a backup, a deployment, a rollback, a renewal: every long operation);
- the runner, which every process of the server goes through (rule 1 of the project), minus the commands
  that are declared as read-only probes: a receipt for anything else is a command that changed or may
  have changed the machine.

Nothing is run. A receipt is a plain mapping, so a scenario can say what it expects as data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.runner import FakeRunner, is_read_only
from noust.web.api import apps as apps_api
from noust.web.api.apps import AppActionResponse
from noust.web.jobs import Job, JobManager, JobType

__all__ = ["Executor", "Receipt"]

#: One order the machine was given.
Receipt = dict[str, Any]


@dataclass
class Executor:
    """
    Records the orders the server gives.

    Attributes:
        receipts: Every receipt, in the order the orders were given, each with its ``phase``.
        phase: Who is acting: ``turn`` while the assistant works, ``approval`` while a person carries out
            what it proposed.
        restarts: The Lab's own list of service actions, kept in step because other checks read it.
    """

    restarts: list[str] | None = None
    receipts: list[Receipt] = field(default_factory=list)
    phase: str = "turn"
    _runner: FakeRunner | None = None
    _seen_runner_calls: int = 0

    def install(self, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner | None) -> None:
        """
        Put the recorders where the server hands orders to the machine.

        Args:
            monkeypatch: Patching helper; the patches are put back when the test ends.
            runner: The machine's runner, whose non-probe commands count as receipts.
        """
        self._runner = runner
        self._seen_runner_calls = 0 if runner is None else len(runner.calls)

        def service_action(domain: str, action: str, past_tense: str) -> AppActionResponse:
            if self.restarts is not None:
                self.restarts.append(f"{action} {domain}")
            self._note({"kind": "service", "verb": action, "domain": domain})
            return AppActionResponse(
                success=True, message=f"Application {past_tense}: {domain}", domain=domain
            )

        def set_limits(domain: str, limits: Any, restart: bool = False) -> Any:
            self._note(
                {
                    "kind": "limits",
                    "domain": domain,
                    "memory_max_mb": limits.memory_max_mb,
                    "cpu_quota_percent": limits.cpu_quota_percent,
                    "tasks_max": limits.tasks_max,
                }
            )
            return SimpleNamespace(
                domain=domain, limits=limits, units=[], restart_required=False, restarted=False
            )

        def create_job(
            manager: JobManager,
            job_type: JobType,
            name: str,
            description: str,
            func: Any,
            args: tuple[Any, ...] = (),
            kwargs: dict[str, Any] | None = None,
            metadata: dict[str, Any] | None = None,
            *rest: Any,
            **options: Any,
        ) -> Job:
            self._note({"kind": "job", "type": job_type.value, **(kwargs or {})})
            return Job(
                id=f"job-{len(self.receipts):04d}",
                type=job_type,
                name=name,
                description=description,
                metadata=dict(metadata or {}),
            )

        monkeypatch.setattr(apps_api, "_service_action", service_action)
        monkeypatch.setattr(apps_api, "set_resource_limits", set_limits)
        monkeypatch.setattr(JobManager, "create_job", create_job)

    def _note(self, receipt: Receipt) -> None:
        self.receipts.append({**receipt, "phase": self.phase})

    def collect_runner(self) -> None:
        """Take what the runner was asked since the last time, minus the probes that only look."""
        if self._runner is None:
            return
        calls = self._runner.calls[self._seen_runner_calls :]
        self._seen_runner_calls = len(self._runner.calls)
        for argv in calls:
            if not is_read_only(argv):
                self._note({"kind": "runner", "argv": list(argv)})

    def enter(self, phase: str) -> None:
        """
        Change who is acting, first giving the runner's orders to the phase that gave them.

        Args:
            phase: ``turn`` or ``approval``.
        """
        self.collect_runner()
        self.phase = phase

    def during(self, phase: str) -> list[Receipt]:
        """
        Args:
            phase: ``turn`` or ``approval``.

        Returns:
            The receipts of that phase so far, in order.
        """
        self.collect_runner()
        return [r for r in self.receipts if r["phase"] == phase]
