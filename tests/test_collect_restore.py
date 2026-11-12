# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Giving a copy back from the central to a server through the tunnel (item 195).

The restore is never reimplemented: the central carries the file and asks the server to restore it as
the person who asked. What is pinned is what protects the data: the copy the central holds is checked
whole before a byte leaves, a server that cannot receive it is told to update before anything is sent,
the file arrives whole (resuming after a failed chunk) or not at all, a failed restore says what the
server said, and the person's identity and sudo mode are what the server sees.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from noust.core.exceptions import NodeRefusedError
from noust.fleet.aggregate import Asker
from noust.fleet.client import ACTOR_HEADER, ACTOR_PERMISSIONS_HEADER, ELEVATED_HEADER, NodeClient
from noust.fleet.collect.layout import Layout
from noust.fleet.collect.records import Collected, CollectedFile
from noust.fleet.collect.restore import GiveBack, RestoreError, damaged_files
from noust.fleet.collect.throttle import Throttle

DATA = bytes(range(256)) * 100
SIDECAR = {"id": "shop_1", "domain": "shop.example.com", "checksum": "x"}


class ReturnNode:
    """A server's side of a copy coming back."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.held_status = 200
        self.data = b""
        self.size = 0
        self.sha = ""
        self.fail_chunk_once_at: int | None = None
        self.restore_body: dict[str, Any] | None = None
        self.job_states: list[dict[str, Any]] = [
            {"status": "completed", "result": {"restored": "x"}}
        ]
        self.deleted = False
        self.complete_status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, method = request.url.path, request.method
        if path == "/api/backup-collection/held":
            return httpx.Response(self.held_status, json={"centrals": []})
        if path == "/api/backup-collection/returns" and method == "POST":
            body = json.loads(request.content)
            self.size, self.sha = body["size"], body["sha256"]
            return httpx.Response(
                201,
                json={
                    "id": "up1",
                    "size": self.size,
                    "received": 0,
                    "chunk_size": 4096,
                    "complete": False,
                    "sha256": self.sha,
                },
            )
        if path == "/api/backup-collection/returns/up1":
            if method == "PUT":
                offset = int(request.url.params["offset"])
                if self.fail_chunk_once_at is not None and offset == self.fail_chunk_once_at:
                    self.fail_chunk_once_at = None
                    return httpx.Response(503, text="busy")
                if offset != len(self.data):
                    return httpx.Response(
                        409, json={"error": "upload_offset", "received": len(self.data)}
                    )
                self.data += request.content
                return httpx.Response(200, json={"id": "up1", "received": len(self.data)})
            if method == "GET":
                return httpx.Response(200, json={"id": "up1", "received": len(self.data)})
            if method == "DELETE":
                self.deleted = True
                return httpx.Response(204)
        if path == "/api/backup-collection/returns/up1/complete":
            return httpx.Response(
                self.complete_status,
                json={"error": "bad digest"} if self.complete_status != 200 else {"complete": True},
            )
        if path == "/api/backup-collection/restore":
            self.restore_body = json.loads(request.content)
            return httpx.Response(202, json={"job_id": "job-9"})
        if path == "/api/jobs/job-9":
            state = self.job_states.pop(0) if len(self.job_states) > 1 else self.job_states[0]
            return httpx.Response(200, json=state)
        return httpx.Response(404, json={})


@pytest.fixture
def node() -> ReturnNode:
    return ReturnNode()


@pytest.fixture
def central(tmp_path: Path) -> tuple[Layout, Collected]:
    layout = Layout(tmp_path / "central")
    directory = layout.copy_dir("web-1", "shop", "app", "shop_1")
    directory.mkdir(parents=True)
    (directory / "shop_1.tar.gz").write_bytes(DATA)
    (directory / "shop_1.json").write_text(json.dumps(SIDECAR))
    copy = Collected(
        node="web-1",
        kind="app",
        item_id="shop_1",
        owner="shop.example.com",
        folder="shop",
        origin="scheduled",
        created_at="2026-11-10T02:00:00+00:00",
        collected_at="2026-11-10T02:05:00+00:00",
        size=len(DATA) + len(json.dumps(SIDECAR)),
        files=(
            CollectedFile("shop_1.tar.gz", len(DATA), hashlib.sha256(DATA).hexdigest(), "archive"),
            CollectedFile(
                "shop_1.json",
                len(json.dumps(SIDECAR)),
                hashlib.sha256(json.dumps(SIDECAR).encode()).hexdigest(),
                "sidecar",
            ),
        ),
        verified="digest",
        meta={"domain": "shop.example.com"},
    )
    return layout, copy


def give(node: ReturnNode, layout: Layout, copy: Collected, tmp_path: Path, **kw: Any) -> GiveBack:
    from noust.core.secrets import SecretStore
    from noust.core.store import NodeRecord, NoustStore
    from noust.fleet.keys import TOKEN, known_hosts_line, secret_name
    from tests.support.fleet_support import HOST_KEY

    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    store.save_node(
        NodeRecord(
            name="web-2",
            ssh_host="web2.example.com",
            ssh_port=22,
            ssh_user="noust-tunnel",
            host_key=known_hosts_line("web-2", HOST_KEY),
            console_port=8080,
        )
    )
    secrets = SecretStore(root=tmp_path / "secrets")
    secrets.write(secret_name("web-2", TOKEN), "tok")
    from noust.fleet.tunnels import set_loopback_for_testing

    set_loopback_for_testing("web-2", 1)

    def client_for(name: str) -> NodeClient:
        return NodeClient(
            name, secrets=secrets, store=store, transport=httpx.MockTransport(node.handler)
        )

    return GiveBack(
        copy=copy,
        target="web-2",
        layout=layout,
        asker=Asker(actor="maria", scope="admin", elevated=True, permissions=("backups.manage",)),
        throttle=Throttle(None),
        client_for=client_for,
        sleep=lambda _s: None,
        **kw,
    )


@pytest.fixture(autouse=True)
def _loopback_off() -> Any:
    yield
    from noust.core.store import NoustStore
    from noust.fleet.tunnels import set_loopback_for_testing

    set_loopback_for_testing("web-2", None)
    NoustStore.reset_instance()


class TestAWholeCopyGoesBack:
    def test_the_file_arrives_whole_and_is_restored_into_the_application_it_names(
        self, node: ReturnNode, central: tuple[Layout, Collected], tmp_path: Path
    ) -> None:
        layout, copy = central

        result = give(node, layout, copy, tmp_path).run(
            target_args={"domain": "shop.example.com"}, options={"restore_env": False}
        )

        assert node.data == DATA and node.sha == hashlib.sha256(DATA).hexdigest()
        assert node.restore_body is not None
        assert node.restore_body["upload_id"] == "up1" and node.restore_body["kind"] == "app"
        assert node.restore_body["sidecar"] == SIDECAR
        assert node.restore_body["meta"] == {"domain": "shop.example.com"}
        assert node.restore_body["target"] == {"domain": "shop.example.com"}
        assert node.restore_body["options"] == {"restore_env": False}
        assert result["node"] == "web-2" and result["result"] == {"restored": "x"}

    def test_the_server_sees_the_person_and_their_sudo_mode_and_nothing_wider(
        self, node: ReturnNode, central: tuple[Layout, Collected], tmp_path: Path
    ) -> None:
        layout, copy = central

        give(node, layout, copy, tmp_path).run()

        for request in node.requests:
            assert request.headers[ACTOR_HEADER] == "maria"
            assert request.headers[ACTOR_PERMISSIONS_HEADER] == "backups.manage"
            assert request.headers[ELEVATED_HEADER] == "1"

    def test_another_server_can_receive_it_too(
        self, node: ReturnNode, central: tuple[Layout, Collected], tmp_path: Path
    ) -> None:
        layout, copy = central

        result = give(node, layout, copy, tmp_path).run()

        assert result["node"] == "web-2" and copy.node == "web-1"

    def test_the_reads_from_the_disk_are_paced(
        self, node: ReturnNode, central: tuple[Layout, Collected], tmp_path: Path
    ) -> None:
        layout, copy = central
        waits: list[float] = []
        now = [0.0]

        def sleep(seconds: float) -> None:
            waits.append(seconds)
            now[0] += seconds

        giver = give(node, layout, copy, tmp_path)
        giver.throttle = Throttle(10_000, clock=lambda: now[0], sleep=sleep)

        giver.run()

        assert sum(waits) == pytest.approx(len(DATA) / 10_000, rel=0.05)


class TestACopyThatIsNotWholeOnTheCentralIsNeverSent:
    def test_a_file_that_changed_on_the_disk_is_named_and_nothing_is_sent(
        self, node: ReturnNode, central: tuple[Layout, Collected], tmp_path: Path
    ) -> None:
        layout, copy = central
        directory = layout.copy_dir("web-1", "shop", "app", "shop_1")
        (directory / "shop_1.tar.gz").write_bytes(DATA[:-1] + b"X")

        with pytest.raises(RestoreError) as raised:
            give(node, layout, copy, tmp_path).run()

        assert (
            "shop_1.tar.gz" in raised.value.details and "Nothing was sent" in raised.value.details
        )
        assert node.requests == []

    def test_a_file_that_is_missing_is_named(
        self, node: ReturnNode, central: tuple[Layout, Collected], tmp_path: Path
    ) -> None:
        layout, copy = central
        (layout.copy_dir("web-1", "shop", "app", "shop_1") / "shop_1.tar.gz").unlink()

        assert damaged_files(copy, layout) == ["shop_1.tar.gz is missing"]

    def test_a_link_in_place_of_a_file_is_not_followed(
        self, node: ReturnNode, central: tuple[Layout, Collected], tmp_path: Path
    ) -> None:
        layout, copy = central
        archive = layout.copy_dir("web-1", "shop", "app", "shop_1") / "shop_1.tar.gz"
        archive.unlink()
        archive.symlink_to(tmp_path / "elsewhere")

        assert damaged_files(copy, layout) == ["shop_1.tar.gz is missing"]


class TestAServerThatCannotReceive:
    def test_a_server_before_43_is_told_to_update_and_nothing_is_sent(
        self, node: ReturnNode, central: tuple[Layout, Collected], tmp_path: Path
    ) -> None:
        layout, copy = central
        node.held_status = 404

        with pytest.raises(RestoreError) as raised:
            give(node, layout, copy, tmp_path).run()

        assert "before 4.3" in raised.value.details and "Nothing was sent" in raised.value.details
        assert not any(r.method == "POST" for r in node.requests)

    def test_a_refused_token_is_not_swallowed(
        self, node: ReturnNode, central: tuple[Layout, Collected], tmp_path: Path
    ) -> None:
        layout, copy = central
        node.held_status = 403

        with pytest.raises(NodeRefusedError):
            give(node, layout, copy, tmp_path).run()


class TestTheFileArrivesWholeOrNotAtAll:
    def test_a_chunk_that_fails_is_asked_again_from_where_the_server_stands(
        self, node: ReturnNode, central: tuple[Layout, Collected], tmp_path: Path
    ) -> None:
        layout, copy = central
        node.fail_chunk_once_at = 8192

        give(node, layout, copy, tmp_path).run()

        assert node.data == DATA

    def test_a_server_that_keeps_failing_is_given_up_on_and_the_upload_thrown_away(
        self, node: ReturnNode, central: tuple[Layout, Collected], tmp_path: Path
    ) -> None:
        layout, copy = central
        node.handler_original = node.handler  # type: ignore[attr-defined]

        def always_busy(request: httpx.Request) -> httpx.Response:
            if request.method == "PUT":
                node.requests.append(request)
                return httpx.Response(503, text="busy")
            return node.handler_original(request)  # type: ignore[attr-defined]

        node.handler = always_busy  # type: ignore[method-assign]

        with pytest.raises(RestoreError) as raised:
            give(node, layout, copy, tmp_path).run()

        assert "failed 6 times in a row" in raised.value.message
        assert node.deleted

    def test_a_digest_the_server_does_not_accept_is_an_error_and_nothing_is_restored(
        self, node: ReturnNode, central: tuple[Layout, Collected], tmp_path: Path
    ) -> None:
        layout, copy = central
        node.complete_status = 400

        with pytest.raises(RestoreError):
            give(node, layout, copy, tmp_path).run()

        assert node.restore_body is None and node.deleted


class TestTheRestoreIsFollowedToItsEnd:
    def test_it_waits_for_the_servers_job(
        self, node: ReturnNode, central: tuple[Layout, Collected], tmp_path: Path
    ) -> None:
        layout, copy = central
        node.job_states = [
            {"status": "running", "current_step": "Restoring"},
            {"status": "running", "current_step": "Starting the application"},
            {"status": "completed", "result": {"restored": "shop.example.com"}},
        ]
        told: list[str] = []

        result = give(node, layout, copy, tmp_path, log=told.append).run()

        assert result["result"] == {"restored": "shop.example.com"}
        assert any("Starting the application" in line for line in told)

    def test_a_restore_that_failed_says_what_the_server_said(
        self, node: ReturnNode, central: tuple[Layout, Collected], tmp_path: Path
    ) -> None:
        layout, copy = central
        node.job_states = [
            {"status": "failed", "error": "The application did not pass its health check"}
        ]

        with pytest.raises(RestoreError) as raised:
            give(node, layout, copy, tmp_path).run()

        assert "failed" in raised.value.message
        assert "health check" in raised.value.details

    def test_a_console_that_stops_leaves_the_restore_going_on_the_server_and_says_so(
        self, node: ReturnNode, central: tuple[Layout, Collected], tmp_path: Path
    ) -> None:
        layout, copy = central
        node.job_states = [{"status": "running"}]
        calls = [0]

        def should_stop() -> bool:
            calls[0] += 1
            return calls[0] > 40

        with pytest.raises(RestoreError) as raised:
            give(node, layout, copy, tmp_path, should_stop=should_stop).run()

        assert "job-9" in raised.value.details
