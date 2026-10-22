# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Sealing the backups on this server's disk, over the API (checklist H-08).

The same operations as ``noust backup encryption``, through the same objects. What is pinned: the
status says what is sealed and what is not and never a key's value; turning sealing on, rotating
and stopping change how backups are kept, so they ask for sudo mode; showing a key is a secret read,
asks for the permission that reads secrets and sudo mode, answers the value once per request and
leaves a record that names no value; importing a key never echoes it; sealing what is already there
is a job that is refused up front when sealing is off; and ``encryption`` is not taken for a
backup's id.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core import backup_files
from noust.core.backup_files import BackupKeys
from noust.core.ens import envelope
from noust.core.secrets import SecretStore
from noust.web.api import backup_encryption as module
from noust.web.api.databases import common as db_common
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.jobs import JobType
from noust.web.permissions import Permission
from noust.web.permissions.routes_backups import ROUTES
from noust.web.server import create_app, get_token_manager

BASE = "/api/backups/encryption"


@pytest.fixture
def keys(tmp_path: Path, runner: Any, monkeypatch: pytest.MonkeyPatch) -> BackupKeys:
    """Keys in a store of the test's own; sealing is off until the test turns it on."""
    (tmp_path / "backups").mkdir()
    store = SecretStore(root=tmp_path / "sealing-secrets")
    monkeypatch.setattr(backup_files, "SecretStore", lambda: store)
    monkeypatch.setattr(backup_files.paths, "backup_dir", lambda: tmp_path / "backups")
    monkeypatch.setattr(envelope, "SCRYPT_N", 2**10)
    return BackupKeys(store)


@pytest.fixture
def app(tmp_path: Path, keys: BackupKeys) -> FastAPI:
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []

    def create_job(**kwargs: Any) -> SimpleNamespace:
        created.append(kwargs)
        return SimpleNamespace(
            id=f"job-{len(created)}",
            status=SimpleNamespace(value="pending"),
            to_dict=lambda: {"id": f"job-{len(created)}", "status": "pending"},
        )

    monkeypatch.setattr(
        db_common, "get_job_manager", lambda: SimpleNamespace(create_job=create_job)
    )
    return created


def sign_in(app: FastAPI, *, elevate: bool) -> TestClient:
    client = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    login = client.post("/api/auth/login", json={"token": token})
    assert login.status_code == 200, login.text
    client.headers[CSRF_HEADER_NAME] = login.json()["csrf_token"]
    if elevate:
        assert client.post("/api/auth/elevate", json={"token": token}).status_code == 200
    return client


@pytest.fixture
def plain(app: FastAPI) -> TestClient:
    return sign_in(app, elevate=False)


@pytest.fixture
def admin(app: FastAPI) -> TestClient:
    return sign_in(app, elevate=True)


class TestStatus:
    def test_it_says_what_is_sealed_and_never_a_value(
        self, plain: TestClient, keys: BackupKeys, tmp_path: Path
    ) -> None:
        (tmp_path / "backups" / "shop").mkdir()
        (tmp_path / "backups" / "shop" / "a.tar.gz").write_bytes(b"in clear")
        key_id = keys.make_key()

        response = plain.get(BASE)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["enabled"] is True and body["current_key"] == key_id
        assert body["keys"] == [{"id": key_id, "current": True}]
        assert (body["sealed"], body["clear"]) == (0, 1)
        assert (keys.passphrase(key_id) or "x") not in response.text

    def test_with_sealing_off_it_says_so(self, plain: TestClient) -> None:
        body = plain.get(BASE).json()

        assert body["enabled"] is False and body["current_key"] is None and body["keys"] == []

    def test_it_is_not_taken_for_a_backups_id(self, plain: TestClient) -> None:
        assert "enabled" in plain.get(BASE).json()


