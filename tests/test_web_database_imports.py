# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/imports`` and the all-databases policy over HTTP (3.4, H-02 and H-18).

What the console builds on:

- an upload is a session the client fills in chunks, resumes from the offset
  the server reports, and completes to learn what the file is; a chunk bigger
  than the API's usual body fits, a bigger one does not;
- a chunk at the wrong offset is a 409 that carries where to resume, and one
  person's upload is invisible to another;
- importing is a job and needs sudo mode, and everything that can be checked
  is checked before the job exists: the upload is finished, a server path is in
  a folder dumps are read from, the engine is running;
- a policy for all databases is addressed as ``*`` and carries its exclusions.
"""

from __future__ import annotations

import gzip
import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.config import Config
from noust.core.runner import set_runner
from noust.core.store import NoustStore
from noust.managers.backup_scheduler import BackupScheduler
from noust.managers.database.base import BaseDatabaseManager
from noust.managers.database.uploads import MAX_CHUNK_BYTES, UploadStore
from tests.database_backup_support import PG_LISTING, RcloneRunner, make_engine
from tests.test_web_databases_api import (  # noqa: F401
    _capture_queued_jobs,
    anonymous,
    app,
    client,
    elevate,
    wire,
)

# ruff: noqa: F811

BASE = "/api/databases"
SQL = b"-- PostgreSQL database dump\nSELECT 1;\n-- PostgreSQL database dump complete\n"
ALL_UNIT = "noust-backup-db-postgresql--all"


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def runner(tmp_path: Path) -> Iterator[RcloneRunner]:
    fake = RcloneRunner(tmp_path / "remote")
    fake.script(("pg_restore", "--list"), stdout=PG_LISTING)
    fake.script(("systemctl", "show"), stdout="LoadState=loaded\n")
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture
def folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The backup directory, redirected so no test touches the real one."""
    backups = tmp_path / "backups"
    (backups / "imports").mkdir(parents=True)
    monkeypatch.setattr(Config, "backup_directory", property(lambda self: backups), raising=False)
    return backups


@pytest.fixture
def systemd_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "systemd"
    path.mkdir()
    monkeypatch.setattr(BackupScheduler, "SYSTEMD_DIR", path)
    return path


@pytest.fixture
def engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner: RcloneRunner,
    systemd_dir: Path,
    folders: Path,
) -> type[BaseDatabaseManager]:
    (tmp_path / "dumps").mkdir()
    cls = make_engine(tmp_path / "dumps", suffix=".sql", content=SQL, databases=("shop", "blog"))
    wire(monkeypatch, [cls])
    return cls


def upload_whole(client: TestClient, name: str, data: bytes, **extra: Any) -> dict[str, Any]:
    """Start, send in one chunk and complete an upload."""
    started = client.post(
        f"{BASE}/imports/uploads", json={"file_name": name, "size": len(data), **extra}
    )
    assert started.status_code == 201, started.text
    upload_id = started.json()["id"]
    sent = client.put(f"{BASE}/imports/uploads/{upload_id}", params={"offset": 0}, content=data)
    assert sent.status_code == 200, sent.text
    done = client.post(f"{BASE}/imports/uploads/{upload_id}/complete")
    assert done.status_code == 200, done.text
    return done.json()


# ============================================================== the uploads


