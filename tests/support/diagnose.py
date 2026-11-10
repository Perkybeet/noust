# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_diagnose.py`` that other tests build on."""

from __future__ import annotations

from collections import namedtuple
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import FakeRunner
from noust.core.store import App, DeploymentRecord, Service
from noust.managers import diagnose as diagnose_module
from noust.managers.diagnose import Check, Diagnosis, diagnose
from tests.support.empty_store_tables import empty_transaction

DOMAIN = "example.com"
APP_NAME = "example-com"
UNIT_FILE = "example-com.service"
FRAGMENT_PATH = f"/etc/systemd/system/{UNIT_FILE}"

#: A path nothing ever writes to, so the nginx log check deterministically skips
#: unless a test points NGINX_ERROR_LOG somewhere else.
NONEXISTENT_LOG = Path("/nonexistent/wasm-test-nginx-error.log")

_DiskUsage = namedtuple("_DiskUsage", "total used free")


class FakeStore:
    """
    Stands in for both the store ``diagnose`` reads directly and the one
    ServiceManager reads internally, so a test can hand it a service and an
    app record in one place. The tables other modules keep in the store (the
    extra copies of an application, say) are empty.
    """

    def __init__(
        self,
        app: App | None = None,
        deployments: list[DeploymentRecord] | None = None,
        services: list[Service] | None = None,
    ) -> None:
        self._app = app
        self._deployments = deployments or []
        self._services = services or []

    def get_app(self, _domain: str) -> App | None:
        return self._app

    def list_deployments(self, _domain: str, limit: int = 50) -> list[DeploymentRecord]:
        return self._deployments[:limit]

    def list_services(self, **_kwargs: Any) -> list[Service]:
        return self._services

    def list_apps(self, **_kwargs: Any) -> list[App]:
        return []

    _transaction = staticmethod(empty_transaction)


def _app(**overrides: Any) -> App:
    fields: dict[str, Any] = {
        "domain": DOMAIN,
        "port": 3000,
        "is_static": False,
        "ssl_enabled": True,
    }
    fields.update(overrides)
    return App(**fields)


def _http(
    direct: tuple[int | None, str | None] = (200, None),
    nginx: tuple[int | None, str | None] = (200, None),
) -> Callable[[str, dict], tuple[int | None, str | None]]:
    """Build a fake http_get answering differently for the Host-header probe."""

    def _get(_url: str, headers: dict) -> tuple[int | None, str | None]:
        return nginx if headers.get("Host") else direct

    return _get


def _disk(percent_used: float, total: int = 1_000_000_000) -> Callable[[str], _DiskUsage]:
    free = int(total * (1 - percent_used / 100))
    return lambda _path: _DiskUsage(total=total, used=total - free, free=free)


def _fragment(runner: FakeRunner) -> None:
    runner.script(
        ["systemctl", "show", "-p", "FragmentPath", UNIT_FILE],
        stdout=f"FragmentPath={FRAGMENT_PATH}\n",
    )


def _unit_state(
    runner: FakeRunner,
    *,
    active: str = "active",
    sub: str = "running",
    result: str = "success",
    exec_status: str = "0",
    restarts: str = "0",
) -> None:
    runner.script(
        [
            "systemctl",
            "show",
            "-p",
            "ActiveState,SubState,Result,ExecMainStatus,NRestarts",
            UNIT_FILE,
        ],
        stdout=(
            f"ActiveState={active}\n"
            f"SubState={sub}\n"
            f"Result={result}\n"
            f"ExecMainStatus={exec_status}\n"
            f"NRestarts={restarts}\n"
        ),
    )


def _listening(runner: FakeRunner, port: int, pid: str = "100", process: str = "node") -> None:
    runner.script(
        ["ss", "-ltnpH"],
        stdout=(
            f"LISTEN 0      511          127.0.0.1:{port}       0.0.0.0:*    "
            f'users:(("{process}",pid={pid},fd=18))\n'
        ),
    )


def _no_findings(runner: FakeRunner) -> None:
    """Script every read-only probe to report nothing of interest."""
    runner.script(
        ["journalctl", "-u", UNIT_FILE, "-n", "50", "--no-pager", "-o", "short-iso"], stdout=""
    )
    runner.script(
        ["journalctl", "-k", "--since", "-7d", "--grep", "oom", "-o", "short-iso"], stdout=""
    )
    runner.script(["certbot", "certificates"], stdout="")


def _diagnose(
    monkeypatch: pytest.MonkeyPatch,
    runner: FakeRunner,
    *,
    app: App | None = None,
    deployments: list[DeploymentRecord] | None = None,
    services: list[Service] | None = None,
    now: datetime = datetime(2026, 1, 1, tzinfo=timezone.utc),
    http_get: Callable[[str, dict], tuple[int | None, str | None]] | None = None,
    disk_usage: Callable[[str], _DiskUsage] | None = None,
    nginx_log_path: Path | None = None,
    cgroup_mount: Path | None = None,
) -> Diagnosis:
    """Wire a diagnosis with every seam under the test's control."""
    if services is None:
        services = [Service(name=APP_NAME)]
    store = FakeStore(app=app, deployments=deployments, services=services)
    monkeypatch.setattr("noust.managers.service_manager.get_store", lambda: store)
    monkeypatch.setattr(diagnose_module, "NGINX_ERROR_LOG", nginx_log_path or NONEXISTENT_LOG)

    return diagnose(
        DOMAIN,
        runner=runner,
        store=store,
        now=lambda: now,
        http_get=http_get or _http(),
        disk_usage=disk_usage or _disk(10.0),
        cgroup_mount=cgroup_mount,
    )


def _check(diagnosis: Diagnosis, name: str) -> Check:
    return next(c for c in diagnosis.checks if c.name == name)
