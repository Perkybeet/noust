# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A destination is tried before it is saved, and its bucket is judged on its own (checklist H-11).

``add`` stored whatever it was given and the test was a step apart, so a wrong key or a mistyped
bucket was found by the first night's backup. What is pinned: the connection is made once, before
the destination is kept, and a failure keeps nothing (no row, no secret, no generated encryption
key) and says rclone's words with the secrets scrubbed; the bucket (the first part of the folder of
the object stores) is checked against its provider's naming rules before anything is contacted and
then looked at, read only, so that a typo is an error and not a bucket created in somebody's
account; and a change to a destination that does not connect leaves the destination as it was.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from noust.core.exceptions import BackupError
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.managers.backup_destinations import (
    BackupDestinationManager,
    bucket_of,
    validate_bucket,
)


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def destinations(runner: FakeRunner) -> BackupDestinationManager:
    runner.only_knows("rclone")
    runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")
    return BackupDestinationManager(runner=runner)


def sftp(**extra: str) -> dict[str, str]:
    return {"host": "nas.example.com", "user": "noust", "pass": "s3cr3t-password-value", **extra}


def s3(**extra: str) -> dict[str, str]:
    return {
        "provider": "Minio",
        "access_key_id": "AKIAEXAMPLE",
        "secret_access_key": "wJalrXUtnFEMI-secret-value",
        "endpoint": "https://minio.example.com",
        "path": "backups/noust",
        **extra,
    }


def rclone_calls(runner: FakeRunner, verb: str) -> list[tuple[str, ...]]:
    return [call for call in runner.calls if call[:2] == ("rclone", verb)]