class TestUploadingOverHttp:
    def test_start_chunks_complete_and_say_what_it_is(
        self, client: TestClient, engine: type[BaseDatabaseManager], folders: Path
    ) -> None:
        data = gzip.compress(SQL)
        size = len(data)
        started = client.post(
            f"{BASE}/imports/uploads", json={"file_name": "export.sql.gz", "size": size}
        ).json()
        path = f"{BASE}/imports/uploads/{started['id']}"

        first = client.put(path, params={"offset": 0}, content=data[:10]).json()
        resumed = client.get(path).json()
        second = client.put(path, params={"offset": 10}, content=data[10:]).json()
        done = client.post(f"{path}/complete").json()

        assert started["received"] == 0
        assert started["chunk_size"] == MAX_CHUNK_BYTES
        assert first["received"] == 10 and resumed["received"] == 10
        assert second["received"] == size
        assert done["complete"] is True
        assert done["format"]["kind"] == "sql"
        assert done["format"]["compression"] == "gzip"
        assert set(done["format"]["engines"]) == {"postgresql", "mysql"}
        assert "owner" not in done

    def test_a_chunk_at_the_wrong_offset_is_a_409_that_says_where_to_resume(
        self, client: TestClient, engine: type[BaseDatabaseManager], folders: Path
    ) -> None:
        started = client.post(
            f"{BASE}/imports/uploads", json={"file_name": "x.sql", "size": 10}
        ).json()
        path = f"{BASE}/imports/uploads/{started['id']}"
        client.put(path, params={"offset": 0}, content=b"hello")

        again = client.put(path, params={"offset": 0}, content=b"hello")

        assert again.status_code == 409
        body = again.json()
        assert body["received"] == 5
        assert body["error"] == "upload_offset"

    def test_a_chunk_bigger_than_the_api_usually_takes_fits(
        self, client: TestClient, engine: type[BaseDatabaseManager], folders: Path
    ) -> None:
        # The general API body cap is 1 MiB; a chunk of an upload may be sixteen.
        data = b"-- dump\n" + b"x" * (2 * 1024 * 1024)
        started = client.post(
            f"{BASE}/imports/uploads", json={"file_name": "x.sql", "size": len(data)}
        ).json()

        sent = client.put(
            f"{BASE}/imports/uploads/{started['id']}", params={"offset": 0}, content=data
        )

        assert sent.status_code == 200, sent.text
        assert sent.json()["received"] == len(data)

    def test_the_same_size_anywhere_else_is_still_refused(
        self, client: TestClient, engine: type[BaseDatabaseManager], folders: Path
    ) -> None:
        response = client.post(
            f"{BASE}/imports/inspect",
            content=b"x" * (2 * 1024 * 1024),
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 413

    def test_the_declared_digest_is_checked(
        self, client: TestClient, engine: type[BaseDatabaseManager], folders: Path
    ) -> None:
        started = client.post(
            f"{BASE}/imports/uploads",
            json={"file_name": "x.sql", "size": 5, "sha256": hashlib.sha256(b"other").hexdigest()},
        ).json()
        path = f"{BASE}/imports/uploads/{started['id']}"
        client.put(path, params={"offset": 0}, content=b"hello")

        done = client.post(f"{path}/complete")

        assert done.status_code == 400
        assert "SHA-256" in done.json()["detail"]
        assert client.get(path).status_code == 400

    def test_completing_early_is_a_409_with_the_offset(
        self, client: TestClient, engine: type[BaseDatabaseManager], folders: Path
    ) -> None:
        started = client.post(
            f"{BASE}/imports/uploads", json={"file_name": "x.sql", "size": 10}
        ).json()
        path = f"{BASE}/imports/uploads/{started['id']}"
        client.put(path, params={"offset": 0}, content=b"hello")

        early = client.post(f"{path}/complete")

        assert early.status_code == 409
        assert early.json()["received"] == 5

    @pytest.mark.parametrize(
        ("body", "status"),
        [
            ({"file_name": "evil.sh", "size": 5}, 400),
            ({"file_name": "x.sql", "size": 0}, 422),
            ({"file_name": "x.sql", "size": 5, "sha256": "short"}, 422),
            ({"file_name": "x.sql", "size": 11 * 1024**3}, 400),
        ],
    )
    def test_an_unusable_upload_is_refused_before_it_starts(
        self,
        client: TestClient,
        engine: type[BaseDatabaseManager],
        folders: Path,
        body: dict[str, Any],
        status: int,
    ) -> None:
        response = client.post(f"{BASE}/imports/uploads", json=body)

        assert response.status_code == status, response.text

    def test_an_upload_is_discarded_on_request(
        self, client: TestClient, engine: type[BaseDatabaseManager], folders: Path
    ) -> None:
        started = client.post(
            f"{BASE}/imports/uploads", json={"file_name": "x.sql", "size": 5}
        ).json()
        path = f"{BASE}/imports/uploads/{started['id']}"

        assert client.delete(path).json()["success"] is True
        assert client.get(path).status_code == 400
        assert not (folders / ".uploads" / started["id"]).exists()

    def test_nothing_here_is_open_to_anyone_without_a_session(
        self, anonymous: TestClient, engine: type[BaseDatabaseManager], folders: Path
    ) -> None:
        for response in (
            anonymous.post(f"{BASE}/imports/uploads", json={"file_name": "x.sql", "size": 5}),
            anonymous.put(f"{BASE}/imports/uploads/{'0' * 32}", params={"offset": 0}, content=b""),
            anonymous.post(f"{BASE}/imports", json={}),
        ):
            assert response.status_code in (401, 403)


# ============================================================ every database

CLUSTER = Path(__file__).parent / "fixtures" / "pg_dumpall" / "all17.sql"


@pytest.fixture
def pg_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner: RcloneRunner,
    systemd_dir: Path,
    folders: Path,
) -> type[BaseDatabaseManager]:
    """A PostgreSQL that has the server's own accounts."""
    (tmp_path / "pgdumps").mkdir()
    base = make_engine(tmp_path / "pgdumps", suffix=".sql", content=SQL, databases=())

    class Postgres(base):  # type: ignore[valid-type, misc]
        def role_names(self) -> set[str]:
            return {"postgres"}

    wire(monkeypatch, [Postgres])
    return Postgres


