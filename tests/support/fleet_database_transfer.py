# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_fleet_database_transfer.py`` that other tests build on."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from noust.web.api.databases.common import DatabaseInfoResponse
from noust.web.api.databases.databases import AccessEntryResponse
from noust.web.api.databases.dumps import BackupInfoResponse, CreateBackupRequest
from noust.web.api.databases.engines import EngineInfo
from noust.web.api.databases.imports import ImportRequest, UploadResponse, UploadStartRequest
from noust.web.api.databases.users import CreateUserRequest
from noust.web.pydantic_compat import dump_model
from tests.support.fleet_views_support import Node

DATABASE = "shop"
ENGINE = "postgresql"
DUMP_NAME = "postgresql-shop-20261017_101500.dump"
PAYLOAD = bytes(range(256)) * 1500
SHA = hashlib.sha256(PAYLOAD).hexdigest()
CHUNK = 64 * 1024
SECRET = "s3cr3t-Pa55word-of-shop"


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
