"""
Tests for ``noust node link`` and ``noust fleet link``: the shells over the link modules.

The modules are tested on their own; these pin what the commands add - root checks, the
link code read from stdin and never echoed, the audit entries, the console kept on
loopback, and the ``--json`` shapes.
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
from noust.fleet import nodes as nodes_module
from noust.fleet.link import node as node_module
from noust.web.auth import STATE_DIR_ENV
from tests.fleet_support import HOST_KEY, TOKEN
from tests.link_support import LINK_UID, NONCE, LinkRig, build_link_rig
from tests.test_fleet_link_node import Server


@pytest.fixture
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "state"
    directory.mkdir()
    monkeypatch.setenv(STATE_DIR_ENV, str(directory))
    return directory


@pytest.fixture(autouse=True)
def _central_named_nas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")
    monkeypatch.setattr("noust.fleet.link.central.central_name", lambda: "nas")
    monkeypatch.setattr("noust.fleet.nodes.getpass.getuser", lambda: "root")


def _audit(state_dir: Path) -> list[dict[str, Any]]:
    path = state_dir / "web-audit.log"
    if not path.exists():
        return []
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    return [e for e in entries if str(e.get("action", "")).startswith("fleet.")]


class _Registry:
    """The node registry as the command builds it, told not to wait for a server to connect."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def add_linked(self, name: str, *, link_code: str) -> Any:
        return self.inner.add_linked(name, link_code=link_code, wait=0)


@pytest.fixture
def rig(tmp_path: Path, state_dir: Path, monkeypatch: pytest.MonkeyPatch) -> LinkRig:
    built = build_link_rig(tmp_path / "central")
    monkeypatch.setattr("noust.cli.commands.node_link._links", lambda: built.links)
    monkeypatch.setattr(nodes_module, "NodeManager", lambda *a, **k: _Registry(built.manager))
    monkeypatch.setattr(fleet_cli, "check_root", lambda: True)
    monkeypatch.setattr("noust.cli.commands.node_link._operator", lambda: "cli:root")
    return built


def run(*args: str, input: str | None = None):
    return CliRunner().invoke(root_cli, list(args), input=input)


class TestCentral:
    def test_setup_needs_root(self, rig, monkeypatch):
        monkeypatch.setattr(fleet_cli, "check_root", lambda: False)
        result = run("node", "link", "setup")
        assert isinstance(result.exception, NodeError)
        assert "needs root" in result.exception.message

    def test_setup_says_what_it_made_and_what_to_do_next(self, rig):
        result = run("node", "link", "setup", "--json")

        assert result.exit_code == 0, result.output
        body = json.loads(result.output)
        assert body["account"] == "noust-link"
        assert body["ssh_port"] == 2222
        assert body["sshd_policy"].endswith("00-noust-link.conf")

    def test_status_before_and_after(self, rig):
        result = run("node", "link", "status")
        assert result.exit_code == 0, result.output
        assert "takes servers that dial in" in result.output
        rig.invite("web-5")
        result = run("node", "link", "status", "--json")
        body = json.loads(result.output)
        assert body["ready"] is True
        assert [row["node"] for row in body["links"]] == ["web-5"]

    def test_invite_prints_the_command_for_the_server(self, rig):
        result = run("node", "link", "invite", "web-5", "--host", "central.example.com")

        assert result.exit_code == 0, result.output
        commands = [
            line for line in result.output.splitlines() if line.startswith("noust fleet link")
        ]
        assert len(commands) == 1
        assert "--host central.example.com" in commands[0]
        assert "--remote-port 24000" in commands[0]
        assert "noust node link add web-5" in result.output

    def test_invite_in_json(self, rig):
        result = run("node", "link", "invite", "web-5", "--host", "central.example.com", "--json")
        body = json.loads(result.output)
        assert body["node"] == "web-5"
        assert body["command"].startswith("noust fleet link ")
        assert "nonce" not in body

    def test_a_rehearsal_invites_nobody(self, rig):
        result = run("--dry-run", "node", "link", "invite", "web-5", "--host", "x.example.com")
        assert result.exit_code == 0, result.output
        assert "would invite" in result.output
        assert rig.links.records.list() == []

    def test_add_reads_the_code_from_stdin_and_never_shows_the_token(self, rig, state_dir):
        invite = rig.invite("web-5")
        rig.central.listen(invite.port, uid=LINK_UID)

        result = run(
            "node", "link", "add", "web-5", "--link-code", "-", input=rig.code(invite) + "\n"
        )

        assert result.exit_code == 0, result.output
        assert "Node web-5 added" in result.output
        assert TOKEN not in result.output
        entries = _audit(state_dir)
        assert [(e["action"], e["result"]) for e in entries if e["action"] == "fleet.node.add"] == [
            ("fleet.node.add", "success")
        ]
        assert TOKEN not in json.dumps(entries)

    def test_a_bad_code_is_audited_as_a_failure(self, rig, state_dir):
        rig.invite("web-5")

        result = run("node", "link", "add", "web-5", "--link-code", "-", input="noust-link:v1:x\n")

        assert isinstance(result.exception, NodeError)
        added = [e for e in _audit(state_dir) if e["action"] == "fleet.node.add"]
        assert [(e["action"], e["result"]) for e in added] == [("fleet.node.add", "failure")]

    def test_cancel_forgets_an_invitation(self, rig):
        rig.invite("web-5")
        result = run("node", "link", "cancel", "web-5")
        assert result.exit_code == 0, result.output
        assert rig.links.records.list() == []


