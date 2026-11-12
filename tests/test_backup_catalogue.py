# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a server lists of its copies for a central to take away (item 195).

The catalogue is the only thing that says which file of the server a central may read, so what is
pinned is what it lists, what it leaves out and that nothing a caller sends reaches a path:

- the four kinds, each as the files it is made of, with the digest Noust recorded and no path;
- a link, a file that is not one and a sidecar that is not a sidecar are not listed;
- one kind that cannot be listed does not hide the others, and says so;
- the fingerprint changes when a copy appears and when one goes, and not otherwise.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from noust.core.exceptions import BackupError
from noust.managers.backup_catalogue import (
    KINDS,
    MAX_SIDECAR_BYTES,
    BackupCatalogue,
    open_for_reading,
)
from tests.support.backup_collection_support import Disk, sha


@pytest.fixture
def disk(tmp_path: Path) -> Disk:
    return Disk(tmp_path)


class TestEveryKindOfCopy:
    def test_each_is_listed_with_its_files_and_the_digest_noust_recorded(self, disk: Disk) -> None:
        disk.everything()

        items = {item.kind: item for item in disk.catalogue().build().items}

        assert set(items) == set(KINDS)
        app = items["app"]
        assert [f.role for f in app.files] == ["archive", "sidecar"]
        assert app.files[0].sha256 == sha(b"archive bytes")
        assert app.files[1].sha256 is None
        assert (app.owner, app.folder) == ("shop.example.com", "shop-example-com")
        assert items["database"].files[0].sha256 == sha(b"dump bytes")
        assert items["database"].owner == "postgresql/shop"
        assert items["volume"].meta["target"] == "path:uploads"
        assert items["noust"].sealed is True

    def test_what_goes_to_a_central_has_no_path_in_it(self, disk: Disk, tmp_path: Path) -> None:
        disk.everything()

        text = repr([item.to_dict() for item in disk.catalogue().build().items])

        assert str(tmp_path) not in text

    def test_the_origin_says_who_made_it(self, disk: Disk) -> None:
        disk.apps.add(stamp="20261101_020000", scheduled=True)
        disk.apps.add(stamp="20261102_020000", scheduled=False)
        disk.databases.add(origin="safety")
        disk.bundles.add(kind="pre-restore")

        origins = [item.origin for item in disk.catalogue().build().items]

        assert sorted(origins) == ["manual", "safety", "safety", "scheduled"]

    def test_a_dump_nobody_recorded_has_no_digest_and_is_still_listed(self, disk: Disk) -> None:
        disk.databases.add(recorded=False, origin="manual")

        (item,) = disk.catalogue().build(["database"]).items

        assert item.files[0].sha256 is None
        assert item.origin == "manual"

    def test_times_are_utc_whatever_zone_the_manager_wrote(self, disk: Disk) -> None:
        disk.volumes.add(stamp="20261110_030000")

        (item,) = disk.catalogue().build(["volume"]).items

        assert item.created_at == "2026-11-10T03:00:00+00:00"

    def test_a_kind_can_be_asked_for_alone(self, disk: Disk) -> None:
        disk.everything()

        assert {i.kind for i in disk.catalogue().build(["app", "volume"]).items} == {
            "app",
            "volume",
        }


class TestWhatIsNotListed:
    def test_a_link_is_not_a_file(self, disk: Disk, tmp_path: Path) -> None:
        meta = disk.apps.add()
        archive, _ = disk.apps.local_files(meta)
        secret = tmp_path / "etc-shadow"
        secret.write_text("root:x")
        archive.unlink()
        archive.symlink_to(secret)

        assert disk.catalogue().build(["app"]).items == ()

    def test_a_sidecar_that_is_a_link_leaves_the_archive_alone(
        self, disk: Disk, tmp_path: Path
    ) -> None:
        meta = disk.apps.add()
        _, sidecar = disk.apps.local_files(meta)
        sidecar.unlink()
        sidecar.symlink_to(tmp_path / "elsewhere")

        (item,) = disk.catalogue().build(["app"]).items

        assert [f.role for f in item.files] == ["archive"]

    def test_a_sidecar_that_is_not_a_few_kilobytes_of_json_is_not_one(self, disk: Disk) -> None:
        meta = disk.apps.add()
        _, sidecar = disk.apps.local_files(meta)
        sidecar.write_bytes(b"x" * (MAX_SIDECAR_BYTES + 1))

        (item,) = disk.catalogue().build(["app"]).items

        assert [f.role for f in item.files] == ["archive"]

    def test_a_file_that_went_away_is_not_listed(self, disk: Disk) -> None:
        meta = disk.apps.add()
        disk.apps.local_files(meta)[0].unlink()

        assert disk.catalogue().build(["app"]).items == ()


