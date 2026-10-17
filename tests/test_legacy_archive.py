# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Backups and exports written before 4.0 are data, and are read for as long as the file exists.

What Noust writes now carries its name (``noust-backup/`` inside an archive, ``"format":
"noust-app"`` in an export). The one reader (``noust.core.legacy_archive``) understands both,
so a backup taken years ago restores, and an export file kept on an operator's disk imports.
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from noust.core import legacy_archive, legacy_names
from noust.core.exceptions import ValidationError
from noust.deployers.app_export import VERSION, validate_document
from noust.managers.backup_manager import PAYLOAD_DIR, BackupManager


def make_archive(path: Path, payload_dir: str, manifest: dict[str, object]) -> Path:
    """
    Write an archive the way a release that called its payload ``payload_dir`` did.

    Args:
        path: Where the archive goes.
        payload_dir: The payload directory's name.
        manifest: What the manifest says.

    Returns:
        The archive.
    """
    with tarfile.open(path, "w:gz") as archive:
        data = json.dumps(manifest).encode()
        info = tarfile.TarInfo(f"{payload_dir}/manifest.json")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
        app = tarfile.TarInfo("shop-example-com/index.html")
        app.size = 2
        archive.addfile(app, io.BytesIO(b"hi"))
    return path


def test_what_is_written_carries_noust_s_name() -> None:
    assert PAYLOAD_DIR == "noust-backup" == legacy_archive.PAYLOAD_DIR
    assert legacy_archive.EXPORT_FORMAT == "noust-app"
    assert legacy_archive.PAYLOAD_DIRS == ("noust-backup", legacy_names.ARCHIVE_PAYLOAD_DIR)


class TestPayloadDirectory:
    @pytest.mark.parametrize("name", ["noust-backup", legacy_names.ARCHIVE_PAYLOAD_DIR])
    def test_either_spelling_is_the_payload(self, name: str) -> None:
        assert legacy_archive.is_payload_dir(name)

    @pytest.mark.parametrize("name", ["shop-example-com", "backup", "noust-backup-extra"])
    def test_nothing_else_is(self, name: str) -> None:
        assert not legacy_archive.is_payload_dir(name)

    def test_an_extracted_archive_says_which_it_uses(self, tmp_path: Path) -> None:
        (tmp_path / legacy_names.ARCHIVE_PAYLOAD_DIR).mkdir()

        assert legacy_archive.payload_dir_in(tmp_path) == legacy_names.ARCHIVE_PAYLOAD_DIR

    def test_the_new_one_is_preferred_when_both_are_there(self, tmp_path: Path) -> None:
        (tmp_path / legacy_names.ARCHIVE_PAYLOAD_DIR).mkdir()
        (tmp_path / "noust-backup").mkdir()

        assert legacy_archive.payload_dir_in(tmp_path) == "noust-backup"

    def test_a_link_in_place_of_the_directory_is_not_one(self, tmp_path: Path) -> None:
        (tmp_path / "noust-backup").symlink_to("/etc")

        assert legacy_archive.payload_dir_in(tmp_path) == "noust-backup"
        assert (tmp_path / legacy_names.ARCHIVE_PAYLOAD_DIR).exists() is False

    def test_prefixes_for_a_kind_of_payload(self) -> None:
        assert legacy_archive.payload_prefixes("databases") == (
            "noust-backup/databases/",
            f"{legacy_names.ARCHIVE_PAYLOAD_DIR}/databases/",
        )
        assert "wasm-backup/databases/x".startswith(legacy_archive.payload_prefixes("databases"))


class TestMembers:
    @pytest.mark.parametrize("payload", ["noust-backup", legacy_names.ARCHIVE_PAYLOAD_DIR])
    def test_a_member_is_found_under_either_spelling(self, tmp_path: Path, payload: str) -> None:
        archive_path = make_archive(tmp_path / "a.tar.gz", payload, {"version": 1})

        with tarfile.open(archive_path, "r:gz") as archive:
            member = legacy_archive.find_member(archive, "manifest.json")

        assert member is not None and member.name == f"{payload}/manifest.json"

    def test_a_member_that_is_in_neither_is_none(self, tmp_path: Path) -> None:
        archive_path = make_archive(tmp_path / "a.tar.gz", "somewhere-else", {})

        with tarfile.open(archive_path, "r:gz") as archive:
            assert legacy_archive.find_member(archive, "manifest.json") is None


