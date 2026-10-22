# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/volume-backups`` (3.4, checklist H-23): an application's data over HTTP.

What the console builds on:

- what an application declares as its data is listed, and a policy is saved
  with its timer, read back and removed, which needs sudo mode;
- taking, checking, sending, restoring and running a policy are jobs, and
  anything that can be checked is refused before the job exists;
- an unknown application or backup is a 404, and what a restore replaces and
  a deletion removes needs sudo mode.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.runner import set_runner
from noust.core.store import App, NoustStore, get_store
from noust.managers.volume_backups import VolumeBackupManager
from noust.web.api import volume_backups as api
from tests.database_backup_support import RcloneRunner
from tests.test_volume_backups import FakeScheduler, FakeServices
from tests.test_web_databases_api import (  # noqa: F401
    _capture_queued_jobs,
    anonymous,
    app,
    client,
    elevate,
)

# ruff: noqa: F811

BASE = "/api/volume-backups"
DOMAIN = "shop.example.com"
APP = "shop-example-com"


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def runner(tmp_path: Path) -> Iterator[RcloneRunner]:
    fake = RcloneRunner(tmp_path / "remote")
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture
def shop(tmp_path: Path, runner: RcloneRunner, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A release application with an uploads directory, the manager's edges faked."""
    root = tmp_path / "apps" / APP
    (root / "shared" / "uploads").mkdir(parents=True)
    (root / "shared" / "uploads" / "a.txt").write_text("alpha")
    (root / "releases" / "r1").mkdir(parents=True)
    (root / "current").symlink_to(root / "releases" / "r1")
    get_store().create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            app_path=str(root),
            layout="releases",
            persistent_paths=["uploads"],
        )
    )
    scheduler = FakeScheduler()
    monkeypatch.setattr(
        VolumeBackupManager, "root", property(lambda self: tmp_path / "backups" / "volumes")
    )
    monkeypatch.setattr(VolumeBackupManager, "scheduler", property(lambda self: scheduler))
    monkeypatch.setattr(VolumeBackupManager, "services", property(lambda self: FakeServices()))
    monkeypatch.setattr(VolumeBackupManager, "_gate", lambda self, app: (True, ""))
    return root


def take(shop: Path) -> str:
    """Back the uploads up through the job the API queues, and return the id."""
    result = api.create_job(DOMAIN, ["path:uploads"], [], "live", "")
    return str(result["backups"][0]["id"])


