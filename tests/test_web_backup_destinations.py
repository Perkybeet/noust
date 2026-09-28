# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for backup destinations and their remote operations, through the JSON API.

Three things matter most here, the same three the manager itself is built
around: every mutation (and revealing an encryption key) requires sudo mode,
a listing never carries a secret's value - only whether one is configured -
and taking a backup off this server (push, or a restore sourced from a
destination) is a background job like every other long operation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wasm.core.runner import FakeRunner
from wasm.core.store import App, WASMStore
from wasm.managers.backup_scheduler import BackupScheduler
from wasm.web.auth import CSRF_HEADER_NAME, SecurityConfig
from wasm.web.server import create_app as build_app
from wasm.web.server import get_token_manager


@pytest.fixture
def store(tmp_path: Path) -> Any:
    """A store of this test's own, with one deployed application."""
    WASMStore.reset_instance()
    instance = WASMStore(tmp_path / "wasm.db")
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
        WASMStore.reset_instance()


@pytest.fixture
def app(tmp_path: Path, store: Any, runner: FakeRunner) -> FastAPI:
    """The application, with the fake runner installed process-wide."""
    runner.only_knows("rclone")
    runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")
    return build_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def master_token(app: FastAPI) -> str:
    return get_token_manager().generate_master_token()


@pytest.fixture
def client(app: FastAPI, master_token: str) -> TestClient:
    """A signed-in client, not yet elevated."""
    signed_in = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    response = signed_in.post("/api/auth/login", json={"token": master_token})
    assert response.status_code == 200, response.text
    signed_in.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return signed_in


def elevate(client: TestClient, master_token: str) -> None:
    response = client.post("/api/auth/elevate", json={"token": master_token})
    assert response.status_code == 200, response.text


@pytest.fixture(autouse=True)
def systemd_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the scheduler's unit directory into the sandbox, for every test."""
    path = tmp_path / "systemd"
    path.mkdir()
    monkeypatch.setattr(BackupScheduler, "SYSTEMD_DIR", path)
    return path


SFTP_FIELDS = {"host": "nas.example.com", "user": "wasm", "pass": "s3cr3t-password-value"}


# ---------------------------------------------------------------------------
# Backends and listing
# ---------------------------------------------------------------------------


def test_backends_lists_every_backend_with_its_fields(client: TestClient) -> None:
    response = client.get("/api/backup-destinations/backends")

    assert response.status_code == 200, response.text
    by_name = {entry["backend"]: entry for entry in response.json()["backends"]}
    assert "sftp" in by_name
    pass_field = next(f for f in by_name["sftp"]["fields"] if f["key"] == "pass")
    assert pass_field["secret"] is True


def test_listing_requires_a_session(app: FastAPI) -> None:
    anonymous = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    assert anonymous.get("/api/backup-destinations").status_code in (401, 403)


