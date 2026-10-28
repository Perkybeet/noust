# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_cli_webapp.py`` that other tests build on."""

from __future__ import annotations

import functools
import io
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli import app as cli_app
from noust.cli.commands import webapp
from noust.core.logger import Logger
from noust.core.utils import domain_to_app_name
from noust.deployers import lifecycle
from tests.support.empty_store_tables import empty_transaction


class ServiceSpy:
    """
    A stand-in for :class:`~noust.managers.service_manager.ServiceManager`.

    Records what the command asked for instead of talking to systemd.

    Attributes:
        calls: Method name and service name, in call order.
        exists: What :meth:`unit_exists` reports.
        status: What :meth:`get_status` reports.
        journal: What :meth:`logs` returns.
        verbose: Verbosity the command constructed it with.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.exists: bool = True
        self.status: dict[str, Any] = {"exists": True, "active": True, "enabled": True}
        #: Per-unit overrides, for the commands that ask about several at once.
        self.statuses: dict[str, dict[str, Any]] = {}
        self.journal: str = "a log line"
        self.verbose: bool = False

    def unit_exists(self, name: str) -> bool:
        """
        Args:
            name: Service name.

        Returns:
            The scripted answer.
        """
        self.calls.append(("unit_exists", name))
        return self.exists

    def start(self, name: str) -> None:
        """
        Args:
            name: Service name.
        """
        self.calls.append(("start", name))

    def stop(self, name: str) -> None:
        """
        Args:
            name: Service name.
        """
        self.calls.append(("stop", name))

    def restart(self, name: str) -> None:
        """
        Args:
            name: Service name.
        """
        self.calls.append(("restart", name))

    def delete_service(self, name: str) -> None:
        """
        Args:
            name: Service name.
        """
        self.calls.append(("delete_service", name))

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
        Args:
            name: Service name.

        Returns:
            The scripted status.
        """
        self.calls.append(("get_status", name))
        return self.statuses.get(name, self.status)

    def logs(self, name: str, lines: int = 50) -> str:
        """
        Args:
            name: Service name.
            lines: How many lines were asked for.

        Returns:
            The scripted journal.
        """
        self.calls.append(("logs", f"{name}:{lines}"))
        return self.journal

    def _resolve_service_name(self, name: str) -> str:
        """
        Args:
            name: Base service name.

        Returns:
            The name unchanged; no legacy unit exists in a test.
        """
        return name


class OneTeam:
    """
    The teams of a server nobody organised in them: only the default one.

    ``noust list`` asks whether to say where each application lives, which it
    does once there is more than one team; the spy has no tables to ask.
    """

    def __init__(self, store: Any = None) -> None:
        """
        Args:
            store: The store spy, unused.
        """

    def list_teams(self) -> list[Any]:
        """
        Returns:
            Nothing beyond the default: one team, which is not "more than one".
        """
        return [object()]


