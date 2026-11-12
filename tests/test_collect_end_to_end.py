# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A central and a server, both real, one pass between them (item 195).

The server is the real console application answering the real routes over the real copies of a disk;
the central is the real collector with the real client of its tunnel, joined by a transport instead
of ssh. So what is pinned is the whole contract at once, the way it would run: the permission the
central asks for is the one the server's ceiling decides on, the files come out byte for byte, the
server ends up knowing which copies the central holds, and the retention that waits for them
(:mod:`noust.core.backup_hold`) reads what the central said.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import backup_hold
from noust.core.backup_collection_store import CentralHeld
from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.core.secrets import SecretStore
from noust.core.store import NodeRecord, NoustStore
from noust.fleet import policy
from noust.fleet.client import NodeClient
from noust.fleet.collect.api import ClientApi
from noust.fleet.collect.collector import NodeCollector
from noust.fleet.collect.layout import Layout
from noust.fleet.collect.records import CollectedStore
from noust.fleet.collect.settings import SettingsStore
from noust.fleet.keys import TOKEN, known_hosts_line, secret_name
from noust.fleet.tunnels import TRANSFER, set_loopback_for_testing
from noust.managers.backup_catalogue import BackupCatalogue
from noust.web.api import backup_collection
from noust.web.server import create_app, get_token_manager
from tests.support.backup_collection_support import Disk, sha
from tests.support.fleet_node_security import client_from
from tests.support.fleet_support import HOST_KEY
from tests.support.web_auth import make_config

NODE = "web-2"


@pytest.fixture(autouse=True)
def _central_named_nas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")


@pytest.fixture
def server(
    sandbox: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[tuple[Any, Disk]]:
    """A server: its console, its copies and, in the same store, the central's tables."""
    monkeypatch.setattr(
        "noust.core.config.DEFAULT_CONFIG_PATH", sandbox / "etc" / "noust" / "config.yaml"
    )
    Config.reset_instance()
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    app = create_app(make_config(sandbox))
    disk = Disk(tmp_path / "server-copies")
    monkeypatch.setattr(backup_collection, "BackupCatalogue", disk.catalogue)
    store.save_node(
        NodeRecord(
            name=NODE,
            ssh_host="web2.example.com",
            ssh_port=22,
            ssh_user="noust-tunnel",
            host_key=known_hosts_line(NODE, HOST_KEY),
            console_port=8080,
        )
    )
    SettingsStore(store).set(enabled=True)
    set_loopback_for_testing(NODE, 1)
    yield app, disk
    set_loopback_for_testing(NODE, None)
    NoustStore.reset_instance()


def central_for(app: Any, tmp_path: Path) -> NodeCollector:
    """The central: its client of the server's console, over the app itself."""
    secrets = SecretStore(root=tmp_path / "central-secrets")
    secrets.write(
        secret_name(NODE, TOKEN), str(get_token_manager().create_fleet_token("fleet-nas")["token"])
    )
    http = client_from(app)
    client = NodeClient(NODE, secrets=secrets, transport=http._transport, channel=TRANSFER)
    return NodeCollector(
        api_for=lambda _node: ClientApi(client),
        layout=Layout(tmp_path / "central"),
        notify=lambda *_a: None,
        sleep=lambda _s: None,
    )


class TestOnePassBetweenTwoRealSides:
    def test_every_kind_comes_out_byte_for_byte(
        self, server: tuple[Any, Disk], tmp_path: Path
    ) -> None:
        app, disk = server
        disk.apps.add(data=b"application archive" * 500)
        disk.volumes.add(data=b"volume" * 300)
        disk.databases.add(data=b"PGDMP dump" * 200)
        disk.bundles.add(data=b"ENVELOPE" * 100)

        result = central_for(app, tmp_path).collect(NODE)

        assert result.state == "ok", result.failures
        held = CollectedStore().list(node=NODE)
        assert {c.kind for c in held} == {"app", "volume", "database", "noust"}
        for copy in held:
            for file in copy.files:
                found = next((tmp_path / "central" / NODE).rglob(file.name))
                assert sha(found.read_bytes()) == file.sha256 and found.stat().st_size == file.size

    def test_the_server_ends_up_knowing_which_copies_the_central_holds(
        self, server: tuple[Any, Disk], tmp_path: Path
    ) -> None:
        app, disk = server
        meta = disk.apps.add()

        central_for(app, tmp_path).collect(NODE)

        assert CentralHeld().holders("app", meta.id) == ["nas"]
        assert [c.central for c in CentralHeld().following()] == ["nas"]

    def test_and_so_its_retention_waits_for_what_the_central_does_not_hold_yet(
        self, server: tuple[Any, Disk], tmp_path: Path
    ) -> None:
        app, disk = server
        taken = disk.apps.add(stamp="20261110_020000")
        central_for(app, tmp_path).collect(NODE)
        late = disk.apps.add(stamp="20261111_020000")

        spared = backup_hold.spared(
            "app", [(taken.id, taken.created_at), (late.id, late.created_at)]
        )

        assert spared == {late.id}

    @pytest.mark.parametrize(
        ("level", "state"), [("read", "failed"), ("deploy", "ok"), ("admin", "ok")]
    )
    def test_the_servers_ceiling_decides(
        self, server: tuple[Any, Disk], tmp_path: Path, level: str, state: str
    ) -> None:
        app, disk = server
        disk.apps.add()
        policy.set_access(policy.FleetAccess(level))  # type: ignore[arg-type]

        result = central_for(app, tmp_path).collect(NODE)

        assert result.state == state
        if state == "failed":
            assert result.code == "ceiling"
            assert CollectedStore().list() == []

    def test_a_server_that_lets_a_copy_leave_only_encrypted_keeps_the_rest_back(
        self, server: tuple[Any, Disk], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app, disk = server
        disk.apps.add()
        disk.bundles.add()
        required = BackupCatalogue(
            apps=disk.apps,  # type: ignore[arg-type]
            volumes=disk.volumes,  # type: ignore[arg-type]
            databases=disk.databases,  # type: ignore[arg-type]
            bundles=disk.bundles,  # type: ignore[arg-type]
            sealing_required=lambda: True,
        )
        monkeypatch.setattr(backup_collection, "BackupCatalogue", lambda: required)

        result = central_for(app, tmp_path).collect(NODE)

        assert result.state == "ok" and result.fetched == 1
        assert [c.kind for c in CollectedStore().list()] == ["noust"]
        assert [entry["reason"] for entry in result.left] == ["blocked"]

    def test_the_second_look_is_one_small_request(
        self, server: tuple[Any, Disk], tmp_path: Path
    ) -> None:
        app, disk = server
        disk.everything()
        runner = central_for(app, tmp_path)
        runner.collect(NODE)

        again = runner.collect(NODE)

        assert again.state == "unchanged"
