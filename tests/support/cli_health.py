# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_cli_health.py`` that other tests build on."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.exceptions import ServiceError
from noust.core.store import App
from noust.core.utils import domain_to_app_name
from noust.managers import health as health_module
from noust.managers.cert_renewal import RenewalStatus

#: One gigabyte, for readable fake disk figures.
_GB = 1024**3


class _FakeWebServer:
    """A web server that is whatever the test says it is."""

    def __init__(
        self,
        installed: bool = True,
        active: bool = True,
        sites: list[Any] = (),  # type: ignore[assignment]
    ) -> None:
        """
        Args:
            installed: Whether the unit or package is installed.
            active: Whether the unit is running.
            sites: Sites this backend serves, as ``list_sites`` would report
                them; only ``enabled`` is read.
        """
        self._installed = installed
        self._active = active
        self._sites = list(sites)

    def is_installed(self) -> bool:
        """Whether the server is installed."""
        return self._installed

    def get_status(self) -> dict[str, Any]:
        """What the server is doing right now."""
        return {"active": self._active}

    def list_sites(self) -> list[Any]:
        """Sites this backend serves."""
        return self._sites


class _FakeServices:
    """A systemd that answers for the units the test set up."""

    def __init__(self, active: dict[str, bool], failing: tuple[str, ...] = ()) -> None:
        """
        Args:
            active: Unit name to whether it is running.
            failing: Units whose query raises, as an unmanaged unit would.
        """
        self.active = active
        self.failing = failing
        self.asked: list[str] = []

    def app_units(self, app: Any) -> list[str]:
        """
        Args:
            app: The application.

        Returns:
            The unit it is named after, as ServiceManager.app_units names it.
        """
        return [] if app.is_static else [domain_to_app_name(app.domain)]

    def serving_units(self, app: Any) -> list[str]:
        """The units serving now: every unit, outside zero-downtime mode."""
        return self.app_units(app)

    def get_status(self, name: str) -> dict[str, Any]:
        """
        Report a unit's state.

        Args:
            name: Service name.

        Returns:
            The status mapping the real manager returns.

        Raises:
            ServiceError: When the test declared this unit unqueryable.
        """
        self.asked.append(name)
        if name in self.failing:
            raise ServiceError(f"{name} is not managed by WASM")
        return {"name": name, "exists": True, "active": self.active.get(name, False)}


class _FakeStore:
    """A store holding exactly the applications a test cares about."""

    def __init__(self, apps: list[App]) -> None:
        """
        Args:
            apps: Applications to report.
        """
        self._apps = apps

    def list_apps(self) -> list[App]:
        """Every deployed application."""
        return self._apps


class _FakeCerts:
    """A certificate manager with a fixed inventory."""

    def __init__(self, certs: list[dict[str, Any]]) -> None:
        """
        Args:
            certs: Certificate entries as certbot reports them.
        """
        self._certs = certs

    def list_certificates(self) -> list[dict[str, Any]]:
        """Every certificate on this machine."""
        return self._certs


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """
    Replace every subsystem the health check reads.

    The default is a healthy server: nginx running, no applications, no
    certificates. A test changes only the part it is about.

    Args:
        monkeypatch: Patching helper, scoped to the test.
        tmp_path: Per-test temporary directory, used as the apps directory.

    Returns:
        A namespace whose attributes each test can rebind before invoking.
    """
    state = type(
        "ServerState",
        (),
        {
            "nginx": _FakeWebServer(installed=True, active=True),
            "apache": _FakeWebServer(installed=False, active=False),
            "services": _FakeServices({}),
            "store": _FakeStore([]),
            "certs": _FakeCerts([]),
        },
    )()

    # The check itself lives in noust.managers.health, shared with the panel's
    # GET /api/system/health; that is the module whose names this patches.
    monkeypatch.setattr(
        health_module,
        "Config",
        lambda *a, **kw: type("FakeConfig", (), {"apps_directory": tmp_path})(),
    )
    # Disk and memory come from the machine running the suite, which would make
    # every assertion below depend on how full the developer's laptop is.
    monkeypatch.setattr(
        health_module.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(total=200 * _GB, used=50 * _GB, free=150 * _GB),
    )
    monkeypatch.setattr(
        health_module,
        "_read_meminfo",
        lambda: {"MemTotal": 16 * 1024 * 1024, "MemAvailable": 12 * 1024 * 1024},
    )
    monkeypatch.setattr(health_module, "NginxManager", lambda *a, **kw: state.nginx)
    monkeypatch.setattr(health_module, "ApacheManager", lambda *a, **kw: state.apache)
    monkeypatch.setattr(health_module, "ServiceManager", lambda *a, **kw: state.services)
    monkeypatch.setattr(health_module, "CertManager", lambda *a, **kw: state.certs)
    monkeypatch.setattr(health_module, "get_store", lambda: state.store)
    # The hardening checks have their own tests (test_server_security_checks.py);
    # here they must not probe the machine running the suite.
    monkeypatch.setattr(health_module, "run_checks", lambda **_kw: None)
    # How certbot renews has its own tests (test_cert_renewal.py); the machine running the suite
    # must not be asked about its timer.
    monkeypatch.setattr(health_module, "read_renewal", lambda runner: RenewalStatus())
    # What registries said of the domains has its own tests (test_health_domain_registrations.py).
    monkeypatch.setattr(health_module, "RegistrationStore", lambda: SimpleNamespace(all=lambda: []))
    # No Noust unit runs on the machine described: its descriptors have their own tests
    # (test_health_descriptors.py), and the suite's own processes must not be asked about.
    monkeypatch.setattr(
        health_module,
        "_check_descriptors",
        lambda issues, warnings: health_module.HealthCheck(
            "File descriptors", "No Noust service is running as a unit", "info"
        ),
    )
    # The audit trail's directory exists, as it does on an installed server;
    # the trail's own states are tested in test_health_builds_and_audit.py.
    (tmp_path / "audit-state").mkdir(exist_ok=True)
    return state
