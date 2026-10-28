# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_service_manager.py`` that other tests build on."""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.runner import FakeRunner
from noust.core.store import App, Service
from noust.managers.service_manager import ServiceManager


class FakeStore:
    """In-memory stand-in for the SQLite store."""

    def __init__(self) -> None:
        self.services: dict[str, Service] = {}
        self.apps: list[App] = []
        self.deleted: list[str] = []

    def list_services(self, **_kwargs: object) -> list[Service]:
        """Return every recorded service."""
        return list(self.services.values())

    def list_apps(self, **_kwargs: object) -> list[App]:
        """Return every recorded application."""
        return list(self.apps)

    def get_service(self, name: str) -> Service | None:
        """Return a service by name, or None."""
        return self.services.get(name)

    def create_service(self, service: Service) -> Service:
        """Record a new service."""
        self.services[service.name] = service
        return service

    def update_service(self, service: Service) -> Service:
        """Replace a recorded service."""
        self.services[service.name] = service
        return service

    def update_service_status(self, name: str, status: str, **_kwargs: object) -> bool:
        """Record a status change."""
        service = self.services.get(name)
        if service is None:
            return False
        service.status = status
        return True

    def delete_service(self, name: str) -> bool:
        """Forget a service."""
        self.deleted.append(name)
        return self.services.pop(name, None) is not None


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    """
    Replace the SQLite store used by the service manager.

    Args:
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The in-memory store the manager will talk to.
    """
    fake = FakeStore()
    monkeypatch.setattr("noust.managers.service_manager.get_store", lambda: fake)
    return fake


@pytest.fixture
def unit_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """
    Point every unit directory the manager knows about at a temporary tree.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        Mapping with the managed directory under ``managed`` and the two
        directories the distribution owns under ``distro`` and ``runtime``.
    """
    managed = tmp_path / "etc/systemd/system"
    runtime = tmp_path / "run/systemd/system"
    distro = tmp_path / "usr/lib/systemd/system"
    for directory in (managed, runtime, distro):
        directory.mkdir(parents=True)

    monkeypatch.setattr(ServiceManager, "SYSTEMD_DIR", managed)
    monkeypatch.setattr(ServiceManager, "UNIT_SEARCH_DIRS", (managed, runtime, distro))
    return {"managed": managed, "runtime": runtime, "distro": distro}


@pytest.fixture
def manager(runner: FakeRunner, store: FakeStore, unit_dirs: dict[str, Path]) -> ServiceManager:
    """
    Build a manager wired to the fake runner, store and unit directories.

    Args:
        runner: The fake command runner installed process-wide.
        store: The in-memory store.
        unit_dirs: The temporary unit directories.

    Returns:
        The manager under test.
    """
    return ServiceManager()