class StoreSpy:
    """
    A stand-in for the SQLite store.

    Attributes:
        apps: Applications keyed by domain.
        relations: What :meth:`get_app_with_relations` returns per domain.
        services: Every service row.
        deleted: Names passed to the delete methods, in call order.
        pins: Pinned branches keyed by domain.
    """

    def __init__(self) -> None:
        self.apps: dict[str, Any] = {}
        self.relations: dict[str, Any] = {}
        self.services: list[Any] = []
        self.deleted: list[tuple[str, str]] = []
        self.pins: dict[str, str] = {}

    def list_apps(self) -> list[Any]:
        """
        Returns:
            Every registered application.
        """
        return list(self.apps.values())

    def list_services(self) -> list[Any]:
        """
        Returns:
            Every registered service.
        """
        return list(self.services)

    def list_deployments(self, domain: str | None = None, limit: int = 20) -> list[Any]:
        """
        Returns:
            No history: the nothing-new check falls back to the checkout.
        """
        return []

    def get_app(self, domain: str) -> Any:
        """
        Args:
            domain: Application domain.

        Returns:
            The application row, or None.
        """
        return self.apps.get(domain)

    def update_app(self, app: Any) -> Any:
        """
        Args:
            app: The row to store.

        Returns:
            The row.
        """
        self.apps[app.domain] = app
        return app

    # Tables other modules keep (extra copies, processes) hold nothing here.
    _transaction = staticmethod(empty_transaction)

    def list_databases(self, engine: str | None = None, app_id: int | None = None) -> list[Any]:
        """
        Returns:
            No databases: the application owns none.
        """
        return []

    def get_branch_pin(self, domain: str) -> str | None:
        """
        Args:
            domain: Application domain.

        Returns:
            The pinned branch.
        """
        return self.pins.get(domain)

    def set_branch_pin(self, domain: str, branch: str | None, *, by: str | None = None) -> None:
        """
        Args:
            domain: Application domain.
            branch: The branch, or None to unpin.
            by: Who chose it.
        """
        if branch is None:
            self.pins.pop(domain, None)
        else:
            self.pins[domain] = branch

    def get_app_with_relations(self, domain: str) -> Any:
        """
        Args:
            domain: Application domain.

        Returns:
            The application and its site, service and databases, or None.
        """
        return self.relations.get(domain)

    def delete_app(self, domain: str) -> None:
        """
        Args:
            domain: Application domain.
        """
        self.deleted.append(("app", domain))

    def delete_site(self, domain: str) -> None:
        """
        Args:
            domain: Site domain.
        """
        self.deleted.append(("site", domain))

    def delete_service(self, name: str) -> None:
        """
        Args:
            name: Service name.
        """
        self.deleted.append(("service", name))

    def get_preview_settings(self, domain: str) -> None:
        """No application here previews anything."""
        return None

    def list_previews(self, parent_domain: str | None = None) -> list[Any]:
        """No previews."""
        return []

    def delete_preview(self, domain: str) -> bool:
        """
        Args:
            domain: The preview's domain.

        Returns:
            False: there are none.
        """
        return False


class DeployerSpy:
    """
    A stand-in for a deployer.

    Attributes:
        configured: The keyword arguments ``configure`` received.
        deployed: Whether ``deploy`` was called.
        updated: Whether ``update`` was called.
        steps: Step descriptions reported during ``update``.
    """

    def __init__(self, verbose: bool = False) -> None:
        """
        Args:
            verbose: Verbosity the command constructed it with.
        """
        self.verbose = verbose
        self.configured: dict[str, Any] = {}
        self.deployed = False
        self.updated = False
        self.steps: list[str] = []

    def configure(self, **kwargs: Any) -> None:
        """
        Args:
            **kwargs: Whatever the command passed.
        """
        self.configured = kwargs

    def deploy(self) -> bool:
        """
        Returns:
            Always True.
        """
        self.deployed = True
        return True

    def update(self, on_step: Any = None) -> Any:
        """
        Args:
            on_step: Progress reporter.

        Returns:
            A result describing a plain, non-static build.
        """
        self.updated = True
        if on_step:
            for message in ("Installing dependencies", "Building"):
                self.steps.append(message)
                on_step(message)
        return SimpleNamespace(
            package_manager="pnpm",
            prisma_updated=False,
            is_static=False,
            start_command="/usr/bin/node server.js",
            restarted=None,
        )


def make_app(
    domain: str = "example.com",
    *,
    app_type: str = "nextjs",
    app_path: str = "/var/www/apps/example-com",
    is_static: bool = False,
) -> SimpleNamespace:
    """
    Build a store row for an application.

    Args:
        domain: Application domain.
        app_type: Application type.
        app_path: Directory the application lives in.
        is_static: Whether the application has no service.

    Returns:
        Something shaped like a store App row.
    """
    return SimpleNamespace(
        id=1,
        domain=domain,
        app_type=app_type,
        app_path=app_path,
        layout="inplace",
        is_static=is_static,
        status="running",
        port=3000,
        ssl_enabled=True,
        source="https://github.com/user/repo",
        branch="main",
        deployed_at="2026-01-01T00:00:00",
        # Store v13: an App row always carries these.
        compose_project=None,
        site_name=None,
        follow_tags=None,
        backup_before_update=True,
        identity=None,
        preview_parent=None,
    )


