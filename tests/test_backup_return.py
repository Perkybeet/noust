# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A copy the central held comes back and is restored by the manager of its kind (item 195).

The receiving end restores nothing itself: it turns a file that arrived and what the central says of
it into the call of the manager that always restored that kind, so what is pinned is the call (what it
is given, what is refused before any is made) and the one thing that is not restored at all, Noust's
own state, which is put where ``noust self-backup restore`` reads it.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.exceptions import BackupError, ValidationError
from noust.managers.backup_return import BackupReturn, ReturnRequest


class Apps:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, dict[str, Any]]] = []

    def restore_archive(self, archive: Path, **kw: Any) -> bool:
        self.calls.append((archive, kw))
        return True


class Volumes:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def restore(self, backup_id: str, **kw: Any) -> dict[str, Any]:
        self.calls.append({"id": backup_id, **kw})
        return {"id": backup_id, "started": True}


class Databases:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def restore_file(self, *args: Any, **kw: Any) -> Any:
        self.calls.append((*args, kw))
        return SimpleNamespace(
            database=kw.get("new_name") or args[1], safety_copy=None, replaced=True
        )


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    path = tmp_path / "arrived"
    path.write_bytes(b"archive")
    return path


VOLUME_SIDECAR = {
    "id": "shop-example-com.uploads_20261110_030000",
    "domain": "shop.example.com",
    "app_name": "shop-example-com",
    "target": "path:uploads",
    "kind": "path",
    "source": "uploads",
    "slug": "uploads",
    "created_at": "2026-11-10T03:00:00+00:00",
    "size_bytes": 7,
    "checksum": "a" * 64,
}


class TestAnApplicationsBackup:
    def test_it_is_restored_by_the_one_that_makes_a_file_portable(self, archive: Path) -> None:
        apps = Apps()

        result = BackupReturn(apps=apps).restore(  # type: ignore[arg-type]
            ReturnRequest(
                "app",
                archive,
                "f" * 64,
                "shop_1.tar.gz",
                meta={"domain": "shop.example.com"},
                options={"restore_env": False},
            )
        )

        ((path, kw),) = apps.calls
        assert path == archive
        assert kw["target_domain"] == "shop.example.com" and kw["restore_env"] is False
        assert kw["expected_checksum"] == "f" * 64
        assert result["restored"] == "shop.example.com"

    def test_it_can_go_into_another_application_when_the_operator_says_so(
        self, archive: Path
    ) -> None:
        apps = Apps()

        BackupReturn(apps=apps).restore(  # type: ignore[arg-type]
            ReturnRequest(
                "app",
                archive,
                "f" * 64,
                "x",
                meta={"domain": "a.example.com"},
                target={"domain": "b.example.com"},
            )
        )

        assert apps.calls[0][1]["target_domain"] == "b.example.com"

    def test_a_copy_that_says_no_application_and_is_told_none_is_refused_before_anything(
        self, archive: Path
    ) -> None:
        apps = Apps()

        with pytest.raises(ValidationError) as raised:
            BackupReturn(apps=apps).restore(ReturnRequest("app", archive, "f" * 64, "x"))  # type: ignore[arg-type]

        assert raised.value.field == "domain" and apps.calls == []

    def test_a_sidecar_that_is_not_usable_is_left_for_the_archive_to_replace(
        self, archive: Path
    ) -> None:
        apps = Apps()

        BackupReturn(apps=apps).restore(  # type: ignore[arg-type]
            ReturnRequest(
                "app",
                archive,
                "f" * 64,
                "x",
                sidecar={"junk": 1},
                meta={"domain": "shop.example.com"},
            )
        )

        assert apps.calls[0][1]["fallback"] is None


class TestAnApplicationsData:
    def test_it_is_restored_from_the_archive_and_its_sidecar(self, archive: Path) -> None:
        volumes = Volumes()

        BackupReturn(volumes=volumes).restore(  # type: ignore[arg-type]
            ReturnRequest("volume", archive, "a" * 64, "v.tar.gz", sidecar=VOLUME_SIDECAR)
        )

        (call,) = volumes.calls
        assert call["id"] == VOLUME_SIDECAR["id"] and call["archive"] == archive
        assert call["metadata"].domain == "shop.example.com" and call["safety_backup"] is True

    def test_without_its_sidecar_it_is_refused_because_nothing_says_where_it_goes(
        self, archive: Path
    ) -> None:
        volumes = Volumes()

        with pytest.raises(BackupError) as raised:
            BackupReturn(volumes=volumes).restore(  # type: ignore[arg-type]
                ReturnRequest("volume", archive, "a" * 64, "v.tar.gz")
            )

        assert "needs the metadata" in raised.value.message and volumes.calls == []

    def test_data_of_one_application_does_not_go_into_another(self, archive: Path) -> None:
        volumes = Volumes()

        with pytest.raises(BackupError) as raised:
            BackupReturn(volumes=volumes).restore(  # type: ignore[arg-type]
                ReturnRequest(
                    "volume",
                    archive,
                    "a" * 64,
                    "v",
                    sidecar=VOLUME_SIDECAR,
                    target={"domain": "other.example.com"},
                )
            )

        assert "not of other.example.com" in raised.value.message and volumes.calls == []

    def test_metadata_that_cannot_be_read_is_refused(self, archive: Path) -> None:
        with pytest.raises(BackupError):
            BackupReturn(volumes=Volumes()).restore(  # type: ignore[arg-type]
                ReturnRequest("volume", archive, "a" * 64, "v", sidecar={"id": "x"})
            )


