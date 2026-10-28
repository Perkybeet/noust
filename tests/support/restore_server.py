# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_restore_server.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from noust.core.ens import envelope
from noust.core.exceptions import BackupError
from noust.core.runner import set_runner
from noust.core.store import App, BackupScheduleRecord, Database, NoustStore
from noust.managers import restore_server
from noust.managers.backup_manager import BackupMetadata
from noust.managers.restore_server import RestoreServer
from tests.support.self_backup import PASSPHRASE, Runner, Server, origin, server


@pytest.fixture(autouse=True)
def cheap_scrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(envelope, "SCRYPT_N", 2**10)


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def runner(tmp_path: Path) -> Iterator[Runner]:
    fake = Runner(tmp_path / "remote")
    fake.script(("systemctl", "is-active", "--quiet", "noust-web.service"), exit_code=3)
    fake.script(("systemctl", "is-active", "--quiet", "noust-monitor.service"), exit_code=3)
    fake.script(("systemctl", "is-active", "--quiet", "noust-previews.service"), exit_code=3)
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


def meta(backup_id: str, domain: str, created: str) -> BackupMetadata:
    return BackupMetadata(
        id=backup_id,
        domain=domain,
        app_name=domain.replace(".", "-"),
        created_at=created,
        size_bytes=10,
        app_type="nodejs",
        version="2.0.0",
        description="",
        includes_env=True,
        includes_node_modules=False,
    )


@dataclass
class FakeBackups:
    """The application backups on this server, and what was restored from them."""

    local: dict[str, list[BackupMetadata]] = field(default_factory=dict)
    restored: list[tuple[str, str]] = field(default_factory=list)
    fail: dict[str, str] = field(default_factory=dict)

    def list_backups(self, domain: str | None = None, **_: Any) -> list[BackupMetadata]:
        return list(self.local.get(domain or "", []))

    def restore(self, backup_id: str, target_domain: str | None = None, **kwargs: Any) -> bool:
        if target_domain in self.fail:
            raise BackupError(f"Cannot restore {backup_id}", details=self.fail[target_domain])
        self.restored.append(("local", backup_id))
        self.options = kwargs
        return True


@dataclass
class FakeDestinations:
    """What destinations hold of each application, and what was restored from them."""

    held: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)
    unreachable: set[str] = field(default_factory=set)
    restored: list[tuple[str, str]] = field(default_factory=list)

    def remote_list(self, destination: str, app_name: str | None = None) -> dict[str, Any]:
        if destination in self.unreachable:
            raise BackupError(f"{destination} did not answer", details="connection refused")
        return {"apps": [], "backups": list(self.held.get((destination, app_name or ""), []))}

    def restore_remote(self, destination: str, backup_id: str, app_name: str, **kwargs: Any) -> str:
        self.restored.append((destination, backup_id))
        return str(kwargs.get("target_domain"))


@dataclass
class FakeVolumes:
    """The data policy and the data backups of each application."""

    policies: dict[str, Any] = field(default_factory=dict)
    local: dict[tuple[str, str], list[Any]] = field(default_factory=dict)
    remote: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = field(default_factory=dict)
    restored: list[str] = field(default_factory=list)
    fail: set[str] = field(default_factory=set)

    def get_policy(self, domain: str) -> Any:
        class View:
            policy = self.policies.get(domain)

        return View()

    def targets(self, domain: str) -> list[Any]:
        class Target:
            key = "path:uploads"

        return [Target()]

    def list_backups(self, domain: str, target: str | None = None) -> list[Any]:
        return list(self.local.get((domain, target or ""), []))

    def remote_backups(self, destination: str, domain: str) -> dict[str, list[dict[str, Any]]]:
        return self.remote.get((destination, domain), {})

    def restore(self, backup_id: str, **_: Any) -> dict[str, Any]:
        if backup_id in self.fail:
            raise BackupError("The data could not be put back", details="no space")
        self.restored.append(backup_id)
        return {"id": backup_id}

    def restore_remote(
        self, destination: str, domain: str, slug: str, file_name: str, **_: Any
    ) -> dict[str, Any]:
        if file_name in self.fail:
            raise BackupError("The data could not be put back", details="no space")
        self.restored.append(f"{destination}:{file_name}")
        return {"id": file_name}


def add_state(made: Server, *, apps: tuple[str, ...] = ()) -> None:
    """Give the origin server what a production server has: schedules and databases."""
    NoustStore.reset_instance()
    store = NoustStore(made.state / "noust.db")
    for domain in apps:
        store.create_app(App(domain=domain, app_type="nodejs", app_path=f"/srv/{domain}"))
    shop = store.get_app("shop.example.com")
    assert shop is not None
    store.save_backup_schedule(
        BackupScheduleRecord(
            app_domain="shop.example.com",
            schedule="daily",
            destinations=[{"name": "nas"}, {"name": "cloud"}],
        )
    )
    store.create_database(
        Database(name="shop", engine="postgresql", app_id=shop.id, username="shop")
    )
    store.close()
    NoustStore.reset_instance()


class Machine:
    """A server that has the web server and Docker, so a test about something else is not about them."""

    def _present(self, name: str) -> Any:
        from noust.managers.server_provision import ProvisionStep

        return ProvisionStep(name, "present", f"{name} is installed")

    def check_webserver(self, name: str) -> Any:
        return self._present(name)

    ensure_webserver = check_webserver

    def check_docker(self) -> Any:
        return self._present("docker")

    ensure_docker = check_docker


class NoOrphans:
    """A server whose store has no database that no application owns."""

    def restore(self, store: Any, *, from_destination: str | None = None) -> list[Any]:
        return []


class Flow:
    """A target server, the flow over it, and every fake it runs with."""

    def __init__(self, base: Path, runner: Runner, old: Server) -> None:
        self.new = server(base, "new")
        self.backups = FakeBackups()
        self.destinations = FakeDestinations()
        self.volumes = FakeVolumes()
        self.sites: list[str] = []
        self.gates: dict[str, tuple[bool, str]] = {}
        self.installed: set[str] = {"postgresql"}
        self.manager = self.new.manager(runner)
        self.bundle = old.manager(runner).create(PASSPHRASE).path
        self.flow = RestoreServer(
            manager=self.manager,
            backups=self.backups,  # type: ignore[arg-type]
            destinations=self.destinations,  # type: ignore[arg-type]
            volumes=self.volumes,  # type: ignore[arg-type]
            gate=lambda app: self.gates.get(app.domain, (True, "")),
            refresh_site=lambda app: self.sites.append(app.domain),
            engine_installed=lambda engine: engine in self.installed,
            provisioner=Machine(),  # type: ignore[arg-type]
            orphans=NoOrphans(),  # type: ignore[arg-type]
            apply_roles=lambda engine, path: None,
            webserver_name=lambda: "nginx",
        )

    def run(self, **kwargs: Any) -> restore_server.ServerRestoreReport:
        return self.flow.run(str(self.bundle), PASSPHRASE, **kwargs)


@pytest.fixture
def old(tmp_path: Path, runner: Runner) -> Iterator[Server]:
    made = origin(tmp_path)
    yield made
    for connection in made.held:
        connection.close()


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "noust.core.audit.record", lambda event, **kwargs: events.append({"event": event, **kwargs})
    )
    return events


def outcome(report: restore_server.ServerRestoreReport, domain: str) -> restore_server.AppOutcome:
    return next(o for o in report.apps if o.domain == domain)