class TestReadingAnArchiveOfBefore:
    """The manager's own readers over an archive the previous release wrote."""

    @pytest.mark.parametrize("payload", ["noust-backup", legacy_names.ARCHIVE_PAYLOAD_DIR])
    def test_the_manifest_is_read_from_the_archive(self, tmp_path: Path, payload: str) -> None:
        archive_path = make_archive(
            tmp_path / "a.tar.gz", payload, {"version": 2, "databases": [], "volumes": []}
        )

        manifest = BackupManager(verbose=False)._read_manifest_in_place(archive_path)

        assert manifest is not None and manifest["version"] == 2

    @pytest.mark.parametrize("payload", ["noust-backup", legacy_names.ARCHIVE_PAYLOAD_DIR])
    def test_the_manifest_is_read_from_an_extracted_archive(
        self, tmp_path: Path, payload: str
    ) -> None:
        directory = tmp_path / "extracted"
        (directory / payload).mkdir(parents=True)
        (directory / payload / "manifest.json").write_text(json.dumps({"version": 2}))

        manifest = BackupManager(verbose=False)._read_manifest(directory)

        assert manifest == {"version": 2}

    @pytest.mark.parametrize("payload", ["noust-backup", legacy_names.ARCHIVE_PAYLOAD_DIR])
    def test_the_application_directory_is_never_taken_for_the_payload(
        self, tmp_path: Path, payload: str
    ) -> None:
        directory = tmp_path / "extracted"
        (directory / payload).mkdir(parents=True)
        (directory / "shop-example-com").mkdir()

        found = BackupManager(verbose=False)._locate_app_root(directory, None, None)

        assert found == directory / "shop-example-com"

    def test_an_application_cannot_be_named_after_the_payload(self) -> None:
        for name in ("noust-backup", legacy_names.ARCHIVE_PAYLOAD_DIR):
            assert legacy_archive.is_payload_dir(name)


class TestExports:
    def document(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "format": "noust-app",
            "version": VERSION,
            "app": {"domain": "shop.example.com", "app_type": "static"},
        }
        base.update(overrides)
        return base

    @pytest.mark.parametrize("name", ["noust-app", legacy_names.EXPORT_FORMAT])
    def test_either_format_name_is_an_export(self, name: str) -> None:
        assert legacy_archive.is_export_document({"format": name})
        assert validate_document(self.document(format=name))["app"]["domain"] == "shop.example.com"

    @pytest.mark.parametrize("value", ["other-app", "", None, 3])
    def test_nothing_else_is(self, value: object) -> None:
        assert not legacy_archive.is_export_document({"format": value})
        with pytest.raises(ValidationError, match="not a Noust application export"):
            validate_document(self.document(format=value))

    def test_a_newer_export_names_the_release_that_made_it_under_either_key(self) -> None:
        for key in ("noust_version", legacy_names.EXPORT_VERSION_KEY):
            document = self.document(version=VERSION + 1, **{key: "9.9.9"})

            with pytest.raises(ValidationError) as caught:
                validate_document(document)

            assert "9.9.9" in (caught.value.details or "")

    def test_the_release_that_made_it(self) -> None:
        assert legacy_archive.export_made_by({"noust_version": "4.0.0"}) == "4.0.0"
        assert legacy_archive.export_made_by({legacy_names.EXPORT_VERSION_KEY: "3.3.1"}) == "3.3.1"
        assert legacy_archive.export_made_by({"noust_version": "", "x": 1}) is None

    def test_a_server_before_4_0_is_given_the_spelling_it_reads(self) -> None:
        document = self.document(noust_version="4.0.0")

        converted = legacy_archive.export_with_legacy_names(document)

        assert converted["format"] == legacy_names.EXPORT_FORMAT
        assert converted[legacy_names.EXPORT_VERSION_KEY] == "4.0.0"
        assert "noust_version" not in converted
        assert document["format"] == "noust-app", "the original is left as it was"
        assert converted["app"] == document["app"]