class TestADatabasesDump:
    def test_it_is_loaded_into_the_database_it_names(self, archive: Path) -> None:
        databases = Databases()

        result = BackupReturn(databases=databases).restore(  # type: ignore[arg-type]
            ReturnRequest(
                "database",
                archive,
                "a" * 64,
                "shop.dump",
                meta={"engine": "postgresql", "database": "shop"},
                options={"drop_existing": True, "safety_backup": False},
            )
        )

        ((engine, database, path, kw),) = databases.calls
        assert (engine, database, path) == ("postgresql", "shop", archive)
        assert kw == {"drop_existing": True, "safety_backup": False, "new_name": None}
        assert result["restored"] == "postgresql/shop"

    def test_or_beside_it_under_a_new_name(self, archive: Path) -> None:
        databases = Databases()

        BackupReturn(databases=databases).restore(  # type: ignore[arg-type]
            ReturnRequest(
                "database",
                archive,
                "a" * 64,
                "shop.dump",
                meta={"engine": "postgresql", "database": "shop"},
                target={"new_name": "shop_copy"},
            )
        )

        assert databases.calls[0][3]["new_name"] == "shop_copy"

    def test_a_dump_that_says_no_database_and_is_told_none_is_refused(self, archive: Path) -> None:
        databases = Databases()

        with pytest.raises(ValidationError):
            BackupReturn(databases=databases).restore(  # type: ignore[arg-type]
                ReturnRequest("database", archive, "a" * 64, "x.dump")
            )

        assert databases.calls == []


class TestNoustsOwnState:
    class Bundles:
        def __init__(self, directory: Path) -> None:
            self.directory = directory

        def sidecar_path(self, bundle: Path) -> Path:
            return bundle.with_name(f"{bundle.name}.json")

    def test_it_is_not_restored_but_put_where_the_restore_at_a_terminal_reads_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        arrived = tmp_path / "arrived"
        arrived.write_bytes(b"ENVELOPE")
        monkeypatch.setattr(
            "noust.core.ens.envelope.read_header", lambda path: {"label": "noust-self-backup"}
        )
        monkeypatch.setattr("noust.managers.self_backup.LABEL", "noust-self-backup")
        bundles = self.Bundles(tmp_path / "bundles")

        result = BackupReturn(bundles=bundles).restore(  # type: ignore[arg-type]
            ReturnRequest(
                "noust",
                arrived,
                "a" * 64,
                "noust-self-host-1.tar.gz.enc",
                sidecar={"kind": "scheduled"},
            )
        )

        placed = tmp_path / "bundles" / "noust-self-host-1.tar.gz.enc"
        assert placed.read_bytes() == b"ENVELOPE" and not arrived.exists()
        assert json.loads((tmp_path / "bundles" / f"{placed.name}.json").read_text()) == {
            "kind": "scheduled"
        }
        assert result["command"] == "noust self-backup restore noust-self-host-1.tar.gz.enc"
        assert oct(placed.stat().st_mode & 0o777) == "0o600"

    def test_a_file_that_is_not_a_bundle_is_not_put_anywhere(self, tmp_path: Path) -> None:
        arrived = tmp_path / "arrived"
        arrived.write_bytes(b"not an envelope at all")
        bundles = self.Bundles(tmp_path / "bundles")

        with pytest.raises(BackupError) as raised:
            BackupReturn(bundles=bundles).restore(  # type: ignore[arg-type]
                ReturnRequest("noust", arrived, "a" * 64, "x.tar.gz.enc")
            )

        assert "not a bundle" in raised.value.message
        assert not (tmp_path / "bundles").exists()

    def test_one_that_is_already_there_is_not_replaced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        arrived = tmp_path / "arrived"
        arrived.write_bytes(b"NEW")
        folder = tmp_path / "bundles"
        folder.mkdir()
        (folder / "b.tar.gz.enc").write_bytes(b"OLD")
        monkeypatch.setattr(
            "noust.core.ens.envelope.read_header", lambda path: {"label": "noust-self-backup"}
        )
        monkeypatch.setattr("noust.managers.self_backup.LABEL", "noust-self-backup")

        with pytest.raises(BackupError):
            BackupReturn(bundles=self.Bundles(folder)).restore(  # type: ignore[arg-type]
                ReturnRequest("noust", arrived, "a" * 64, "b.tar.gz.enc")
            )

        assert (folder / "b.tar.gz.enc").read_bytes() == b"OLD"


def test_a_kind_nobody_knows_is_refused(archive: Path) -> None:
    with pytest.raises(ValidationError) as raised:
        BackupReturn().restore(ReturnRequest("secrets", archive, "a" * 64, "x"))

    assert raised.value.field == "kind"
