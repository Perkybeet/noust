# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The 2.2 backup safety fixes, through the CLI and the JSON API.

The managers enforce each rule (see ``test_backup_safety_22.py``); these tests
pin what an operator sees: a secret refused in ``--field``, an existing key
accepted with ``--key-stdin`` or ``encryption_key``, an encrypted destination
whose removal prints or requires its key, and a schedule whose retention stays
null - "server default" - instead of turning into 7/30 when it is edited.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.cli.app import Context
from noust.cli.commands.backup import cli
from noust.core.runner import FakeRunner
from noust.core.store import App, BackupScheduleRecord, NoustStore, get_store
from noust.managers.backup_destinations import BackupDestinationManager
from noust.managers.backup_scheduler import BackupScheduler
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app as build_app
from noust.web.server import get_token_manager

SFTP_FIELDS = {"host": "nas.example.com", "user": "wasm", "pass": "s3cr3t-password-value"}
KEY = {"password": "old-server-passphrase", "password2": "old-server-salt"}


@pytest.fixture(autouse=True)
def _rclone(runner: FakeRunner) -> None:
    runner.only_knows("rclone")
    runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")


@pytest.fixture(autouse=True)
def systemd_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "systemd"
    path.mkdir()
    monkeypatch.setattr(BackupScheduler, "SYSTEMD_DIR", path)
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


def invoke(argv: list[str], **kwargs: Any) -> Any:
    return CliRunner().invoke(cli, argv, obj=Context(), **kwargs)


def _add_args(*extra: str) -> list[str]:
    return [
        "backup",
        "destination",
        "add",
        "nas",
        "--type",
        "sftp",
        "--field",
        "host=nas.example.com",
        "--field",
        "user=wasm",
        *extra,
    ]


@pytest.mark.usefixtures("cli_store")
class TestCli:
    def test_a_secret_in_field_is_refused(self) -> None:
        result = invoke(_add_args("--field", "pass=hunter2"))

        assert result.exit_code != 0
        assert "--stdin" in result.output
        assert get_store().get_backup_destination("nas") is None

    def test_a_secret_in_field_is_refused_on_update(self) -> None:
        BackupDestinationManager().add("nas", "sftp", SFTP_FIELDS)

        result = invoke(["backup", "destination", "update", "nas", "--field", "pass=hunter2"])

        assert result.exit_code != 0
        assert "--prompt" in result.output

    def test_key_stdin_takes_what_show_key_printed(self) -> None:
        printed = (
            "! Encryption keys for nas. Store them somewhere safe.\n"
            f"i   password:  {KEY['password']}\n"
            f"i   password2: {KEY['password2']}\n"
        )

        # An SFTP password is optional (a key file can stand in), so standard
        # input carries only the encryption key here.
        result = invoke(_add_args("--key-stdin"), input=printed)

        assert result.exit_code == 0, result.output
        manager = BackupDestinationManager()
        assert manager.get("nas") is not None
        assert manager.get("nas").encrypted is True  # type: ignore[union-attr]
        assert manager.show_key("nas") == KEY

    def test_key_stdin_and_stdin_cannot_share_stdin(self) -> None:
        result = invoke(_add_args("--stdin", "--key-stdin"), input="x\n")

        assert result.exit_code != 0
        assert "--prompt" in result.output

    def test_removing_an_encrypted_destination_prints_its_key(self) -> None:
        BackupDestinationManager().add("nas", "sftp", SFTP_FIELDS, encrypted=True, crypt_key=KEY)

        result = invoke(["backup", "destination", "remove", "nas"], input="y\n")

        assert result.exit_code == 0, result.output
        assert "cannot be read" in result.output
        assert KEY["password"] in result.output
        assert KEY["password2"] in result.output
        assert get_store().get_backup_destination("nas") is None

    def test_declining_keeps_an_encrypted_destination_and_prints_nothing(self) -> None:
        BackupDestinationManager().add("nas", "sftp", SFTP_FIELDS, encrypted=True, crypt_key=KEY)

        result = invoke(["backup", "destination", "remove", "nas"], input="n\n")

        assert KEY["password"] not in result.output
        assert get_store().get_backup_destination("nas") is not None

    def test_schedule_update_keeps_null_retention(self) -> None:
        get_store().save_backup_schedule(
            BackupScheduleRecord(app_domain="shop.example.com", schedule="daily")
        )

        result = invoke(
            ["backup", "schedule", "update", "shop.example.com", "--schedule", "weekly"]
        )

        assert result.exit_code == 0, result.output
        record = get_store().get_backup_schedule("shop.example.com")
        assert record is not None
        assert record.retention_count is None
        assert record.retention_days is None
        assert "server default" in result.output

    def test_schedule_create_accepts_the_server_default(self) -> None:
        result = invoke(
            [
                "backup",
                "schedule",
                "create",
                "shop.example.com",
                "--retention-count",
                "default",
                "--retention-days",
                "none",
            ]
        )

        assert result.exit_code == 0, result.output
        record = get_store().get_backup_schedule("shop.example.com")
        assert record is not None
        assert (record.retention_count, record.retention_days) == (None, None)

    def test_schedule_create_still_defaults_to_7_and_30(self) -> None:
        result = invoke(["backup", "schedule", "create", "shop.example.com"])

        assert result.exit_code == 0, result.output
        record = get_store().get_backup_schedule("shop.example.com")
        assert record is not None
        assert (record.retention_count, record.retention_days) == (7, 30)
        assert "never touched" in result.output

    def test_a_retention_of_zero_is_refused(self) -> None:
        result = invoke(
            ["backup", "schedule", "create", "shop.example.com", "--retention-count", "0"]
        )
        assert result.exit_code != 0

    def test_remote_restore_confirmation_names_the_application(self) -> None:
        BackupDestinationManager().add("nas", "sftp", SFTP_FIELDS)

        result = invoke(
            ["backup", "restore", "shop-example-com_20260101_000000", "--from", "nas"],
            input="n\n",
        )

        assert "into the application shop-example-com" in result.output


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    instance.create_app(
        App(
            domain="shop.example.com",
            app_type="static",
            source="https://github.com/you/shop",
            port=3000,
            app_path="/var/www/apps/shop-example-com",
            status="running",
        )
    )
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def app(tmp_path: Path, store: NoustStore) -> FastAPI:
    return build_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    token = get_token_manager().generate_master_token()
    signed_in = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    response = signed_in.post("/api/auth/login", json={"token": token})
    assert response.status_code == 200, response.text
    signed_in.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    elevated = signed_in.post("/api/auth/elevate", json={"token": token})
    assert elevated.status_code == 200, elevated.text
    return signed_in