class TestTriedBeforeItIsKept:
    def test_the_connection_is_made_once_and_the_destination_is_kept(
        self, destinations: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        saved = destinations.add("nas", "sftp", sftp())

        assert saved.name == "nas" and destinations.get("nas") is not None
        assert len(rclone_calls(runner, "mkdir")) == 1
        assert len(rclone_calls(runner, "lsf")) == 1

    def test_a_destination_can_be_added_without_trying_it_when_it_is_asked(
        self, destinations: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        destinations.add("nas", "sftp", sftp(), verify=False)

        assert rclone_calls(runner, "mkdir") == [] and rclone_calls(runner, "lsf") == []

    def test_one_that_does_not_connect_is_not_kept_and_says_why_with_its_secrets_scrubbed(
        self, destinations: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        runner.script(
            ("rclone", "mkdir"),
            stderr="Failed to create file system: ssh: handshake failed (s3cr3t-password-value)",
            exit_code=1,
        )

        with pytest.raises(BackupError) as caught:
            destinations.add("nas", "sftp", sftp())

        text = str(caught.value)
        assert "was not saved" in text and "handshake failed" in text
        assert "s3cr3t-password-value" not in text
        assert destinations.get("nas") is None
        assert destinations.secrets.read_protected_json("backup-destinations/nas") == {}
        # ...and the name is free to try again.
        runner.script(("rclone", "mkdir"), exit_code=0)
        assert destinations.add("nas", "sftp", sftp()).name == "nas"

    def test_an_encrypted_one_that_does_not_connect_leaves_no_key_behind(
        self, destinations: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        runner.script(("rclone", "mkdir"), stderr="connection refused", exit_code=1)

        with pytest.raises(BackupError):
            destinations.add("nas", "sftp", sftp(), encrypted=True)

        assert destinations.get("nas") is None
        assert destinations.secrets.read_protected_json("backup-destinations/nas") == {}

    def test_a_change_that_does_not_connect_leaves_the_destination_as_it_was(
        self, destinations: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        destinations.add("nas", "sftp", sftp())
        runner.script(("rclone", "mkdir"), stderr="no route to host", exit_code=1)

        with pytest.raises(BackupError) as caught:
            destinations.update("nas", {"host": "elsewhere.example.com", "pass": "new-password"})

        assert "was not changed" in str(caught.value) and "no route to host" in str(caught.value)
        kept = destinations.get("nas")
        assert kept is not None and kept.settings["host"] == "nas.example.com"
        assert destinations.secrets.read_protected_json("backup-destinations/nas")["pass"] == (
            "s3cr3t-password-value"
        )

    def test_a_change_that_connects_is_kept(
        self, destinations: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        destinations.add("nas", "sftp", sftp())
        runner.calls.clear()

        destinations.update("nas", {"host": "other.example.com"})

        assert len(rclone_calls(runner, "mkdir")) == 1
        updated = destinations.get("nas")
        assert updated is not None and updated.settings["host"] == "other.example.com"

    def test_a_change_to_nothing_that_matters_to_the_connection_does_not_try_it(
        self, destinations: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        destinations.add("nas", "sftp", sftp())
        runner.calls.clear()

        destinations.update("nas", {})

        assert rclone_calls(runner, "mkdir") == []


class TestTheBucket:
    @pytest.mark.parametrize(
        ("backend", "path", "bucket"),
        [
            ("s3", "backups/noust", "backups"),
            ("s3", "/backups", "backups"),
            ("gcs", "my_bucket/folder", "my_bucket"),
            ("azureblob", "container/noust-backups", "container"),
            ("b2", "bucket-name", "bucket-name"),
            ("sftp", "/srv/backups", None),
            ("local", "/mnt/disk", None),
        ],
    )
    def test_it_is_the_first_part_of_the_folder_for_the_object_stores_only(
        self, backend: str, path: str, bucket: str | None
    ) -> None:
        assert bucket_of(backend, path) == bucket

    @pytest.mark.parametrize(
        ("backend", "name"),
        [
            ("s3", "my-backups"),
            ("s3", "backups.2026"),
            ("gcs", "my_bucket"),
            ("azureblob", "noust-backups"),
            ("b2", "noust-backups-1"),
        ],
    )
    def test_a_name_its_provider_accepts_passes(self, backend: str, name: str) -> None:
        validate_bucket(backend, name)

    @pytest.mark.parametrize(
        ("backend", "name", "why"),
        [
            ("s3", "ab", "3 to 63"),
            ("s3", "My_Backups", "lowercase"),
            ("s3", "-backups", "start"),
            ("s3", "backups-", "end"),
            ("s3", "back..ups", "two dots"),
            ("s3", "192.168.1.1", "address"),
            ("s3", "a" * 64, "3 to 63"),
            ("gcs", "Upper", "lowercase"),
            ("azureblob", "my_container", "lowercase letters, digits and single hyphens"),
            ("azureblob", "a--b", "lowercase letters, digits and single hyphens"),
            ("b2", "short", "6 to 50"),
            ("b2", "b2-reserved", "b2-"),
        ],
    )
    def test_a_name_it_does_not_is_refused_in_the_providers_words(
        self, backend: str, name: str, why: str
    ) -> None:
        with pytest.raises(BackupError) as caught:
            validate_bucket(backend, name)

        assert why in str(caught.value)

    def test_a_bad_bucket_is_refused_before_anything_is_contacted_or_stored(
        self, destinations: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        with pytest.raises(BackupError, match="bucket"):
            destinations.add("cloud", "s3", s3(path="Bad_Bucket/noust"))

        assert destinations.get("cloud") is None
        assert [c for c in runner.calls if c[0] == "rclone" and c[1] != "obscure"] == []

    def test_an_object_store_with_no_bucket_named_is_refused(
        self, destinations: BackupDestinationManager
    ) -> None:
        fields = s3()
        del fields["path"]

        with pytest.raises(BackupError, match="Name the bucket"):
            destinations.add("cloud", "s3", fields)

    def test_the_bucket_is_looked_at_read_only_before_anything_is_created(
        self, destinations: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        destinations.add("cloud", "s3", s3())

        calls = [c for c in runner.calls if c[0] == "rclone" and c[1] != "obscure"]
        assert calls[0][:3] == ("rclone", "lsf", "cloud:backups")
        assert calls.index(next(c for c in calls if c[1] == "mkdir")) > 0

    def test_a_bucket_that_does_not_exist_is_an_error_and_is_never_created(
        self, destinations: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        runner.script(("rclone", "lsf", "cloud:backups"), stderr="directory not found", exit_code=3)

        with pytest.raises(BackupError) as caught:
            destinations.add("cloud", "s3", s3())

        text = str(caught.value)
        assert "bucket 'backups' does not exist" in text and "does not create buckets" in text
        assert rclone_calls(runner, "mkdir") == []
        assert destinations.get("cloud") is None

    def test_a_key_that_cannot_list_the_bucket_says_so_in_rclones_words(
        self, destinations: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        runner.script(
            ("rclone", "lsf", "cloud:backups"),
            stderr="AccessDenied: Access Denied (wJalrXUtnFEMI-secret-value)",
            exit_code=1,
        )

        with pytest.raises(BackupError) as caught:
            destinations.add("cloud", "s3", s3())

        assert "AccessDenied" in str(caught.value)
        assert "wJalrXUtnFEMI" not in str(caught.value)

    def test_rclones_notice_about_its_config_file_is_not_a_missing_bucket(
        self, destinations: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        runner.script(
            ("rclone", "lsf", "cloud:backups"),
            stderr='NOTICE: Config file "/root/.config/rclone/rclone.conf" not found - using '
            "default\nERROR : error listing: SignatureDoesNotMatch: the request signature differs",
            exit_code=1,
        )

        with pytest.raises(BackupError) as caught:
            destinations.add("cloud", "s3", s3())

        assert "SignatureDoesNotMatch" in str(caught.value)
        assert "does not exist" not in str(caught.value)

    def test_the_test_command_looks_at_the_bucket_too(
        self, destinations: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        destinations.add("cloud", "s3", s3(), verify=False)
        runner.script(("rclone", "lsf", "cloud:backups"), stderr="directory not found", exit_code=3)

        with pytest.raises(BackupError, match="does not exist"):
            destinations.test("cloud")
