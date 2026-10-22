# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The backends a backup destination can use, and the ones added for checklist H-15: Azure Blob
Storage, Google Cloud Storage, FTP/FTPS and Box, and the S3 providers DigitalOcean Spaces,
IDrive e2 and Ceph.

Everything goes through the fake runner: what is asserted is the environment rclone would be given
and where each secret lands.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from noust.core.exceptions import BackupError
from noust.core.runner import FakeRunner
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore, get_store
from noust.managers.backup_destinations import (
    BACKEND_FIELDS,
    RCLONE_TYPES,
    BackupDestinationManager,
    backend_fields,
    destination_host,
)

SERVICE_ACCOUNT = json.dumps(
    {
        "type": "service_account",
        "project_id": "noust-backups",
        "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkq\n-----END PRIVATE KEY-----\n",
        "client_email": "noust@noust-backups.iam.gserviceaccount.com",
    },
    indent=2,
)


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def manager(runner: FakeRunner) -> BackupDestinationManager:
    runner.only_knows("rclone")
    return BackupDestinationManager(runner=runner)


def test_every_backend_has_at_most_one_secret_field_because_the_command_line_reads_only_one() -> (
    None
):
    for backend, fields in BACKEND_FIELDS.items():
        secrets = [field.key for field in fields if field.secret]
        assert len(secrets) <= 1, (backend, secrets)


def test_every_name_rclone_knows_a_backend_by_is_one_noust_offers() -> None:
    assert set(RCLONE_TYPES) <= set(BACKEND_FIELDS)
    assert RCLONE_TYPES["gcs"] == "google cloud storage"


class TestAzureBlob:
    def test_the_account_key_is_a_secret_rclone_reads_as_it_is(
        self, manager: BackupDestinationManager
    ) -> None:
        destination = manager.add(
            "azure",
            "azureblob",
            {"account": "noustbackups", "key": "a2V5LW9mLXRoZS1hY2NvdW50", "path": "backups/noust"},
        )

        assert destination.settings == {"account": "noustbackups", "path": "backups/noust"}
        env = manager.remote_env("azure")
        assert env["RCLONE_CONFIG_AZURE_TYPE"] == "azureblob"
        assert env["RCLONE_CONFIG_AZURE_ACCOUNT"] == "noustbackups"
        assert env["RCLONE_CONFIG_AZURE_KEY"] == "a2V5LW9mLXRoZS1hY2NvdW50"

    def test_the_account_and_the_key_are_both_needed(
        self, manager: BackupDestinationManager
    ) -> None:
        with pytest.raises(BackupError):
            manager.add("azure", "azureblob", {"account": "noustbackups"})
        with pytest.raises(BackupError):
            manager.add("azure", "azureblob", {"key": "a2V5"})


