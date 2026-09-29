"""
Tests for ``noust fleet`` and ``noust node``: the shells over the fleet modules.

The modules are tested on their own; these pin what the commands add - root
checks, the join code read from stdin and never echoed, the audit entries,
the console kept on loopback, and the ``--json`` shapes.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.cli.commands import fleet as fleet_cli
from noust.core.exceptions import NodeError
from noust.core.store import NoustStore
from noust.fleet import authorize as authorize_module
from noust.web.auth import STATE_DIR_ENV
from tests.fleet_support import TOKEN, build_fleet, join_code
from tests.test_fleet_authorize import CENTRAL_KEY, Node


@pytest.fixture
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "state"
    directory.mkdir()
    monkeypatch.setenv(STATE_DIR_ENV, str(directory))
    return directory


@pytest.fixture(autouse=True)
def _central_named_nas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")


def _audit(state_dir: Path) -> list[dict[str, Any]]:
    path = state_dir / "web-audit.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


class TestAuthorizeCommand:
    @pytest.fixture
    def node(self, tmp_path: Path, state_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Node:
        from noust.cli.web_state import token_manager

        node = Node(tmp_path, token_manager())
        monkeypatch.setattr(fleet_cli, "check_root", lambda: True)
        monkeypatch.setattr(fleet_cli, "ensure_console_on_loopback", lambda verbose, dry: 8080)
        monkeypatch.setattr(
            authorize_module,
            "authorize",
            functools.partial(
                authorize_module.authorize,
                runner=node.runner,
                host_key_file=node.host_key_file,
                sshd_config=node.sshd_config,
                passwd=node.passwd,
            ),
        )
        monkeypatch.setattr(
            authorize_module,
            "deauthorize",
            functools.partial(
                authorize_module.deauthorize,
                runner=node.runner,
                sshd_config=node.sshd_config,
                passwd=node.passwd,
            ),
        )
        return node

    def test_needs_root(self, state_dir, monkeypatch):
        monkeypatch.setattr(fleet_cli, "check_root", lambda: False)
        result = CliRunner().invoke(
            root_cli, ["fleet", "authorize", "--central-key", CENTRAL_KEY, "--name", "nas"]
        )
        assert isinstance(result.exception, NodeError)
        assert "needs root" in result.exception.message

    def test_prints_the_join_code_and_audits(self, node, state_dir):
        result = CliRunner().invoke(
            root_cli, ["fleet", "authorize", "--central-key", CENTRAL_KEY, "--name", "nas"]
        )

        assert result.exit_code == 0, result.output
        codes = [line for line in result.output.splitlines() if line.startswith("noust-join:v1:")]
        assert len(codes) == 1
        entries = _audit(state_dir)
        assert [(e["action"], e["result"]) for e in entries] == [("fleet.authorize", "success")]
        assert TOKEN not in json.dumps(entries)
        assert entries[0]["resource"] == "central:nas"
        assert "noust_tok_" not in entries[0]["detail"]

    def test_json_shape(self, node, state_dir):
        result = CliRunner().invoke(
            root_cli,
            ["fleet", "authorize", "--central-key", CENTRAL_KEY, "--name", "nas", "--json"],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert set(payload) == {
            "join_code",
            "central",
            "token_name",
            "node_name",
            "ssh_user",
            "ssh_port",
            "console_port",
            "authorized_keys",
            "key_fingerprint",
            "host_key_fingerprint",
            "key_changed",
            "replaced_tokens",
        }
        assert payload["join_code"].startswith("noust-join:v1:")
        assert (payload["central"], payload["console_port"]) == ("nas", 8080)

    def test_a_second_authorize_asks_before_replacing(self, node, state_dir):
        args = ["fleet", "authorize", "--central-key", CENTRAL_KEY, "--name", "nas"]
        CliRunner().invoke(root_cli, args)

        declined = CliRunner().invoke(root_cli, args, input="n\n")
        assert isinstance(declined.exception, NodeError)
        accepted = CliRunner().invoke(root_cli, [*args, "--yes", "--json"])
        assert json.loads(accepted.output)["replaced_tokens"] == ["fleet-nas"]
        results = [e["result"] for e in _audit(state_dir)]
        assert results == ["success", "failure", "success"]

    def test_deauthorize(self, node, state_dir):
        CliRunner().invoke(
            root_cli, ["fleet", "authorize", "--central-key", CENTRAL_KEY, "--name", "nas"]
        )

        result = CliRunner().invoke(root_cli, ["fleet", "deauthorize", "--name", "nas", "--json"])

        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == {
            "central": "nas",
            "authorized_keys": str(node.keys_file()),
            "removed_keys": 1,
            "revoked_tokens": ["fleet-nas"],
        }
        assert _audit(state_dir)[-1]["action"] == "fleet.deauthorize"

    def test_dry_run_authorize_mints_no_token_and_leaves_authorized_keys_alone(
        self, node, state_dir
    ):
        result = CliRunner().invoke(
            root_cli,
            ["--dry-run", "fleet", "authorize", "--central-key", CENTRAL_KEY, "--name", "nas"],
        )

        assert result.exit_code == 0, result.output
        assert "Rehearsal: this join code's token was not saved" in result.output
        assert node.tokens.list_api_tokens() == []
        assert not node.keys_file().exists()

    def test_dry_run_deauthorize_revokes_nothing(self, node, state_dir):
        CliRunner().invoke(
            root_cli, ["fleet", "authorize", "--central-key", CENTRAL_KEY, "--name", "nas"]
        )
        before_tokens = node.tokens.list_api_tokens()
        before_keys = node.keys_file().read_text()

        result = CliRunner().invoke(
            root_cli, ["--dry-run", "fleet", "deauthorize", "--name", "nas"]
        )

        assert result.exit_code == 0, result.output
        assert node.tokens.list_api_tokens() == before_tokens
        assert node.keys_file().read_text() == before_keys


class TestConsoleOnLoopback:
    @pytest.fixture
    def web(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from noust.cli.commands import web

        unit = tmp_path / "noust-web.service"
        state: dict[str, Any] = {"active": False, "enabled_with": None}
        monkeypatch.setattr(web, "_service_unit_path", lambda: unit)
        monkeypatch.setattr(
            web,
            "_service_status",
            lambda verbose: {"active": state["active"]} if unit.exists() else None,
        )

        def enable(options, verbose, *, dry_run=False):
            state["enabled_with"] = options
            return 0

        monkeypatch.setattr(web, "_enable", enable)
        return unit, state

    def _unit(self, unit: Path, *flags: str) -> None:
        unit.write_text(
            "[Service]\nExecStart=/usr/bin/noust web start --under-systemd "
            + " ".join(flags)
            + "\n"
        )

    def test_a_running_loopback_console_is_left_alone(self, web):
        unit, state = web
        self._unit(unit, "--host", "127.0.0.1", "--port", "8123")
        state["active"] = True

        assert fleet_cli.ensure_console_on_loopback(False, False) == 8123
        assert state["enabled_with"] is None

    def test_no_console_is_enabled_on_loopback(self, web):
        _, state = web

        assert fleet_cli.ensure_console_on_loopback(False, False) == 8080
        assert (state["enabled_with"].host, state["enabled_with"].port) == ("127.0.0.1", 8080)

    def test_a_stopped_loopback_console_is_started_on_its_port(self, web):
        unit, state = web
        self._unit(unit, "--host", "127.0.0.1", "--port", "9000")

        assert fleet_cli.ensure_console_on_loopback(False, False) == 9000
        assert state["enabled_with"].port == 9000

    def test_an_exposed_console_is_refused_with_the_fix(self, web):
        unit, state = web
        everywhere = ".".join(["0"] * 4)
        self._unit(unit, "--host", everywhere, "--port", "8080", "--self-signed")
        state["active"] = True

        with pytest.raises(NodeError) as caught:
            fleet_cli.ensure_console_on_loopback(False, False)

        assert everywhere in caught.value.message
        assert "noust web enable --host 127.0.0.1 --port 8080" in caught.value.details
        assert state["enabled_with"] is None

    def test_a_tls_loopback_console_is_refused(self, web):
        unit, state = web
        self._unit(unit, "--host", "127.0.0.1", "--port", "8080", "--self-signed")
        state["active"] = True

        with pytest.raises(NodeError) as caught:
            fleet_cli.ensure_console_on_loopback(False, False)
        assert "TLS" in caught.value.message

    def test_a_console_that_fails_to_start_is_an_error(self, web, monkeypatch):
        from noust.cli.commands import web as web_module

        monkeypatch.setattr(web_module, "_enable", lambda options, verbose, dry_run=False: 1)
        with pytest.raises(NodeError):
            fleet_cli.ensure_console_on_loopback(False, False)


class TestNodeCommands:
    @pytest.fixture
    def fleet(self, tmp_path: Path, state_dir: Path, monkeypatch: pytest.MonkeyPatch):
        built = build_fleet(tmp_path)
        monkeypatch.setattr("noust.cli.commands.node._manager", lambda: built.manager)
        monkeypatch.setattr("noust.fleet.status.NodeManager", lambda: built.manager)
        yield built
        NoustStore.reset_instance()

    def _add(self, fleet) -> Any:
        key = fleet.manager.central_public_key("web-2")
        return CliRunner().invoke(
            root_cli,
            [
                "node",
                "add",
                "web-2",
                "--ssh",
                "root@web2.example.com",
                "--join-code",
                "-",
                "--json",
            ],
            input=join_code(key) + "\n",
        )

    def test_key(self, fleet):
        result = CliRunner().invoke(root_cli, ["node", "key", "web-2", "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert set(payload) == {"node", "public_key", "fingerprint", "authorize_command"}
        assert payload["authorize_command"].startswith("noust fleet authorize --central-key '")
        assert payload["authorize_command"].endswith("--name nas")

    def test_add_reads_the_code_from_stdin_and_never_echoes_it(self, fleet, state_dir):
        result = self._add(fleet)

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert (payload["name"], payload["status"], payload["version"]) == (
            "web-2",
            "reachable",
            "3.0.1",
        )
        assert TOKEN not in result.output
        assert "noust-join" not in result.output
        assert _audit(state_dir)[-1]["action"] == "fleet.node.add"

    def test_add_prompts_without_echo(self, fleet):
        key = fleet.manager.central_public_key("web-2")
        result = CliRunner().invoke(
            root_cli,
            ["node", "add", "web-2", "--ssh", "root@web2.example.com"],
            input=join_code(key) + "\n",
        )
        assert result.exit_code == 0, result.output
        assert "noust-join" not in result.output

    def test_a_failed_add_is_audited(self, fleet, state_dir):
        result = CliRunner().invoke(
            root_cli,
            ["node", "add", "web-2", "--ssh", "web2", "--join-code", "-"],
            input="garbage\n",
        )
        assert isinstance(result.exception, NodeError)
        assert _audit(state_dir)[-1]["result"] == "failure"

    def test_dry_run_add_registers_nothing(self, fleet):
        key = fleet.manager.central_public_key("web-2")
        result = CliRunner().invoke(
            root_cli,
            [
                "--dry-run",
                "node",
                "add",
                "web-2",
                "--ssh",
                "root@web2.example.com",
                "--join-code",
                "-",
            ],
            input=join_code(key) + "\n",
        )

        assert result.exit_code == 0, result.output
        assert fleet.manager.list() == []
        assert fleet.node.requests == []

    def test_dry_run_remove_removes_nothing(self, fleet):
        self._add(fleet)

        result = CliRunner().invoke(root_cli, ["--dry-run", "node", "remove", "web-2", "-f"])

        assert result.exit_code == 0, result.output
        assert fleet.manager.get("web-2") is not None
        assert not fleet.node.revoked

    def test_list_show_test_json(self, fleet):
        self._add(fleet)

        listed = json.loads(CliRunner().invoke(root_cli, ["node", "list", "--json"]).output)
        assert [node["name"] for node in listed["nodes"]] == ["web-2"]
        assert set(listed["nodes"][0]) == {
            "name",
            "ssh_host",
            "ssh_port",
            "ssh_user",
            "host_key",
            "console_port",
            "version",
            "status",
            "last_seen",
            "allow_shell",
            "created_at",
            "updated_at",
        }

        shown = json.loads(CliRunner().invoke(root_cli, ["node", "show", "web-2", "--json"]).output)
        assert shown["tunnel"]["open"] is True
        assert shown["central_key_fingerprint"].startswith("SHA256:")

        tested = CliRunner().invoke(root_cli, ["node", "test", "web-2", "--json"])
        assert tested.exit_code == 0
        assert set(json.loads(tested.output)) == {
            "node",
            "reachable",
            "status",
            "version",
            "latency_ms",
            "error",
            "details",
        }

    def test_test_exits_1_when_unreachable(self, fleet):
        self._add(fleet)
        fleet.node.revoked = True

        result = CliRunner().invoke(root_cli, ["node", "test", "web-2"])

        assert result.exit_code == 1
        assert "refused the fleet token" in result.output

    def test_remove(self, fleet, state_dir):
        self._add(fleet)

        result = CliRunner().invoke(root_cli, ["node", "remove", "web-2", "-f", "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["removed"] is True and payload["node"] == "web-2"
        assert any("deauthorize" in warning for warning in payload["warnings"])
        assert fleet.node.revoked
        assert _audit(state_dir)[-1]["action"] == "fleet.node.remove"

    def test_remove_asks_first(self, fleet):
        self._add(fleet)
        result = CliRunner().invoke(root_cli, ["node", "remove", "web-2"], input="n\n")
        assert "Cancelled" in result.output
        assert fleet.manager.get("web-2")

    def test_list_empty(self, fleet):
        result = CliRunner().invoke(root_cli, ["node", "list"])
        assert "manages no nodes" in result.output

    def test_fleet_status_json(self, fleet):
        self._add(fleet)

        result = CliRunner().invoke(root_cli, ["fleet", "status", "--json"])

        assert result.exit_code == 0, result.output
        (summary,) = json.loads(result.output)["nodes"]
        assert set(summary) == {
            "name",
            "ssh",
            "status",
            "reachable",
            "version",
            "last_seen",
            "latency_ms",
            "apps",
            "units",
            "certificates_expiring",
            "error",
            "details",
            "warnings",
        }
        assert summary["certificates_expiring"] == 1

    def test_fleet_status_table(self, fleet):
        self._add(fleet)
        result = CliRunner().invoke(root_cli, ["fleet", "status"])
        assert result.exit_code == 0, result.output
        assert "web-2" in result.output
