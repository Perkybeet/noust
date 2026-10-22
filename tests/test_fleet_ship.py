# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Building an application on one server and running it on others (checklist A-49).

The servers do the work (the build, the checks, the activation behind the health gate, each through
its own API and its own permissions); the central picks the builder, asks it for the bundle once,
carries it in chunks through the two tunnels to each target, and puts the targets that moved back
when the job stops. What these tests pin is each step that goes wrong without anybody noticing:
the build is made once however many servers receive it, a bundle is sent exactly as the builder
reported it, a transfer that breaks resumes where the target stands, a server that is not ready is
refused before anything is built, and the builder lets go of the bundle only when every server has
it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore
from noust.fleet.aggregate import Asker, set_aggregator
from noust.fleet.builders import Builders
from noust.web import fleet_jobs
from noust.web.fleet_jobs import FleetJobs, Runner, plan, retry_request
from noust.web.fleet_ship_actions import ship_request
from tests.fleet_views_support import SCHEMA_30, Central, Node, build_central

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


def request(*nodes: str, **overrides: Any):
    values: dict[str, Any] = {"builder": "build-1", "rollback_on_failure": True}
    values.update(overrides)
    return ship_request(DOMAIN, list(nodes or ("web-1", "web-2")), **values)


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


class TestShipping:
    def test_the_release_is_built_once_and_every_server_gets_those_bytes(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        snapshot, _, _ = run(central, request("web-1", "web-2"))

        assert snapshot["status"] == "succeeded", snapshot
        assert states(snapshot) == {"web-1": "succeeded", "web-2": "succeeded"}
        # One build, however many servers: that is what a build server is for.
        assert len(builder.builds) == 1
        for name in ("web-1", "web-2"):
            target = targets[name]
            assert bytes(target.received) == PAYLOAD
            assert target.uploads == [{"size": len(PAYLOAD), "sha256": SHA}]
            assert target.receives[0]["upload_id"] == "u1"
            assert target.receives[0]["schema_changed_ok"] is False
            assert target.commit == COMMIT

    def test_the_first_server_goes_alone_and_the_rest_are_sent_what_it_pinned(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        the_plan = plan(
            request("web-1", "web-2", "web-3", serial=3), manager=central.manager, asker=ADMIN
        )

        described = the_plan.to_dict()
        assert the_plan.request.canary == "web-1"
        assert [node["batch"] for node in described["nodes"]] == [0, 1, 1]
        assert any("goes first and alone" in note for note in described["notes"])

    def test_it_is_carried_in_chunks_the_builder_never_holds_back(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        run(central, request("web-1"))

        # The target said how big a chunk may be, and no more than that was sent at once.
        assert len(builder.reads) >= 1
        assert all(length <= 4 * 1024 * 1024 for _, length in builder.reads)
        assert builder.reads[0][0] == 0
        puts = [r for r in central.nodes["web-1"].requests if r.method == "PUT"]
        assert len(puts) == -(-len(PAYLOAD) // CHUNK)
        assert all(len(r.content) <= CHUNK for r in puts)

    def test_the_builder_lets_go_of_the_bundle_when_everyone_has_it(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        run(central, request("web-1", "web-2"))

        assert builder.deleted == [BUNDLE_ID]

    def test_the_commit_and_the_confirmations_travel(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        run(
            central,
            request("web-1", commit="feed123", schema_changed_ok=True, frozen_ok=True, keep=True),
        )

        assert builder.builds == [{"keep": True, "commit": "feed123"}]
        assert targets["web-1"].receives[0]["schema_changed_ok"] is True
        assert targets["web-1"].receives[0]["frozen_ok"] is True

    def test_a_server_already_on_the_commit_is_left_alone(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        targets["web-2"].commit = COMMIT
        targets["web-2"].refresh()

        snapshot, _, _ = run(central, request("web-1", "web-2"))

        assert states(snapshot) == {"web-1": "succeeded", "web-2": "skipped"}
        assert targets["web-2"].uploads == []

    def test_each_server_records_where_the_release_came_from(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        snapshot, _, _ = run(central, request("web-1"))

        item = snapshot["nodes"][0]["items"][0]
        assert item["bundle"]["built_on"] == "build-1"
        assert item["bundle"]["release_id"] == RELEASE and item["bundle"]["sha256"] == SHA
        assert item["state"] == "succeeded" and item["changed"] is True
        assert item["before"]["commit"] == "aaa1111"
        assert item["after"]["commit"] == COMMIT


class TestBeforeAnythingIsBuilt:
    def test_a_ship_names_its_builder(self, central: Central, targets: dict[str, Target]) -> None:
        with pytest.raises(ValidationError) as caught:
            plan(request(builder=None), manager=central.manager, asker=ADMIN)

        assert "Say which server builds" in caught.value.message
        assert "noust fleet builder set" in (caught.value.details or "")

    def test_the_recorded_builder_is_used_when_none_is_named(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        Builders(central.store).set(DOMAIN, "build-1")

        snapshot, _, _ = run(central, request("web-1", builder=None))

        assert snapshot["status"] == "succeeded", snapshot
        assert snapshot["options"]["builder"] == "build-1"

    def test_the_builder_cannot_also_receive(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            plan(request("build-1", "web-1"), manager=central.manager, asker=ADMIN)

        assert "also a server to ship it to" in caught.value.message

    def test_a_builder_that_is_not_a_node_is_refused(
        self, central: Central, targets: dict[str, Target]
    ) -> None:
        with pytest.raises(Exception, match="No node named ghost"):
            plan(request("web-1", builder="ghost"), manager=central.manager, asker=ADMIN)

    def test_the_builder_needs_the_application(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        central.nodes["build-1"].apps = []

        with pytest.raises(ValidationError) as caught:
            plan(request("web-1"), manager=central.manager, asker=ADMIN)

        assert "not installed on build-1" in caught.value.message
        assert "noust fleet placement copy" in (caught.value.details or "")
        assert builder.builds == []

    def test_the_builder_needs_the_release_layout(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        central.nodes["build-1"].apps = [row(layout="inplace")]

        with pytest.raises(ValidationError, match="release layout on build-1"):
            plan(request("web-1"), manager=central.manager, asker=ADMIN)

    def test_a_target_of_another_type_is_refused(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        targets["web-2"].app_type = "python"
        targets["web-2"].refresh()

        with pytest.raises(ValidationError) as caught:
            plan(request("web-1", "web-2"), manager=central.manager, asker=ADMIN)

        assert "python on web-2 and nodejs on build-1" in caught.value.message
        assert builder.builds == []

    def test_a_target_without_the_application_is_refused(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        central.nodes["web-2"].apps = []

        with pytest.raises(ValidationError, match="not installed on web-2"):
            plan(request("web-1", "web-2"), manager=central.manager, asker=ADMIN)

    def test_a_target_in_place_is_refused(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        targets["web-1"].layout = "inplace"
        targets["web-1"].refresh()

        with pytest.raises(ValidationError, match="release layout on web-1"):
            plan(request("web-1"), manager=central.manager, asker=ADMIN)

    def test_a_builder_that_cannot_ship_is_named_as_too_old(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        central.nodes["build-1"].schema = dict(SCHEMA_30)

        with pytest.raises(ValidationError, match="does not offer"):
            plan(request("web-1"), manager=central.manager, asker=ADMIN)

    def test_a_target_that_cannot_receive_is_skipped_as_too_old(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        central.nodes["web-2"].schema = dict(SCHEMA_30)

        described = plan(request("web-1", "web-2"), manager=central.manager, asker=ADMIN).to_dict()

        assert {n["node"]: n.get("reason") for n in described["nodes"]}["web-2"] == "unsupported"

    @pytest.mark.parametrize(
        "options",
        [
            {"domain": "https://x.example.com", "builder": "build-1"},
            {"domain": DOMAIN, "builder": "build-1", "commit": "main"},
            {"domain": DOMAIN, "builder": "build-1", "frozen_ok": "yes"},
            {"domain": DOMAIN, "builder": "build-1", "extra": 1},
            {"domain": DOMAIN, "builder": "build-1", "bundle": {"id": 1}},
        ],
    )
    def test_options_that_are_not_valid_are_refused(
        self, central: Central, builder: Builder, targets: dict[str, Target], options: Any
    ) -> None:
        with pytest.raises(ValidationError):
            plan(
                fleet_jobs.FleetRequest("placement_ship", nodes=["web-1"], options=options),
                manager=central.manager,
                asker=ADMIN,
            )


class TestWhenSomethingGoesWrong:
    def test_a_build_that_fails_stops_everything_with_the_builders_words(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        builder.fail_build = "npm ERR! missing script: build"

        snapshot, _, _ = run(central, request("web-1", "web-2"))

        assert snapshot["status"] == "aborted"
        assert states(snapshot) == {"web-1": "failed", "web-2": "skipped"}
        assert "npm ERR! missing script: build" in (snapshot["nodes"][0]["output"] or "")
        assert "build-1" in snapshot["nodes"][0]["error"]["message"]
        assert targets["web-1"].uploads == [] and builder.deleted == []

    def test_a_broken_transfer_resumes_where_the_target_stands(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        targets["web-1"].break_after = CHUNK * 5

        snapshot, recorder, _ = run(central, request("web-1"))

        assert snapshot["status"] == "succeeded", snapshot
        assert targets["web-1"].breaks == 1
        assert bytes(targets["web-1"].received) == PAYLOAD
        assert any("resuming" in line for _, line in recorder.lines)

    def test_a_transfer_that_keeps_breaking_gives_up_and_drops_the_upload(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        central.nodes["web-1"].handlers[("PUT", f"/api/apps/{DOMAIN}/releases/uploads/u1")] = (
            lambda request: (_ for _ in ()).throw(
                httpx.ReadError("the tunnel dropped", request=request)
            )
        )

        snapshot, _, _ = run(central, request("web-1"))

        assert states(snapshot) == {"web-1": "failed"}
        assert "failed 6 times in a row" in snapshot["nodes"][0]["error"]["message"]
        assert targets["web-1"].dropped == ["u1"]
        assert targets["web-1"].receives == []
        # The bundle stays on the builder: a retry sends it, it does not build again.
        assert builder.deleted == []

    def test_a_release_that_does_not_answer_keeps_the_servers_words(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        targets[
            "web-1"
        ].fail_receive = "Release 20261016-101500-feed123 did not pass its health check"

        snapshot, _, _ = run(central, request("web-1"))

        assert states(snapshot) == {"web-1": "failed"}
        assert "did not pass its health check" in (snapshot["nodes"][0]["output"] or "")

    def test_a_server_that_does_not_serve_afterwards_is_a_failure(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        targets["web-1"].status_after = "failed"

        snapshot, _, _ = run(central, request("web-1", rollback_on_failure=False))

        assert states(snapshot) == {"web-1": "failed"}
        assert "failed on web-1 after the release" in snapshot["nodes"][0]["error"]["message"]
        assert targets["web-1"].activations == []

    def test_and_it_is_put_back_by_the_release_that_served_when_that_is_asked(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        targets["web-1"].status_after = "failed"

        snapshot, _, _ = run(central, request("web-1"))

        assert states(snapshot) == {"web-1": "rolled_back"}
        assert targets["web-1"].activations == ["r1"]

    def test_the_servers_already_moved_are_put_back_when_a_later_one_fails(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        targets["web-2"].fail_receive = "disk full"

        snapshot, _, _ = run(central, request("web-1", "web-2"))

        assert states(snapshot) == {"web-1": "rolled_back", "web-2": "failed"}
        assert targets["web-1"].activations == ["r1"] and targets["web-1"].commit == "aaa1111"

    def test_a_retry_sends_the_bundle_the_failed_job_pinned_and_builds_nothing(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        targets["web-2"].fail_receive = "disk full"
        snapshot, _, job_id = run(central, request("web-1", "web-2", rollback_on_failure=False))
        assert states(snapshot) == {"web-1": "succeeded", "web-2": "failed"}
        targets["web-2"].fail_receive = None

        retried = retry_request(job_id, FleetJobs(central.store))
        again, _, _ = run(central, retried)

        assert again["status"] == "succeeded", again
        assert len(builder.builds) == 1, "the retry used the build the job had made"
        assert bytes(targets["web-2"].received) == PAYLOAD

    def test_a_bundle_the_builder_no_longer_has_is_built_again(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        pinned = request("web-1")
        pinned.options["bundle"] = Builder.bundle()
        builder.kept = False

        snapshot, recorder, _ = run(central, pinned)

        assert snapshot["status"] == "succeeded", snapshot
        assert len(builder.builds) == 1
        assert any("no longer on build-1" in line for _, line in recorder.lines)