class TestGoogleCloudStorage:
    def test_the_service_account_json_is_kept_in_the_secret_store_and_never_in_the_settings(
        self, manager: BackupDestinationManager
    ) -> None:
        manager.add(
            "gcs",
            "gcs",
            {"service_account_credentials": SERVICE_ACCOUNT, "path": "my-bucket/noust"},
        )

        stored = get_store().get_backup_destination("gcs")
        assert stored is not None
        assert "private_key" not in json.dumps(stored.settings)
        kept = SecretStore().read_protected_json("backup-destinations/gcs")
        assert json.loads(kept["service_account_credentials"])["project_id"] == "noust-backups"

    def test_rclone_is_told_the_backend_by_its_own_name_and_given_the_json_whole(
        self, manager: BackupDestinationManager
    ) -> None:
        manager.add(
            "gcs",
            "gcs",
            {
                "service_account_credentials": SERVICE_ACCOUNT,
                "bucket_policy_only": "true",
                "path": "my-bucket/noust",
            },
        )

        env = manager.remote_env("gcs")

        assert env["RCLONE_CONFIG_GCS_TYPE"] == "google cloud storage"
        assert env["RCLONE_CONFIG_GCS_SERVICE_ACCOUNT_CREDENTIALS"] == SERVICE_ACCOUNT
        assert env["RCLONE_CONFIG_GCS_BUCKET_POLICY_ONLY"] == "true"

    def test_the_json_never_reaches_a_command_line(
        self, manager: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        manager.add(
            "gcs",
            "gcs",
            {"service_account_credentials": SERVICE_ACCOUNT, "path": "my-bucket/noust"},
        )
        manager.remote_env("gcs")

        for call in runner.calls:
            assert not any("private_key" in part or "MIIEvQIBADANBgkq" in part for part in call)

    def test_uniform_access_is_true_or_false_and_nothing_else(
        self, manager: BackupDestinationManager
    ) -> None:
        with pytest.raises(BackupError):
            manager.add(
                "gcs",
                "gcs",
                {"service_account_credentials": SERVICE_ACCOUNT, "bucket_policy_only": "maybe"},
            )


class TestFtp:
    FIELDS = {
        "host": "ftp.example.com",
        "user": "noust",
        "pass": "ftp-password-value",
        "explicit_tls": "true",
    }

    def test_the_password_is_obscured_through_stdin_like_every_rclone_password(
        self, manager: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        manager.add("files", "ftp", dict(self.FIELDS))
        runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")

        env = manager.remote_env("files")

        assert env["RCLONE_CONFIG_FILES_TYPE"] == "ftp"
        assert env["RCLONE_CONFIG_FILES_HOST"] == "ftp.example.com"
        assert env["RCLONE_CONFIG_FILES_PASS"] == "OBSCURED"
        assert env["RCLONE_CONFIG_FILES_EXPLICIT_TLS"] == "true"
        assert "ftp-password-value" in runner.inputs
        for call in runner.calls:
            assert "ftp-password-value" not in call

    def test_whether_to_use_ftps_must_be_said_because_plain_ftp_sends_the_password_in_clear(
        self, manager: BackupDestinationManager
    ) -> None:
        without = {key: value for key, value in self.FIELDS.items() if key != "explicit_tls"}

        with pytest.raises(BackupError):
            manager.add("files", "ftp", without)

        plain = manager.add("files", "ftp", {**self.FIELDS, "explicit_tls": "false"})
        assert plain.settings["explicit_tls"] == "false"

    def test_its_host_is_judged_like_every_other_a_destination_connects_to(self) -> None:
        assert destination_host("ftp", {"host": "ftp.example.com"}) == "ftp.example.com"
        assert destination_host("ftp", {"host": "10.0.0.5:2121"}) == "10.0.0.5"

    def test_a_host_inside_the_machines_own_network_is_refused_until_it_is_allowed(
        self, manager: BackupDestinationManager
    ) -> None:
        with pytest.raises(BackupError):
            manager.add("files", "ftp", {**self.FIELDS, "host": "127.0.0.1"})


class TestBox:
    def test_the_token_is_a_secret_pasted_from_rclone_authorize(
        self, manager: BackupDestinationManager
    ) -> None:
        manager.add("box", "box", {"token": '{"access_token":"x","expiry":"2030-01-01"}'})

        env = manager.remote_env("box")

        assert env["RCLONE_CONFIG_BOX_TYPE"] == "box"
        assert env["RCLONE_CONFIG_BOX_TOKEN"].startswith('{"access_token"')
        assert [field.key for field in backend_fields("box") if field.secret] == ["token"]


class TestS3Providers:
    @pytest.mark.parametrize("provider", ["DigitalOcean", "IDrive", "Ceph"])
    def test_a_provider_of_the_form_is_accepted_with_its_endpoint(
        self, manager: BackupDestinationManager, provider: str
    ) -> None:
        destination = manager.add(
            "bucket",
            "s3",
            {
                "provider": provider,
                "access_key_id": "AKIAEXAMPLE",
                "secret_access_key": "the-secret-access-key",
                "endpoint": "https://s3.example.com",
                "path": "my-bucket/noust",
            },
        )

        assert destination.settings["provider"] == provider
        assert manager.remote_env("bucket")["RCLONE_CONFIG_BUCKET_PROVIDER"] == provider

    def test_a_provider_outside_the_list_is_passed_on_as_written_because_rclone_knows_many_more(
        self, manager: BackupDestinationManager
    ) -> None:
        manager.add(
            "bucket",
            "s3",
            {
                "provider": "Alibaba",
                "access_key_id": "A",
                "secret_access_key": "B",
                "endpoint": "oss-eu-west-1.aliyuncs.com",
                "path": "my-bucket/noust",
            },
        )

        assert manager.remote_env("bucket")["RCLONE_CONFIG_BUCKET_PROVIDER"] == "Alibaba"
