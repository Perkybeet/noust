# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An archive made to measure writes and reads only inside what a restore is for (item 195).

A central gives a copy back to a server with the permission to restore, and what it sends is whatever
it likes: an archive that was never taken here, a manifest of its own, a sidecar of its own. A restore
of any archive goes through the same code, so what is held here is that code against an archive built to
get out of it:

- **Writing**: a member with an absolute name or a ``..``, a link that leaves the tree, a member written
  through a link, a hard link to a file of the machine, a device and a pipe are refused, and nothing
  lands where the archive said.
- **Where the manifest points**: the application's root and the files of its dumps are inside the
  archive or the restore stops.
- **Reading**: an archive of 1.x named where its dumps were kept on the machine. That is the one way a
  restore reads a file that is not in the archive, and it reads only what lies in the folder of this
  server's backups; a manifest or a sidecar that names a file anywhere else is refused, so a restore
  cannot be asked to load ``/etc`` into a database.
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from typing import Any

import pytest

from noust.managers.backup_manager import MANIFEST_NAME, PAYLOAD_DIR, BackupError, BackupManager
from tests.support.backup_integrity import (  # noqa: F401
    FakeApp,
    FakeDatabase,
    FakeDatabaseManager,
    FakeStore,
    _use_store,
    apps_dir,
    manager,
)

# A fixture imported from the support module is found by pytest in this module's namespace, and a
# test that asks for it by name looks like a redefinition of an import that is never used.
# ruff: noqa: F811

DOMAIN = "shop.example.com"
APP = "shop-example-com"


def add_file(tar: tarfile.TarFile, name: str, data: bytes = b"data") -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def add_member(tar: tarfile.TarFile, name: str, kind: bytes, linkname: str = "") -> None:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = linkname
    tar.addfile(info)


def archive_of(path: Path, build: Any) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        add_file(tar, f"{APP}/index.js", b"console.log('restored')\n")
        build(tar)
    return path


def with_manifest(tar: tarfile.TarFile, manifest: dict[str, Any]) -> None:
    add_file(tar, f"{PAYLOAD_DIR}/{MANIFEST_NAME}", json.dumps(manifest).encode())


