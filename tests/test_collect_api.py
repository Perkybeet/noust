# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the collector sends a server, and what it does with the answer (item 195).

A server before 4.3 has none of the routes, and what a central asks of any server is the least it can
ask: the one permission it needs and no other, over its connection for transfers, never in a URL.
What is pinned: the requests (paths, parameters, headers), a 404 on the list (a server before 4.3) as
the one thing that is not an error, what a 4.2 server's dumps become (the same shape, judged by the
same rules), and the sudo mode a 4.2 server asks for its downloads being vouched for and nothing else.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from noust.core.exceptions import NodeError, NodeRefusedError
from noust.core.store import NodeRecord
from noust.fleet.client import (
    ACTOR_HEADER,
    ACTOR_PERMISSIONS_HEADER,
    ACTOR_SCOPE_HEADER,
    ELEVATED_HEADER,
    NodeClient,
)
from noust.fleet.collect.api import ACTOR, ClientApi, LegacyApi, NodeUnsupported
from noust.fleet.collect.names import RemoteFile, RemoteItem
from noust.fleet.keys import TOKEN, known_hosts_line, secret_name
from noust.fleet.tunnels import TRANSFER
from tests.support.fleet_support import HOST_KEY, build_fleet


@pytest.fixture(autouse=True)
def _central_named_nas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")


