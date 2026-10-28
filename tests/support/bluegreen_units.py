# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_bluegreen_units.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore, Service, get_store
from noust.core.utils import domain_to_app_name
from noust.managers.nginx_manager import NginxManager
from noust.managers.service_manager import ResourceLimits, ServiceManager
from noust.managers.webserver import NGINX_BACKEND

DOMAIN = "bg.example.com"
BASE = domain_to_app_name(DOMAIN)
PORT = 3100


@pytest.fixture
def store() -> Iterator[NoustStore]:
    """The process-wide store, at the location conftest redirects it to."""
    NoustStore.reset_instance()
    instance = get_store()
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def unit_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private managed unit directory, and nothing in the system ones."""
    managed = tmp_path / "etc/systemd/system"
    managed.mkdir(parents=True)
    monkeypatch.setattr(ServiceManager, "SYSTEMD_DIR", managed)
    monkeypatch.setattr(ServiceManager, "UNIT_SEARCH_DIRS", (managed,))
    return managed


@pytest.fixture
def nginx(tmp_path: Path, runner: FakeRunner) -> NginxManager:
    """An nginx manager writing into a temporary configuration tree."""
    return NginxManager(
        backend=replace(
            NGINX_BACKEND,
            sites_available=tmp_path / "nginx/sites-available",
            sites_enabled=tmp_path / "nginx/sites-enabled",
            upstreams_dir=tmp_path / "nginx/noust-upstreams",
        )
    )


def bg_app(store: NoustStore, root: Path, *, color: str | None = "green", on: bool = True) -> App:
    """Record an application on releases, in zero-downtime mode unless asked."""
    row = store.create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            port=PORT,
            app_path=str(root),
            layout="releases",
            webserver="nginx",
        )
    )
    store.create_service(
        Service(
            app_id=row.id,
            name=BASE,
            unit_file=f"/etc/systemd/system/{BASE}.service",
            working_directory=str(root / "current"),
            command="/usr/bin/npm run start",
            environment={"PORT": str(PORT), "NODE_ENV": "production"},
        )
    )
    if on:
        store.set_zero_downtime(DOMAIN, True)
        store.set_active_color(DOMAIN, color)
    found = store.get_app(DOMAIN)
    assert found is not None
    return found


def install_template(root: Path) -> ServiceManager:
    """Write the template the way enabling the mode does."""
    manager = ServiceManager()
    manager.install_instance_template(
        BASE,
        command=f"{root}/colors/%i/venv/bin/python -m gunicorn app:app -b 0.0.0.0:${{PORT}}",
        colors_directory=str(root / "colors"),
        environment={"NODE_ENV": "production"},
        environment_file=str(root / "shared/.env"),
        description=f"Noust: {DOMAIN} (python)",
        limits=ResourceLimits(memory_max_mb=512),
    )
    return manager


def roomy_disk(path: str) -> SimpleNamespace:
    """A disk with room to spare."""
    return SimpleNamespace(total=100, used=1, free=99)
