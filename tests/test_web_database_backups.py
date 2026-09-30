# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/backup-policies`` and the backup endpoints under ``/backups``.

What the console builds on, and what keeps it honest:

- a policy is a root timer whose retention decides what is deleted, so setting
  or removing one needs sudo mode; reading one does not;
- taking, checking, sending and restoring are jobs, and a job that fails carries
  the tool's own words;
- a dump is downloaded through sudo mode, streamed, and recorded as a sensitive
  read; a name is never a path;
- the listing carries what the table shows: kind, verification, copies, age.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.exceptions import DatabaseBackupError
from noust.core.runner import set_runner
from noust.core.store import NoustStore
from noust.managers.backup_scheduler import BackupScheduler
from noust.managers.database.base import BaseDatabaseManager
from tests.database_backup_support import PG_ARCHIVE, PG_LISTING, RcloneRunner, make_engine
from tests.test_database_backups import add_destination
from tests.test_web_databases_api import (  # noqa: F401
    _capture_queued_jobs,
    anonymous,
    app,
    client,
    elevate,
    wire,
)

# The fixtures above are the databases API's own, imported so there is one
# definition of "a signed-in client".
# ruff: noqa: F811

UNIT = "noust-backup-db-postgresql-shop"


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    """Force a fresh store singleton per test."""
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def runner(tmp_path: Path) -> Iterator[RcloneRunner]:
    """
    Args:
        tmp_path: Per-test temporary directory.

    Yields:
        A fake rclone that also lists pg_restore content, installed as the runner.
    """
    fake = RcloneRunner(tmp_path / "remote")
    fake.script(("pg_restore", "--list"), stdout=PG_LISTING)
    fake.script(
        ("systemctl", "show", f"{UNIT}.timer"),
        stdout="LoadState=loaded\nNextElapseUSecRealtime=Tue 2026-09-30 02:03:11 UTC\n",
    )
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture
def systemd_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The unit directory, inside the sandbox.
    """
    path = tmp_path / "systemd"
    path.mkdir()
    monkeypatch.setattr(BackupScheduler, "SYSTEMD_DIR", path)
    return path


@pytest.fixture
def engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: RcloneRunner, systemd_dir: Path
) -> type[BaseDatabaseManager]:
    """
    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.
        runner: The installed fake runner.
        systemd_dir: The sandboxed unit directory.

    Returns:
        A PostgreSQL fake with one database, wired in front of the service.
    """
    (tmp_path / "dumps").mkdir()
    cls = make_engine(tmp_path / "dumps")
    wire(monkeypatch, [cls])
    return cls


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """
    Args:
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        What reaches the audit trail, one dict per event.
    """
    events: list[dict[str, Any]] = []

    def record(event: str, **kwargs: Any) -> None:
        events.append({"event": event, **kwargs})

    monkeypatch.setattr("noust.core.audit.record", record)
    return events


BASE = "/api/databases"


def take_dump(engine: type[BaseDatabaseManager], **_: Any) -> str:
    """
    Write a dump the way the fake engine does and return its file name.

    Args:
        engine: The fake engine.

    Returns:
        The dump's file name.
    """
    return engine().backup("shop").path.name


# ================================================================ policies


def test_the_policies_start_empty_and_name_the_unprotected_databases(
    client: TestClient, engine: type[BaseDatabaseManager]
) -> None:
    body = client.get(f"{BASE}/backup-policies").json()

    assert body == {
        "policies": [],
        "total": 0,
        "unprotected": [{"engine": "postgresql", "database": "shop"}],
    }


def test_a_database_with_no_policy_reads_as_not_configured(
    client: TestClient, engine: type[BaseDatabaseManager]
) -> None:
    body = client.get(f"{BASE}/backup-policies/postgresql/shop").json()

    assert body["configured"] is False
    assert body["engine"] == "postgresql"
    assert body["database"] == "shop"
    assert body["destinations"] == []


def test_setting_a_policy_needs_sudo_mode(
    client: TestClient, engine: type[BaseDatabaseManager], systemd_dir: Path
) -> None:
    response = client.put(f"{BASE}/backup-policies/postgresql/shop", json={})

    assert response.status_code == 403
    assert response.json()["error"] == "elevation_required"
    assert list(systemd_dir.iterdir()) == []


def test_a_policy_is_set_read_and_removed(
    client: TestClient,
    engine: type[BaseDatabaseManager],
    systemd_dir: Path,
    audited: list[dict[str, Any]],
) -> None:
    elevate(client)

    put = client.put(
        f"{BASE}/backup-policies/postgresql/shop",
        json={"schedule": "weekly", "retention_count": 4, "retention_days": 21},
    )

    assert put.status_code == 200, put.text
    body = put.json()
    assert body["configured"] is True
    assert body["schedule"] == "Mon *-*-* 02:00:00"
    assert body["schedule_alias"] == "weekly"
    assert (body["retention_count"], body["retention_days"]) == (4, 21)
    assert body["enabled"] is True
    assert body["timer"] == {
        "installed": True,
        "next_run": "Tue 2026-09-30 02:03:11 UTC",
        "last_run": None,
    }
    assert (systemd_dir / f"{UNIT}.timer").is_file()
    assert client.get(f"{BASE}/backup-policies").json()["total"] == 1
    assert client.get(f"{BASE}/backup-policies").json()["unprotected"] == []
    assert (
        client.get(f"{BASE}/backup-policies/postgresql/shop").json()["schedule_alias"] == "weekly"
    )

    deleted = client.delete(f"{BASE}/backup-policies/postgresql/shop")

    assert deleted.status_code == 200
    assert deleted.json()["success"] is True
    assert not (systemd_dir / f"{UNIT}.timer").exists()
    assert client.get(f"{BASE}/backup-policies/postgresql/shop").json()["configured"] is False
    assert [e["event"] for e in audited] == ["db.backup.policy", "db.backup.policy.remove"]


def test_removing_a_policy_needs_sudo_mode(
    client: TestClient, engine: type[BaseDatabaseManager]
) -> None:
    response = client.delete(f"{BASE}/backup-policies/postgresql/shop")

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"schedule": "daily\nExecStart=/bin/evil"}, 422),
        ({"retention_count": 0}, 422),
        ({"retention_days": 100000}, 422),
        ({"destinations": [{"name": "nowhere"}]}, 400),
        ({"dump_format": "directory"}, 400),
    ],
)
def test_an_unusable_policy_is_refused_and_installs_no_timer(
    client: TestClient,
    engine: type[BaseDatabaseManager],
    systemd_dir: Path,
    body: dict[str, Any],
    status: int,
) -> None:
    elevate(client)

    response = client.put(f"{BASE}/backup-policies/postgresql/shop", json=body)

    assert response.status_code == status, response.text
    assert list(systemd_dir.iterdir()) == []


def test_a_policy_for_a_database_that_does_not_exist_is_a_404(
    client: TestClient, engine: type[BaseDatabaseManager]
) -> None:
    elevate(client)

    response = client.put(f"{BASE}/backup-policies/postgresql/ghost", json={})

    assert response.status_code == 404


def test_a_policy_names_its_destinations_and_their_retention(
    client: TestClient, engine: type[BaseDatabaseManager], runner: RcloneRunner
) -> None:
    add_destination(runner)
    elevate(client)

    response = client.put(
        f"{BASE}/backup-policies/postgresql/shop",
        json={"destinations": [{"name": "nas", "retention_count": 3, "retention_days": 30}]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["destinations"] == [
        {
            "name": "nas",
            "retention_count": 3,
            "retention_days": 30,
            "exists": True,
            "encrypted": False,
        }
    ]


def test_running_a_policy_is_a_job_that_does_what_the_timer_does(
    client: TestClient,
    engine: type[BaseDatabaseManager],
    monkeypatch: pytest.MonkeyPatch,
    runner: RcloneRunner,
) -> None:
    add_destination(runner)
    elevate(client)
    client.put(
        f"{BASE}/backup-policies/postgresql/shop",
        json={"destinations": [{"name": "nas"}]},
    )
    created = _capture_queued_jobs(monkeypatch)

    response = client.post(f"{BASE}/backup-policies/postgresql/shop/run")

    assert response.status_code == 202, response.text
    job = created[0]
    assert job["job_type"].value == "backup"
    assert job["metadata"] == {"engine": "postgresql", "database": "shop", "kind": "database"}
    result = job["func"](**job["kwargs"])
    assert result["dump"]["kind"] == "scheduled"
    assert result["dump"]["verify_status"] == "ok"
    assert result["destinations"]["nas"]["ok"] is True
    policy = client.get(f"{BASE}/backup-policies/postgresql/shop").json()
    assert policy["last_status"] == "ok"


def test_a_policy_run_that_fails_its_check_fails_the_job_with_the_tools_words(
    client: TestClient,
    engine: type[BaseDatabaseManager],
    monkeypatch: pytest.MonkeyPatch,
    runner: RcloneRunner,
) -> None:
    created = _capture_queued_jobs(monkeypatch)
    runner.script(("pg_restore", "--list"), stderr="pg_restore: error: bad header", exit_code=1)
    client.post(f"{BASE}/backup-policies/postgresql/shop/run")
    job = created[0]

    with pytest.raises(DatabaseBackupError) as failure:
        job["func"](**job["kwargs"])

    assert "bad header" in str(failure.value)


# ==================================================================== listing


def test_the_listing_carries_what_the_table_shows(
    client: TestClient, engine: type[BaseDatabaseManager]
) -> None:
    name = take_dump(engine)

    (dump,) = client.get(f"{BASE}/backups?engine=postgresql&database=shop").json()["backups"]

    assert dump["name"] == name
    assert dump["format"] == "custom"
    assert dump["kind"] == "unknown"
    assert dump["verify_status"] == "unverified"
    assert dump["destinations"] == []
    assert dump["age_seconds"] >= 0
    assert dump["sha256"] is None


def test_a_manual_dump_job_records_and_checks_the_dump(
    client: TestClient, engine: type[BaseDatabaseManager], monkeypatch: pytest.MonkeyPatch
) -> None:
    created = _capture_queued_jobs(monkeypatch)
    client.post(f"{BASE}/backups", json={"engine": "postgresql", "database": "shop"})
    job = created[0]

    result = job["func"](**job["kwargs"])

    assert result["kind"] == "manual"
    assert result["verify_status"] == "ok"
    (dump,) = client.get(f"{BASE}/backups?engine=postgresql").json()["backups"]
    assert dump["verify_status"] == "ok"
    assert len(dump["sha256"]) == 64


# ============================================================ verify, push


def test_verifying_is_a_job_and_a_failed_check_fails_it(
    client: TestClient,
    engine: type[BaseDatabaseManager],
    monkeypatch: pytest.MonkeyPatch,
    runner: RcloneRunner,
) -> None:
    created = _capture_queued_jobs(monkeypatch)
    name = take_dump(engine)

    response = client.post(
        f"{BASE}/backups/{name}/verify", json={"engine": "postgresql", "restore_test": True}
    )

    assert response.status_code == 202, response.text
    job = created[0]
    assert job["job_type"].value == "database"
    result = job["func"](**job["kwargs"])
    assert result["verify_status"] == "ok"
    assert result["restore_test_status"] == "ok"
    runner.script(("pg_restore", "--list"), stderr="pg_restore: error: bad", exit_code=1)
    with pytest.raises(DatabaseBackupError, match="failed its check") as failure:
        job["func"](**job["kwargs"])
    assert "pg_restore: error: bad" in str(failure.value)


def test_verifying_a_dump_that_does_not_exist_is_a_404(
    client: TestClient, engine: type[BaseDatabaseManager]
) -> None:
    response = client.post(
        f"{BASE}/backups/postgresql-shop-20250101_010101.dump/verify",
        json={"engine": "postgresql"},
    )

    assert response.status_code == 404


def test_pushing_is_a_job_that_sends_the_dump(
    client: TestClient,
    engine: type[BaseDatabaseManager],
    monkeypatch: pytest.MonkeyPatch,
    runner: RcloneRunner,
) -> None:
    add_destination(runner)
    created = _capture_queued_jobs(monkeypatch)
    name = take_dump(engine)

    response = client.post(
        f"{BASE}/backups/{name}/push", json={"engine": "postgresql", "destination": "nas"}
    )

    assert response.status_code == 202, response.text
    job = created[0]
    assert job["job_type"].value == "push"
    summary = job["func"](**job["kwargs"])
    assert summary["destination"] == "nas"
    assert (
        runner.root / "nas" / "wasm-backups" / "databases" / "postgresql" / "shop" / name
    ).is_file()
    (dump,) = client.get(f"{BASE}/backups?engine=postgresql").json()["backups"]
    assert dump["destinations"][0]["destination"] == "nas"


# ===================================================== download and delete


def test_downloading_needs_sudo_mode(client: TestClient, engine: type[BaseDatabaseManager]) -> None:
    name = take_dump(engine)

    response = client.get(f"{BASE}/backups/{name}/download?engine=postgresql")

    assert response.status_code == 403
    assert response.json()["error"] == "elevation_required"


def test_a_download_is_the_file_as_an_attachment_and_a_sensitive_read(
    client: TestClient, engine: type[BaseDatabaseManager], audited: list[dict[str, Any]]
) -> None:
    name = take_dump(engine)
    elevate(client)

    response = client.get(f"{BASE}/backups/{name}/download?engine=postgresql")

    assert response.status_code == 200
    assert response.content == PG_ARCHIVE
    assert response.headers["content-type"] == "application/octet-stream"
    assert f'filename="{name}"' in response.headers["content-disposition"]
    event = next(e for e in audited if e["event"] == "db.backup.download")
    assert event["target"] == "db:postgresql"
    assert event["details"] == {"file": name, "size": len(PG_ARCHIVE)}


@pytest.mark.parametrize("name", ["..%2F..%2Fetc%2Fpasswd", "postgresql-x-20250101_010101.dump"])
def test_a_download_is_of_a_dump_and_never_of_a_path(
    client: TestClient, engine: type[BaseDatabaseManager], name: str
) -> None:
    elevate(client)

    response = client.get(f"{BASE}/backups/{name}/download?engine=postgresql")

    assert response.status_code in (400, 404)
    assert b"root:" not in response.content


def test_deleting_a_dump_needs_sudo_mode_and_removes_the_file(
    client: TestClient, engine: type[BaseDatabaseManager], audited: list[dict[str, Any]]
) -> None:
    name = take_dump(engine)
    path = engine.BACKUP_DIR / name
    assert client.delete(f"{BASE}/backups/{name}?engine=postgresql").status_code == 403
    assert path.is_file()
    elevate(client)

    response = client.delete(f"{BASE}/backups/{name}?engine=postgresql")

    assert response.status_code == 200, response.text
    assert not path.exists()
    assert audited[-1]["event"] == "db.backup.delete"


def test_deleting_a_copy_on_a_destination_needs_the_database(
    client: TestClient, engine: type[BaseDatabaseManager], runner: RcloneRunner
) -> None:
    add_destination(runner)
    name = take_dump(engine)
    elevate(client)

    refused = client.delete(f"{BASE}/backups/{name}?engine=postgresql&destination=nas")

    assert refused.status_code == 400
    assert (engine.BACKUP_DIR / name).is_file()


def test_deleting_a_copy_on_a_destination_leaves_the_local_file(
    client: TestClient,
    engine: type[BaseDatabaseManager],
    runner: RcloneRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_destination(runner)
    created = _capture_queued_jobs(monkeypatch)
    name = take_dump(engine)
    client.post(f"{BASE}/backups/{name}/push", json={"engine": "postgresql", "destination": "nas"})
    created[0]["func"](**created[0]["kwargs"])
    elevate(client)

    response = client.delete(
        f"{BASE}/backups/{name}?engine=postgresql&destination=nas&database=shop"
    )

    assert response.status_code == 200, response.text
    folder = runner.root / "nas" / "wasm-backups" / "databases" / "postgresql" / "shop"
    assert list(folder.iterdir()) == []
    assert (engine.BACKUP_DIR / name).is_file()


# ============================================================ remote side


def test_the_remote_listing_says_who_sent_each_dump(
    client: TestClient,
    engine: type[BaseDatabaseManager],
    runner: RcloneRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_destination(runner)
    created = _capture_queued_jobs(monkeypatch)
    name = take_dump(engine)
    client.post(f"{BASE}/backups/{name}/push", json={"engine": "postgresql", "destination": "nas"})
    created[0]["func"](**created[0]["kwargs"])

    body = client.get(
        f"{BASE}/backups/remote?engine=postgresql&database=shop&destination=nas"
    ).json()

    assert body["total"] == 1
    assert body["destination"] == "nas"
    (dump,) = body["dumps"]
    assert dump["name"] == name
    assert dump["own"] is True
    assert dump["local"] is True
    assert len(dump["sha256"]) == 64


def test_the_remote_databases_of_an_engine(
    client: TestClient, engine: type[BaseDatabaseManager], runner: RcloneRunner
) -> None:
    add_destination(runner)
    (runner.root / "nas" / "wasm-backups" / "databases" / "postgresql" / "shop").mkdir(parents=True)

    body = client.get(f"{BASE}/backups/remote/databases?engine=postgresql&destination=nas").json()

    assert body == {"destination": "nas", "engine": "postgresql", "databases": ["shop"]}


def test_a_destination_that_does_not_exist_is_an_error_the_console_can_show(
    client: TestClient, engine: type[BaseDatabaseManager]
) -> None:
    response = client.get(
        f"{BASE}/backups/remote?engine=postgresql&database=shop&destination=nowhere"
    )

    assert response.status_code == 400
    assert "nowhere" in response.json()["detail"]
    assert "destination" in response.json()["fields"]


def test_a_free_name_is_suggested_for_restoring_as_a_new_database(
    client: TestClient, engine: type[BaseDatabaseManager]
) -> None:
    body = client.get(
        f"{BASE}/backups/suggest-name?engine=postgresql&database=shop"
        "&backup_name=postgresql-shop-20260928_020000.dump"
    ).json()

    assert body == {"name": "shop_restored_20260928"}


def test_restoring_from_a_destination_needs_sudo_mode(
    client: TestClient, engine: type[BaseDatabaseManager], monkeypatch: pytest.MonkeyPatch
) -> None:
    created = _capture_queued_jobs(monkeypatch)

    response = client.post(
        f"{BASE}/backups/restore-remote",
        json={
            "engine": "postgresql",
            "database": "shop",
            "destination": "nas",
            "backup_name": "postgresql-shop-20260928_020000.dump",
        },
    )

    assert response.status_code == 403
    assert created == []


def test_restoring_from_a_destination_is_a_job_that_restores_as_a_new_database(
    client: TestClient,
    engine: type[BaseDatabaseManager],
    runner: RcloneRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_destination(runner)
    created = _capture_queued_jobs(monkeypatch)
    name = take_dump(engine)
    client.post(f"{BASE}/backups/{name}/push", json={"engine": "postgresql", "destination": "nas"})
    created[0]["func"](**created[0]["kwargs"])
    (engine.BACKUP_DIR / name).unlink()
    elevate(client)

    response = client.post(
        f"{BASE}/backups/restore-remote",
        json={
            "engine": "postgresql",
            "database": "shop",
            "destination": "nas",
            "backup_name": name,
            "new_name": "shop_copy",
        },
    )

    assert response.status_code == 202, response.text
    job = created[1]
    assert job["job_type"].value == "restore"
    assert job["metadata"] == {
        "engine": "postgresql",
        "database": "shop_copy",
        "backup_id": name,
        "destination": "nas",
        "kind": "database",
    }
    result = job["func"](**job["kwargs"])
    assert result["database"] == "shop_copy"
    assert ("load", "shop_copy", name) in engine.state["calls"]  # type: ignore[attr-defined]


# ================================================== the contract with the console


def test_every_new_route_is_in_the_permission_map_and_the_destructive_ones_need_sudo_mode(
    app: Any,
) -> None:
    schema = app.openapi()
    paths = schema["paths"]
    elevated = {
        (method.upper(), path)
        for path, item in paths.items()
        for method, operation in item.items()
        if path.startswith(f"{BASE}/backup") and operation.get("x-noust-requires-elevation")
    }

    assert elevated == {
        ("PUT", f"{BASE}/backup-policies/{{engine}}/{{database}}"),
        ("DELETE", f"{BASE}/backup-policies/{{engine}}/{{database}}"),
        ("GET", f"{BASE}/backups/{{name}}/download"),
        ("DELETE", f"{BASE}/backups/{{name}}"),
        ("POST", f"{BASE}/backups/restore-remote"),
        ("POST", f"{BASE}/backups/restore"),
    }
    for path, item in paths.items():
        if path.startswith(f"{BASE}/backup"):
            for operation in item.values():
                assert operation.get("x-noust-permission"), path


def test_the_openapi_models_carry_the_fields_the_console_reads(app: Any) -> None:
    schemas = app.openapi()["components"]["schemas"]

    assert {"kind", "verify_status", "verify_detail", "destinations", "age_seconds"} <= set(
        schemas["BackupInfoResponse"]["properties"]
    )
    assert {"configured", "schedule_alias", "timer", "last_status", "last_error"} <= set(
        schemas["BackupPolicyResponse"]["properties"]
    )
    assert json.dumps(schemas["BackupPolicyListResponse"]["properties"])
