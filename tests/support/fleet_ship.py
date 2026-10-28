# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_fleet_ship.py`` that other tests build on."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from noust.core.store import NoustStore
from noust.fleet.aggregate import Asker, set_aggregator
from noust.web import fleet_jobs
from noust.web.fleet_jobs import FleetJobs, Runner, plan
from tests.support.fleet_views_support import Central, Node, build_central

ADMIN = Asker(actor="ana", scope="admin", role="admin", elevated=True)
DOMAIN = "shop.example.com"
NAMES = ("build-1", "web-1", "web-2", "web-3")
PAYLOAD = bytes(range(256)) * 2000
SHA = hashlib.sha256(PAYLOAD).hexdigest()
COMMIT = "feed123" + "0" * 33
BUNDLE_ID = "b" * 32
RELEASE = "20261016-101500-feed123"
CHUNK = 64 * 1024


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

    def log(self, message: str, level: str = "info") -> None:
        self.lines.append((level, message))

    def publish(self, snapshot: dict[str, Any], done: int, step: str) -> None:
        pass


def row(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "name": DOMAIN,
        "domain": DOMAIN,
        "status": "running",
        "layout": "releases",
        "app_type": "nodejs",
        "port": 3000,
        "last_deployment": {"id": 1, "status": "success", "git_commit": "aaa1111"},
    }
    values.update(overrides)
    return values


@dataclass
class Builder:
    """A fake build server: builds on request, keeps the bundle, reads it by ranges."""

    node: Node
    builds: list[dict[str, Any]] = field(default_factory=list)
    reads: list[tuple[int, int]] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    kept: bool = True
    fail_build: str | None = None

    def __post_init__(self) -> None:
        self.node.apps = [row()]
        base = f"/api/apps/{DOMAIN}/releases"
        self.node.handlers[("POST", f"{base}/bundle")] = self._build
        self.node.handlers[("GET", f"{base}/bundles/{BUNDLE_ID}")] = self._info
        self.node.handlers[("GET", f"{base}/bundles/{BUNDLE_ID}/data")] = self._data
        self.node.handlers[("DELETE", f"{base}/bundles/{BUNDLE_ID}")] = self._delete

    def _info(self, request: httpx.Request) -> httpx.Response:
        if not self.kept:
            return httpx.Response(400, json={"detail": "There is no such bundle"})
        return httpx.Response(200, json=self.bundle())

    @staticmethod
    def bundle() -> dict[str, Any]:
        return {
            "id": BUNDLE_ID,
            "domain": DOMAIN,
            "release_id": RELEASE,
            "commit": COMMIT,
            "sha256": SHA,
            "size": len(PAYLOAD),
            "files": 10,
            "built_at": "2026-10-16T10:15:00+00:00",
            "builder": "build-1",
            "noust_version": "3.4.0",
            "app_type": "nodejs",
        }

    def _build(self, request: httpx.Request) -> httpx.Response:
        self.builds.append(json.loads(request.content or b"{}"))
        if self.fail_build:
            return self.node.queue(status="failed", error=self.fail_build)
        self.kept = True
        return self.node.queue(
            result={"domain": DOMAIN, "status": "packed", "bundle": self.bundle()}
        )

    def _data(self, request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        length = int(request.url.params["length"])
        self.reads.append((offset, length))
        return httpx.Response(200, content=PAYLOAD[offset : offset + length])

    def _delete(self, request: httpx.Request) -> httpx.Response:
        self.deleted.append(BUNDLE_ID)
        self.kept = False
        return httpx.Response(204)


@dataclass
class Target:
    """A fake server that receives bundles and activates them."""

    node: Node
    commit: str = "aaa1111"
    received: bytearray = field(default_factory=bytearray)
    receives: list[dict[str, Any]] = field(default_factory=list)
    activations: list[str] = field(default_factory=list)
    uploads: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    break_after: int | None = None
    breaks: int = 0
    fail_receive: str | None = None
    status_after: str = "running"
    layout: str = "releases"
    app_type: str = "nodejs"
    chunk_size: int = CHUNK

    def __post_init__(self) -> None:
        self.refresh()
        base = f"/api/apps/{DOMAIN}/releases"
        handlers = self.node.handlers
        handlers[("POST", f"{base}/uploads")] = self._start
        handlers[("PUT", f"{base}/uploads/u1")] = self._chunk
        handlers[("GET", f"{base}/uploads/u1")] = self._status
        handlers[("POST", f"{base}/uploads/u1/complete")] = self._complete
        handlers[("DELETE", f"{base}/uploads/u1")] = self._drop
        handlers[("POST", f"{base}/receive")] = self._receive
        handlers[("GET", base)] = self._releases
        handlers[("POST", f"{base}/r1/activate")] = self._activate

    def refresh(self) -> None:
        self.node.apps = [
            row(
                status=self.status_after if self.commit != "aaa1111" else "running",
                layout=self.layout,
                app_type=self.app_type,
                last_deployment={"id": 1, "status": "success", "git_commit": self.commit},
            )
        ]

    def _upload(self) -> dict[str, Any]:
        return {
            "id": "u1",
            "size": len(PAYLOAD),
            "received": len(self.received),
            "sha256": SHA,
            "complete": False,
            "chunk_size": self.chunk_size,
        }

    def _start(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
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
        return httpx.Response(200, json={**self._upload(), "complete": True})

    def _drop(self, request: httpx.Request) -> httpx.Response:
        self.dropped.append("u1")
        return httpx.Response(204)

    def _receive(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.receives.append({**body, "content": bytes(self.received)})
        if self.fail_receive:
            return self.node.queue(status="failed", error=self.fail_receive)
        self.commit = COMMIT
        self.refresh()
        return self.node.queue(
            result={
                "domain": DOMAIN,
                "status": "received",
                "release_id": RELEASE,
                "commit": COMMIT,
                "deployment_id": 7,
                "built_on": "build-1",
            }
        )

    def _releases(self, request: httpx.Request) -> httpx.Response:
        items = [
            {"id": "r1", "commit": "aaa1111", "active": self.commit == "aaa1111", "on_disk": True}
        ]
        if self.commit != "aaa1111":
            items.insert(0, {"id": RELEASE, "commit": COMMIT, "active": True, "on_disk": True})
            items[1]["active"] = False
        return httpx.Response(200, json={"domain": DOMAIN, "items": items, "total": len(items)})

    def _activate(self, request: httpx.Request) -> httpx.Response:
        self.activations.append("r1")
        self.commit = "aaa1111"
        self.refresh()
        return httpx.Response(
            200, json={"domain": DOMAIN, "release_id": "r1", "changed": True, "rolled_back": True}
        )


@pytest.fixture
def builder(central: Central) -> Builder:
    return Builder(central.nodes["build-1"])


@pytest.fixture
def targets(central: Central) -> dict[str, Target]:
    return {name: Target(central.nodes[name]) for name in ("web-1", "web-2", "web-3")}


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