class TestImportingEveryDatabase:
    def test_the_plan_reads_the_file_and_changes_nothing(
        self, client: TestClient, pg_engine: type[BaseDatabaseManager]
    ) -> None:
        done = upload_whole(client, "all.sql", CLUSTER.read_bytes())

        response = client.post(
            f"{BASE}/imports/cluster/plan",
            json={"engine": "postgresql", "upload_id": done["id"], "restore_users": True},
        )

        assert response.status_code == 200, response.text
        plan = response.json()
        assert plan["format"]["scope"] == "cluster"
        assert {d["name"]: d["action"] for d in plan["databases"]}["shop"] == "create"
        assert any(r["name"] == "shopapp" and r["action"] == "create" for r in plan["roles"])
        assert any("account(s) will be created" in w for w in plan["warnings"])
        assert pg_engine.state["dbs"] == {}

    def test_a_file_that_holds_one_database_is_refused_with_where_to_go(
        self, client: TestClient, pg_engine: type[BaseDatabaseManager]
    ) -> None:
        done = upload_whole(client, "one.sql", SQL)

        response = client.post(
            f"{BASE}/imports/cluster/plan", json={"engine": "postgresql", "upload_id": done["id"]}
        )

        assert response.status_code == 400
        assert "not a dump of all databases" in response.json()["detail"]

    def test_it_needs_sudo_mode(
        self, client: TestClient, pg_engine: type[BaseDatabaseManager]
    ) -> None:
        done = upload_whole(client, "all.sql", CLUSTER.read_bytes())

        response = client.post(
            f"{BASE}/imports/cluster", json={"engine": "postgresql", "upload_id": done["id"]}
        )

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"

    def test_the_accounts_are_refused_until_the_operator_confirms_them(
        self,
        client: TestClient,
        pg_engine: type[BaseDatabaseManager],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        created = _capture_queued_jobs(monkeypatch)
        done = upload_whole(client, "all.sql", CLUSTER.read_bytes())
        elevate(client)

        response = client.post(
            f"{BASE}/imports/cluster",
            json={"engine": "postgresql", "upload_id": done["id"], "restore_users": True},
        )

        assert response.status_code == 400
        assert response.json()["error"] == "confirmation_required"
        assert not created

    def test_it_is_queued_as_a_job_with_the_options(
        self,
        client: TestClient,
        pg_engine: type[BaseDatabaseManager],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        created = _capture_queued_jobs(monkeypatch)
        done = upload_whole(client, "all.sql", CLUSTER.read_bytes())
        elevate(client)

        response = client.post(
            f"{BASE}/imports/cluster",
            json={
                "engine": "postgresql",
                "upload_id": done["id"],
                "restore_users": True,
                "confirm_users": True,
                "databases": ["shop"],
                "replace": True,
            },
        )

        assert response.status_code == 202, response.text
        (job,) = created
        assert job["func"].__name__ == "cluster_import_job"
        assert job["kwargs"]["restore_users"] is True
        assert job["kwargs"]["databases"] == ["shop"]
        assert job["kwargs"]["replace"] is True

    def test_a_path_outside_the_allowed_folders_is_refused_before_a_job_exists(
        self,
        client: TestClient,
        pg_engine: type[BaseDatabaseManager],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        created = _capture_queued_jobs(monkeypatch)
        elevate(client)

        response = client.post(
            f"{BASE}/imports/cluster", json={"engine": "postgresql", "path": str(CLUSTER)}
        )

        assert response.status_code == 400
        assert not created

    def test_an_engine_that_has_no_such_dump_is_refused(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, folders: Path
    ) -> None:
        (tmp_path / "mdumps").mkdir()
        cache = make_engine(
            tmp_path / "mdumps", engine="redis", suffix=".rdb", content=SQL, databases=("0",)
        )
        wire(monkeypatch, [cache])
        done = upload_whole(client, "all.sql", CLUSTER.read_bytes())
        elevate(client)

        response = client.post(
            f"{BASE}/imports/cluster", json={"engine": "redis", "upload_id": done["id"]}
        )

        assert response.status_code == 400
        assert "no dump of all databases" in response.json()["detail"]

    def test_the_job_imports_every_database_and_removes_the_upload(
        self, client: TestClient, pg_engine: type[BaseDatabaseManager], folders: Path
    ) -> None:
        from noust.web.api.databases.imports import cluster_import_job

        done = upload_whole(client, "all.sql", CLUSTER.read_bytes())

        result = cluster_import_job("postgresql", upload_id=done["id"])

        assert {d["name"]: d["status"] for d in result["databases"]}["shop"] == "created"
        assert {"shop", "blog"} <= set(pg_engine.state["dbs"])
        assert not (folders / ".uploads" / done["id"]).exists()

    def test_a_database_that_failed_makes_the_job_fail_and_keeps_the_upload(
        self, client: TestClient, pg_engine: type[BaseDatabaseManager], folders: Path
    ) -> None:
        from noust.core.exceptions import DatabaseBackupError
        from noust.web.api.databases.imports import cluster_import_job

        original = pg_engine._load_backup

        def fail_blog(self: Any, database: str, backup_path: Path, **kwargs: Any) -> None:
            if database == "blog":
                raise DatabaseBackupError("Failed to restore", details="ERROR: permission denied")
            original(self, database, backup_path, **kwargs)

        pg_engine._load_backup = fail_blog  # type: ignore[method-assign]
        done = upload_whole(client, "all.sql", CLUSTER.read_bytes())

        with pytest.raises(DatabaseBackupError) as raised:
            cluster_import_job("postgresql", upload_id=done["id"])

        assert "1 of 2 databases were not imported" in raised.value.message
        assert "blog:" in (raised.value.details or "") and "permission denied" in (
            raised.value.details or ""
        )
        assert "Imported: shop" in (raised.value.details or "")
        assert (folders / ".uploads" / done["id"]).exists()


# ============================================================= the import


class TestImportingOverHttp:
    def test_it_needs_sudo_mode(
        self, client: TestClient, engine: type[BaseDatabaseManager], folders: Path
    ) -> None:
        done = upload_whole(client, "x.sql", SQL)

        response = client.post(
            f"{BASE}/imports",
            json={"engine": "postgresql", "database": "fresh", "upload_id": done["id"]},
        )

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"

    def test_an_upload_is_queued_as_a_job_with_the_options(
        self,
        client: TestClient,
        engine: type[BaseDatabaseManager],
        folders: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        created = _capture_queued_jobs(monkeypatch)
        done = upload_whole(client, "x.sql", SQL)
        elevate(client)

        response = client.post(
            f"{BASE}/imports",
            json={
                "engine": "postgresql",
                "database": "fresh",
                "upload_id": done["id"],
                "replace": True,
                "keep_owners": True,
                "safety_backup": False,
            },
        )

        assert response.status_code == 202, response.text
        (job,) = created
        assert job["kwargs"]["upload_id"] == done["id"]
        assert job["kwargs"]["database"] == "fresh"
        assert job["kwargs"]["replace"] is True
        assert job["kwargs"]["keep_owners"] is True
        assert job["kwargs"]["safety_backup"] is False
        assert job["func"].__name__ == "import_job"

    def test_the_superuser_is_refused_until_the_operator_confirms_it(
        self,
        client: TestClient,
        engine: type[BaseDatabaseManager],
        folders: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        created = _capture_queued_jobs(monkeypatch)
        done = upload_whole(client, "x.sql", SQL)
        elevate(client)

        response = client.post(
            f"{BASE}/imports",
            json={
                "engine": "postgresql",
                "database": "fresh",
                "upload_id": done["id"],
                "as_superuser": True,
            },
        )

        assert response.status_code == 400
        body = response.json()
        assert body["error"] == "confirmation_required"
        assert "COPY ... FROM PROGRAM" in " ".join(body["warnings"])
        assert not created

    def test_confirmed_the_superuser_reaches_the_job(
        self,
        client: TestClient,
        engine: type[BaseDatabaseManager],
        folders: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        created = _capture_queued_jobs(monkeypatch)
        done = upload_whole(client, "x.sql", SQL)
        elevate(client)

        response = client.post(
            f"{BASE}/imports",
            json={
                "engine": "postgresql",
                "database": "fresh",
                "upload_id": done["id"],
                "as_superuser": True,
                "confirm_superuser": True,
            },
        )

        assert response.status_code == 202, response.text
        (job,) = created
        assert job["kwargs"]["as_superuser"] is True

    def test_by_default_the_job_is_not_the_superuser(
        self,
        client: TestClient,
        engine: type[BaseDatabaseManager],
        folders: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        created = _capture_queued_jobs(monkeypatch)
        done = upload_whole(client, "x.sql", SQL)
        elevate(client)

        client.post(
            f"{BASE}/imports",
            json={"engine": "postgresql", "database": "fresh", "upload_id": done["id"]},
        )

        assert created[0]["kwargs"]["as_superuser"] is False

    def test_the_owner_of_a_new_database_reaches_the_job(
        self,
        client: TestClient,
        engine: type[BaseDatabaseManager],
        folders: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            engine, "user_exists", lambda self, username, host="localhost": username == "shopapp"
        )
        created = _capture_queued_jobs(monkeypatch)
        done = upload_whole(client, "x.sql", SQL)
        elevate(client)

        response = client.post(
            f"{BASE}/imports",
            json={
                "engine": "postgresql",
                "database": "fresh",
                "upload_id": done["id"],
                "owner": "shopapp",
            },
        )

        assert response.status_code == 202, response.text
        assert created[0]["kwargs"]["owner"] == "shopapp"

    def test_an_owner_that_does_not_exist_is_refused_before_a_job_exists(
        self,
        client: TestClient,
        engine: type[BaseDatabaseManager],
        folders: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        created = _capture_queued_jobs(monkeypatch)
        done = upload_whole(client, "x.sql", SQL)
        elevate(client)

        response = client.post(
            f"{BASE}/imports",
            json={
                "engine": "postgresql",
                "database": "fresh",
                "upload_id": done["id"],
                "owner": "ghost",
            },
        )

        assert response.status_code == 400
        assert "no account called ghost" in response.text
        assert created == []

    def test_the_job_makes_the_database_with_that_owner(
        self, client: TestClient, engine: type[BaseDatabaseManager], folders: Path
    ) -> None:
        from noust.web.api.databases.imports import import_job

        done = upload_whole(client, "x.sql", SQL)

        import_job("postgresql", "fresh", upload_id=done["id"], owner="shopapp")

        assert engine.state["dbs"]["fresh"]["owner"] == "shopapp"

    def test_the_job_loads_the_dump_and_removes_the_upload(
        self, client: TestClient, engine: type[BaseDatabaseManager], folders: Path
    ) -> None:
        from noust.web.api.databases.imports import import_job

        done = upload_whole(client, "x.sql", SQL)

        result = import_job("postgresql", "fresh", upload_id=done["id"])

        assert result["created"] is True
        assert result["format"]["kind"] == "sql"
        assert "fresh" in engine.state["dbs"]
        assert not (folders / ".uploads" / done["id"]).exists()

    def test_an_unfinished_upload_is_refused_before_a_job_exists(
        self,
        client: TestClient,
        engine: type[BaseDatabaseManager],
        folders: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        created = _capture_queued_jobs(monkeypatch)
        started = client.post(
            f"{BASE}/imports/uploads", json={"file_name": "x.sql", "size": 10}
        ).json()
        elevate(client)

        response = client.post(
            f"{BASE}/imports",
            json={"engine": "postgresql", "database": "fresh", "upload_id": started["id"]},
        )

        assert response.status_code == 400
        assert "not finished" in response.json()["detail"]
        assert not created

    def test_a_path_must_be_in_a_folder_dumps_are_read_from(
        self,
        client: TestClient,
        engine: type[BaseDatabaseManager],
        folders: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        created = _capture_queued_jobs(monkeypatch)
        outside = tmp_path / "elsewhere.sql"
        outside.write_bytes(SQL)
        inside = folders / "imports" / "ok.sql"
        inside.write_bytes(SQL)
        elevate(client)

        refused = client.post(
            f"{BASE}/imports",
            json={"engine": "postgresql", "database": "fresh", "path": str(outside)},
        )
        accepted = client.post(
            f"{BASE}/imports",
            json={"engine": "postgresql", "database": "fresh", "path": str(inside)},
        )

        assert refused.status_code == 400
        assert "not in a folder" in refused.json()["detail"]
        assert accepted.status_code == 202
        assert len(created) == 1

    def test_exactly_one_source_is_needed(
        self,
        client: TestClient,
        engine: type[BaseDatabaseManager],
        folders: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        created = _capture_queued_jobs(monkeypatch)
        elevate(client)

        neither = client.post(f"{BASE}/imports", json={"engine": "postgresql", "database": "fresh"})
        both = client.post(
            f"{BASE}/imports",
            json={"engine": "postgresql", "database": "fresh", "upload_id": "a", "path": "/b"},
        )

        assert neither.status_code == 400 and both.status_code == 400
        assert not created

    def test_inspect_says_what_a_file_on_the_server_is(
        self, client: TestClient, engine: type[BaseDatabaseManager], folders: Path
    ) -> None:
        path = folders / "imports" / "d.sql"
        path.write_bytes(SQL)

        body = client.post(
            f"{BASE}/imports/inspect", json={"path": str(path), "engine": "postgresql"}
        ).json()

        assert body["format"]["kind"] == "sql"
        assert body["loadable"] is True
        assert body["size"] == len(SQL)


# ============================================== the policy for all databases


class TestThePolicyForAllDatabases:
    def test_it_is_addressed_as_a_star_and_carries_its_exclusions(
        self,
        client: TestClient,
        engine: type[BaseDatabaseManager],
        systemd_dir: Path,
        folders: Path,
    ) -> None:
        elevate(client)

        put = client.put(
            f"{BASE}/backup-policies/postgresql/*",
            json={"exclude_databases": ["blog"], "exclude_tables": ["sessions"]},
        )

        assert put.status_code == 200, put.text
        body = put.json()
        assert body["database"] == "*"
        assert body["scope"] == "all"
        assert body["exclusions"] == {"databases": ["blog"], "tables": ["sessions"]}
        assert (systemd_dir / f"{ALL_UNIT}.timer").is_file()
        listed = client.get(f"{BASE}/backup-policies").json()
        assert [p["database"] for p in listed["policies"]] == ["*"]
        # The one it covers is not unprotected; the one it leaves out is.
        assert listed["unprotected"] == [{"engine": "postgresql", "database": "blog"}]

    def test_databases_cannot_be_excluded_from_a_policy_for_one(
        self,
        client: TestClient,
        engine: type[BaseDatabaseManager],
        systemd_dir: Path,
        folders: Path,
    ) -> None:
        elevate(client)

        response = client.put(
            f"{BASE}/backup-policies/postgresql/shop", json={"exclude_databases": ["blog"]}
        )

        assert response.status_code == 400
        assert list(systemd_dir.iterdir()) == []

    def test_a_name_that_could_be_an_option_is_refused(
        self,
        client: TestClient,
        engine: type[BaseDatabaseManager],
        systemd_dir: Path,
        folders: Path,
    ) -> None:
        elevate(client)

        response = client.put(
            f"{BASE}/backup-policies/postgresql/shop", json={"exclude_tables": ["--help"]}
        )

        assert response.status_code == 400
        assert response.json()["fields"] == {"exclude_tables": response.json()["detail"]}

    def test_running_it_is_a_job_for_the_all_policy(
        self,
        client: TestClient,
        engine: type[BaseDatabaseManager],
        folders: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        created = _capture_queued_jobs(monkeypatch)

        response = client.post(f"{BASE}/backup-policies/postgresql/*/run")

        assert response.status_code == 202, response.text
        assert created[0]["kwargs"]["database"] == "*"

    def test_removing_it_removes_its_timer(
        self,
        client: TestClient,
        engine: type[BaseDatabaseManager],
        systemd_dir: Path,
        folders: Path,
    ) -> None:
        elevate(client)
        client.put(f"{BASE}/backup-policies/postgresql/*", json={})

        deleted = client.delete(f"{BASE}/backup-policies/postgresql/*")

        assert deleted.json()["success"] is True
        assert not (systemd_dir / f"{ALL_UNIT}.timer").exists()


def test_the_upload_directory_is_under_the_backup_directory(
    folders: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert UploadStore().root == folders / ".uploads"