class TestTargetsAndPolicies:
    def test_the_targets_of_an_application(self, client: TestClient, shop: Path) -> None:
        body = client.get(f"{BASE}/targets/{DOMAIN}").json()

        assert [t["key"] for t in body] == ["path:uploads"]
        assert body[0]["exists"] is True and body[0]["slug"] == "path-uploads"

    def test_an_unknown_application_is_a_404(self, client: TestClient, shop: Path) -> None:
        assert client.get(f"{BASE}/targets/nope.example.com").status_code == 404
        assert client.post(f"{BASE}/policies/nope.example.com/run").status_code == 404

    def test_a_policy_is_set_read_and_removed(self, client: TestClient, shop: Path) -> None:
        assert client.get(f"{BASE}/policies/{DOMAIN}").json()["configured"] is False
        elevate(client)

        put = client.put(
            f"{BASE}/policies/{DOMAIN}",
            json={"schedule": "weekly", "retention_count": 4, "consistency": "pause"},
        )

        assert put.status_code == 200, put.text
        body = put.json()
        assert body["configured"] is True and body["consistency"] == "pause"
        assert body["schedule"] == "Mon *-*-* 02:00:00"
        assert body["timer"]["installed"] is True
        assert client.get(f"{BASE}/policies").json()["total"] == 1

        deleted = client.delete(f"{BASE}/policies/{DOMAIN}")

        assert deleted.json()["success"] is True
        assert client.get(f"{BASE}/policies/{DOMAIN}").json()["configured"] is False

    def test_a_policy_keeps_its_backups_by_size_too(self, client: TestClient, shop: Path) -> None:
        elevate(client)

        put = client.put(
            f"{BASE}/policies/{DOMAIN}",
            json={
                "retention_gb": 2.5,
                "destinations": [{"name": "nas", "retention_count": 3, "retention_gb": 20}],
            },
        )

        assert put.status_code == 400, put.text
        refused = client.put(f"{BASE}/policies/{DOMAIN}", json={"retention_gb": 0})
        assert refused.status_code == 422
        again = client.put(f"{BASE}/policies/{DOMAIN}", json={"retention_gb": 2.5})
        assert again.status_code == 200, again.text
        assert again.json()["retention_gb"] == 2.5
        assert client.get(f"{BASE}/policies/{DOMAIN}").json()["retention_gb"] == 2.5

    def test_a_policy_that_keeps_nothing_here_needs_a_destination(
        self, client: TestClient, shop: Path
    ) -> None:
        elevate(client)

        refused = client.put(f"{BASE}/policies/{DOMAIN}", json={"remote_only": True})

        assert refused.status_code == 400, refused.text
        assert "destination" in refused.text
        assert client.get(f"{BASE}/policies/{DOMAIN}").json()["configured"] is False
        kept = client.put(f"{BASE}/policies/{DOMAIN}", json={})
        assert kept.status_code == 200, kept.text
        assert kept.json()["remote_only"] is False

    def test_setting_or_removing_a_policy_needs_sudo_mode(
        self, client: TestClient, shop: Path
    ) -> None:
        put = client.put(f"{BASE}/policies/{DOMAIN}", json={})
        delete = client.delete(f"{BASE}/policies/{DOMAIN}")

        assert put.status_code == 403 and delete.status_code == 403
        assert put.json()["error"] == "elevation_required"

    @pytest.mark.parametrize(
        ("body", "status"),
        [
            ({"schedule": "daily\nExecStart=/bin/evil"}, 422),
            ({"retention_count": 0}, 422),
            ({"consistency": "freeze"}, 400),
            ({"targets": ["path:nope"]}, 400),
            ({"extra_paths": ["../x"]}, 400),
            ({"destinations": [{"name": "nowhere"}]}, 400),
        ],
    )
    def test_an_unusable_policy_is_refused(
        self, client: TestClient, shop: Path, body: dict[str, Any], status: int
    ) -> None:
        elevate(client)

        response = client.put(f"{BASE}/policies/{DOMAIN}", json=body)

        assert response.status_code == status, response.text
        assert client.get(f"{BASE}/policies/{DOMAIN}").json()["configured"] is False

    def test_running_a_policy_is_a_job(
        self, client: TestClient, shop: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        created = _capture_queued_jobs(monkeypatch)

        response = client.post(f"{BASE}/policies/{DOMAIN}/run")

        assert response.status_code == 202, response.text
        assert created[0]["kwargs"]["domain"] == DOMAIN
        assert created[0]["func"] is api.policy_run_job


class TestBackups:
    def test_creating_is_a_job_checked_first(
        self, client: TestClient, shop: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        created = _capture_queued_jobs(monkeypatch)

        ok = client.post(f"{BASE}", json={"domain": DOMAIN, "targets": ["path:uploads"]})
        bad_target = client.post(f"{BASE}", json={"domain": DOMAIN, "targets": ["path:nope"]})
        bad_mode = client.post(f"{BASE}", json={"domain": DOMAIN, "consistency": "freeze"})
        unknown = client.post(f"{BASE}", json={"domain": "nope.example.com"})

        assert ok.status_code == 202
        assert (bad_target.status_code, bad_mode.status_code) == (400, 400)
        assert unknown.status_code == 404
        assert len(created) == 1

    def test_a_backup_is_listed_read_and_checked(self, client: TestClient, shop: Path) -> None:
        backup_id = take(shop)

        listed = client.get(BASE, params={"domain": DOMAIN}).json()
        one = client.get(f"{BASE}/{backup_id}").json()

        assert listed["total"] == 1 and listed["backups"][0]["id"] == backup_id
        assert one["target"] == "path:uploads" and len(one["checksum"]) == 64
        assert "owner" not in one and "mac" not in one
        verdict = api.verify_job(backup_id, deep=True)
        assert verdict["valid"] is True

    def test_an_unknown_backup_is_a_404(self, client: TestClient, shop: Path) -> None:
        assert client.get(f"{BASE}/nope").status_code == 404
        elevate(client)
        assert client.delete(f"{BASE}/nope").status_code == 404
        assert client.post(f"{BASE}/nope/verify").status_code == 404

    def test_restoring_and_deleting_need_sudo_mode(self, client: TestClient, shop: Path) -> None:
        backup_id = take(shop)

        restore = client.post(f"{BASE}/{backup_id}/restore", json={})
        delete = client.delete(f"{BASE}/{backup_id}")

        assert restore.status_code == 403 and delete.status_code == 403

    def test_restoring_is_a_job_and_puts_the_data_back(
        self, client: TestClient, shop: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backup_id = take(shop)
        (shop / "shared" / "uploads" / "a.txt").write_text("changed")
        created = _capture_queued_jobs(monkeypatch)
        elevate(client)

        response = client.post(f"{BASE}/{backup_id}/restore", json={"safety_backup": False})

        assert response.status_code == 202, response.text
        job = created[0]
        job["func"](**job["kwargs"])
        assert (shop / "shared" / "uploads" / "a.txt").read_text() == "alpha"

    def test_deleting_removes_it(self, client: TestClient, shop: Path) -> None:
        backup_id = take(shop)
        elevate(client)

        assert client.delete(f"{BASE}/{backup_id}").json()["success"] is True
        assert client.get(f"{BASE}/{backup_id}").status_code == 404

    def test_pushing_is_a_job(
        self, client: TestClient, shop: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backup_id = take(shop)
        created = _capture_queued_jobs(monkeypatch)

        response = client.post(f"{BASE}/{backup_id}/push", json={"destination": "nas"})

        assert response.status_code == 202
        assert created[0]["kwargs"]["backup_id"] == backup_id
        assert created[0]["kwargs"]["destination"] == "nas"

    def test_a_restore_from_a_destination_is_a_job_with_sudo_mode(
        self, client: TestClient, shop: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        created = _capture_queued_jobs(monkeypatch)
        body = {
            "destination": "nas",
            "domain": DOMAIN,
            "slug": "path-uploads",
            "file_name": "x.tar.gz",
        }

        refused = client.post(f"{BASE}/restore-remote", json=body)
        elevate(client)
        accepted = client.post(f"{BASE}/restore-remote", json=body)

        assert refused.status_code == 403 and accepted.status_code == 202
        assert created[0]["kwargs"]["slug"] == "path-uploads"

    def test_nothing_is_open_without_a_session(self, anonymous: TestClient, shop: Path) -> None:
        for response in (
            anonymous.get(BASE),
            anonymous.get(f"{BASE}/policies"),
            anonymous.post(BASE, json={"domain": DOMAIN}),
            anonymous.delete(f"{BASE}/whatever"),
        ):
            assert response.status_code in (401, 403)
