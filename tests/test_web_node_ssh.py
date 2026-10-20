# Copyright (c) 2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A server's connection options over HTTP and from the terminal (checklist F-06).

The rules (the pinned bastion key, the hop, what is kept when the change does not work)
are tested in ``test_fleet_ssh_options.py``; what is pinned here is what the surfaces owe:
an administrator's permission, the bastion's fingerprint shown and its key never, the
line for the bastion's operator, and a change that is refused leaving things as they were.
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
from noust.core.exceptions import NodeError
from noust.core.runner import FakeRunner, set_runner
from noust.core.store import NoustStore
from noust.fleet.aggregate import set_aggregator
from noust.fleet.ssh_options import NodeSSHOptions
from noust.web.jobs import JobManager
from noust.web.permissions.registry import route_map
from noust.web.server import create_app, get_token_manager
from tests.fleet_support import ed25519_line, fingerprint
from tests.fleet_views_support import Central, build_central
from tests.test_web_auth import make_config
from tests.test_web_central import elevate, sign_in

BASTION_KEY = ed25519_line(7)


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


@pytest.fixture
def signed(client: TestClient) -> TestClient:
    elevate(client, sign_in(client))
    return client


def body(**fields):
    base = {
        "connect_timeout": 30,
        "bastion": {
            "address": "hop@bastion.example.com:2222",
            "host_key": BASTION_KEY,
        },
    }
    base.update(fields)
    return base


def test_the_routes_are_an_administrators_to_change_and_a_readers_to_look() -> None:
    routes = route_map()

    assert routes[("GET", "/api/nodes/{node}/ssh")] == "fleet.read"
    assert routes[("PUT", "/api/nodes/{node}/ssh")] == "fleet.manage"


def test_a_server_with_no_options_says_the_defaults(client, central) -> None:
    sign_in(client)

    found = client.get("/api/nodes/web-2/ssh").json()

    assert found == {
        "connect_timeout": None,
        "effective_timeout": 10,
        "default_timeout": 10,
        "bastion": None,
        "authorized_key_line": None,
    }


def test_the_options_are_set_tested_and_shown_without_the_key(signed, central) -> None:
    response = signed.put("/api/nodes/web-2/ssh", json=body())

    assert response.status_code == 200, response.text
    shown = response.json()
    assert shown["connect_timeout"] == 30
    assert shown["bastion"]["address"] == "hop@bastion.example.com:2222"
    assert shown["bastion"]["fingerprint"] == fingerprint(BASTION_KEY)
    assert BASTION_KEY.split()[1] not in json.dumps(shown), "the key is not part of what is shown"
    assert shown["authorized_key_line"].startswith("restrict,port-forwarding,permitopen=")
    assert 'command="/usr/bin/false"' in shown["authorized_key_line"]
    assert NodeSSHOptions(central.store).get("web-2").bastion is not None


def test_the_same_bastion_again_keeps_its_pinned_key(signed, central) -> None:
    signed.put("/api/nodes/web-2/ssh", json=body())

    again = signed.put(
        "/api/nodes/web-2/ssh",
        json={"connect_timeout": 60, "bastion": {"address": "hop@bastion.example.com:2222"}},
    )

    assert again.status_code == 200, again.text
    assert NodeSSHOptions(central.store).get("web-2").bastion.host_key == BASTION_KEY
    assert again.json()["connect_timeout"] == 60


def test_a_new_bastion_without_a_key_or_a_fingerprint_is_refused(signed, central) -> None:
    response = signed.put(
        "/api/nodes/web-2/ssh",
        json={"connect_timeout": None, "bastion": {"address": "hop@other.example.com"}},
    )

    assert response.status_code == 400, response.text
    assert "first use" in response.text
    assert NodeSSHOptions(central.store).get("web-2").bastion is None


def test_a_fingerprint_is_checked_by_asking_the_bastion(signed, central) -> None:
    central.fleet.runner.script(["ssh-keyscan"], stdout=f"bastion.example.com {BASTION_KEY}\n")

    response = signed.put(
        "/api/nodes/web-2/ssh",
        json={
            "connect_timeout": None,
            "bastion": {
                "address": "hop@bastion.example.com",
                "fingerprint": fingerprint(BASTION_KEY),
            },
        },
    )

    assert response.status_code == 200, response.text
    assert NodeSSHOptions(central.store).get("web-2").bastion.host_key == BASTION_KEY


def test_removing_the_bastion_is_putting_none(signed, central) -> None:
    signed.put("/api/nodes/web-2/ssh", json=body())

    response = signed.put("/api/nodes/web-2/ssh", json={"connect_timeout": 30, "bastion": None})

    assert response.status_code == 200, response.text
    assert response.json()["bastion"] is None
    assert NodeSSHOptions(central.store).get("web-2").connect_timeout == 30


def test_a_change_the_server_does_not_answer_to_is_refused_and_put_back(signed, central) -> None:
    central.fleet.tunnels.close("web-2")
    central.fleet.runner.script(
        ["ssh"], exit_code=255, stderr="hop@bastion.example.com: Permission denied (publickey)."
    )

    response = signed.put("/api/nodes/web-2/ssh", json=body())

    assert response.status_code in (400, 502), response.text
    assert "bastion" in response.text.lower()
    assert NodeSSHOptions(central.store).get("web-2").bastion is None


