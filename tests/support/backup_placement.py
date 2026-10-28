# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_backup_placement.py`` that other tests build on."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from noust.managers.backup_manager import BackupManager, BackupMetadata


def plant_backup(root: Path, domain: str, stamp: str = "20260801_101500") -> tuple[Path, Path]:
    """
    Write a backup pair the way ``BackupManager.create`` lays it out.

    Args:
        root: The directory that plays the backup root.
        domain: Domain the backup belongs to.
        stamp: ``YYYYMMDD_HHMMSS`` part of the identifier.

    Returns:
        The archive and its metadata file.
    """
    app_name = domain.replace(".", "-")
    backup_id = f"{app_name}_{stamp}"
    directory = root / app_name
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / f"{backup_id}.tar.gz"
    archive.write_bytes(b"archive of " + backup_id.encode())
    metadata = BackupMetadata(
        id=backup_id,
        domain=domain,
        app_name=app_name,
        created_at=datetime(2026, 8, 1, 10, 15).isoformat(),
        size_bytes=archive.stat().st_size,
        app_type="nextjs",
        version=BackupManager.BACKUP_VERSION,
        description="",
        includes_env=True,
        includes_node_modules=False,
    )
    sidecar = directory / f"{backup_id}.json"
    sidecar.write_text(json.dumps(metadata.to_dict()))
    return archive, sidecar


def plant_home_clutter(root: Path) -> list[Path]:
    """
    Write what a real ``/root`` holds next to misplaced backups.

    Args:
        root: The directory that plays ``/root``.

    Returns:
        Every file planted, so a test can assert none of them moved.
    """
    files = {
        ".ssh/id_ed25519": "private key",
        ".docker/config.json": '{"auths": {}}',
        ".cache/settings.json": "{}",
        # A tarball that is not a Noust backup, in a directory named like one.
        "shop-example-com/export.tar.gz": "not a backup",
        # Named like a backup but with no metadata to prove it.
        "orphan-example-com/orphan-example-com_20260801_101500.tar.gz": "no sidecar",
    }
    planted = []
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        planted.append(path)
    return planted
