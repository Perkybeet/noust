# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/self-backup`` (3.4, checklist H-27): looking after Noust's own state over HTTP.

What the console builds on:

- which bundles there are and how the schedule is going, without where they are
  on disk and without the passphrase;
- a schedule is a root timer that carries every secret away and keeps a
  passphrase for the timer, so setting or removing it needs sudo mode;
- taking, proving, sending and running are jobs, refused up front when they
  cannot work (no passphrase, no such bundle);
- there is no way to download a bundle or to restore one from here.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.ens import envelope
from noust.core.runner import set_runner
from noust.core.store import NoustStore
from noust.managers import self_backup
from noust.managers.self_backup import SelfBackupManager
from noust.web.api import self_backup as api
from tests.test_self_backup import PASSPHRASE, FakeTimers, Runner, Server, origin
from tests.test_web_databases_api import (  # noqa: F401
    _capture_queued_jobs,
    anonymous,
    app,
    client,
    elevate,
)

# ruff: noqa: F811

BASE = "/api/self-backup"
pytestmark = pytest.mark.allow_subprocess


@pytest.fixture(autouse=True)
def cheap_scrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(envelope, "SCRYPT_N", 2**10)


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def runner(tmp_path: Path) -> Iterator[Runner]:
    fake = Runner(tmp_path / "remote")
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture
def old(tmp_path: Path, runner: Runner, monkeypatch: pytest.MonkeyPatch) -> Iterator[Server]:
    """A server with state worth keeping, and the manager's places pointed at it."""
    made = origin(tmp_path)
    timers = FakeTimers()
    monkeypatch.setattr(
        SelfBackupManager, "_labels", lambda self: {"config": made.config, "state": made.state}
    )
    monkeypatch.setattr(SelfBackupManager, "directory", property(lambda self: made.bundles))
    monkeypatch.setattr(SelfBackupManager, "scheduler", property(lambda self: timers))
    monkeypatch.setattr(self_backup, "SYSTEMD_DIR", made.units)
    monkeypatch.setattr("noust.managers.cert_manager.CertManager.LETSENCRYPT_DIR", made.letsencrypt)
    yield made
    for connection in made.held:
        connection.close()


def take(old: Server) -> str:
    """Take a bundle through the job the API queues, and return its name."""
    return str(api.create_job(PASSPHRASE)["name"])


class TestOverview:
    def test_nothing_is_configured_and_nothing_is_listed_at_first(
        self, client: TestClient, old: Server
    ) -> None:
        body = client.get(BASE).json()

        assert body["bundles"] == []
        assert body["schedule"]["configured"] is False

    def test_a_bundle_is_listed_without_where_it_is_or_its_passphrase(
        self, client: TestClient, old: Server
    ) -> None:
        name = take(old)

        (bundle,) = client.get(BASE).json()["bundles"]

        assert bundle["name"] == name and len(bundle["sha256"]) == 64
        assert bundle["kind"] == "manual" and bundle["scheduled"] is False
        assert "path" not in bundle
        assert str(old.bundles) not in json.dumps(bundle)
        assert PASSPHRASE not in json.dumps(client.get(BASE).json())

    def test_nothing_is_open_without_a_session(self, anonymous: TestClient, old: Server) -> None:
        for response in (
            anonymous.get(BASE),
            anonymous.post(BASE, json={}),
            anonymous.put(f"{BASE}/schedule", json={}),
            anonymous.delete(f"{BASE}/x"),
        ):
            assert response.status_code in (401, 403)

    def test_a_bundle_is_not_downloadable_nor_restorable_from_here(
        self, client: TestClient, old: Server
    ) -> None:
        name = take(old)

        assert client.get(f"{BASE}/{name}").status_code in (404, 405)
        assert client.post(f"{BASE}/{name}/restore", json={}).status_code in (404, 405)


