# Copyright (c) 2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A server's inventory over HTTP and from the terminal (checklist F-21).

The rules (what is in the document, what is refused, what is left out and why) are tested in
``test_fleet_inventory.py``; what is pinned here is what the surfaces owe: who may export and who may
apply, a damaged document refused whole, the file the terminal writes and reads, and that adding a server
with ``--inventory`` records it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.config import Config
from noust.core.runner import FakeRunner, set_runner
from noust.core.store import NoustStore
from noust.fleet.aggregate import set_aggregator
from noust.fleet.labels import NodeLabels
from noust.fleet.placements import Placements
from noust.web.jobs import JobManager
from noust.web.permissions.registry import route_map
from noust.web.server import create_app, get_token_manager
from tests.fleet_support import join_code
from tests.fleet_views_support import Central, Node, build_central
from tests.test_web_auth import make_config
from tests.test_web_central import sign_in


@pytest.fixture(autouse=True)
def default_runner_after_a_rehearsal() -> Iterator[None]:
    """--dry-run installs a runner that refuses to change anything; the next test must not get it."""
    yield
    set_runner(None)


@pytest.fixture
def central(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Central]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    Config.reset_instance()
    built = build_central(tmp_path / "fleet")
    monkeypatch.setattr("noust.fleet.nodes.NodeManager", lambda *a, **k: built.manager)
    JobManager.reset_instance()
    yield built
    JobManager.reset_instance()
    set_aggregator(None)
    Config.reset_instance()
    NoustStore.reset_instance()


@pytest.fixture
def client(central: Central, sandbox: Path, runner: FakeRunner) -> TestClient:
    return TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))


def arrange(central: Central) -> None:
    NodeLabels(central.store).change("web-2", set_labels={"env": "prod"})
    Placements(central.store).save("shop.example.com", nodes=["web-2"], serial=2)


def test_the_routes_are_a_readers_to_export_and_an_administrators_to_apply() -> None:
    routes = route_map()

    assert routes[("GET", "/api/nodes/{node}/inventory")] == "fleet.read"
    assert routes[("POST", "/api/nodes/{node}/inventory")] == "fleet.manage"


def test_the_inventory_is_exported_without_a_secret(client, central) -> None:
    sign_in(client)
    arrange(central)

    response = client.get("/api/nodes/web-2/inventory")

    assert response.status_code == 200, response.text
    document = response.json()
    assert document["format"] == "noust-node-inventory"
    assert document["labels"] == {"env": "prod"}
    assert [p["domain"] for p in document["placements"]] == ["shop.example.com"]
    assert "token" not in json.dumps(document).lower().replace("token_", "")


def test_a_server_that_is_not_registered_is_a_404(client) -> None:
    sign_in(client)

    assert client.get("/api/nodes/nope/inventory").status_code == 404
    assert client.post("/api/nodes/nope/inventory", json={"document": {}}).status_code == 404


def test_an_inventory_is_applied_and_reported(client, central) -> None:
    sign_in(client)
    arrange(central)
    document = client.get("/api/nodes/web-2/inventory").json()
    NodeLabels(central.store).change("web-3", set_labels={"role": "old"})

    response = client.post("/api/nodes/web-3/inventory", json={"document": document})

    assert response.status_code == 200, response.text
    parts = {item["part"] for item in response.json()["applied"]}
    assert {"labels", "placements"} <= parts
    assert NodeLabels(central.store).of("web-3") == {"env": "prod", "role": "old"}
    placement = Placements(central.store).get("shop.example.com")
    assert placement is not None and set(placement.nodes) == {"web-2", "web-3"}


def test_a_document_that_is_not_an_inventory_is_a_400_and_writes_nothing(client, central) -> None:
    sign_in(client)

    response = client.post("/api/nodes/web-3/inventory", json={"document": {"format": "x"}})

    assert response.status_code == 400, response.text
    assert NodeLabels(central.store).of("web-3") == {}


def test_a_read_token_cannot_apply_one(client, central) -> None:
    sign_in(client)
    document = client.get("/api/nodes/web-2/inventory").json()
    issued = get_token_manager().create_api_token("reader", "read")

    response = client.post(
        "/api/nodes/web-3/inventory",
        json={"document": document},
        headers={"Authorization": f"Bearer {issued['token']}"},
    )

    assert response.status_code == 403


# -- the terminal --------------------------------------------------------------------------


@pytest.fixture
def terminal(central, monkeypatch):
    monkeypatch.setattr("noust.cli.commands.node._manager", lambda: central.manager)
    return central


def invoke(*args: str):
    return CliRunner().invoke(root_cli, ["node", *args])


def test_the_terminal_prints_and_writes_the_inventory(terminal, tmp_path) -> None:
    arrange(terminal)
    target = tmp_path / "web-2.json"

    printed = invoke("export", "web-2")
    written = invoke("export", "web-2", "-o", str(target))

    assert printed.exit_code == 0, printed.output
    assert json.loads(printed.output)["labels"] == {"env": "prod"}
    assert written.exit_code == 0, written.output
    assert json.loads(target.read_text())["node"]["name"] == "web-2"
    assert (target.stat().st_mode & 0o777) == 0o600


def test_the_terminal_applies_a_file_to_a_server_already_added(terminal, tmp_path) -> None:
    arrange(terminal)
    target = tmp_path / "web-2.json"
    invoke("export", "web-2", "-o", str(target))

    result = invoke("inventory", "web-3", str(target))

    assert result.exit_code == 0, result.output
    assert "labels recorded" in result.output
    assert NodeLabels(terminal.store).of("web-3") == {"env": "prod"}


def test_a_file_that_is_not_json_is_said(terminal, tmp_path) -> None:
    target = tmp_path / "broken.json"
    target.write_text("{not json")

    result = invoke("inventory", "web-3", str(target))

    assert result.exit_code != 0
    assert "is not JSON" in str(result.exception)


def test_a_rehearsal_applies_nothing(terminal, tmp_path) -> None:
    arrange(terminal)
    target = tmp_path / "web-2.json"
    invoke("export", "web-2", "-o", str(target))

    result = CliRunner().invoke(root_cli, ["--dry-run", "node", "inventory", "web-3", str(target)])

    assert result.exit_code == 0, result.output
    assert NodeLabels(terminal.store).of("web-3") == {}


def test_adding_a_server_with_an_inventory_records_it(terminal, tmp_path) -> None:
    arrange(terminal)
    target = tmp_path / "web-2.json"
    invoke("export", "web-2", "-o", str(target))
    terminal.manager._blockers = list
    terminal.nodes["web-9"] = Node(version="3.1.0")
    key = terminal.manager.central_public_key("web-9")

    result = invoke(
        "add",
        "web-9",
        "--ssh",
        "root@web9.example.com",
        "--join-code",
        join_code(key, node_name="web-9"),
        "--inventory",
        str(target),
    )

    assert result.exit_code == 0, result.output
    assert NodeLabels(terminal.store).of("web-9") == {"env": "prod"}
    assert "Inventory: labels recorded" in result.output