class Node:
    """A server that records what it is asked and answers from a table."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.answers: dict[str, httpx.Response] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.answers.get(request.url.path, httpx.Response(404, json={}))


@pytest.fixture
def node() -> Node:
    return Node()


@pytest.fixture
def client(tmp_path: Path, node: Node) -> NodeClient:
    fleet = build_fleet(tmp_path)
    fleet.store.save_node(
        NodeRecord(
            name="web-2",
            ssh_host="web2.example.com",
            ssh_port=22,
            ssh_user="noust-tunnel",
            host_key=known_hosts_line("web-2", HOST_KEY),
            console_port=8080,
        )
    )
    fleet.secrets.write(secret_name("web-2", TOKEN), "token-for-web-2")
    return NodeClient(
        "web-2",
        tunnels=fleet.tunnels,
        secrets=fleet.secrets,
        store=fleet.store,
        transport=httpx.MockTransport(node.handler),
        channel=TRANSFER,
    )


ITEM = RemoteItem(
    "app",
    "shop_1",
    "shop.example.com",
    "shop",
    "2026-11-10T02:00:00+00:00",
    "scheduled",
    True,
    (RemoteFile("shop_1.tar.gz", 5, None, "archive"),),
)


def listing(*items: dict[str, Any]) -> dict[str, Any]:
    return {"fingerprint": "f" * 24, "items": list(items)}


def raw(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": "app",
        "id": "shop_1",
        "owner": "shop.example.com",
        "folder": "shop",
        "created_at": "2026-11-10T02:00:00+00:00",
        "origin": "scheduled",
        "sealed": True,
        "files": [{"name": "shop_1.tar.gz", "size": 5, "sha256": None, "role": "archive"}],
    }
    body.update(changes)
    return body


class TestAServerThatHasTheRoutes:
    def test_the_list_is_asked_with_the_one_permission_collecting_needs(
        self, client: NodeClient, node: Node
    ) -> None:
        node.answers["/api/backup-collection"] = httpx.Response(200, json=listing(raw()))

        catalogue = ClientApi(client).list()

        (request,) = node.requests
        assert request.method == "GET" and request.url.params.get("known") is None
        assert request.headers[ACTOR_PERMISSIONS_HEADER] == "backups.collect"
        assert request.headers[ACTOR_HEADER] == ACTOR
        assert request.headers[ACTOR_SCOPE_HEADER] == "read"
        assert ELEVATED_HEADER not in request.headers
        assert request.headers["Authorization"] == "Bearer token-for-web-2"
        assert "token-for-web-2" not in str(request.url)
        assert [i.id for i in catalogue.items] == ["shop_1"]

    def test_the_fingerprint_the_central_holds_is_sent(
        self, client: NodeClient, node: Node
    ) -> None:
        node.answers["/api/backup-collection"] = httpx.Response(
            200, json={"fingerprint": "abc", "unchanged": True, "items": []}
        )

        catalogue = ClientApi(client).list(known="abc")

        assert node.requests[0].url.params["known"] == "abc" and catalogue.unchanged

    def test_what_the_server_says_is_judged_not_believed(
        self, client: NodeClient, node: Node
    ) -> None:
        node.answers["/api/backup-collection"] = httpx.Response(
            200, json=listing(raw(), raw(id="evil", folder="../x"))
        )

        catalogue = ClientApi(client).list()

        assert [i.id for i in catalogue.items] == ["shop_1"]
        assert "folder" in catalogue.rejected[0].reason

    def test_a_range_is_asked_by_what_the_list_named(self, client: NodeClient, node: Node) -> None:
        node.answers["/api/backup-collection/files"] = httpx.Response(200, content=b"abcde")

        data = ClientApi(client).read(ITEM, ITEM.main, 10, 5)

        (request,) = node.requests
        assert data == b"abcde"
        assert dict(request.url.params) == {
            "kind": "app",
            "item": "shop_1",
            "name": "shop_1.tar.gz",
            "offset": "10",
            "length": "5",
        }

    def test_a_failed_range_is_an_error_with_the_servers_words(
        self, client: NodeClient, node: Node
    ) -> None:
        node.answers["/api/backup-collection/files"] = httpx.Response(
            404, json={"detail": "This server lists no such copy file"}
        )

        with pytest.raises(NodeError) as raised:
            ClientApi(client).read(ITEM, ITEM.main, 0, 5)

        assert "no such copy file" in raised.value.details

    def test_what_the_central_holds_is_put_in_place_of_what_it_said(
        self, client: NodeClient, node: Node
    ) -> None:
        node.answers["/api/backup-collection/held"] = httpx.Response(
            200, json={"central": "nas", "held": 1, "synced_at": "x"}
        )

        ClientApi(client).say_held([("app", "shop_1", "a" * 64)])

        (request,) = node.requests
        assert request.method == "PUT"
        assert (
            request.read()
            == b'{"items":[{"kind":"app","id":"shop_1","sha256":"' + b"a" * 64 + b'"}]}'
        )

    def test_a_server_that_does_not_answer_the_list_is_an_error_and_not_nothing(
        self, client: NodeClient, node: Node
    ) -> None:
        node.answers["/api/backup-collection"] = httpx.Response(500, text="boom")

        with pytest.raises(NodeError) as raised:
            ClientApi(client).list()

        assert not isinstance(raised.value, NodeUnsupported) and "boom" in raised.value.details

    def test_a_refusal_of_the_token_is_not_swallowed(self, client: NodeClient, node: Node) -> None:
        node.answers["/api/backup-collection"] = httpx.Response(403, json={"error": "forbidden"})

        with pytest.raises(NodeRefusedError) as raised:
            ClientApi(client).list()

        assert raised.value.status_code == 403


class TestAServerBefore43:
    def test_the_missing_route_is_not_an_error_but_a_thing_to_say(
        self, client: NodeClient, node: Node
    ) -> None:
        with pytest.raises(NodeUnsupported):
            ClientApi(client).list()


class TestItsDatabaseDumps:
    DUMP = {
        "name": "shop_20261110_040000.dump",
        "engine": "postgresql",
        "database": "shop",
        "size": 30,
        "created": "2026-11-10T04:00:00+00:00",
        "kind": "scheduled",
        "sha256": "b" * 64,
    }

    def test_they_become_copies_in_the_same_shape_and_are_judged_by_the_same_rules(
        self, client: NodeClient, node: Node
    ) -> None:
        node.answers["/api/databases/backups"] = httpx.Response(
            200,
            json={
                "backups": [
                    self.DUMP,
                    {**self.DUMP, "name": "../../etc/passwd"},
                    {**self.DUMP, "name": "x.dump", "size": -4},
                ]
            },
        )

        catalogue = LegacyApi(client).list()

        (item,) = catalogue.items
        assert item.kind == "database" and item.id == f"postgresql:{self.DUMP['name']}"
        assert item.owner == "postgresql/shop" and item.origin == "scheduled"
        assert item.main.sha256 == "b" * 64 and item.sealed is False
        assert len(catalogue.rejected) == 2
        assert node.requests[0].headers[ACTOR_PERMISSIONS_HEADER] == "databases.read"

    def test_a_dump_is_read_by_the_route_it_always_had_with_the_sudo_the_operator_gave(
        self, client: NodeClient, node: Node
    ) -> None:
        node.answers["/api/databases/backups/shop_20261110_040000.dump/data"] = httpx.Response(
            200, content=b"PGDMP"
        )
        item = RemoteItem(
            "database",
            "postgresql:shop_20261110_040000.dump",
            "postgresql/shop",
            "postgresql.shop",
            "2026-11-10T04:00:00+00:00",
            "scheduled",
            False,
            (RemoteFile("shop_20261110_040000.dump", 5, None, "archive"),),
            {"engine": "postgresql", "database": "shop"},
        )

        data = LegacyApi(client).read(item, item.main, 0, 5)

        (request,) = node.requests
        assert data == b"PGDMP"
        assert request.url.params["engine"] == "postgresql"
        assert request.headers[ACTOR_PERMISSIONS_HEADER] == "databases.manage"
        assert request.headers[ELEVATED_HEADER] == "1"

    def test_a_name_with_a_slash_cannot_leave_the_route(
        self, client: NodeClient, node: Node
    ) -> None:
        item = RemoteItem(
            "database",
            "postgresql:x",
            "o",
            "f",
            "2026-11-10T04:00:00+00:00",
            "manual",
            False,
            (RemoteFile("a/b", 1, None, "archive"),),
            {"engine": "postgresql"},
        )

        with pytest.raises(NodeError):
            LegacyApi(client).read(item, item.main, 0, 1)

        assert "%2F" in str(node.requests[0].url)

    def test_it_cannot_be_told_what_is_held_and_does_not_try(
        self, client: NodeClient, node: Node
    ) -> None:
        LegacyApi(client).say_held([("database", "x", None)])

        assert node.requests == []
