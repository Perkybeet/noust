# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A database moved to another server of the fleet (checklist C-21).

The servers do the work (the dump, the load as the database's owner, the accounts, each through its
own API and its own permissions); the central asks the first one for a dump, reads it by ranges
and writes it, through the second one's upload, in chunks that resume. What these tests pin is each
step that goes wrong without anybody noticing: the bytes that arrive are the bytes that left, a
server that is not ready is refused before a dump is taken, the dump made for the move does not
stay on the source's disk, no password reaches a log or the job's record, and what the source
holds is never touched unless the operator said so.

The fake servers validate every body they are sent with the real request models of the API, so a
field the real route would refuse is refused here too.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore
from noust.fleet.aggregate import Asker, set_aggregator
from noust.web import fleet_jobs
from noust.web.api.databases.common import DatabaseInfoResponse
from noust.web.api.databases.databases import AccessEntryResponse
from noust.web.api.databases.dumps import BackupInfoResponse, CreateBackupRequest
from noust.web.api.databases.engines import EngineInfo
from noust.web.api.databases.imports import ImportRequest, UploadResponse, UploadStartRequest
from noust.web.api.databases.users import CreateUserRequest
from noust.web.fleet_database_actions import transfer_request
from noust.web.fleet_jobs import FleetJobs, Runner, plan, retry_request
from noust.web.pydantic_compat import dump_model
from tests.fleet_views_support import SCHEMA_30, Central, Node, build_central

ADMIN = Asker(actor="ana", scope="admin", role="admin", elevated=True)
NAMES = ("db-1", "db-2", "db-3")
DATABASE = "shop"
ENGINE = "postgresql"
DUMP_NAME = "postgresql-shop-20261017_101500.dump"
PAYLOAD = bytes(range(256)) * 1500
SHA = hashlib.sha256(PAYLOAD).hexdigest()
CHUNK = 64 * 1024
SECRET = "s3cr3t-Pa55word-of-shop"