def test_a_timeout_out_of_range_is_a_validation_error(signed, central) -> None:
    response = signed.put("/api/nodes/web-2/ssh", json={"connect_timeout": 0, "bastion": None})

    assert response.status_code in (400, 422), response.text


def test_a_node_that_is_not_registered_is_a_404(signed) -> None:
    assert signed.get("/api/nodes/nope/ssh").status_code == 404
    assert signed.put("/api/nodes/nope/ssh", json=body()).status_code == 404


def test_a_read_token_cannot_change_them(client, central) -> None:
    issued = get_token_manager().create_api_token("reader", "read")

    response = client.put(
        "/api/nodes/web-2/ssh",
        json=body(),
        headers={"Authorization": f"Bearer {issued['token']}"},
    )

    assert response.status_code == 403
    assert NodeSSHOptions(central.store).get("web-2").bastion is None


# -- the terminal ---------------------------------------------------------------------------


@pytest.fixture
def terminal(central, monkeypatch):
    monkeypatch.setattr("noust.cli.commands.node._manager", lambda: central.manager)
    return central


def run(*args: str):
    return CliRunner().invoke(root_cli, ["node", *args])


def test_the_terminal_shows_the_defaults_without_changing_anything(terminal) -> None:
    result = run("ssh", "web-2")

    assert result.exit_code == 0, result.output
    assert "10 seconds" in result.output
    assert "no bastion" in result.output.lower()


def test_the_terminal_sets_the_wait_and_the_bastion(terminal) -> None:
    result = run(
        "ssh",
        "web-2",
        "--timeout",
        "45",
        "--bastion",
        "hop@bastion.example.com:2222",
        "--bastion-host-key",
        BASTION_KEY,
    )

    assert result.exit_code == 0, result.output
    options = NodeSSHOptions(terminal.store).get("web-2")
    assert options.connect_timeout == 45
    assert options.bastion.address == "hop@bastion.example.com:2222"
    assert fingerprint(BASTION_KEY) in result.output


def test_the_terminal_prints_the_line_for_the_bastions_operator(terminal) -> None:
    run("ssh", "web-2", "--bastion", "hop@bastion.example.com", "--bastion-host-key", BASTION_KEY)

    result = run("ssh", "web-2", "--show")

    assert result.exit_code == 0, result.output
    line = next(row for row in result.output.splitlines() if row.startswith("restrict,"))
    assert 'permitopen="' in line and "port-forwarding" in line


def test_the_line_is_printed_before_the_bastion_is_set_and_nothing_changes(terminal) -> None:
    # Setting a bastion opens the tunnel through it, which fails until the bastion authorizes the
    # central; `--show` used to need the bastion already set, so the operator could not learn the
    # line without passing --no-test. Asked with --bastion, it prints that bastion's line and keeps
    # the node as it is.
    result = run(
        "ssh",
        "web-2",
        "--bastion",
        "hop@bastion.example.com",
        "--bastion-host-key",
        BASTION_KEY,
        "--show",
    )

    assert result.exit_code == 0, result.output
    line = next(row for row in result.output.splitlines() if row.startswith("restrict,"))
    assert 'permitopen="' in line and "port-forwarding" in line
    assert NodeSSHOptions(terminal.store).get("web-2").bastion is None


def test_showing_a_line_is_not_a_change_so_it_is_not_changed_with_other_options(terminal) -> None:
    result = run("ssh", "web-2", "--timeout", "45", "--show")

    assert result.exit_code == 2
    assert "only prints a line" in result.output
    assert NodeSSHOptions(terminal.store).get("web-2").connect_timeout is None


def test_showing_the_line_of_a_node_with_no_bastion_says_how_to_ask(terminal) -> None:
    result = run("ssh", "web-2", "--show")

    assert isinstance(result.exception, NodeError)
    assert "--show" in (result.exception.details or "")


def test_a_bastion_needs_its_key_or_its_fingerprint(terminal) -> None:
    result = run("ssh", "web-2", "--bastion", "hop@bastion.example.com")

    assert isinstance(result.exception, NodeError)
    assert "first use" in (result.exception.details or "")


def test_the_wait_goes_back_to_the_default_and_the_bastion_is_dropped(terminal) -> None:
    run(
        "ssh",
        "web-2",
        "--timeout",
        "45",
        "--bastion",
        "hop@b.example.com",
        "--bastion-host-key",
        BASTION_KEY,
    )

    result = run("ssh", "web-2", "--default-timeout", "--no-bastion")

    assert result.exit_code == 0, result.output
    assert NodeSSHOptions(terminal.store).get("web-2").connect_timeout is None
    assert NodeSSHOptions(terminal.store).get("web-2").bastion is None


def test_a_rehearsal_changes_nothing(terminal) -> None:
    result = CliRunner().invoke(root_cli, ["--dry-run", "node", "ssh", "web-2", "--timeout", "45"])

    assert result.exit_code == 0, result.output
    assert NodeSSHOptions(terminal.store).get("web-2").connect_timeout is None


def test_json_output_is_the_options(terminal) -> None:
    run("ssh", "web-2", "--timeout", "45")

    result = CliRunner().invoke(root_cli, ["node", "ssh", "web-2", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["connect_timeout"] == 45