def test_creating_requires_elevation(client: TestClient) -> None:
    response = client.post(
        "/api/backup-destinations",
        json={"name": "nas", "backend": "sftp", "fields": SFTP_FIELDS},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "elevation_required"


def test_created_destination_is_listed_with_its_secret_redacted(
    client: TestClient, master_token: str
) -> None:
    elevate(client, master_token)
    create = client.post(
        "/api/backup-destinations",
        json={"name": "nas", "backend": "sftp", "fields": SFTP_FIELDS},
    )
    assert create.status_code == 201, create.text
    assert "s3cr3t-password-value" not in create.text

    listing = client.get("/api/backup-destinations")
    assert listing.status_code == 200, listing.text
    assert "s3cr3t-password-value" not in listing.text
    destination = listing.json()["destinations"][0]
    assert destination["settings"]["host"] == "nas.example.com"
    assert "pass" not in destination["settings"]
    assert destination["configured_secret_fields"] == ["pass"]


def test_update_requires_elevation(client: TestClient, master_token: str) -> None:
    elevate(client, master_token)
    client.post(
        "/api/backup-destinations", json={"name": "nas", "backend": "sftp", "fields": SFTP_FIELDS}
    )

    anonymous_put = TestClient(client.app, client=("testclient", 50000), follow_redirects=False)
    anonymous_put.post("/api/auth/login", json={"token": master_token})
    response = anonymous_put.put("/api/backup-destinations/nas", json={"fields": {"host": "new"}})
    assert response.status_code == 403


def test_update_changes_a_field_and_keeps_blank_secrets(
    client: TestClient, master_token: str
) -> None:
    elevate(client, master_token)
    client.post(
        "/api/backup-destinations", json={"name": "nas", "backend": "sftp", "fields": SFTP_FIELDS}
    )

    response = client.put("/api/backup-destinations/nas", json={"fields": {"host": "new-host"}})

    assert response.status_code == 200, response.text
    assert response.json()["destination"]["settings"]["host"] == "new-host"
    assert response.json()["destination"]["configured_secret_fields"] == ["pass"]


def test_delete_requires_elevation(client: TestClient, master_token: str) -> None:
    elevate(client, master_token)
    client.post(
        "/api/backup-destinations", json={"name": "nas", "backend": "sftp", "fields": SFTP_FIELDS}
    )

    anonymous = TestClient(client.app, client=("testclient", 50000), follow_redirects=False)
    anonymous.post("/api/auth/login", json={"token": master_token})
    assert anonymous.delete("/api/backup-destinations/nas").status_code == 403


def test_delete_removes_it(client: TestClient, master_token: str) -> None:
    elevate(client, master_token)
    client.post(
        "/api/backup-destinations", json={"name": "nas", "backend": "sftp", "fields": SFTP_FIELDS}
    )

    response = client.delete("/api/backup-destinations/nas")

    assert response.status_code == 200, response.text
    assert client.get("/api/backup-destinations").json()["total"] == 0


def test_delete_refuses_when_a_schedule_references_it_and_returns_the_error_verbatim(
    client: TestClient, master_token: str
) -> None:
    elevate(client, master_token)
    client.post(
        "/api/backup-destinations", json={"name": "nas", "backend": "sftp", "fields": SFTP_FIELDS}
    )
    client.post(
        "/api/backup-schedules",
        json={
            "domain": "shop.example.com",
            "destinations": [{"name": "nas"}],
        },
    )

    response = client.delete("/api/backup-destinations/nas")

    assert response.status_code >= 400
    assert "nas" in response.json()["detail"]


def test_test_endpoint_reaches_the_destination(
    client: TestClient, master_token: str, runner: FakeRunner
) -> None:
    elevate(client, master_token)
    client.post(
        "/api/backup-destinations", json={"name": "nas", "backend": "sftp", "fields": SFTP_FIELDS}
    )
    runner.script(
        ("rclone", "lsf", "nas:wasm-backups", "--max-depth", "1"), stdout="shop-example-com/\n"
    )

    response = client.post("/api/backup-destinations/nas/test")

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "entries": ["shop-example-com/"]}


class TestShowKey:
    def test_requires_elevation(self, client: TestClient, master_token: str) -> None:
        elevate(client, master_token)
        client.post(
            "/api/backup-destinations",
            json={"name": "nas", "backend": "sftp", "fields": SFTP_FIELDS, "encrypted": True},
        )
        anonymous = TestClient(client.app, client=("testclient", 50000), follow_redirects=False)
        anonymous.post("/api/auth/login", json={"token": master_token})
        assert anonymous.post("/api/backup-destinations/nas/show-key").status_code == 403

    def test_returns_both_passphrases_for_an_encrypted_destination(
        self, client: TestClient, master_token: str
    ) -> None:
        elevate(client, master_token)
        client.post(
            "/api/backup-destinations",
            json={"name": "nas", "backend": "sftp", "fields": SFTP_FIELDS, "encrypted": True},
        )

        response = client.post("/api/backup-destinations/nas/show-key")

        assert response.status_code == 200, response.text
        assert response.json()["password"]
        assert response.json()["password2"]

    def test_refuses_for_an_unencrypted_destination(
        self, client: TestClient, master_token: str
    ) -> None:
        elevate(client, master_token)
        client.post(
            "/api/backup-destinations",
            json={"name": "nas", "backend": "sftp", "fields": SFTP_FIELDS},
        )

        response = client.post("/api/backup-destinations/nas/show-key")

        assert response.status_code >= 400


# ---------------------------------------------------------------------------
# Push and restore, as jobs
# ---------------------------------------------------------------------------


