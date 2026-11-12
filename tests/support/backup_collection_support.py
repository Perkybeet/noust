# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A server's copies on a disk of the test's own, for the tests of collecting them (item 195).

Real files in a directory, listed through the managers' own types, so what the catalogue says about
them (the digest recorded, the sidecar beside the archive, a bundle's envelope) is the real
catalogue's and not a stand-in's. The managers are the thinnest fakes that return what the real
ones do: a list of the same dataclasses and where the files are.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from noust.managers.backup_catalogue import BackupCatalogue
from noust.managers.backup_manager import BackupMetadata
from noust.managers.database.base import BackupInfo
from noust.managers.self_backup import SelfBackupEntry
from noust.managers.volume_backups import VolumeBackupMetadata


def sha(data: bytes) -> str:
    """
    Args:
        data: Bytes.

    Returns:
        Their SHA-256.
    """
    return hashlib.sha256(data).hexdigest()


class Apps:
    """The application backups of a directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.items: list[BackupMetadata] = []

    def add(
        self,
        domain: str = "shop.example.com",
        stamp: str = "20261110_020000",
        *,
        data: bytes = b"archive bytes",
        scheduled: bool = True,
    ) -> BackupMetadata:
        name = domain.replace(".", "-")
        backup_id = f"{name}_{stamp}"
        folder = self.root / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{backup_id}.tar.gz").write_bytes(data)
        meta = BackupMetadata(
            id=backup_id,
            domain=domain,
            app_name=name,
            created_at=datetime.strptime(stamp, "%Y%m%d_%H%M%S").isoformat(),
            size_bytes=len(data),
            app_type="nodejs",
            version="4.3.0",
            description="",
            includes_env=True,
            includes_node_modules=False,
            checksum=sha(data),
            tags=["scheduled"] if scheduled else [],
        )
        (folder / f"{backup_id}.json").write_text(json.dumps(meta.to_dict()))
        self.items.append(meta)
        return meta

    def list_backups(self) -> list[BackupMetadata]:
        return sorted(self.items, key=lambda item: item.created_at, reverse=True)

    def local_files(self, backup: BackupMetadata) -> tuple[Path, Path]:
        folder = self.root / backup.app_name
        return folder / f"{backup.id}.tar.gz", folder / f"{backup.id}.json"


class Volumes:
    """The data backups of a directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.items: list[VolumeBackupMetadata] = []

    def add(
        self,
        domain: str = "shop.example.com",
        stamp: str = "20261110_030000",
        *,
        data: bytes = b"volume bytes",
    ) -> VolumeBackupMetadata:
        name = domain.replace(".", "-")
        backup_id = f"{name}.uploads_{stamp}"
        folder = self.root / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{backup_id}.tar.gz").write_bytes(data)
        meta = VolumeBackupMetadata(
            id=backup_id,
            domain=domain,
            app_name=name,
            target="path:uploads",
            kind="path",
            source="uploads",
            slug="uploads",
            created_at=datetime.strptime(stamp, "%Y%m%d_%H%M%S").isoformat() + "+00:00",
            size_bytes=len(data),
            checksum=sha(data),
            tags=["scheduled"],
        )
        (folder / f"{backup_id}.json").write_text(json.dumps(meta.to_dict()))
        self.items.append(meta)
        return meta

    def list_backups(self) -> list[VolumeBackupMetadata]:
        return list(self.items)

    def archive_path(self, metadata: VolumeBackupMetadata) -> Path:
        return self.root / metadata.app_name / f"{metadata.id}.tar.gz"


class Databases:
    """The dumps of a directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.views: list[Any] = []

    def add(
        self,
        engine: str = "postgresql",
        database: str = "shop",
        stamp: str = "20261110_040000",
        *,
        data: bytes = b"dump bytes",
        recorded: bool = True,
        origin: str = "scheduled",
    ) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{database}_{stamp}.dump"
        path.write_bytes(data)
        info = BackupInfo(
            path=path,
            database=database,
            engine=engine,
            size=len(data),
            created=datetime.strptime(stamp, "%Y%m%d_%H%M%S"),
        )
        record = (
            SimpleNamespace(sha256=sha(data), origin=origin, db_name=database) if recorded else None
        )
        self.views.append(SimpleNamespace(info=info, record=record))
        return path

    def list_dumps(self) -> list[Any]:
        return list(self.views)


class Bundles:
    """The bundles of Noust's own state of a directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.items: list[SelfBackupEntry] = []

    def add(
        self, stamp: str = "20261110_050000", *, data: bytes = b"ENVELOPE", kind: str = "scheduled"
    ) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"noust-self-host-{stamp}.tar.gz.enc"
        path.write_bytes(data)
        self.sidecar_path(path).write_text(json.dumps({"kind": kind, "sha256": sha(data)}))
        self.items.append(
            SelfBackupEntry(
                name=path.name,
                path=path,
                size=len(data),
                created_at=datetime.strptime(stamp, "%Y%m%d_%H%M%S").isoformat() + "+00:00",
                host="host",
                scheduled=kind == "scheduled",
                kind=kind,
                sha256=sha(data),
            )
        )
        return path

    def entries(self) -> list[SelfBackupEntry]:
        return list(self.items)

    def sidecar_path(self, bundle: Path) -> Path:
        return bundle.with_name(f"{bundle.name}.json")


@dataclass
class Disk:
    """A server's copies: the four managers and the catalogue over them."""

    root: Path
    apps: Apps = field(init=False)
    volumes: Volumes = field(init=False)
    databases: Databases = field(init=False)
    bundles: Bundles = field(init=False)

    def __post_init__(self) -> None:
        self.apps = Apps(self.root / "apps")
        self.volumes = Volumes(self.root / "volumes")
        self.databases = Databases(self.root / "databases")
        self.bundles = Bundles(self.root / "self")

    def catalogue(self) -> BackupCatalogue:
        return BackupCatalogue(
            apps=self.apps,  # type: ignore[arg-type]
            volumes=self.volumes,  # type: ignore[arg-type]
            databases=self.databases,  # type: ignore[arg-type]
            bundles=self.bundles,  # type: ignore[arg-type]
        )

    def everything(self) -> None:
        """One copy of each kind."""
        self.apps.add()
        self.volumes.add()
        self.databases.add()
        self.bundles.add()
