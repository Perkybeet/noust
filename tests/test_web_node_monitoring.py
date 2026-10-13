"""
Pausing a node's monitoring, over HTTP and from the terminal.

The rules are tested in ``test_fleet_liveness.py``; what is pinned here is what the surfaces
owe: an administrator's permission, the pause shown beside the node with who and why, the node's
rows in the fleet saying so, and the commands that do the same from a terminal.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.fleet.aggregate import set_aggregator
from noust.fleet.liveness import Liveness
from noust.web.jobs import JobManager
from noust.web.permissions.registry import route_map
from noust.web.server import create_app, get_token_manager
from tests.fleet_views_support import Central, build_central
from tests.test_web_auth import make_config
from tests.test_web_central import sign_in


@pytest.fixture
def central(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Central]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
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


def test_pausing_is_an_administrators_and_resuming_too() -> None:
    assert route_map()[("PUT", "/api/nodes/{node}/monitoring")] == "fleet.manage"


def test_a_node_is_paused_with_a_reason_and_shown_so(client, central) -> None:
    sign_in(client)

    paused = client.put(
        "/api/nodes/web-3/monitoring", json={"paused": True, "reason": "moving racks"}
    )
    listed = {node["name"]: node for node in client.get("/api/nodes").json()["items"]}

    assert paused.status_code == 200, paused.text
    assert paused.json()["monitoring"]["paused"] is True
    assert paused.json()["monitoring"]["paused_reason"] == "moving racks"
    assert listed["web-3"]["monitoring"]["paused"] is True
    assert listed["web-2"]["monitoring"] is None
    assert Liveness(central.store).get("web-3").paused is True


def test_resuming_puts_it_back_with_a_clean_count(client, central) -> None:
    sign_in(client)
    client.put("/api/nodes/web-3/monitoring", json={"paused": True})

    resumed = client.put("/api/nodes/web-3/monitoring", json={"paused": False})

    assert resumed.status_code == 200
    assert resumed.json()["monitoring"] is None
    assert Liveness(central.store).all() == {}


def test_a_node_that_is_not_registered_is_a_404(client, central) -> None:
    sign_in(client)

    assert client.put("/api/nodes/nope/monitoring", json={"paused": True}).status_code == 404


def test_a_read_token_cannot_pause(client, central) -> None:
    issued = get_token_manager().create_api_token("reader", "read")

    response = client.put(
        "/api/nodes/web-3/monitoring",
        json={"paused": True},
        headers={"Authorization": f"Bearer {issued['token']}"},
    )

    assert response.status_code == 403
    assert Liveness(central.store).all() == {}


def test_the_terminal_pauses_and_resumes_a_node(central, monkeypatch) -> None:
    monkeypatch.setattr("noust.cli.commands.node._manager", lambda: central.manager)

    paused = CliRunner().invoke(root_cli, ["node", "pause", "web-3", "--reason", "new disk"])
    state = Liveness(central.store).get("web-3")
    resumed = CliRunner().invoke(root_cli, ["node", "resume", "web-3"])

    assert paused.exit_code == 0, paused.output
    assert (state.paused, state.paused_reason) == (True, "new disk")
    assert state.paused_by is not None and state.paused_by.startswith("cli:")
    assert resumed.exit_code == 0, resumed.output
    assert Liveness(central.store).all() == {}


def test_a_rehearsal_pauses_nothing(central, monkeypatch) -> None:
    monkeypatch.setattr("noust.cli.commands.node._manager", lambda: central.manager)

    result = CliRunner().invoke(root_cli, ["--dry-run", "node", "pause", "web-3"])

    assert result.exit_code == 0, result.output
    assert Liveness(central.store).all() == {}