class TestApi:
    def test_create_with_an_existing_key(self, client: TestClient) -> None:
        response = client.post(
            "/api/backup-destinations",
            json={
                "name": "nas",
                "backend": "sftp",
                "fields": SFTP_FIELDS,
                "encrypted": True,
                "encryption_key": KEY,
            },
        )

        assert response.status_code == 201, response.text
        assert KEY["password"] not in response.text
        assert client.post("/api/backup-destinations/nas/show-key").json() == KEY

    def test_delete_of_an_encrypted_destination_needs_key_saved(self, client: TestClient) -> None:
        client.post(
            "/api/backup-destinations",
            json={"name": "nas", "backend": "sftp", "fields": SFTP_FIELDS, "encrypted": True},
        )

        refused = client.delete("/api/backup-destinations/nas")
        assert refused.status_code >= 400
        assert "show-key" in refused.text
        assert client.get("/api/backup-destinations").json()["total"] == 1

        removed = client.delete("/api/backup-destinations/nas", params={"key_saved": "true"})
        assert removed.status_code == 200, removed.text
        assert client.get("/api/backup-destinations").json()["total"] == 0

    def test_browsing_with_a_path_as_app_is_refused(self, client: TestClient) -> None:
        client.post(
            "/api/backup-destinations",
            json={"name": "nas", "backend": "sftp", "fields": SFTP_FIELDS},
        )

        response = client.get("/api/backup-destinations/nas/backups", params={"app": "../.."})

        assert response.status_code >= 400
        assert "Invalid application name" in response.text

    def test_a_schedule_can_be_saved_with_null_retention(
        self, client: TestClient, runner: FakeRunner
    ) -> None:
        response = client.put(
            "/api/backup-schedules/shop.example.com",
            json={
                "domain": "shop.example.com",
                "schedule": "daily",
                "retention_count": None,
                "retention_days": None,
                "include_databases": False,
            },
        )

        assert response.status_code == 200, response.text
        record = get_store().get_backup_schedule("shop.example.com")
        assert record is not None
        assert (record.retention_count, record.retention_days) == (None, None)
        assert record.include_databases is False
        echoed = response.json()["schedule"]
        assert echoed["retention_count"] is None

    def test_listing_carries_the_server_default_and_include_databases(
        self, client: TestClient, runner: FakeRunner
    ) -> None:
        get_store().save_backup_schedule(
            BackupScheduleRecord(
                app_domain="shop.example.com", schedule="*-*-* 02:00:00", include_databases=False
            )
        )
        runner.script(
            ("systemctl", "list-timers"),
            stdout="n/a n/a noust-backup-shop-example-com.timer x.service\n",
        )
        runner.script(
            ("systemctl", "show"),
            stdout="Description=Noust backup timer for shop.example.com\n",
        )

        listing = client.get("/api/backup-schedules").json()

        assert listing["default_retention_count"] >= 1
        schedule = listing["schedules"][0]
        assert schedule["retention_count"] is None
        assert schedule["include_databases"] is False