class TestOneKindFailingDoesNotHideTheOthers:
    def test_the_problem_is_named_and_the_rest_is_listed(self, disk: Disk) -> None:
        disk.everything()

        def broken() -> list[object]:
            raise BackupError("The engines could not be asked", details="Is postgres up?")

        disk.databases.list_dumps = broken  # type: ignore[method-assign]

        catalogue = disk.catalogue().build()

        assert {i.kind for i in catalogue.items} == {"app", "volume", "noust"}
        assert [p["kind"] for p in catalogue.problems] == ["database"]
        assert "could not be asked" in catalogue.problems[0]["message"]


class TestFindingAFile:
    def test_a_file_is_found_by_what_the_list_says_and_nothing_else(self, disk: Disk) -> None:
        meta = disk.apps.add()
        catalogue = disk.catalogue()

        found = catalogue.item("app", meta.id)

        assert found is not None and found.file(f"{meta.id}.tar.gz") is not None
        assert found.file(f"../{meta.id}.tar.gz") is None
        assert found.file("/etc/passwd") is None
        assert catalogue.item("app", f"{meta.id}/../x") is None
        assert catalogue.item("etc", meta.id) is None

    def test_a_file_that_became_a_link_since_the_list_is_not_opened(
        self, disk: Disk, tmp_path: Path
    ) -> None:
        meta = disk.apps.add()
        found = disk.catalogue().item("app", meta.id)
        assert found is not None
        archive = disk.apps.local_files(meta)[0]
        archive.unlink()
        archive.symlink_to(tmp_path)

        with pytest.raises(OSError):
            open_for_reading(found.files[0])

    def test_a_file_that_is_listed_opens_and_reads(self, disk: Disk) -> None:
        meta = disk.apps.add(data=b"0123456789")
        found = disk.catalogue().item("app", meta.id)
        assert found is not None

        descriptor = open_for_reading(found.files[0])
        try:
            os.lseek(descriptor, 4, os.SEEK_SET)
            assert os.read(descriptor, 3) == b"456"
        finally:
            os.close(descriptor)


class TestTheFingerprint:
    def test_it_is_the_same_while_nothing_changes(self, disk: Disk) -> None:
        disk.everything()

        assert disk.catalogue().build().fingerprint == disk.catalogue().build().fingerprint

    def test_it_changes_when_a_copy_appears_and_when_one_goes(self, disk: Disk) -> None:
        disk.apps.add(stamp="20261101_020000")
        before = disk.catalogue().build().fingerprint

        disk.apps.add(stamp="20261102_020000")
        after_one_more = disk.catalogue().build().fingerprint
        disk.apps.items.pop()
        back = disk.catalogue().build().fingerprint

        assert after_one_more != before
        assert back == before

    def test_an_empty_server_has_one(self, disk: Disk) -> None:
        catalogue = BackupCatalogue(
            apps=disk.apps,  # type: ignore[arg-type]
            volumes=disk.volumes,  # type: ignore[arg-type]
            databases=disk.databases,  # type: ignore[arg-type]
            bundles=disk.bundles,  # type: ignore[arg-type]
        ).build()

        assert catalogue.items == () and len(catalogue.fingerprint) == 24


class TestWhenACopyMayLeaveOnlyEncrypted:
    """The ens-medium profile (or ``backup.encryption: required``) lets a copy leave sealed."""

    def required(self, disk: Disk, tmp_path: Path) -> BackupCatalogue:
        return BackupCatalogue(
            apps=disk.apps,  # type: ignore[arg-type]
            volumes=disk.volumes,  # type: ignore[arg-type]
            databases=disk.databases,  # type: ignore[arg-type]
            bundles=disk.bundles,  # type: ignore[arg-type]
            sealing_required=lambda: True,
        )

    def test_a_copy_that_is_not_sealed_is_listed_blocked_and_one_that_is_not(
        self, disk: Disk, tmp_path: Path
    ) -> None:
        disk.apps.add()
        disk.bundles.add()

        items = {item.kind: item for item in self.required(disk, tmp_path).build().items}

        assert items["app"].blocked == "sealing_required"
        # Noust's own bundle is encrypted under its passphrase whatever else is true.
        assert items["noust"].blocked is None

    def test_a_server_with_no_such_rule_blocks_nothing(self, disk: Disk) -> None:
        disk.apps.add()

        catalogue = BackupCatalogue(
            apps=disk.apps,  # type: ignore[arg-type]
            volumes=disk.volumes,  # type: ignore[arg-type]
            databases=disk.databases,  # type: ignore[arg-type]
            bundles=disk.bundles,  # type: ignore[arg-type]
            sealing_required=lambda: False,
        ).build()

        assert [item.blocked for item in catalogue.items] == [None]

    def test_what_a_central_is_told_says_why(self, disk: Disk, tmp_path: Path) -> None:
        disk.apps.add()

        (item,) = self.required(disk, tmp_path).build(["app"]).items

        assert item.to_dict()["blocked"] == "sealing_required"
