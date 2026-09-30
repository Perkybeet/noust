"""
Tests for what a central changes about a registered node: its key, its account, its ceiling.

What is pinned: ``rekey`` generates the new pair once and switches to it only
when the node answers with it and the new token - otherwise the old key, token
and record are back and the same code can be pasted again; ``migrate-tunnel``
keeps the key, refuses a code that still names root, and switches the account;
a code from another server (another host key) is refused; and the ceiling a
node publishes is recorded, a 3.0 node's as unknown.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.exceptions import NodeError
from noust.core.store import NoustStore
from noust.fleet.keys import TOKEN, secret_name
from noust.fleet.policy import FleetAccess
from tests.fleet_support import OTHER_TOKEN, build_fleet, ed25519_line, join_code


@pytest.fixture(autouse=True)
def _central_named_nas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")


@pytest.fixture
def fleet(tmp_path: Path):
    built = build_fleet(tmp_path)
    built.added()
    yield built
    NoustStore.reset_instance()


def _token(fleet) -> str | None:
    return fleet.secrets.read(secret_name("web-2", TOKEN))


class TestAccess:
    def test_add_records_what_the_node_publishes(self, fleet):
        record = fleet.manager.get("web-2")
        assert (record.access_level, record.host_access) == ("admin", False)
        assert record.access_read_at

    def test_test_refreshes_it(self, fleet):
        fleet.node.access = {"level": "read", "host_access": False}

        fleet.manager.test("web-2")

        assert fleet.manager.get("web-2").access_level == "read"

    def test_a_3_0_node_is_unknown(self, fleet):
        fleet.node.access = None

        assert fleet.manager.refresh_access("web-2") is None
        assert fleet.manager.get("web-2").access_level is None

    def test_a_refusal_or_nonsense_keeps_what_was_known(self, fleet):
        fleet.node.responses["/api/auth/fleet/self"] = (403, {"error": "forbidden"})
        assert fleet.manager.refresh_access("web-2") == FleetAccess("admin")

        fleet.node.responses["/api/auth/fleet/self"] = (200, {"level": "owner"})
        assert fleet.manager.refresh_access("web-2") == FleetAccess("admin")
        assert fleet.manager.get("web-2").access_level == "admin"

    def test_it_is_asked_for_as_read(self, fleet):
        fleet.node.requests.clear()
        fleet.manager.refresh_access("web-2")
        (request,) = fleet.node.requests
        assert request.url.path == "/api/auth/fleet/self"
        assert request.headers["X-Noust-Actor-Scope"] == "read"


class TestRekey:
    def test_the_new_key_is_generated_once_and_is_not_the_old_one(self, fleet):
        old = fleet.keys.public_key("web-2")

        first = fleet.manager.rekey_command("web-2")
        second = fleet.manager.rekey_command("web-2")

        pending = fleet.keys.pending_public_key("web-2")
        assert first == second
        assert pending is not None and old is not None
        assert pending.fingerprint != old.fingerprint
        assert pending.blob in first
        assert first.endswith("--name nas")

    def test_the_node_s_code_for_the_new_key_switches_to_it(self, fleet):
        fleet.manager.rekey_command("web-2")
        new = fleet.keys.pending_public_key("web-2")
        fleet.node.token = OTHER_TOKEN

        record = fleet.manager.rekey(
            "web-2", join_code=join_code(new.line(), token=OTHER_TOKEN, ssh_user="noust-tunnel")
        )

        assert record.status == "reachable"
        assert record.ssh_user == "noust-tunnel"
        assert fleet.keys.public_key("web-2").fingerprint == new.fingerprint
        assert fleet.keys.pending_public_key("web-2") is None
        assert _token(fleet) == OTHER_TOKEN

    def test_a_node_that_does_not_answer_puts_everything_back(self, fleet):
        old_key = fleet.keys.public_key("web-2")
        old_record = fleet.manager.get("web-2")
        fleet.manager.rekey_command("web-2")
        new = fleet.keys.pending_public_key("web-2")
        # The node never accepted the new token.
        with pytest.raises(NodeError):
            fleet.manager.rekey("web-2", join_code=join_code(new.line(), token=OTHER_TOKEN))

        assert fleet.keys.public_key("web-2").fingerprint == old_key.fingerprint
        assert fleet.keys.pending_public_key("web-2").fingerprint == new.fingerprint
        assert _token(fleet) != OTHER_TOKEN
        assert fleet.manager.get("web-2").ssh_user == old_record.ssh_user

        # Resumable: once the node takes it, the same code finishes the job.
        fleet.node.token = OTHER_TOKEN
        record = fleet.manager.rekey("web-2", join_code=join_code(new.line(), token=OTHER_TOKEN))
        assert record.status == "reachable"
        assert fleet.keys.public_key("web-2").fingerprint == new.fingerprint

    def test_a_code_for_the_old_key_is_refused(self, fleet):
        old = fleet.keys.public_key("web-2")
        fleet.manager.rekey_command("web-2")

        with pytest.raises(NodeError) as caught:
            fleet.manager.rekey("web-2", join_code=join_code(old.line()))

        assert "another key than the new one" in caught.value.message
        assert fleet.keys.pending_public_key("web-2") is not None

    def test_without_a_rotation_under_way(self, fleet):
        stranger = ed25519_line(5, "noust-central@nas")
        with pytest.raises(NodeError) as caught:
            fleet.manager.rekey("web-2", join_code=join_code(stranger))
        assert "noust node rekey web-2" in caught.value.details

    def test_a_code_from_another_server_is_refused(self, fleet):
        fleet.manager.rekey_command("web-2")
        new = fleet.keys.pending_public_key("web-2")
        before = fleet.keys.public_key("web-2")

        with pytest.raises(NodeError) as caught:
            fleet.manager.rekey(
                "web-2", join_code=join_code(new.line(), ssh_host_key=ed25519_line(77))
            )

        assert "another server" in caught.value.message
        assert fleet.keys.public_key("web-2").fingerprint == before.fingerprint


class TestMigrateTunnel:
    def test_the_command_keeps_the_key(self, fleet):
        key = fleet.keys.public_key("web-2")
        command = fleet.manager.migrate_tunnel_command("web-2")
        assert key.blob in command
        assert "--ssh-user" not in command

    def test_switches_the_account(self, fleet):
        key = fleet.keys.public_key("web-2").line()
        fleet.node.token = OTHER_TOKEN

        record = fleet.manager.migrate_tunnel(
            "web-2",
            join_code=join_code(key, ssh_user="noust-tunnel", token=OTHER_TOKEN, console_port=9000),
        )

        assert (record.ssh_user, record.console_port, record.status) == (
            "noust-tunnel",
            9000,
            "reachable",
        )
        # The tunnel is reopened as the new account.
        argv = fleet.runner.processes[-1].argv
        assert argv[argv.index("-l") + 1] == "noust-tunnel"

    def test_a_code_that_still_names_root_is_refused(self, fleet):
        key = fleet.keys.public_key("web-2").line()
        with pytest.raises(NodeError) as caught:
            fleet.manager.migrate_tunnel("web-2", join_code=join_code(key, ssh_user="root"))
        assert "still authorizes root" in caught.value.message

    def test_a_code_for_another_key_is_refused(self, fleet):
        with pytest.raises(NodeError):
            fleet.manager.migrate_tunnel(
                "web-2", join_code=join_code(ed25519_line(5), ssh_user="noust-tunnel")
            )

    def test_a_failure_puts_the_record_and_token_back(self, fleet):
        key = fleet.keys.public_key("web-2").line()
        before = _token(fleet)

        with pytest.raises(NodeError):
            fleet.manager.migrate_tunnel(
                "web-2", join_code=join_code(key, ssh_user="noust-tunnel", token=OTHER_TOKEN)
            )

        assert fleet.manager.get("web-2").ssh_user == "root"
        assert _token(fleet) == before


class TestCommands:
    @pytest.fixture
    def cli_fleet(self, fleet, tmp_path, monkeypatch):
        monkeypatch.setattr("noust.cli.commands.node._manager", lambda: fleet.manager)
        monkeypatch.setenv("NOUST_WEB_STATE_DIR", str(tmp_path / "state"))
        return fleet

    def test_rekey_in_two_steps(self, cli_fleet):
        first = CliRunner().invoke(root_cli, ["node", "rekey", "web-2", "--json"])
        assert first.exit_code == 0, first.output
        command = json.loads(first.stdout)["authorize_command"]
        new = cli_fleet.keys.pending_public_key("web-2")
        assert new.blob in command

        cli_fleet.node.token = OTHER_TOKEN
        second = CliRunner().invoke(
            root_cli,
            ["node", "rekey", "web-2", "--join-code", "-", "--json"],
            input=join_code(new.line(), token=OTHER_TOKEN) + "\n",
        )
        assert second.exit_code == 0, second.output
        assert json.loads(second.stdout)["status"] == "reachable"
        assert OTHER_TOKEN not in second.output

    def test_migrate_tunnel_in_two_steps(self, cli_fleet):
        first = CliRunner().invoke(root_cli, ["node", "migrate-tunnel", "web-2"])
        assert first.exit_code == 0, first.output
        assert "noust fleet authorize --central-key" in first.stdout
        assert "noust node migrate-tunnel web-2 --join-code -" in first.stdout

        key = cli_fleet.keys.public_key("web-2").line()
        cli_fleet.node.token = OTHER_TOKEN
        second = CliRunner().invoke(
            root_cli,
            ["node", "migrate-tunnel", "web-2", "--join-code", "-"],
            input=join_code(key, token=OTHER_TOKEN, ssh_user="noust-tunnel") + "\n",
        )
        assert second.exit_code == 0, second.output
        assert "is reached as noust-tunnel" in second.output

    def test_list_shows_access_and_suggests_the_move(self, cli_fleet):
        result = CliRunner().invoke(root_cli, ["node", "list"])
        assert result.exit_code == 0, result.output
        assert "admin" in result.output
        assert "noust node migrate-tunnel NAME" in result.output

    def test_rehearsals_change_nothing(self, cli_fleet):
        result = CliRunner().invoke(root_cli, ["--dry-run", "node", "rekey", "web-2"])
        assert result.exit_code == 0, result.output
        assert cli_fleet.keys.pending_public_key("web-2") is None