@pytest.fixture
def victim(tmp_path: Path) -> Path:
    """A file of the machine outside the archive and outside the folder of backups."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    file = outside / "stolen.sql"
    file.write_text("-- the machine's own file\n")
    return file


@pytest.fixture
def restoring(manager: BackupManager, monkeypatch: pytest.MonkeyPatch) -> BackupManager:
    _use_store(monkeypatch, FakeApp(1, DOMAIN), [FakeDatabase("shop", "postgresql")])
    return manager


class TestWhatAnArchiveWritesStaysWhereItWasExtracted:
    HOSTILE = {
        "an absolute name": lambda tar, outside: add_file(tar, f"{outside}/planted.txt"),
        "a name that climbs": lambda tar, outside: add_file(
            tar, f"../../{outside.name}/planted.txt"
        ),
        "an absolute link": lambda tar, outside: add_member(
            tar, f"{APP}/link", tarfile.SYMTYPE, str(outside)
        ),
        "a link that climbs": lambda tar, outside: add_member(
            tar, f"{APP}/link", tarfile.SYMTYPE, f"../../../{outside.name}"
        ),
        "a hard link to a file of the machine": lambda tar, outside: add_member(
            tar, f"{APP}/link", tarfile.LNKTYPE, str(outside / "stolen.sql")
        ),
        "a hard link that climbs": lambda tar, outside: add_member(
            tar, f"{APP}/link", tarfile.LNKTYPE, f"../../{outside.name}/stolen.sql"
        ),
        "a device": lambda tar, outside: add_member(tar, f"{APP}/null", tarfile.CHRTYPE),
        "a pipe": lambda tar, outside: add_member(tar, f"{APP}/pipe", tarfile.FIFOTYPE),
    }

    @pytest.mark.parametrize("name", sorted(HOSTILE))
    def test_it_is_refused_and_nothing_lands_outside(
        self, name: str, restoring: BackupManager, victim: Path, tmp_path: Path
    ) -> None:
        outside = victim.parent
        archive = archive_of(
            tmp_path / "hostile.tar.gz", lambda tar: self.HOSTILE[name](tar, outside)
        )

        with pytest.raises(BackupError):
            restoring.restore_archive(archive, target_domain=DOMAIN)

        assert sorted(p.name for p in outside.iterdir()) == ["stolen.sql"]
        assert victim.read_text() == "-- the machine's own file\n"

    def test_a_member_written_through_a_link_of_the_same_archive_is_refused(
        self, restoring: BackupManager, victim: Path, tmp_path: Path
    ) -> None:
        def build(tar: tarfile.TarFile) -> None:
            add_member(tar, f"{APP}/link", tarfile.SYMTYPE, "../")
            add_file(tar, f"{APP}/link/../../{victim.parent.name}/planted.txt")
            add_member(tar, f"{APP}/hole", tarfile.SYMTYPE, str(victim.parent))
            add_file(tar, f"{APP}/hole/planted.txt")

        archive = archive_of(tmp_path / "through.tar.gz", build)

        with pytest.raises(BackupError):
            restoring.restore_archive(archive, target_domain=DOMAIN)

        assert sorted(p.name for p in victim.parent.iterdir()) == ["stolen.sql"]


class TestWhereTheManifestPointsIsInsideTheArchive:
    @pytest.mark.parametrize("root", ["../..", "/etc", "../elsewhere", "a/../../.."])
    def test_the_root_of_the_application_cannot_be_outside(
        self, root: str, restoring: BackupManager, tmp_path: Path
    ) -> None:
        archive = archive_of(
            tmp_path / "root.tar.gz",
            lambda tar: with_manifest(tar, {"domain": DOMAIN, "app_root": root}),
        )

        with pytest.raises(BackupError):
            restoring.restore_archive(archive)

        assert FakeDatabaseManager.restored == []

    @pytest.mark.parametrize("path", ["../../elsewhere/stolen.sql", "/etc/passwd", "../x"])
    def test_the_file_of_a_dump_cannot_be_outside(
        self, path: str, restoring: BackupManager, tmp_path: Path
    ) -> None:
        manifest = {
            "domain": DOMAIN,
            "app_root": APP,
            "databases": [{"engine": "postgresql", "name": "shop", "archive_path": path}],
        }
        archive = archive_of(tmp_path / "dump.tar.gz", lambda tar: with_manifest(tar, manifest))

        with pytest.raises(BackupError):
            restoring.restore_archive(archive)

        assert FakeDatabaseManager.restored == []


class TestADumpThatLivesOutsideTheArchiveIsOnlyReadFromTheFolderOfBackups:
    """The 1.x way: the archive says where on this machine its dump was kept."""

    def manifest(self, where: Path, key: str = "backup_path") -> dict[str, Any]:
        return {
            "domain": DOMAIN,
            "app_root": APP,
            "databases": [{"engine": "postgresql", "name": "shop", key: str(where)}],
        }

    @pytest.mark.parametrize("key", ["backup_path", "path"])
    def test_a_file_of_the_machine_is_not_loaded_into_a_database(
        self, key: str, restoring: BackupManager, victim: Path, tmp_path: Path
    ) -> None:
        archive = archive_of(
            tmp_path / "old.tar.gz", lambda tar: with_manifest(tar, self.manifest(victim, key))
        )

        with pytest.raises(BackupError) as caught:
            restoring.restore_archive(archive)

        assert FakeDatabaseManager.restored == []
        assert "points outside" in str(caught.value) or "outside" in str(caught.value)
        assert str(victim.parent) not in "".join(getattr(caught.value, "output", "") or "")

    def test_nor_through_a_link_that_leads_there(
        self, restoring: BackupManager, victim: Path, tmp_path: Path
    ) -> None:
        folder = restoring.backup_dir / "old"
        folder.mkdir(parents=True)
        link = folder / "shop.sql"
        link.symlink_to(victim)
        archive = archive_of(
            tmp_path / "linked.tar.gz", lambda tar: with_manifest(tar, self.manifest(link))
        )

        with pytest.raises(BackupError):
            restoring.restore_archive(archive)

        assert FakeDatabaseManager.restored == []

    def test_nor_when_a_sidecar_says_it_and_the_archive_has_no_manifest(
        self, restoring: BackupManager, victim: Path, tmp_path: Path
    ) -> None:
        from noust.managers.backup_manager import BackupMetadata

        archive = archive_of(tmp_path / "bare.tar.gz", lambda tar: None)
        metadata = BackupMetadata(
            id="shop-example-com_20261110_020000",
            domain=DOMAIN,
            app_name=APP,
            created_at="2026-11-10T02:00:00",
            size_bytes=1,
            app_type="nodejs",
            version="1.0.0",
            description="",
            includes_env=False,
            includes_node_modules=False,
            includes_databases=True,
            database_backups=[{"engine": "postgresql", "name": "shop", "path": str(victim)}],
        )

        with pytest.raises(BackupError):
            restoring.restore_archive(archive, fallback=metadata)

        assert FakeDatabaseManager.restored == []

    def test_a_dump_kept_in_the_folder_of_backups_is_still_restored(
        self, restoring: BackupManager, tmp_path: Path
    ) -> None:
        """What 1.x did: the dump is where Noust wrote it, in the folder of its backups."""
        kept = restoring.backup_dir / "databases" / "shop.sql"
        kept.parent.mkdir(parents=True)
        kept.write_text("-- the dump Noust kept\n")
        archive = archive_of(
            tmp_path / "old.tar.gz", lambda tar: with_manifest(tar, self.manifest(kept))
        )

        restoring.restore_archive(archive)

        assert [content for _db, _path, content in FakeDatabaseManager.restored] == [
            "-- the dump Noust kept\n"
        ]