class TestSchedule:
    def test_it_is_set_read_and_removed_with_sudo_mode(
        self, client: TestClient, old: Server
    ) -> None:
        elevate(client)

        put = client.put(
            f"{BASE}/schedule",
            json={"schedule": "weekly", "retention_count": 4, "passphrase": PASSPHRASE},
        )

        assert put.status_code == 200, put.text
        body = put.json()
        assert body["configured"] is True and body["passphrase_stored"] is True
        assert body["schedule"] == "Mon *-*-* 02:00:00" and body["timer"]["installed"] is True
        assert PASSPHRASE not in put.text
        assert client.get(BASE).json()["schedule"]["passphrase_stored"] is True

        deleted = client.delete(f"{BASE}/schedule")

        assert deleted.json()["success"] is True
        assert client.get(BASE).json()["schedule"]["configured"] is False

    def test_it_keeps_its_bundles_by_size_too(self, client: TestClient, old: Server) -> None:
        elevate(client)

        put = client.put(f"{BASE}/schedule", json={"retention_gb": 1.5, "passphrase": PASSPHRASE})

        assert put.status_code == 200, put.text
        assert put.json()["retention_gb"] == 1.5
        assert client.get(BASE).json()["schedule"]["retention_gb"] == 1.5
        refused = client.put(
            f"{BASE}/schedule", json={"retention_gb": -1, "passphrase": PASSPHRASE}
        )
        assert refused.status_code == 422

    def test_a_schedule_that_keeps_nothing_here_needs_a_destination(
        self, client: TestClient, old: Server
    ) -> None:
        elevate(client)

        refused = client.put(
            f"{BASE}/schedule", json={"remote_only": True, "passphrase": PASSPHRASE}
        )

        assert refused.status_code == 400, refused.text
        assert "destination" in refused.text
        assert client.get(BASE).json()["schedule"]["configured"] is False
        kept = client.put(f"{BASE}/schedule", json={"passphrase": PASSPHRASE})
        assert kept.status_code == 200, kept.text
        assert kept.json()["remote_only"] is False

    def test_setting_or_removing_it_needs_sudo_mode(self, client: TestClient, old: Server) -> None:
        put = client.put(f"{BASE}/schedule", json={"passphrase": PASSPHRASE})
        delete = client.delete(f"{BASE}/schedule")

        assert put.status_code == 403 and delete.status_code == 403
        assert put.json()["error"] == "elevation_required"

    @pytest.mark.parametrize(
        ("body", "status"),
        [
            ({"schedule": "daily\nExecStart=/bin/evil", "passphrase": PASSPHRASE}, 422),
            ({"retention_count": 0, "passphrase": PASSPHRASE}, 422),
            ({"passphrase": "short"}, 422),
            ({}, 400),
            ({"passphrase": PASSPHRASE, "destinations": [{"name": "nowhere"}]}, 400),
        ],
    )
    def test_an_unusable_schedule_is_refused(
        self, client: TestClient, old: Server, body: dict[str, Any], status: int
    ) -> None:
        elevate(client)

        response = client.put(f"{BASE}/schedule", json=body)

        assert response.status_code == status, response.text
        assert client.get(BASE).json()["schedule"]["configured"] is False


class TestJobs:
    def test_taking_one_is_a_job_that_needs_a_passphrase_from_somewhere(
        self, client: TestClient, old: Server, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        created = _capture_queued_jobs(monkeypatch)

        none = client.post(BASE, json={})
        given = client.post(BASE, json={"passphrase": PASSPHRASE})

        assert none.status_code == 400 and "No passphrase" in none.json()["detail"]
        assert given.status_code == 202 and len(created) == 1
        assert created[0]["func"] is api.create_job

    def test_running_the_schedule_needs_a_stored_passphrase(
        self, client: TestClient, old: Server, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        created = _capture_queued_jobs(monkeypatch)

        refused = client.post(f"{BASE}/run")
        elevate(client)
        client.put(f"{BASE}/schedule", json={"passphrase": PASSPHRASE})
        accepted = client.post(f"{BASE}/run")

        assert refused.status_code == 400
        assert accepted.status_code == 202 and created[0]["func"] is api.run_job

    def test_the_run_job_takes_proves_and_records(self, client: TestClient, old: Server) -> None:
        elevate(client)
        client.put(f"{BASE}/schedule", json={"passphrase": PASSPHRASE})

        result = api.run_job()

        assert result["verification"]["ok"] is True
        listed = client.get(BASE).json()
        assert listed["bundles"][0]["scheduled"] is True
        assert listed["schedule"]["last_status"] == "ok"

    def test_proving_is_a_job_and_an_unknown_bundle_is_a_404(
        self, client: TestClient, old: Server, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        name = take(old)
        created = _capture_queued_jobs(monkeypatch)

        ok = client.post(f"{BASE}/{name}/verify", json={"passphrase": PASSPHRASE})
        unknown = client.post(
            f"{BASE}/noust-self-nope-20260101T000000Z.tar.gz.enc/verify",
            json={"passphrase": PASSPHRASE},
        )
        no_passphrase = client.post(f"{BASE}/{name}/verify", json={})

        assert ok.status_code == 202 and created[0]["func"] is api.verify_job
        assert unknown.status_code == 404
        assert no_passphrase.status_code == 400

    def test_the_verify_job_reports_and_a_wrong_passphrase_fails_it(
        self, client: TestClient, old: Server
    ) -> None:
        name = take(old)

        report = api.verify_job(name, PASSPHRASE)

        assert report["ok"] is True
        with pytest.raises(envelope.EnvelopeError):
            api.verify_job(name, "not the passphrase")

    def test_sending_is_a_job(
        self, client: TestClient, old: Server, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        name = take(old)
        created = _capture_queued_jobs(monkeypatch)

        response = client.post(f"{BASE}/{name}/push", json={"destination": "nas"})

        assert response.status_code == 202
        assert created[0]["kwargs"]["name"] == name and created[0]["kwargs"]["destination"] == "nas"
        assert (
            client.post(
                f"{BASE}/nope-20260101.tar.gz.enc/push", json={"destination": "nas"}
            ).status_code
            == 404
        )

    def test_deleting_needs_sudo_mode_and_removes_it(self, client: TestClient, old: Server) -> None:
        name = take(old)

        refused = client.delete(f"{BASE}/{name}")
        elevate(client)
        deleted = client.delete(f"{BASE}/{name}")

        assert refused.status_code == 403 and deleted.json()["success"] is True
        assert client.get(BASE).json()["bundles"] == []
        assert client.delete(f"{BASE}/{name}").status_code == 404
