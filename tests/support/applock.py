# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_applock.py`` that other tests build on."""

from __future__ import annotations

import threading

from noust.core.applock import AppBusyError, app_lock


class Holder:
    """Holds an application's lock in another thread until told to let go."""

    def __init__(self, domain: str, operation: str) -> None:
        self.domain = domain
        self.operation = operation
        self.acquired = threading.Event()
        self.release = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        with app_lock(self.domain, self.operation):
            self.acquired.set()
            self.release.wait(timeout=10)

    def __enter__(self) -> Holder:
        self.thread.start()
        assert self.acquired.wait(timeout=5)
        return self

    def __exit__(self, *exc: object) -> None:
        self.release.set()
        self.thread.join(timeout=5)


def attempt_elsewhere(domain: str, operation: str) -> BaseException | None:
    """
    Try to take a lock from another thread, as a concurrent request would.

    Returns:
        What the attempt raised, or None when it got the lock.
    """
    outcome: list[BaseException | None] = []

    def run() -> None:
        try:
            with app_lock(domain, operation):
                outcome.append(None)
        except AppBusyError as error:
            outcome.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(timeout=5)
    return outcome[0]