@pytest.fixture
def queued_backups(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture jobs queued through wasm.web.api.backups without running them."""
    return _capture_jobs(monkeypatch, "wasm.web.api.backups.get_job_manager")


@pytest.fixture
def queued_destinations(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture jobs queued through wasm.web.api.backup_destinations without running them."""
    return _capture_jobs(monkeypatch, "wasm.web.api.backup_destinations.get_job_manager")


def _capture_jobs(monkeypatch: pytest.MonkeyPatch, target: str) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def create_job(**kwargs: Any) -> Any:
        captured.append(kwargs)
        return type(
            "Queued",
            (),
            {
                "id": "job-1",
                "status": type("S", (), {"value": "pending"})(),
                "to_dict": lambda self: {"id": "job-1"},
            },
        )()

    monkeypatch.setattr(target, lambda: type("M", (), {"create_job": staticmethod(create_job)})())
    return captured


def test_push_requires_elevation(client: TestClient) -> None:
    response = client.post(
        "/api/backups/shop-example-com_20260101_000000/push", json={"destination": "nas"}
    )
    assert response.status_code == 403


def test_push_queues_a_job_naming_the_backup_and_destination(
    client: TestClient,
    master_token: str,
    monkeypatch: pytest.MonkeyPatch,
    queued_backups: list[dict[str, Any]],
) -> None:
    elevate(client, master_token)

    class FakeBackup:
        id = "shop-example-com_20260101_000000"
        domain = "shop.example.com"

    class FakeManager:
        def __init__(self, verbose: bool = False) -> None:
            pass

        def get_backup(self, backup_id: str) -> FakeBackup:
            return FakeBackup()

    monkeypatch.setattr("wasm.web.api.backups.BackupManager", FakeManager)

    response = client.post(
        "/api/backups/shop-example-com_20260101_000000/push", json={"destination": "nas"}
    )

    assert response.status_code == 202, response.text
    assert queued_backups[0]["kwargs"] == {
        "backup_id": "shop-example-com_20260101_000000",
        "destination_name": "nas",
    }


def test_restore_from_destination_requires_elevation(client: TestClient) -> None:
    response = client.post(
        "/api/backup-destinations/nas/backups/shop-example-com_20260101_000000/restore"
    )
    assert response.status_code == 403


def test_restore_from_destination_derives_the_app_name_from_the_backup_id(
    client: TestClient, master_token: str, queued_destinations: list[dict[str, Any]]
) -> None:
    elevate(client, master_token)

    response = client.post(
        "/api/backup-destinations/nas/backups/shop-example-com_20260101_000000/restore"
    )

    assert response.status_code == 202, response.text
    assert queued_destinations[0]["kwargs"]["app_name"] == "shop-example-com"
    assert queued_destinations[0]["kwargs"]["destination_name"] == "nas"


def test_restore_from_destination_400s_when_the_app_cannot_be_derived(
    client: TestClient, master_token: str
) -> None:
    elevate(client, master_token)

    response = client.post("/api/backup-destinations/nas/backups/not-a-wasm-id/restore")

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Schedules gain destinations and PUT
# ---------------------------------------------------------------------------


def test_schedule_create_accepts_destinations(client: TestClient, master_token: str) -> None:
    elevate(client, master_token)
    client.post(
        "/api/backup-destinations", json={"name": "nas", "backend": "sftp", "fields": SFTP_FIELDS}
    )

    response = client.post(
        "/api/backup-schedules",
        json={
            "domain": "shop.example.com",
            "destinations": [{"name": "nas", "retention_count": 3}],
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["schedule"]["destinations"] == [
        {"name": "nas", "retention_count": 3, "retention_days": None}
    ]


def test_schedule_put_replaces_it(client: TestClient, master_token: str) -> None:
    elevate(client, master_token)
    client.post("/api/backup-schedules", json={"domain": "shop.example.com", "schedule": "daily"})

    response = client.put(
        "/api/backup-schedules/shop.example.com",
        json={"domain": "shop.example.com", "schedule": "weekly", "retention_count": 2},
    )

    assert response.status_code == 200, response.text
    assert response.json()["schedule"]["schedule"] == "weekly"
    assert response.json()["schedule"]["on_calendar"] == "Mon *-*-* 02:00:00"
    assert response.json()["schedule"]["retention_count"] == 2


def test_schedule_put_domain_mismatch_is_refused(client: TestClient, master_token: str) -> None:
    elevate(client, master_token)
    client.post("/api/backup-schedules", json={"domain": "shop.example.com", "schedule": "daily"})

    response = client.put(
        "/api/backup-schedules/shop.example.com",
        json={"domain": "other.example.com", "schedule": "daily"},
    )

    assert response.status_code == 400