@pytest.fixture
def cli_runner() -> CliRunner:
    """
    Provide a Click test runner.

    Returns:
        A runner that keeps stderr in the captured output.
    """
    return CliRunner()


@pytest.fixture
def console(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """
    Collect what the commands print.

    ``Logger`` binds ``sys.stdout`` as a default argument value at import time,
    so neither capsys nor the CliRunner ever sees its output; giving it an
    explicit stream is the only reliable way to read it back. Both the shared
    context logger and the one the argparse entry point builds are redirected.

    Args:
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The buffer the commands write into.
    """
    buffer = io.StringIO()
    patched = functools.partial(Logger, stream=buffer)
    monkeypatch.setattr(cli_app, "Logger", patched)
    monkeypatch.setattr(webapp, "Logger", patched)
    return buffer


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> StoreSpy:
    """
    Replace the SQLite store with a spy for the duration of a test.

    Args:
        monkeypatch: Patching helper.

    Returns:
        The spy the commands will use.
    """
    spy = StoreSpy()
    monkeypatch.setattr(webapp, "get_store", lambda: spy)
    monkeypatch.setattr(lifecycle, "get_store", lambda: spy)
    # The spy is not a database: no update here is held to a backup it cannot take.
    monkeypatch.setattr(lifecycle, "get_update_policy", lambda domain, store=None: None)
    monkeypatch.setattr(webapp, "TeamStore", OneTeam)
    return spy


@pytest.fixture
def services(monkeypatch: pytest.MonkeyPatch) -> ServiceSpy:
    """
    Replace the service manager with a spy for the duration of a test.

    Args:
        monkeypatch: Patching helper.

    Returns:
        The spy the commands will use.
    """
    spy = ServiceSpy()

    def _build(verbose: bool = False) -> ServiceSpy:
        spy.verbose = verbose
        return spy

    monkeypatch.setattr(webapp, "ServiceManager", _build)
    monkeypatch.setattr(lifecycle, "ServiceManager", _build)
    return spy


@pytest.fixture
def deployer(monkeypatch: pytest.MonkeyPatch) -> DeployerSpy:
    """
    Replace the deployer registry with a spy, and clear the readiness gate.

    Args:
        monkeypatch: Patching helper.

    Returns:
        The spy the commands will use.
    """
    spy = DeployerSpy()
    monkeypatch.setattr(webapp, "get_deployer", lambda app_type, verbose=False: spy)
    monkeypatch.setattr(lifecycle, "get_deployer", lambda app_type, verbose=False: spy)
    monkeypatch.setattr(
        webapp,
        "check_deployment_ready",
        lambda app_type, package_manager, verbose: (True, [], []),
    )
    return spy


class UpdateCalls:
    """What ``wasm update`` asked of the lifecycle."""

    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.checks: list[dict[str, Any]] = []


@pytest.fixture
def update_calls(monkeypatch: pytest.MonkeyPatch) -> UpdateCalls:
    """
    Replace the update and the upstream check the command calls.

    The upstream check answers "nothing new since abc1234" unless a test
    changes ``answer``.

    Args:
        monkeypatch: Patching helper.

    Returns:
        The recorded calls.
    """
    calls = UpdateCalls()
    calls.answer = lifecycle.UpstreamState(  # type: ignore[attr-defined]
        domain="example.com", branch="main", live_commit="abc1234", remote_commit="abc1234" * 5
    )

    def check_upstream(domain: str, **kwargs: Any) -> Any:
        calls.checks.append({"domain": domain, **kwargs})
        return calls.answer  # type: ignore[attr-defined]

    def update_app(domain: str, **kwargs: Any) -> Any:
        calls.updates.append({"domain": domain, **kwargs})
        return lifecycle.AppUpdate(
            domain=domain,
            app_type="nextjs",
            package_manager="npm",
            prisma_updated=False,
            is_static=False,
            restarted=("example-com",),
            active=True,
        )

    monkeypatch.setattr(webapp, "check_upstream", check_upstream)
    monkeypatch.setattr(webapp, "update_app", update_app)
    return calls
