# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_server_migration.py`` that other tests build on."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from noust.core.app_state import AppState
from noust.core.exceptions import BackupError
from noust.core.store import App, NoustStore
from noust.deployers.domains import DnsCheck
from noust.managers.server_migration import ServerMigration
from noust.monitor.reachability import AppProbe

HOST = "old-host"
NEW_ADDRESS = "203.0.113.10"


class Recorder:
    """Stands in for every manager that takes or sends a copy, and remembers the order."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail: dict[tuple[str, str], Exception] = {}
        self.targets: dict[str, list[str]] = {}
        self.destination_exists = True
        self.reachable = True
        self.passphrase: str | None = None
        self.backed_up: dict[str, str] = {}

    def failure(self, kind: str, name: str) -> None:
        error = self.fail.get((kind, name))
        if error is not None:
            raise error

    # --- BackupManager
    def create(self, domain: str, **kwargs: Any) -> SimpleNamespace:
        assert kwargs["include_databases"] and kwargs["include_docker_volumes"]
        assert kwargs["tags"] == ["migration"]
        self.failure("application", domain)
        self.calls.append(("backup", domain))
        return SimpleNamespace(id=f"{domain}-b1", domain=domain, created_at="2026-10-13T10:00:00")

    def get_latest_backup(self, domain: str) -> SimpleNamespace | None:
        found = self.backed_up.get(domain)
        return SimpleNamespace(created_at=found) if found else None

    # --- VolumeBackupManager
    def targets_for(self, domain: str) -> list[str]:
        return self.targets.get(domain, [])

    def push_volume(self, backup_id: str, destination: str) -> dict[str, Any]:
        self.failure("push", backup_id)
        self.calls.append(("push", backup_id))
        return {}

    # --- BackupDestinationManager
    def get(self, name: str) -> object | None:
        return object() if self.destination_exists else None

    def test(self, name: str) -> dict[str, Any]:
        if not self.reachable:
            raise BackupError("Could not reach backup destination 'nas'", details="timeout")
        return {"ok": True}

    def push(self, metadata: Any, destination: str, **kwargs: Any) -> dict[str, Any]:
        self.failure("push", metadata.id)
        self.calls.append(("push", metadata.id))
        return {}

    # --- SelfBackupManager
    def host_slug(self) -> str:
        return HOST

    def stored_passphrase(self) -> str | None:
        return self.passphrase

    def seal(self, passphrase: str, *, kind: str) -> SimpleNamespace:
        self.failure("noust", HOST)
        self.calls.append(("seal", passphrase))
        return SimpleNamespace(path=Path(f"noust-self-{HOST}-20261013.tar.gz.enc"))

    def push_bundle(self, name: str, destination: str) -> dict[str, Any]:
        self.failure("noust-push", name)
        self.calls.append(("push", name))
        return {}


class FakeVolumes:
    def __init__(self, recorder: Recorder) -> None:
        self.recorder = recorder

    def targets(self, domain: str) -> list[str]:
        return self.recorder.targets_for(domain)

    def create(self, domain: str, **kwargs: Any) -> list[SimpleNamespace]:
        self.recorder.failure("data", domain)
        made = [
            SimpleNamespace(id=f"{domain}.{target}-v1", target=target)
            for target in self.recorder.targets_for(domain)
        ]
        self.recorder.calls.append(("data", domain))
        return made

    def push(self, backup_id: str, destination: str) -> dict[str, Any]:
        return self.recorder.push_volume(backup_id, destination)


class FakeSelf:
    def __init__(self, recorder: Recorder) -> None:
        self.recorder = recorder

    def host_slug(self) -> str:
        return self.recorder.host_slug()

    def stored_passphrase(self) -> str | None:
        return self.recorder.stored_passphrase()

    def create(self, passphrase: str, *, kind: str = "manual") -> SimpleNamespace:
        return self.recorder.seal(passphrase, kind=kind)

    def push(self, name: str, destination: str) -> dict[str, Any]:
        return self.recorder.push_bundle(name, destination)


def add_app(store: NoustStore, domain: str, **fields: Any) -> App:
    return store.create_app(App(domain=domain, app_path=f"/var/www/apps/{domain}", **fields))


def dns_for(addresses: dict[str, list[str]]):  # type: ignore[no-untyped-def]
    def check(domain: str) -> DnsCheck:
        resolved = tuple(sorted(addresses.get(domain, [])))
        return DnsCheck(
            domain=domain,
            expected_addresses=(NEW_ADDRESS,),
            resolved_addresses=resolved,
            points_here=bool(resolved) and set(resolved) <= {NEW_ADDRESS},
        )

    return check


def checker(
    store: NoustStore,
    recorder: Recorder,
    *,
    states: dict[str, AppState] | None = None,
    probes: dict[str, AppProbe | None] | None = None,
    certificates: list[dict[str, Any]] | None = None,
    dns: dict[str, list[str]] | None = None,
    engines: dict[str, tuple[bool, str]] | None = None,
) -> ServerMigration:
    return ServerMigration(
        store=store,
        backups=recorder,  # type: ignore[arg-type]
        volumes=FakeVolumes(recorder),  # type: ignore[arg-type]
        destinations=recorder,  # type: ignore[arg-type]
        self_backups=FakeSelf(recorder),  # type: ignore[arg-type]
        dns=dns_for(dns or {}),
        probe=lambda app: (probes or {}).get(app.domain, AppProbe(ok=True, detail="")),
        states=lambda apps: {
            app.domain: (states or {}).get(app.domain, AppState("Running", True)) for app in apps
        },
        certificates=lambda: certificates or [],
        engine_up=lambda engine, database: (engines or {}).get(f"{engine}/{database}", (True, "")),
    )
