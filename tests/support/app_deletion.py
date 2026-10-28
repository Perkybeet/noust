# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_app_deletion.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.config import Config
from noust.core.exceptions import ServiceError
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore, Service, Site
from noust.deployers import lifecycle
from noust.managers.webserver import SiteDeletion
from noust.web.jobs import Job, JobContext, JobType

DOMAIN = "shop.example.com"


@pytest.fixture(autouse=True)
def apps_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Noust's apps directory, in the test directory: what is outside it was adopted."""
    apps = tmp_path / "apps"
    monkeypatch.setattr(Config, "apps_directory", property(lambda _self: apps))
    return apps


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    """A store in the test directory, where every module looks."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    monkeypatch.setattr(lifecycle, "get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


class FakeUnits:
    """Stands in for ServiceManager, recording what was removed."""

    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.refuse: set[str] = set()

    def delete_service(self, name: str) -> None:
        if name in self.refuse:
            raise ServiceError(f"Failed to stop {name}", details="Job canceled")
        self.deleted.append(name)


@pytest.fixture
def machine(monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> Any:
    """The units, the sites and docker, faked."""
    units = FakeUnits()
    sites: list[tuple[str, bool]] = []

    def delete_site(domain: str, **kwargs: Any) -> SiteDeletion:
        sites.append((domain, kwargs["delete_certificate"]))
        return SiteDeletion(domain=domain, nginx_removed=True, certificate_removed=True)

    monkeypatch.setattr(lifecycle, "ServiceManager", lambda **kwargs: units)
    monkeypatch.setattr(lifecycle, "delete_site_completely", delete_site)
    return type("Machine", (), {"units": units, "sites": sites, "runner": runner})


def compose_app(store: NoustStore, root: Path) -> App:
    """A Docker Compose application with a bind-mounted data directory."""
    root.mkdir(parents=True)
    (root / "docker-compose.prod.yml").write_text("services:\n  db:\n    image: postgres\n")
    (root / "data").mkdir()
    (root / "data" / "PG_VERSION").write_text("16")
    app = store.create_app(
        App(domain=DOMAIN, app_type="docker-compose", port=8080, app_path=str(root))
    )
    store.create_service(
        Service(app_id=app.id, name=root.name, command="docker compose up", working_directory="")
    )
    store.create_site(Site(app_id=app.id, domain=DOMAIN, proxy_port=8080))
    return app


def downs(runner: FakeRunner) -> list[tuple[str, ...]]:
    """Every ``docker compose ... down`` that ran."""
    return [call for call in runner.calls if call[:2] == ("docker", "compose") and "down" in call]


def job_context() -> JobContext:
    """A context for running a job function outside the job manager."""
    return JobContext(Job(id="job-1", type=JobType.DELETE, name="delete", description=""), print)
