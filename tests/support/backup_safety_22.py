# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_backup_safety_22.py`` that other tests build on."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.managers import backup_manager as backup_manager_module
from noust.managers.backup_destinations import BackupDestinationManager
from noust.managers.backup_manager import SCHEDULED_TAG, BackupManager, BackupMetadata, server_id
from noust.managers.backup_scheduler import BackupScheduler

APP = "shop-example-com"
DOMAIN = "shop.example.com"


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def manager(runner: FakeRunner, tmp_path: Path) -> BackupManager:
    backup_manager = BackupManager(verbose=False, runner=runner)
    backup_manager.backup_dir = tmp_path / "backups"
    backup_manager.max_backups = 2
    return backup_manager


@pytest.fixture
def destinations(runner: FakeRunner) -> BackupDestinationManager:
    runner.only_knows("rclone")
    runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")
    return BackupDestinationManager(runner=runner)


def _sftp_fields() -> dict[str, str]:
    return {"host": "nas.example.com", "user": "wasm", "pass": "s3cr3t-password-value"}


def _write_backup(
    manager: BackupManager, day: int, tags: list[str], *, origin: str | None = None
) -> BackupMetadata:
    backup_id = f"{APP}_202601{day:02d}_000000"
    metadata = BackupMetadata(
        id=backup_id,
        domain=DOMAIN,
        app_name=APP,
        created_at=f"2026-01-{day:02d}T00:00:00",
        size_bytes=7,
        app_type="static",
        version="2.0.0",
        description="",
        includes_env=True,
        includes_node_modules=False,
        tags=tags,
        origin=origin,
    )
    app_dir = manager.backup_dir / APP
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / f"{backup_id}.tar.gz").write_bytes(b"content")
    (app_dir / f"{backup_id}.json").write_text(json.dumps(metadata.to_dict()))
    return metadata


def _remaining(manager: BackupManager) -> set[str]:
    return {backup.id for backup in manager.list_backups(app_name=APP)}


@pytest.fixture
def systemd_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "systemd"
    path.mkdir()
    monkeypatch.setattr(BackupScheduler, "SYSTEMD_DIR", path)
    return path


TARGET = f"nas:noust-backups/{APP}"


def _push_setup(
    destinations: BackupDestinationManager, manager: BackupManager, day: int = 2
) -> BackupMetadata:
    destinations.add("nas", "sftp", _sftp_fields())
    return _write_backup(manager, day, [SCHEDULED_TAG], origin=server_id())


def _listing(manager: BackupManager, metadata: BackupMetadata, **archive: Any) -> str:
    sidecar = manager.backup_dir / APP / f"{metadata.id}.json"
    entries = [
        {"Name": f"{metadata.id}.tar.gz", "Size": 7, "ModTime": "2026-01-02T00:00:00Z", **archive},
        {"Name": f"{metadata.id}.json", "Size": sidecar.stat().st_size},
    ]
    return json.dumps(entries)


class RemoteRetentionOnASharedFolderTests:
    def _entries(self, ids: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "Name": f"{backup_id}.tar.gz",
                "Size": 7,
                "ModTime": f"2026-01-{index + 1:02d}T00:00:00Z",
            }
            for index, backup_id in enumerate(ids)
        ]

    def test_only_this_servers_backups_are_counted_and_deleted(
        self, destinations: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        destinations.add("nas", "sftp", _sftp_fields())
        own = server_id()
        mine_old, foreign, legacy, mine_new = (
            f"{APP}_20260101_000000",
            f"{APP}_20260102_000000",
            f"{APP}_20260103_000000",
            f"{APP}_20260104_000000",
        )
        sidecars = (
            json.dumps({"id": mine_old, "origin": own})
            + "\n"
            + json.dumps({"id": foreign, "origin": "0123456789abcdef"})
            + json.dumps({"id": legacy})
            + json.dumps({"id": mine_new, "origin": own})
        )
        runner.script(("rclone", "cat", TARGET), stdout=sidecars)

        deleted = destinations._apply_remote_retention(
            destinations.remote_env("nas"),
            TARGET,
            self._entries([mine_old, foreign, legacy, mine_new]),
            1,
            None,
            [],
        )

        assert deleted == [mine_old]

    def test_an_unreadable_sidecar_is_kept(
        self, destinations: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        destinations.add("nas", "sftp", _sftp_fields())
        old, new = f"{APP}_20260101_000000", f"{APP}_20260102_000000"
        runner.script(
            ("rclone", "cat", TARGET),
            stdout=json.dumps({"id": new, "origin": server_id()})
            + "{not json"
            + json.dumps({"id": old, "origin": server_id()}),
        )

        deleted = destinations._apply_remote_retention(
            destinations.remote_env("nas"), TARGET, self._entries([old, new]), 1, None, []
        )

        assert deleted == []

    def test_a_new_backup_records_this_server(self) -> None:
        metadata = BackupMetadata.from_dict(
            {
                "id": "x_20260101_000000",
                "domain": DOMAIN,
                "app_name": APP,
                "created_at": "2026-01-01T00:00:00",
                "origin": server_id(),
            }
        )
        assert metadata.to_dict()["origin"] == server_id()

    def test_server_id_without_a_machine_id(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(backup_manager_module, "MACHINE_ID_PATH", tmp_path / "missing")
        first = server_id()
        assert len(first) == 16
        assert first == server_id()