class TestServer:
    @pytest.fixture
    def server(self, tmp_path: Path, state_dir: Path, monkeypatch: pytest.MonkeyPatch):
        from noust.cli.web_state import token_manager

        built = Server(tmp_path, token_manager())
        monkeypatch.setattr(fleet_cli, "check_root", lambda: True)
        monkeypatch.setattr("noust.cli.commands.node_link._operator", lambda: "cli:root")
        monkeypatch.setattr(
            "noust.cli.commands.node_link.ensure_console_on_loopback",
            lambda verbose, dry, **kw: fleet_cli.ConsoleOutcome(port=8080),
        )
        monkeypatch.setattr("noust.core.store.get_store", lambda *a, **k: built.store)
        monkeypatch.setattr(
            node_module,
            "link",
            functools.partial(
                node_module.link,
                runner=built.runner,
                link_root=built.link_root,
                unit_dir=built.unit_dir,
                store=built.store,
                ssh_program="/usr/bin/ssh",
            ),
        )
        monkeypatch.setattr(
            node_module,
            "unlink",
            functools.partial(
                node_module.unlink,
                runner=built.runner,
                link_root=built.link_root,
                unit_dir=built.unit_dir,
            ),
        )
        yield built
        NoustStore.reset_instance()

    def host_key_args(self) -> tuple[str, ...]:
        return (
            "fleet",
            "link",
            "--name",
            "nas",
            "--as",
            "web-5",
            "--host",
            "central.example.com",
            "--ssh-port",
            "2222",
            "--host-key",
            HOST_KEY,
            "--remote-port",
            "24001",
            "--invite",
            NONCE,
        )

    def test_link_needs_root(self, server, monkeypatch):
        monkeypatch.setattr(fleet_cli, "check_root", lambda: False)
        result = run(*self.host_key_args())
        assert isinstance(result.exception, NodeError)
        assert "needs root" in result.exception.message

    def test_link_prints_the_code_and_audits_without_the_token(self, server, state_dir):
        result = run(*self.host_key_args())

        assert result.exit_code == 0, result.output
        codes = [line for line in result.output.splitlines() if line.startswith("noust-link:v1:")]
        assert len(codes) == 1
        assert "noust node link add web-5" in result.output
        entries = _audit(state_dir)
        assert [(e["action"], e["result"]) for e in entries] == [("fleet.link", "success")]
        assert "noust_tok_" not in json.dumps(entries)
        assert entries[0]["resource"] == "central:nas"

    def test_link_in_json_holds_the_code(self, server):
        result = run(*self.host_key_args(), "--json")

        body = json.loads(result.stdout)
        assert body["link_code"].startswith("noust-link:v1:")
        assert body["unit"] == "noust-link-nas.service"
        assert body["remote_port"] == 24001

    def test_a_rehearsal_prints_no_code(self, server):
        result = run("--dry-run", *self.host_key_args())

        assert result.exit_code == 0, result.output
        assert "noust-link:v1:" not in result.output
        assert "Rehearsal" in result.output
        assert not server.unit.exists()

    def test_a_refusal_is_audited(self, server, state_dir):
        args = list(self.host_key_args())
        args[args.index("--remote-port") + 1] = "443"

        result = run(*args)

        assert isinstance(result.exception, NodeError)
        assert [(e["action"], e["result"]) for e in _audit(state_dir)] == [
            ("fleet.link", "failure")
        ]

    def test_unlink_removes_the_link_and_audits(self, server, state_dir):
        run(*self.host_key_args())

        result = run("fleet", "unlink", "--name", "nas")

        assert result.exit_code == 0, result.output
        assert "no longer dials nas" in result.output
        assert not server.unit.exists()
        assert [e["action"] for e in _audit(state_dir)] == ["fleet.link", "fleet.unlink"]

    def test_unlink_on_a_server_that_never_linked(self, server):
        result = run("fleet", "unlink", "--name", "nas")
        assert result.exit_code == 0, result.output
        assert "holds no link" in result.output