class TestTurningItOnAndOff:
    def test_enabling_asks_for_sudo_mode(self, plain: TestClient, keys: BackupKeys) -> None:
        response = plain.post(f"{BASE}/enable")

        assert response.status_code in (401, 403)
        assert keys.enabled() is False

    def test_enabling_makes_a_key_once_and_does_not_show_it(
        self, admin: TestClient, keys: BackupKeys
    ) -> None:
        first = admin.post(f"{BASE}/enable")
        again = admin.post(f"{BASE}/enable")

        assert first.status_code == 200, first.text
        key_id = keys.current_id()
        assert first.json() == {"enabled": True, "key_id": key_id, "created": True}
        assert again.json() == {"enabled": True, "key_id": key_id, "created": False}
        assert (keys.passphrase(key_id or "") or "x") not in first.text

    def test_rotating_makes_a_new_current_key_and_keeps_the_old(
        self, admin: TestClient, keys: BackupKeys
    ) -> None:
        old = keys.make_key()

        response = admin.post(f"{BASE}/rotate")

        assert response.status_code == 200, response.text
        new = response.json()["key_id"]
        assert new != old and keys.current_id() == new
        assert {view.id for view in keys.keys()} == {old, new}

    def test_rotating_what_is_not_sealed_is_a_bad_request_that_says_how_to_start(
        self, admin: TestClient
    ) -> None:
        response = admin.post(f"{BASE}/rotate")

        assert response.status_code == 400
        assert "not sealed" in response.json()["detail"]
        assert "noust backup encryption enable" in (response.json()["hint"] or "")

    def test_stopping_keeps_every_key(self, admin: TestClient, keys: BackupKeys) -> None:
        key_id = keys.make_key()

        response = admin.post(f"{BASE}/disable")

        assert response.json() == {"enabled": False, "was_enabled": True}
        assert keys.enabled() is False and keys.passphrase(key_id) is not None

    def test_stopping_twice_says_it_was_not_sealing(self, admin: TestClient) -> None:
        assert admin.post(f"{BASE}/disable").json() == {"enabled": False, "was_enabled": False}


