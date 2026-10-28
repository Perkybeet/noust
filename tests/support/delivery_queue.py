# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_delivery_queue.py`` that other tests build on."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import cancellation, delivery
from noust.core.store import App, AppLayout, NoustStore
from noust.web import jobs as jobs_module
from noust.web.jobs import Job, JobContext, JobManager, JobType

A = "alpha.example.com"
B = "bravo.example.com"
C = "charlie.example.com"
D = "delta.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """
    A store in the test directory, with an application on releases for each domain.

    Yields:
        The store; the application locks live beside it.
    """
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "store" / "noust.db")
    for domain in (A, B, C, D):
        instance.create_app(
            App(
                domain=domain,
                app_type="nodejs",
                app_path=f"/var/www/apps/{domain}",
                port=3000,
                layout=AppLayout.RELEASES.value,
            )
        )
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def limits(monkeypatch: pytest.MonkeyPatch) -> list[delivery.DeliverySettings]:
    """
    The server's limits, as a one-element list a test replaces.

    Returns:
        The settings handed out.
    """
    current = [delivery.DeliverySettings(zero_downtime=False, max_concurrent_builds=2)]
    monkeypatch.setattr(delivery, "current", lambda **_kwargs: current[0])
    return current


@pytest.fixture
def manager(store: NoustStore, limits: list[delivery.DeliverySettings]) -> Iterator[JobManager]:
    """
    A job manager of its own for the test, looking at the queue ten times as often.

    Yields:
        The manager.
    """
    JobManager.reset_instance()
    mp = pytest.MonkeyPatch()
    mp.setattr(jobs_module, "DISPATCH_POLL_SECONDS", 0.05)
    mp.setattr(jobs_module, "LOCK_RETRY_SECONDS", 0.05)
    mp.setattr(jobs_module, "EXTERNAL_SYNC_SECONDS", 0.05)
    mp.setattr(cancellation, "POLL_INTERVAL", 0.05)
    instance = JobManager()
    try:
        yield instance
    finally:
        JobManager.reset_instance()
        mp.undo()


def wait_until(condition: Callable[[], object], seconds: float = 20.0) -> bool:
    """
    Poll a condition.

    Args:
        condition: Returns something true when it holds.
        seconds: How long to wait.

    Returns:
        Whether it held in time.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


class Work:
    """
    A job's function that the test holds open.

    Attributes:
        name: What the test calls it.
        started: Set when the function begins.
        release: The function returns when this is set.
        log: Where every function of the test writes ``start``/``end`` lines.
    """

    def __init__(self, name: str, log: list[str]) -> None:
        self.name = name
        self.started = threading.Event()
        self.release = threading.Event()
        self.log = log

    def __call__(self, *, job_context: JobContext | None = None, **_kwargs: Any) -> dict[str, str]:
        self.log.append(f"start {self.name}")
        self.started.set()
        self.release.wait(10)
        self.log.append(f"end {self.name}")
        return {"name": self.name}


def queue_work(
    manager: JobManager,
    work: Work,
    domain: str | None,
    job_type: JobType = JobType.UPDATE,
    **options: Any,
) -> Job:
    """
    Queue a gate as a job of a type on an application.

    Args:
        manager: The manager.
        work: The gate.
        domain: The application, or None for a job about the server.
        job_type: The type.
        **options: More arguments of ``create_job``.

    Returns:
        The queued job.
    """
    return manager.create_job(
        job_type=job_type,
        name=f"{job_type.value} {domain}",
        description=work.name,
        func=work,
        kwargs=options.pop("kwargs", {}),
        metadata=options.pop("metadata", {"domain": domain} if domain else {}),
        **options,
    )


def finish(*works: Work) -> None:
    """
    Let gates return.

    Args:
        *works: The gates.
    """
    for work in works:
        work.release.set()


@pytest.fixture
def log() -> list[str]:
    """What the jobs wrote, in order."""
    return []