@pytest.fixture
def central(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")
    built = build_central(tmp_path, NAMES)
    yield built
    set_aggregator(None)
    NoustStore.reset_instance()


class Recorder:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []
        self.published: list[str] = []

    def log(self, message: str, level: str = "info") -> None:
        self.lines.append((level, message))

    def publish(self, snapshot: dict[str, Any], done: int, step: str) -> None:
        # Every state the console would have shown while it ran, not only the last one.
        self.published.append(json.dumps(snapshot, default=str) + step)


def engine_row(
    version: str = "16.4", *, running: bool = True, name: str = ENGINE
) -> dict[str, Any]:
    return dump_model(
        EngineInfo(
            name=name,
            engine_type=name.partition("@")[0],
            display_name="PostgreSQL",
            installed=True,
            version=version,
            running=running,
            port=5432,
            service="postgresql",
        )
    )


def database_row(
    name: str = DATABASE,
    *,
    engine: str = ENGINE,
    apps: tuple[str, ...] = (),
    owner: str | None = "shop",
) -> Any:
    return dump_model(DatabaseInfoResponse(name=name, engine=engine, owner=owner, apps=list(apps)))


def access_row(username: str, profile: str = "read_write", *, internal: bool = False, **extra: Any):
    return dump_model(
        AccessEntryResponse(
            username=username, profile=profile, internal=internal, managed=True, **extra
        )
    )


@dataclass
class Source:
    """A fake server that holds the database: dumps it on request and reads the dump by ranges."""

    node: Node
    databases: list[str] = field(default_factory=lambda: [DATABASE])
    version: str = "16.4"
    owner: str | None = "shop"
    dumps: list[dict[str, Any]] = field(default_factory=list)
    reads: list[tuple[int, int]] = field(default_factory=list)
    deleted_dumps: list[str] = field(default_factory=list)
    dropped: list[dict[str, str]] = field(default_factory=list)
    drop_error: str | None = None
    fail_dump: str | None = None
    short_after: int | None = None
    kept: bool = True
    access: list[dict[str, Any]] = field(default_factory=list)
    passwords: dict[str, str] = field(default_factory=dict)
    reveals: list[str] = field(default_factory=list)
    problem: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        base = "/api/databases"
        handlers = self.node.handlers
        handlers[("GET", f"{base}/databases")] = self._databases
        handlers[("GET", f"{base}/engines")] = self._engines
        handlers[("POST", f"{base}/backups")] = self._dump
        handlers[("GET", f"{base}/backups/{DUMP_NAME}/data")] = self._data
        handlers[("DELETE", f"{base}/backups/{DUMP_NAME}")] = self._delete_dump
        handlers[("GET", f"{base}/databases/{ENGINE}/{DATABASE}/access")] = self._access
        handlers[("DELETE", f"{base}/databases/{ENGINE}/{DATABASE}")] = self._drop
        self.set_passwords(self.passwords)

    def set_passwords(self, passwords: dict[str, str]) -> None:
        self.passwords = passwords
        for username in {entry["username"] for entry in self.access} | set(passwords):
            self.node.handlers[
                ("POST", f"/api/databases/users/{ENGINE}/{username}/password/reveal")
            ] = self._reveal(username)

    def _databases(self, request: httpx.Request) -> httpx.Response:
        rows = [database_row(name, owner=self.owner) for name in self.databases]
        problems = [self.problem] if self.problem else []
        return httpx.Response(
            200, json={"databases": rows, "total": len(rows), "problems": problems}
        )

    def _engines(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"engines": [engine_row(self.version)]})

    @staticmethod
    def info() -> dict[str, Any]:
        return dump_model(
            BackupInfoResponse(
                path=f"/var/backups/noust/databases/postgresql/{DUMP_NAME}",
                name=DUMP_NAME,
                database=DATABASE,
                engine=ENGINE,
                size=len(PAYLOAD),
                size_human="375 KB",
                created="2026-10-17T10:15:00+00:00",
                compressed=False,
                format="custom",
                kind="manual",
                sha256=SHA,
            )
        )

    def _dump(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        CreateBackupRequest(**body)
        self.dumps.append(body)
        if self.fail_dump:
            return self.node.queue(status="failed", error=self.fail_dump)
        self.kept = True
        return self.node.queue(result=self.info())

    def _data(self, request: httpx.Request) -> httpx.Response:
        assert request.url.params["engine"] == ENGINE
        offset = int(request.url.params["offset"])
        length = int(request.url.params["length"])
        self.reads.append((offset, length))
        end = len(PAYLOAD) if self.short_after is None else self.short_after
        return httpx.Response(200, content=PAYLOAD[offset : min(offset + length, end)])

    def _delete_dump(self, request: httpx.Request) -> httpx.Response:
        assert request.url.params["engine"] == ENGINE
        self.deleted_dumps.append(DUMP_NAME)
        self.kept = False
        return httpx.Response(200, json={"success": True, "message": "Backup deleted"})

    def _access(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access": self.access})

    def _reveal(self, username: str):
        def reveal(request: httpx.Request) -> httpx.Response:
            self.reveals.append(username)
            if username not in self.passwords:
                return httpx.Response(
                    400, json={"detail": f"Noust holds no password for {username}", "error": "x"}
                )
            return httpx.Response(
                200, json={"username": username, "password": self.passwords[username]}
            )

        return reveal

    def _drop(self, request: httpx.Request) -> httpx.Response:
        self.dropped.append(dict(request.url.params))
        if self.drop_error:
            return self.node.queue(status="failed", error=self.drop_error)
        return self.node.queue(result={"engine": ENGINE, "database": DATABASE})


@dataclass
class Target:
    """A fake server that takes a database: an upload, an import, and the accounts."""

    node: Node
    version: str = "16.4"
    running: bool = True
    databases: list[str] = field(default_factory=list)
    users: list[str] = field(default_factory=list)
    received: bytearray = field(default_factory=bytearray)
    uploads: list[dict[str, Any]] = field(default_factory=list)
    imports: list[dict[str, Any]] = field(default_factory=list)
    created_users: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    break_after: int | None = None
    breaks: int = 0
    fail_import: str | None = None
    fail_user: str | None = None
    chunk_size: int = CHUNK
    engine_key: str = ENGINE

    def __post_init__(self) -> None:
        base = "/api/databases"
        handlers = self.node.handlers
        handlers[("GET", f"{base}/databases")] = self._databases
        handlers[("GET", f"{base}/engines")] = self._engines
        handlers[("POST", f"{base}/imports/uploads")] = self._start
        handlers[("PUT", f"{base}/imports/uploads/u1")] = self._chunk
        handlers[("GET", f"{base}/imports/uploads/u1")] = self._status
        handlers[("POST", f"{base}/imports/uploads/u1/complete")] = self._complete
        handlers[("DELETE", f"{base}/imports/uploads/u1")] = self._drop
        handlers[("POST", f"{base}/imports")] = self._import
        handlers[("GET", f"{base}/users/{self.engine_key}")] = self._user_list
        handlers[("POST", f"{base}/users")] = self._create_user

    def _databases(self, request: httpx.Request) -> httpx.Response:
        rows = [database_row(name, engine=self.engine_key) for name in self.databases]
        return httpx.Response(200, json={"databases": rows, "total": len(rows), "problems": []})

    def _engines(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "engines": [engine_row(self.version, running=self.running, name=self.engine_key)]
            },
        )

    def _upload(self, *, complete: bool = False) -> dict[str, Any]:
        return dump_model(
            UploadResponse(
                id="u1",
                file_name=DUMP_NAME,
                size=len(PAYLOAD),
                received=len(self.received),
                sha256=SHA,
                created_at="2026-10-17T10:16:00+00:00",
                updated_at="2026-10-17T10:16:00+00:00",
                complete=complete,
                chunk_size=self.chunk_size,
            )
        )

    def _start(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        UploadStartRequest(**body)
        self.uploads.append(body)
        self.received = bytearray()
        return httpx.Response(201, json=self._upload())

    def _chunk(self, request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        if self.break_after is not None and len(self.received) >= self.break_after:
            self.break_after = None
            self.breaks += 1
            raise httpx.ReadError("the tunnel dropped", request=request)
        if offset != len(self.received):
            return httpx.Response(
                409,
                json={
                    "detail": "wrong offset",
                    "error": "upload_offset",
                    "received": len(self.received),
                },
            )
        self.received += request.content
        return httpx.Response(200, json=self._upload())

    def _status(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=self._upload())

    def _complete(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=self._upload(complete=True))

    def _drop(self, request: httpx.Request) -> httpx.Response:
        self.dropped.append("u1")
        return httpx.Response(200, json={"success": True, "message": "Upload removed"})

    def _import(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        ImportRequest(**body)
        self.imports.append({**body, "content": bytes(self.received)})
        if self.fail_import:
            return self.node.queue(status="failed", error=self.fail_import)
        self.databases.append(body["database"])
        return self.node.queue(result={"engine": body["engine"], "database": body["database"]})

    def _user_list(self, request: httpx.Request) -> httpx.Response:
        rows = [
            {"username": name, "engine": self.engine_key, "host": "localhost"}
            for name in self.users
        ]
        return httpx.Response(200, json={"users": rows, "total": len(rows)})

    def _create_user(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        CreateUserRequest(**body)
        self.created_users.append(body)
        if self.fail_user and body["username"] == self.fail_user:
            return httpx.Response(400, json={"detail": "role already exists", "error": "x"})
        self.users.append(body["username"])
        password = body.get("password") or "generated-by-the-target"
        return httpx.Response(
            200,
            json={"username": body["username"], "password": password, "message": "User created"},
        )


@pytest.fixture
def source(central: Central) -> Source:
    return Source(central.nodes["db-1"])


@pytest.fixture
def target(central: Central) -> Target:
    return Target(central.nodes["db-2"])


def request(*nodes: str, **overrides: Any):
    values: dict[str, Any] = {"engine": ENGINE, "from_node": "db-1"}
    values.update(overrides)
    return transfer_request(DATABASE, to=(nodes or ("db-2",))[0], **values)


def run(central: Central, fleet_request, *, poll: float = 0):
    the_plan = plan(fleet_request, manager=central.manager, asker=ADMIN)
    recorder = Recorder()
    jobs = FleetJobs(central.store)
    job_id = fleet_jobs.new_job_id()
    jobs.create(job_id, the_plan, created_by="ana")
    snapshot = Runner(
        plan=the_plan,
        job_id=job_id,
        manager=central.manager,
        asker=ADMIN,
        reporter=recorder,
        elevated_until=float("inf"),
        jobs=jobs,
        poll=poll,
        sleep=lambda seconds: None,
    ).run()
    return snapshot, recorder, job_id


def states(snapshot: dict[str, Any]) -> dict[str, str]:
    return {node["node"]: node["state"] for node in snapshot["nodes"]}


class TestTheTransfer:
    def test_the_dump_arrives_whole_and_is_loaded_by_the_servers_own_import(
        self, central: Central, source: Source, target: Target
    ) -> None:
        snapshot, _, _ = run(central, request())

        assert snapshot["status"] == "succeeded", snapshot
        assert states(snapshot) == {"db-2": "succeeded"}
        assert bytes(target.received) == PAYLOAD
        # The digest and the size the source reported are what the target checks the upload against.
        assert target.uploads == [{"file_name": DUMP_NAME, "size": len(PAYLOAD), "sha256": SHA}]
        (imported,) = target.imports
        assert imported["engine"] == ENGINE and imported["database"] == DATABASE
        assert imported["upload_id"] == "u1"
        assert imported["replace"] is False
        assert imported["safety_backup"] is True
        # Loaded as the database's owner, like any dump from elsewhere: the superuser is a decision
        # the operator takes on the target, never one a central takes for them.
        assert not imported.get("as_superuser")
        assert not imported.get("keep_owners")
        assert target.databases == [DATABASE]

    def test_the_source_is_asked_for_a_dump_of_that_database_only(
        self, central: Central, source: Source, target: Target
    ) -> None:
        run(central, request())

        (asked,) = source.dumps
        assert asked["database"] == DATABASE and asked["engine"] == ENGINE

    def test_it_is_carried_in_pieces_the_servers_can_take(
        self, central: Central, source: Source, target: Target
    ) -> None:
        run(central, request())

        assert source.reads[0][0] == 0
        assert all(length <= 4 * 1024 * 1024 for _, length in source.reads)
        puts = [r for r in central.nodes["db-2"].requests if r.method == "PUT"]
        assert len(puts) == -(-len(PAYLOAD) // CHUNK)
        assert all(len(r.content) <= CHUNK for r in puts)

    def test_the_dump_made_for_the_move_does_not_stay_on_the_source(
        self, central: Central, source: Source, target: Target
    ) -> None:
        run(central, request())

        assert source.deleted_dumps == [DUMP_NAME]

    def test_the_source_database_is_left_alone(
        self, central: Central, source: Source, target: Target
    ) -> None:
        run(central, request())

        assert source.dropped == []
        assert source.databases == [DATABASE]

    def test_the_record_says_where_it_came_from_and_what_was_sent(
        self, central: Central, source: Source, target: Target
    ) -> None:
        snapshot, _, _ = run(central, request())

        item = snapshot["nodes"][0]["items"][0]
        assert item["state"] == "succeeded"
        assert item["from"] == "db-1" and item["database"] == DATABASE
        assert item["engine"] == ENGINE
        assert item["dump"] == {"name": DUMP_NAME, "size": len(PAYLOAD), "sha256": SHA}
        assert item["source_dropped"] is False

    def test_it_can_arrive_under_another_name_and_in_another_instance(
        self, central: Central, source: Source, target: Target
    ) -> None:
        target.engine_key = "postgresql@pg17"
        target.__post_init__()

        snapshot, _, _ = run(
            central, request(new_name="shop_copy", target_engine="postgresql@pg17")
        )

        assert snapshot["status"] == "succeeded", snapshot
        (imported,) = target.imports
        assert imported["engine"] == "postgresql@pg17" and imported["database"] == "shop_copy"

    def test_a_database_the_target_has_is_replaced_only_when_asked(
        self, central: Central, source: Source, target: Target
    ) -> None:
        target.databases = [DATABASE]

        with pytest.raises(ValidationError, match="already on db-2"):
            plan(request(), manager=central.manager, asker=ADMIN)
        snapshot, _, _ = run(central, request(replace=True))

        assert snapshot["status"] == "succeeded", snapshot
        assert target.imports[0]["replace"] is True


class TestTheAccounts:
    def test_without_asking_no_account_is_read_or_made(
        self, central: Central, source: Source, target: Target
    ) -> None:
        source.access = [access_row("shop_rw")]
        source.set_passwords({"shop_rw": SECRET})

        run(central, request())

        assert source.reveals == [] and target.created_users == []
        paths = [r.url.path for r in central.nodes["db-1"].requests]
        assert not any(path.endswith("/access") for path in paths)

    def test_an_account_arrives_with_the_password_and_the_profile_it_had(
        self, central: Central, source: Source, target: Target
    ) -> None:
        source.access = [access_row("shop_rw", "read_write"), access_row("shop_ro", "read_only")]
        source.set_passwords({"shop_rw": SECRET, "shop_ro": "another-Secret-1"})

        snapshot, _, _ = run(central, request(accounts=True))

        assert snapshot["status"] == "succeeded", snapshot
        made = {body["username"]: body for body in target.created_users}
        assert made["shop_rw"]["password"] == SECRET
        assert made["shop_rw"]["profile"] == "read_write"
        assert made["shop_rw"]["database"] == DATABASE
        assert made["shop_rw"]["engine"] == ENGINE
        assert made["shop_ro"]["profile"] == "read_only"
        # After the data: an account's profile is a grant on a database that has to exist.
        paths = [(r.method, r.url.path) for r in central.nodes["db-2"].requests]
        assert paths.index(("POST", "/api/databases/imports")) < paths.index(
            ("POST", "/api/databases/users")
        )

    def test_a_password_never_reaches_a_log_or_the_job_record(
        self, central: Central, source: Source, target: Target
    ) -> None:
        source.access = [access_row("shop_rw")]
        source.set_passwords({"shop_rw": SECRET})

        snapshot, recorder, job_id = run(central, request(accounts=True))

        assert SECRET not in json.dumps(snapshot)
        assert recorder.published
        assert all(SECRET not in shown for shown in recorder.published)
        assert all(SECRET not in line for _, line in recorder.lines)
        stored = FleetJobs(central.store).get(job_id)
        assert SECRET not in json.dumps(stored, default=str)

    def test_the_engines_own_accounts_and_ones_the_target_has_are_left_as_they_are(
        self, central: Central, source: Source, target: Target
    ) -> None:
        source.access = [
            access_row("postgres", "owner", internal=True),
            access_row("shop_rw"),
            access_row("shop_ro", "read_only"),
        ]
        source.set_passwords({"shop_rw": SECRET, "shop_ro": "x-Secret-2"})
        target.users = ["shop_ro"]

        snapshot, _, _ = run(central, request(accounts=True))

        assert [body["username"] for body in target.created_users] == ["shop_rw"]
        accounts = snapshot["nodes"][0]["items"][0]["accounts"]
        assert accounts["created"] == ["shop_rw"] and accounts["existing"] == ["shop_ro"]
        assert source.reveals == ["shop_rw"]

    def test_a_password_noust_does_not_hold_is_made_new_on_the_target_and_named(
        self, central: Central, source: Source, target: Target
    ) -> None:
        source.access = [access_row("shop_rw")]
        source.set_passwords({})
        source.node.handlers[("POST", f"/api/databases/users/{ENGINE}/shop_rw/password/reveal")] = (
            source._reveal("shop_rw")
        )

        snapshot, recorder, _ = run(central, request(accounts=True))

        assert snapshot["status"] == "succeeded", snapshot
        (made,) = target.created_users
        assert "password" not in made or made["password"] is None
        accounts = snapshot["nodes"][0]["items"][0]["accounts"]
        assert accounts["new_password"] == ["shop_rw"]
        assert "generated-by-the-target" not in json.dumps(snapshot)

    def test_an_account_that_cannot_be_made_fails_the_server_after_the_others_were_tried(
        self, central: Central, source: Source, target: Target
    ) -> None:
        source.access = [access_row("shop_bad"), access_row("shop_good")]
        source.set_passwords({"shop_bad": "a-Secret-3", "shop_good": "b-Secret-4"})
        target.fail_user = "shop_bad"

        snapshot, _, _ = run(central, request(accounts=True, drop_source=True))

        assert states(snapshot) == {"db-2": "failed"}
        assert "shop_bad" in snapshot["nodes"][0]["error"]["message"]
        assert "role already exists" in (snapshot["nodes"][0]["output"] or "")
        assert [body["username"] for body in target.created_users] == ["shop_bad", "shop_good"]
        # The data is there, and the source is not dropped on the strength of half a move.
        assert target.databases == [DATABASE] and source.dropped == []
        assert snapshot["nodes"][0]["items"][0]["data_loaded"] is True


class TestTheOwner:
    """The database belongs on the target to the account it belongs to on the source."""

    @staticmethod
    def owned(source: Source, owner: str | None = "shopapp") -> None:
        source.owner = owner
        source.access = [access_row("shopapp", "owner"), access_row("shop_rw")]
        source.set_passwords({"shopapp": "owner-Secret-1", "shop_rw": SECRET})

    def test_the_owners_account_is_made_first_and_the_database_is_made_with_it(
        self, central: Central, source: Source, target: Target
    ) -> None:
        self.owned(source)

        snapshot, _, _ = run(central, request(accounts=True))

        assert snapshot["status"] == "succeeded", snapshot
        first, second = target.created_users
        assert first["username"] == "shopapp" and "database" not in first
        assert second["username"] == "shop_rw" and second["database"] == DATABASE
        (imported,) = target.imports
        assert imported["owner"] == "shopapp"
        paths = [(r.method, r.url.path) for r in central.nodes["db-2"].requests]
        users = [i for i, p in enumerate(paths) if p == ("POST", "/api/databases/users")]
        loading = paths.index(("POST", "/api/databases/imports"))
        assert users[0] < loading < users[1]
        accounts = snapshot["nodes"][0]["items"][0]["accounts"]
        assert accounts["created"] == ["shopapp", "shop_rw"]

    def test_an_owner_is_not_made_twice(
        self, central: Central, source: Source, target: Target
    ) -> None:
        self.owned(source)

        run(central, request(accounts=True))

        assert [body["username"] for body in target.created_users].count("shopapp") == 1

    def test_without_the_accounts_nobody_is_named_the_owner(
        self, central: Central, source: Source, target: Target
    ) -> None:
        self.owned(source)

        run(central, request())

        (imported,) = target.imports
        assert not imported.get("owner")

    def test_an_owner_the_target_already_has_is_used_and_not_made(
        self, central: Central, source: Source, target: Target
    ) -> None:
        self.owned(source)
        target.users = ["shopapp"]

        snapshot, _, _ = run(central, request(accounts=True))

        assert snapshot["status"] == "succeeded", snapshot
        assert [body["username"] for body in target.created_users] == ["shop_rw"]
        assert target.imports[0]["owner"] == "shopapp"
        assert snapshot["nodes"][0]["items"][0]["accounts"]["existing"] == ["shopapp"]

    def test_the_engines_own_account_is_never_asked_for(
        self, central: Central, source: Source, target: Target
    ) -> None:
        source.owner = "postgres"
        source.access = [access_row("postgres", "owner", internal=True), access_row("shop_rw")]
        source.set_passwords({"shop_rw": SECRET})

        run(central, request(accounts=True))

        assert not target.imports[0].get("owner")

    def test_an_owner_the_source_does_not_list_is_not_asked_for(
        self, central: Central, source: Source, target: Target
    ) -> None:
        source.owner = "ghost"
        source.access = [access_row("shop_rw")]
        source.set_passwords({"shop_rw": SECRET})

        run(central, request(accounts=True))

        assert not target.imports[0].get("owner")

    def test_an_owner_that_cannot_be_made_stops_before_anything_is_dumped(
        self, central: Central, source: Source, target: Target
    ) -> None:
        self.owned(source)
        target.fail_user = "shopapp"

        snapshot, _, _ = run(central, request(accounts=True))

        assert states(snapshot) == {"db-2": "failed"}
        assert "shopapp" in snapshot["nodes"][0]["error"]["message"]
        assert source.dumps == [] and target.uploads == [] and target.imports == []


class TestDroppingTheSource:
    def test_it_is_dropped_after_a_last_dump_when_that_is_asked(
        self, central: Central, source: Source, target: Target
    ) -> None:
        snapshot, _, _ = run(central, request(drop_source=True))

        assert snapshot["status"] == "succeeded", snapshot
        (params,) = source.dropped
        assert params["keep_backup"] == "true"
        assert params.get("force", "false") == "false" and params.get("unlink", "false") == "false"
        assert snapshot["nodes"][0]["items"][0]["source_dropped"] is True

    def test_a_source_an_application_still_uses_is_kept_and_said_so(
        self, central: Central, source: Source, target: Target
    ) -> None:
        source.drop_error = "shop is used by shop.example.com"

        snapshot, _, _ = run(central, request(drop_source=True))

        assert states(snapshot) == {"db-2": "succeeded"}
        item = snapshot["nodes"][0]["items"][0]
        assert item["source_dropped"] is False
        assert any("shop.example.com" in warning for warning in item["warnings"])

    def test_nothing_is_dropped_when_the_load_failed(
        self, central: Central, source: Source, target: Target
    ) -> None:
        target.fail_import = "pg_restore: error: could not execute query"

        run(central, request(drop_source=True))

        assert source.dropped == []


class TestWhenSomethingGoesWrong:
    def test_a_dump_that_fails_stops_with_the_sources_words_and_sends_nothing(
        self, central: Central, source: Source, target: Target
    ) -> None:
        source.fail_dump = "pg_dump: error: connection to server failed"

        snapshot, _, _ = run(central, request())

        assert states(snapshot) == {"db-2": "failed"}
        assert "pg_dump: error: connection to server failed" in (
            snapshot["nodes"][0]["output"] or ""
        )
        assert "db-1" in snapshot["nodes"][0]["error"]["message"]
        assert target.uploads == [] and target.imports == []

    def test_a_load_that_fails_keeps_the_targets_words_and_still_lets_go_of_the_dump(
        self, central: Central, source: Source, target: Target
    ) -> None:
        target.fail_import = "pg_restore: error: could not execute query: relation exists"

        snapshot, _, _ = run(central, request())

        assert states(snapshot) == {"db-2": "failed"}
        assert "relation exists" in (snapshot["nodes"][0]["output"] or "")
        assert source.deleted_dumps == [DUMP_NAME]
        # The upload is a copy of the database on the target's disk: it does not stay a day.
        assert target.dropped == ["u1"]

    def test_a_broken_transfer_resumes_where_the_target_stands(
        self, central: Central, source: Source, target: Target
    ) -> None:
        target.break_after = CHUNK * 5

        snapshot, recorder, _ = run(central, request())

        assert snapshot["status"] == "succeeded", snapshot
        assert target.breaks == 1
        assert bytes(target.received) == PAYLOAD
        assert any("resuming" in line for _, line in recorder.lines)

    def test_a_transfer_that_keeps_breaking_gives_up_drops_the_upload_and_the_dump(
        self, central: Central, source: Source, target: Target
    ) -> None:
        central.nodes["db-2"].handlers[("PUT", "/api/databases/imports/uploads/u1")] = (
            lambda request: (_ for _ in ()).throw(
                httpx.ReadError("the tunnel dropped", request=request)
            )
        )

        snapshot, _, _ = run(central, request())

        assert states(snapshot) == {"db-2": "failed"}
        assert "failed 6 times in a row" in snapshot["nodes"][0]["error"]["message"]
        assert target.dropped == ["u1"]
        assert target.imports == []
        assert source.deleted_dumps == [DUMP_NAME]

    def test_a_dump_that_ends_early_is_a_failure_and_not_a_short_database(
        self, central: Central, source: Source, target: Target
    ) -> None:
        source.short_after = CHUNK * 3

        snapshot, _, _ = run(central, request())

        assert states(snapshot) == {"db-2": "failed"}
        assert "ended at" in snapshot["nodes"][0]["error"]["message"]
        assert target.imports == [] and target.dropped == ["u1"]

    def test_a_retry_takes_a_new_dump_and_carries_it_again(
        self, central: Central, source: Source, target: Target
    ) -> None:
        target.fail_import = "disk full"
        snapshot, _, job_id = run(central, request())
        assert states(snapshot) == {"db-2": "failed"}
        target.fail_import = None

        again, _, _ = run(central, retry_request(job_id, FleetJobs(central.store)))

        assert again["status"] == "succeeded", again
        assert len(source.dumps) == 2
        assert bytes(target.received) == PAYLOAD


class TestBeforeAnythingIsDumped:
    @pytest.mark.parametrize(
        "options",
        [
            {"engine": "redis", "from_node": "db-1"},
            {"engine": "clickhouse", "from_node": "db-1"},
            {"engine": ENGINE, "from_node": "db-1", "target_engine": "mongodb"},
        ],
    )
    def test_an_engine_whose_databases_do_not_travel_by_dump_is_refused(
        self, central: Central, source: Source, target: Target, options: dict[str, Any]
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            plan(
                request(**{k: v for k, v in options.items() if k != "from_node"}),
                manager=central.manager,
                asker=ADMIN,
            )

        assert source.dumps == []
        assert caught.value.field in ("engine", "target_engine")

    def test_the_source_is_not_also_the_target(
        self, central: Central, source: Source, target: Target
    ) -> None:
        with pytest.raises(ValidationError, match="same server"):
            plan(request("db-1"), manager=central.manager, asker=ADMIN)

    def test_a_database_goes_to_one_server(
        self, central: Central, source: Source, target: Target
    ) -> None:
        two = transfer_request(DATABASE, to="db-2", engine=ENGINE, from_node="db-1")
        two.nodes.append("db-3")

        with pytest.raises(ValidationError, match="one server"):
            plan(two, manager=central.manager, asker=ADMIN)

    def test_a_source_that_is_not_a_node_is_refused(
        self, central: Central, source: Source, target: Target
    ) -> None:
        with pytest.raises(Exception, match="No node named ghost"):
            plan(request(from_node="ghost"), manager=central.manager, asker=ADMIN)

    def test_a_database_the_source_does_not_have_is_refused(
        self, central: Central, source: Source, target: Target
    ) -> None:
        source.databases = []

        with pytest.raises(ValidationError, match="not a database of postgresql on db-1"):
            plan(request(), manager=central.manager, asker=ADMIN)
        assert source.dumps == []

    def test_an_engine_the_source_could_not_read_is_not_taken_for_an_empty_one(
        self, central: Central, source: Source, target: Target
    ) -> None:
        source.databases = []
        source.problem = {
            "engine": ENGINE,
            "display_name": "PostgreSQL",
            "message": "Cannot sign in",
            "hint": "",
            "output": "FATAL: password authentication failed",
            "access": True,
        }

        with pytest.raises(ValidationError, match="could not read the databases of postgresql"):
            plan(request(), manager=central.manager, asker=ADMIN)

    def test_an_engine_that_is_not_running_on_the_target_is_refused(
        self, central: Central, source: Source, target: Target
    ) -> None:
        target.running = False

        with pytest.raises(ValidationError, match="not running on db-2"):
            plan(request(), manager=central.manager, asker=ADMIN)

    def test_a_target_older_than_the_source_is_refused(
        self, central: Central, source: Source, target: Target
    ) -> None:
        target.version = "15.8"

        with pytest.raises(ValidationError) as caught:
            plan(request(), manager=central.manager, asker=ADMIN)

        assert "older than the source" in caught.value.message
        assert source.dumps == []

    def test_a_newer_target_is_fine(self, central: Central, source: Source, target: Target) -> None:
        target.version = "17.2"

        snapshot, _, _ = run(central, request())

        assert snapshot["status"] == "succeeded", snapshot

    def test_a_source_that_cannot_serve_a_dump_is_named_as_too_old(
        self, central: Central, source: Source, target: Target
    ) -> None:
        central.nodes["db-1"].schema = dict(SCHEMA_30)

        with pytest.raises(ValidationError, match="does not offer"):
            plan(request(), manager=central.manager, asker=ADMIN)

    def test_a_source_that_cannot_give_its_accounts_is_named_when_they_are_asked_for(
        self, central: Central, source: Source, target: Target
    ) -> None:
        schema = dict(central.nodes["db-1"].schema)
        del schema["/api/databases/users/{engine}/{username}/password/reveal"]
        central.nodes["db-1"].schema = schema

        plan(request(), manager=central.manager, asker=ADMIN)
        with pytest.raises(ValidationError, match="does not offer"):
            plan(request(accounts=True), manager=central.manager, asker=ADMIN)

    def test_a_target_that_cannot_take_a_dump_is_skipped_as_too_old(
        self, central: Central, source: Source, target: Target
    ) -> None:
        central.nodes["db-2"].schema = dict(SCHEMA_30)

        described = plan(request(), manager=central.manager, asker=ADMIN).to_dict()

        assert {n["node"]: n.get("reason") for n in described["nodes"]}["db-2"] == "unsupported"

    def test_a_source_that_lets_its_central_only_read_is_refused(
        self, central: Central, source: Source, target: Target
    ) -> None:
        central.store.set_node_access("db-1", "read", False)

        with pytest.raises(ValidationError, match="at most 'read"):
            plan(request(), manager=central.manager, asker=ADMIN)

    @pytest.mark.parametrize(
        "options",
        [
            {"engine": ENGINE, "database": "../etc", "from_node": "db-1"},
            {"engine": ENGINE, "database": "shop\n", "from_node": "db-1"},
            {"engine": "pg;rm", "database": DATABASE, "from_node": "db-1"},
            {"engine": ENGINE, "database": DATABASE},
            {"engine": ENGINE, "database": DATABASE, "from_node": "db-1", "replace": "yes"},
            {"engine": ENGINE, "database": DATABASE, "from_node": "db-1", "extra": 1},
            {"engine": ENGINE, "database": DATABASE, "from_node": "db-1", "new_name": "a b"},
        ],
    )
    def test_options_that_are_not_valid_are_refused(
        self, central: Central, source: Source, target: Target, options: Any
    ) -> None:
        with pytest.raises(ValidationError):
            plan(
                fleet_jobs.FleetRequest("database_transfer", nodes=["db-2"], options=options),
                manager=central.manager,
                asker=ADMIN,
            )


class TestThePlan:
    def test_it_needs_sudo_mode_and_says_what_will_happen(
        self, central: Central, source: Source, target: Target
    ) -> None:
        described = plan(request(accounts=True), manager=central.manager, asker=ADMIN).to_dict()

        assert described["requires_elevation"] is True
        notes = " ".join(described["notes"])
        assert "db-1" in notes and "db-2" in notes
        assert "owner" in notes
        assert "accounts" in notes

    def test_it_says_the_source_is_kept_unless_dropped(
        self, central: Central, source: Source, target: Target
    ) -> None:
        kept = " ".join(plan(request(), manager=central.manager, asker=ADMIN).to_dict()["notes"])
        dropped = " ".join(
            plan(request(drop_source=True), manager=central.manager, asker=ADMIN).to_dict()["notes"]
        )

        assert "stays" in kept and "writes" in kept
        assert "dropped" in dropped and "last dump" in dropped

    def test_the_request_names_the_action_and_the_one_server(self) -> None:
        built = transfer_request(
            DATABASE,
            to="db-2",
            engine=ENGINE,
            from_node="db-1",
            new_name="copy",
            replace=True,
            accounts=True,
            drop_source=True,
        )

        assert built.action == "database_transfer" and built.nodes == ["db-2"]
        assert built.options == {
            "database": DATABASE,
            "engine": ENGINE,
            "from_node": "db-1",
            "target_engine": None,
            "new_name": "copy",
            "replace": True,
            "accounts": True,
            "drop_source": True,
        }


class TestTheModulesLoadInAnyOrder:
    """
    The fleet's job module loads the action modules at its end, and they load the shared
    carrying: a process whose first fleet import is any one of them must still find them all made.
    The central's API imports the action only when a request needs it, so the first import of a
    running central is whichever route is asked first.
    """

    @pytest.mark.parametrize(
        "first",
        [
            "noust.web.fleet_carry",
            "noust.web.fleet_database_actions",
            "noust.web.fleet_ship_actions",
            "noust.web.fleet_jobs",
        ],
    )
    def test_whichever_is_imported_first_registers_every_action(
        self, first: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import noust.web

        fleet = [name for name in sys.modules if name.startswith("noust.web.fleet_")]
        saved = {name: sys.modules[name] for name in fleet}
        for name in fleet:
            monkeypatch.delitem(sys.modules, name)
            # `from noust.web import fleet_jobs` looks at the package's attribute first.
            monkeypatch.delattr(noust.web, name.rpartition(".")[2], raising=False)
        try:
            importlib.import_module(first)
            jobs = importlib.import_module("noust.web.fleet_jobs")
            importlib.import_module("noust.web.fleet_database_actions")
            assert {
                "placement_ship",
                "placement_copy",
                "placement_rollout",
                "database_transfer",
            } <= set(jobs.ACTIONS)
        finally:
            for name in [n for n in sys.modules if n.startswith("noust.web.fleet_")]:
                del sys.modules[name]
            sys.modules.update(saved)
            for name, module in saved.items():
                setattr(noust.web, name.rpartition(".")[2], module)