class TestTheKeys:
    def test_showing_one_asks_for_sudo_mode(self, plain: TestClient, keys: BackupKeys) -> None:
        key_id = keys.make_key()

        response = plain.get(f"{BASE}/keys/{key_id}")

        assert response.status_code in (401, 403)
        assert (keys.passphrase(key_id) or "x") not in response.text

    def test_showing_one_answers_its_value_and_marks_it_uncacheable(
        self, admin: TestClient, keys: BackupKeys
    ) -> None:
        key_id = keys.make_key()

        response = admin.get(f"{BASE}/keys/{key_id}")

        assert response.status_code == 200, response.text
        assert response.json() == {"key_id": key_id, "key": keys.passphrase(key_id)}
        assert "no-store" in response.headers["cache-control"]

    def test_the_current_key_has_a_name_of_its_own(
        self, admin: TestClient, keys: BackupKeys
    ) -> None:
        key_id = keys.make_key()

        response = admin.get(f"{BASE}/keys/current")

        assert response.status_code == 200 and response.json()["key_id"] == key_id

    def test_an_unknown_key_and_no_key_are_not_found(
        self, admin: TestClient, keys: BackupKeys
    ) -> None:
        assert admin.get(f"{BASE}/keys/current").status_code == 404
        keys.make_key()
        assert admin.get(f"{BASE}/keys/deadbeef").status_code == 404
        assert admin.get(f"{BASE}/keys/..%2F..%2Fetc").status_code in (400, 404)

    def test_showing_is_recorded_without_the_value(
        self, admin: TestClient, keys: BackupKeys, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        key_id = keys.make_key()
        events: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(
            backup_files.audit,
            "record",
            lambda event, **kwargs: events.append((event, kwargs.get("details", {}))),
        )

        admin.get(f"{BASE}/keys/{key_id}")

        assert ("backup.encryption.reveal", {"key": key_id}) in events
        assert (keys.passphrase(key_id) or "x") not in repr(events)

    def test_a_lost_key_is_imported_and_never_echoed(
        self, admin: TestClient, keys: BackupKeys
    ) -> None:
        key_id = keys.make_key()
        kept = keys.passphrase(key_id) or ""
        keys.store.delete(f"{backup_files.KEY_PREFIX}/{key_id}")

        response = admin.post(f"{BASE}/keys", json={"key_id": key_id, "key": kept})

        assert response.status_code == 200, response.text
        assert response.json() == {"key_id": key_id}
        assert kept not in response.text
        assert keys.passphrase(key_id) == kept

    def test_a_key_that_cannot_be_one_is_refused_without_echoing_it(
        self, admin: TestClient, keys: BackupKeys
    ) -> None:
        response = admin.post(f"{BASE}/keys", json={"key_id": "0123abcd", "key": "too-short"})

        assert response.status_code == 400
        assert "too-short" not in response.text
        assert keys.passphrase("0123abcd") is None

    def test_importing_asks_for_sudo_mode(self, plain: TestClient) -> None:
        response = plain.post(f"{BASE}/keys", json={"key_id": "0123abcd", "key": "x" * 48})

        assert response.status_code in (401, 403)

    def test_showing_needs_the_permission_that_reads_secrets(self) -> None:
        assert ROUTES[("GET", f"{BASE}/keys/{{key_id}}")] == Permission.SECRETS_REVEAL
        assert ROUTES[("POST", f"{BASE}/keys")] == Permission.BACKUPS_MANAGE


class TestSealingWhatIsThere:
    def test_the_plan_counts_without_changing_anything(
        self, plain: TestClient, keys: BackupKeys, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        keys.make_key()
        monkeypatch.setattr(
            module,
            "SealExisting",
            lambda: SimpleNamespace(
                plan=lambda: {
                    "applications": 2,
                    "data": 1,
                    "databases": 0,
                    "pitr": 0,
                    "total": 3,
                    "sealing": True,
                }
            ),
        )

        response = plain.get(f"{BASE}/seal-existing")

        assert response.status_code == 200, response.text
        assert response.json()["total"] == 3 and response.json()["sealing"] is True

    def test_it_is_a_job_and_asks_for_sudo_mode(
        self, plain: TestClient, admin: TestClient, keys: BackupKeys, queued: list[dict[str, Any]]
    ) -> None:
        keys.make_key()

        assert plain.post(f"{BASE}/seal-existing").status_code in (401, 403)
        response = admin.post(f"{BASE}/seal-existing")

        assert response.status_code == 202, response.text
        [job] = queued
        assert job["job_type"] == JobType.CUSTOM
        assert job["metadata"]["kind"] == "seal-existing"

    def test_it_is_refused_up_front_while_sealing_is_off(
        self, admin: TestClient, queued: list[dict[str, Any]]
    ) -> None:
        response = admin.post(f"{BASE}/seal-existing")

        assert response.status_code == 400
        assert "noust backup encryption enable" in (response.json()["hint"] or "")
        assert queued == []

    def test_the_job_runs_the_same_sealing_the_command_does(
        self, keys: BackupKeys, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        keys.make_key()
        steps: list[str] = []

        class Context:
            def update(self, message: str, percent: int) -> None:
                steps.append(message)

            def log(self, line: str) -> None:
                steps.append(line)

        class Sealer:
            def run(self, on_item: Any = None) -> Any:
                from noust.managers.backup_sealing import SealItem, SealReport

                item = SealItem("database", "postgresql/a.dump", "sealed")
                if on_item:
                    on_item(item)
                return SealReport(items=[item], clear_left=0)

        monkeypatch.setattr(module, "SealExisting", lambda: Sealer())

        result = module.seal_job(job_context=Context())  # type: ignore[arg-type]

        assert result["ok"] is True and result["counts"] == {"sealed": 1}
        assert any("postgresql/a.dump" in step for step in steps)
