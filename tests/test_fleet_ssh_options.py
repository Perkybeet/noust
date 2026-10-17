# Copyright (c) 2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How the central connects to one server: the wait for the connection, and a bastion in between
(checklist F-06).

The bastion is the one hop that could weaken the trust model if it were done with ``ssh -J``:
a jump host is started by a second ssh that is told only the account and the port, so with
``-F /dev/null`` it would read the operator's own keys and ``known_hosts`` and ask the
operator about an unknown host. These tests pin the other way: the central builds the whole
command of the hop itself, so the bastion's host key is pinned and checked strictly, no agent,
no password, the same algorithms, and the hop can only forward one stream (``-W``). The
server's own host key stays pinned under its own alias, and what it permits (``PermitOpen`` on
its console) is the server's, untouched.
"""

from __future__ import annotations

import shlex
import sqlite3
from pathlib import Path

import pytest

from noust.core.exceptions import NodeError, NodeRefusedError, NodeUnreachableError
from noust.core.store import NodeRecord
from noust.fleet.keys import known_hosts_line
from noust.fleet.ssh_options import (
    DEFAULT_CONNECT_TIMEOUT,
    Bastion,
    NodeSSHOptions,
    SSHOptions,
    parse_bastion,
    validate_connect_timeout,
)
from noust.fleet.tunnels import explain_ssh_failure, key_was_revoked, ssh_argv
from tests.fleet_support import HOST_KEY, build_fleet, ed25519_line, fingerprint

BASTION_KEY = ed25519_line(7)


@pytest.fixture(autouse=True)
def _central_named_nas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")


@pytest.fixture
def fleet(tmp_path: Path):
    return build_fleet(tmp_path)


def register(fleet, name: str = "web-2", **fields) -> NodeRecord:
    values = {
        "name": name,
        "ssh_host": "10.0.0.5",
        "ssh_port": 22,
        "ssh_user": "noust-tunnel",
        "host_key": known_hosts_line(name, HOST_KEY),
        "console_port": 8080,
    }
    values.update(fields)
    return fleet.store.save_node(NodeRecord(**values))


def bastion(**fields) -> Bastion:
    values = {"host": "bastion.example.com", "port": 2222, "user": "hop", "host_key": BASTION_KEY}
    values.update(fields)
    return Bastion(**values)


def proxy_argv(argv: list[str]) -> list[str]:
    """The tunnel's ``ProxyCommand``, split the way the shell ssh starts it splits it."""
    options = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-o"]
    command = next(o for o in options if o.startswith("ProxyCommand="))
    return shlex.split(command.removeprefix("ProxyCommand="))


def options_of(argv: list[str]) -> dict[str, str]:
    pairs = (argv[i + 1].split("=", 1) for i, arg in enumerate(argv) if arg == "-o")
    return dict(pairs)


class TestValidation:
    @pytest.mark.parametrize("value", [1, 10, 300])
    def test_a_timeout_between_one_and_three_hundred_seconds_is_accepted(self, value):
        assert validate_connect_timeout(value) == value

    @pytest.mark.parametrize("value", [0, -1, 301, 10.5, "10", True, None])
    def test_anything_else_is_refused(self, value):
        with pytest.raises(NodeError) as caught:
            validate_connect_timeout(value)
        assert "1 to 300" in caught.value.details

    def test_a_bastion_is_user_host_and_port(self):
        parsed = parse_bastion("ops@bastion.example.com:2222", BASTION_KEY + " root@bastion")

        assert (parsed.user, parsed.host, parsed.port) == ("ops", "bastion.example.com", 2222)
        assert parsed.host_key == BASTION_KEY
        assert parsed.address == "ops@bastion.example.com:2222"

    def test_the_port_defaults_to_22(self):
        assert parse_bastion("ops@bastion.example.com", BASTION_KEY).port == 22

    def test_an_ipv6_bastion_is_written_in_brackets_with_a_port(self):
        parsed = parse_bastion("ops@[2001:db8::1]:2200", BASTION_KEY)

        assert (parsed.host, parsed.port) == ("2001:db8::1", 2200)

    def test_the_account_is_required(self):
        with pytest.raises(NodeError) as caught:
            parse_bastion("bastion.example.com", BASTION_KEY)
        assert "account" in caught.value.message.lower()

    @pytest.mark.parametrize(
        "target",
        [
            "ops@bad host",
            "ops@-oProxyCommand=x",
            "ops@host;id",
            "ops@host:99999",
            "ops@",
            "ops@host\nid",
        ],
    )
    def test_a_target_that_could_reach_ssh_as_an_option_is_refused(self, target):
        with pytest.raises(NodeError):
            parse_bastion(target, BASTION_KEY)

    def test_a_host_key_that_is_not_an_ed25519_key_is_refused(self):
        with pytest.raises(NodeError):
            parse_bastion("ops@bastion.example.com", "ssh-rsa AAAAB3NzaC1yc2E")


class TestStore:
    def test_a_node_without_options_has_the_defaults(self, fleet):
        register(fleet)

        found = NodeSSHOptions(fleet.store).get("web-2")

        assert found == SSHOptions(connect_timeout=None, bastion=None)
        assert found.effective_timeout == DEFAULT_CONNECT_TIMEOUT == 10

    def test_options_survive_a_read(self, fleet):
        register(fleet)
        options = NodeSSHOptions(fleet.store)

        options.configure("web-2", connect_timeout=45, bastion=bastion(), actor="op")

        found = options.get("web-2")
        assert found.connect_timeout == 45 and found.effective_timeout == 45
        assert found.bastion == bastion()

    def test_changing_the_timeout_keeps_the_bastion(self, fleet):
        register(fleet)
        options = NodeSSHOptions(fleet.store)
        options.configure("web-2", bastion=bastion())

        options.configure("web-2", connect_timeout=30)

        assert options.get("web-2").bastion == bastion()
        assert options.get("web-2").connect_timeout == 30

    def test_the_bastion_is_removed_on_its_own(self, fleet):
        register(fleet)
        options = NodeSSHOptions(fleet.store)
        options.configure("web-2", connect_timeout=30, bastion=bastion())

        options.configure("web-2", bastion=None)

        assert options.get("web-2") == SSHOptions(connect_timeout=30, bastion=None)

    def test_the_timeout_goes_back_to_the_default(self, fleet):
        register(fleet)
        options = NodeSSHOptions(fleet.store)
        options.configure("web-2", connect_timeout=30)

        options.configure("web-2", connect_timeout=None)

        assert options.get("web-2").connect_timeout is None

    def test_an_unknown_node_cannot_have_options(self, fleet):
        with pytest.raises(NodeError) as caught:
            NodeSSHOptions(fleet.store).configure("ghost", connect_timeout=30)
        assert "ghost" in caught.value.message

    def test_removing_the_node_removes_its_options(self, fleet):
        register(fleet)
        NodeSSHOptions(fleet.store).configure("web-2", connect_timeout=30, bastion=bastion())

        fleet.store.delete_node("web-2")

        rows = fleet.store._get_connection().execute("SELECT * FROM node_ssh_options").fetchall()
        assert rows == []

    def test_a_bastion_cannot_be_the_server_itself(self, fleet):
        register(fleet, ssh_host="10.0.0.5", ssh_port=22)

        with pytest.raises(NodeError) as caught:
            NodeSSHOptions(fleet.store).configure(
                "web-2", bastion=bastion(host="10.0.0.5", port=22)
            )

        assert "itself" in caught.value.message

    def test_a_link_server_has_no_ssh_to_configure(self, fleet):
        register(fleet)
        fleet.store._get_connection().execute(
            "INSERT INTO node_links (node, port, state, nonce, central_host, central_port, created_at) "
            "VALUES ('web-2', 51000, 'active', 'n', 'central.example.com', 22, '2026-01-01T00:00:00')"
        )

        with pytest.raises(NodeError) as caught:
            NodeSSHOptions(fleet.store).configure("web-2", connect_timeout=20)

        assert "dials" in caught.value.message.lower() or "link" in caught.value.message.lower()


class TestArgv:
    def argv(self, fleet, record, **kwargs):
        return ssh_argv(
            record,
            50123,
            identity=fleet.keys.private_key_path(record.name),
            known_hosts=fleet.keys.known_hosts_path(record.name),
            **kwargs,
        )

    def test_without_options_nothing_changes(self, fleet):
        record = register(fleet)

        argv = self.argv(fleet, record)

        assert options_of(argv)["ConnectTimeout"] == "10"
        assert "ProxyCommand" not in options_of(argv)
        assert "-J" not in argv

    def test_the_wait_is_the_servers_own(self, fleet):
        record = register(fleet)

        argv = self.argv(fleet, record, options=SSHOptions(connect_timeout=120))

        assert options_of(argv)["ConnectTimeout"] == "120"

    def test_a_bastion_is_a_proxy_command_the_central_builds_whole(self, fleet):
        record = register(fleet)
        identity = fleet.keys.private_key_path("web-2")
        bastion_hosts = fleet.keys.bastion_known_hosts_path("web-2")

        argv = self.argv(
            fleet,
            record,
            options=SSHOptions(connect_timeout=20, bastion=bastion()),
            bastion_known_hosts=bastion_hosts,
        )

        proxy = proxy_argv(argv)
        assert proxy[:11] == [
            "ssh",
            "-W",
            "[%h]:%p",
            "-T",
            "-F",
            "/dev/null",
            "-i",
            str(identity),
            "-p",
            "2222",
            "-l",
        ]
        assert proxy[11] == "hop"
        assert proxy[-2:] == ["--", "bastion.example.com"]
        hop = options_of(proxy)
        assert hop["StrictHostKeyChecking"] == "yes"
        assert hop["UserKnownHostsFile"] == str(bastion_hosts)
        assert hop["GlobalKnownHostsFile"] == "/dev/null"
        assert hop["HostKeyAlias"] == "noust-bastion-web-2"
        assert hop["BatchMode"] == "yes"
        assert hop["IdentitiesOnly"] == "yes"
        assert hop["IdentityAgent"] == "none"
        assert hop["PasswordAuthentication"] == "no"
        assert hop["KbdInteractiveAuthentication"] == "no"
        assert hop["ForwardAgent"] == "no"
        assert hop["ConnectTimeout"] == "20"
        assert hop["HostKeyAlgorithms"] == "ssh-ed25519"
        assert "Ciphers" in hop and "KexAlgorithms" in hop and "MACs" in hop

    def test_the_server_behind_it_is_still_pinned_under_its_own_alias(self, fleet):
        record = register(fleet)

        argv = self.argv(
            fleet,
            record,
            options=SSHOptions(bastion=bastion()),
            bastion_known_hosts=fleet.keys.bastion_known_hosts_path("web-2"),
        )

        outer = options_of(argv)
        assert outer["HostKeyAlias"] == "noust-node-web-2"
        assert outer["StrictHostKeyChecking"] == "yes"
        assert outer["UserKnownHostsFile"] == str(fleet.keys.known_hosts_path("web-2"))
        assert argv[-2:] == ["--", "10.0.0.5"]
        assert "127.0.0.1:50123:127.0.0.1:8080" in argv, "the console's port, and no other"

    def test_there_is_no_jump_option_and_no_agent(self, fleet):
        record = register(fleet)

        argv = self.argv(
            fleet,
            record,
            options=SSHOptions(bastion=bastion()),
            bastion_known_hosts=fleet.keys.bastion_known_hosts_path("web-2"),
        )

        joined = " ".join(argv)
        assert "-J" not in argv
        assert "ProxyJump" not in joined
        assert "-A" not in argv and "ForwardAgent=yes" not in joined
        assert "-W" not in argv, "the outer ssh forwards a port; only the hop forwards a stream"

    def test_a_bastion_without_its_pinned_key_is_never_connected_to(self, fleet):
        record = register(fleet)

        with pytest.raises(NodeError) as caught:
            self.argv(fleet, record, options=SSHOptions(bastion=bastion()))

        assert "host key" in caught.value.message

    def test_a_percent_sign_in_a_path_is_not_a_token_of_ssh(self, fleet):
        record = register(fleet)

        argv = ssh_argv(
            record,
            50123,
            identity=Path("/tmp/100%/id_ed25519"),
            known_hosts=Path("/tmp/100%/known_hosts"),
            options=SSHOptions(bastion=bastion()),
            bastion_known_hosts=Path("/tmp/100%/bastion_known_hosts"),
        )

        command = next(a for a in argv if a.startswith("ProxyCommand=")).removeprefix(
            "ProxyCommand="
        )
        assert "/tmp/100%%/id_ed25519" in command
        assert "[%h]:%p" in command

    def test_a_path_with_a_space_or_a_quote_stays_one_argument(self, fleet):
        record = register(fleet)

        argv = ssh_argv(
            record,
            50123,
            identity=Path("/tmp/it's here/id_ed25519"),
            known_hosts=Path("/tmp/it's here/known_hosts"),
            options=SSHOptions(bastion=bastion()),
            bastion_known_hosts=Path("/tmp/it's here/bastion_known_hosts"),
        )

        proxy = proxy_argv(argv)
        assert proxy[proxy.index("-i") + 1] == "/tmp/it's here/id_ed25519"
        assert options_of(proxy)["UserKnownHostsFile"] == "/tmp/it's here/bastion_known_hosts"


class TestTunnel:
    def configure(self, fleet, **kwargs):
        register(fleet)
        NodeSSHOptions(fleet.store).configure("web-2", **kwargs)

    def test_the_tunnel_goes_through_the_bastion_and_pins_both_keys(self, fleet):
        self.configure(fleet, bastion=bastion(), connect_timeout=25)

        fleet.tunnels.endpoint("web-2")

        argv = fleet.runner.processes[0].argv
        assert options_of(argv)["ConnectTimeout"] == "25"
        hop = options_of(proxy_argv(argv))
        assert hop["HostKeyAlias"] == "noust-bastion-web-2"
        assert fleet.keys.bastion_known_hosts_path("web-2").read_text() == (
            f"noust-bastion-web-2 {BASTION_KEY}\n"
        )
        assert fleet.keys.known_hosts_path("web-2").read_text() == f"noust-node-web-2 {HOST_KEY}\n"

    def test_the_pins_are_rewritten_from_the_store_on_every_open(self, fleet):
        self.configure(fleet, bastion=bastion())
        fleet.keys.pin_bastion_host_key("web-2", "noust-bastion-web-2 ssh-ed25519 AAAAother")

        fleet.tunnels.endpoint("web-2")

        assert BASTION_KEY in fleet.keys.bastion_known_hosts_path("web-2").read_text()

    def test_removing_the_bastion_removes_its_pin(self, fleet):
        self.configure(fleet, bastion=bastion())
        fleet.tunnels.endpoint("web-2")
        fleet.tunnels.close("web-2")

        NodeSSHOptions(fleet.store).configure("web-2", bastion=None)
        fleet.tunnels.endpoint("web-2")

        argv = fleet.runner.processes[-1].argv
        assert "ProxyCommand" not in options_of(argv)
        assert not fleet.keys.bastion_known_hosts_path("web-2").exists()

    def test_forgetting_the_node_deletes_the_pin(self, fleet):
        self.configure(fleet, bastion=bastion())
        fleet.tunnels.endpoint("web-2")

        fleet.keys.forget("web-2")

        assert not fleet.keys.bastion_known_hosts_path("web-2").exists()

    def test_a_bastion_that_refuses_the_key_is_unreachable_not_a_node_that_revoked_it(self, fleet):
        self.configure(fleet, bastion=bastion())
        fleet.runner.script(
            ["ssh"], exit_code=255, stderr="hop@bastion.example.com: Permission denied (publickey)."
        )

        with pytest.raises(NodeUnreachableError) as caught:
            fleet.tunnels.endpoint("web-2")

        assert "bastion" in caught.value.message.lower()
        assert "hop@bastion.example.com" in caught.value.message
        assert fleet.store.get_node("web-2").status != "refused", (
            "the node did not refuse anything; the bastion did"
        )

    def test_the_node_refusing_the_key_behind_a_bastion_is_still_a_refusal(self, fleet):
        self.configure(fleet, bastion=bastion())
        fleet.runner.script(
            ["ssh"],
            exit_code=255,
            stderr="noust-tunnel@10.0.0.5: Permission denied (publickey).",
        )

        with pytest.raises(NodeRefusedError):
            fleet.tunnels.endpoint("web-2")

        assert fleet.store.get_node("web-2").status == "refused"

    def test_a_changed_bastion_key_names_the_bastion_and_closes_the_tunnel(
        self, fleet, monkeypatch
    ):
        self.configure(fleet, bastion=bastion())
        told: list[dict] = []
        monkeypatch.setattr(
            "noust.core.notifications.fleet.notify_node_host_key_changed",
            lambda node, **kwargs: told.append({"node": node, **kwargs}),
        )
        words = (
            "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\n"
            "Offending ED25519 key in /run/noust/fleet/nodes/web-2/bastion_known_hosts:1\n"
            "Host key verification failed."
        )
        fleet.runner.script(["ssh"], exit_code=255, stderr=words)

        with pytest.raises(NodeUnreachableError) as caught:
            fleet.tunnels.endpoint("web-2")

        assert "bastion" in caught.value.message.lower()
        assert "does not match the one pinned" in caught.value.message
        assert len(told) == 1
        assert told[0]["address"].startswith("bastion hop@bastion.example.com:2222")

    def test_a_bastion_that_will_not_forward_to_the_server_says_what_to_allow(self, fleet):
        self.configure(fleet, bastion=bastion())
        fleet.runner.script(
            ["ssh"],
            exit_code=255,
            stderr=(
                "channel 0: open failed: administratively prohibited: open failed\n"
                "stdio forwarding failed"
            ),
        )

        with pytest.raises(NodeUnreachableError) as caught:
            fleet.tunnels.endpoint("web-2")

        assert "bastion.example.com" in caught.value.message
        assert "10.0.0.5:22" in caught.value.message
        assert "permitopen" in caught.value.message.lower()

    def test_without_a_bastion_the_old_words_are_the_same(self):
        record = NodeRecord(
            name="web-2",
            ssh_host="web2",
            ssh_port=22,
            ssh_user="root",
            host_key="x",
            console_port=1,
        )

        assert "refused this central's key" in explain_ssh_failure(
            "root@web2: Permission denied (publickey).", record, 255
        )
        assert key_was_revoked("root@web2: Permission denied (publickey).")


class TestBastionAuthorizedLine:
    def test_the_line_for_the_bastion_allows_one_stream_to_the_server_and_nothing_else(self, fleet):
        record = register(fleet)
        public = fleet.manager.central_public_key("web-2")

        line = bastion().authorized_key_line(public, record)

        options, _, key = line.partition(" ssh-ed25519 ")
        assert options.split(",")[:2] == ["restrict", "port-forwarding"]
        assert 'permitopen="10.0.0.5:22"' in options
        assert 'command="/usr/bin/false"' in options
        assert key.startswith(public.split()[1])

    def test_an_ipv6_server_is_bracketed(self, fleet):
        record = register(fleet, ssh_host="2001:db8::5")
        public = fleet.manager.central_public_key("web-2")

        line = bastion().authorized_key_line(public, record)

        assert 'permitopen="[2001:db8::5]:22"' in line


class TestFingerprint:
    def test_the_bastion_reports_its_fingerprint(self):
        assert bastion().fingerprint == fingerprint(BASTION_KEY)


class TestManager:
    def test_adding_a_server_through_a_bastion_opens_the_first_tunnel_through_it(self, fleet):
        key = fleet.manager.central_public_key("web-2")
        from tests.fleet_support import join_code

        fleet.manager.add(
            "web-2",
            ssh_target="noust-tunnel@10.0.0.5",
            join_code=join_code(key, ssh_user="noust-tunnel"),
            bastion=bastion(),
            connect_timeout=30,
        )

        argv = fleet.runner.processes[0].argv
        assert proxy_argv(argv)[-1] == "bastion.example.com"
        assert options_of(argv)["ConnectTimeout"] == "30"
        assert NodeSSHOptions(fleet.store).get("web-2").bastion == bastion()

    def test_a_failed_add_leaves_no_options_behind(self, fleet):
        key = fleet.manager.central_public_key("web-2")
        from tests.fleet_support import join_code

        fleet.runner.script(
            ["ssh"], exit_code=255, stderr="hop@bastion.example.com: Permission denied (publickey)."
        )

        with pytest.raises(NodeUnreachableError):
            fleet.manager.add(
                "web-2",
                ssh_target="noust-tunnel@10.0.0.5",
                join_code=join_code(key, ssh_user="noust-tunnel"),
                bastion=bastion(),
            )

        assert fleet.store.get_node("web-2") is None
        rows = fleet.store._get_connection().execute("SELECT * FROM node_ssh_options").fetchall()
        assert rows == []
        assert not fleet.keys.bastion_known_hosts_path("web-2").exists()

    def test_changing_a_working_connection_is_tested_and_put_back_when_it_breaks(self, fleet):
        fleet.added()
        fleet.tunnels.close("web-2")
        fleet.runner.script(
            ["ssh"], exit_code=255, stderr="hop@bastion.example.com: Permission denied (publickey)."
        )

        with pytest.raises(NodeError) as caught:
            fleet.manager.set_ssh_options("web-2", bastion=bastion())

        assert (
            "put back" in (caught.value.details or "").lower()
            or "kept" in caught.value.message.lower()
        )
        assert NodeSSHOptions(fleet.store).get("web-2").bastion is None

    def test_a_change_that_works_is_kept_and_the_old_tunnel_is_closed(self, fleet):
        fleet.added()
        first = fleet.runner.processes[0]

        fleet.manager.set_ssh_options("web-2", connect_timeout=45)

        assert first.terminated
        assert NodeSSHOptions(fleet.store).get("web-2").connect_timeout == 45
        assert options_of(fleet.runner.processes[-1].argv)["ConnectTimeout"] == "45"

    def test_a_change_can_be_forced_for_a_server_that_is_down(self, fleet):
        fleet.added()
        fleet.tunnels.close("web-2")
        fleet.runner.script(["ssh"], exit_code=255, stderr="ssh: connect: Connection refused")

        fleet.manager.set_ssh_options("web-2", connect_timeout=60, test=False)

        assert NodeSSHOptions(fleet.store).get("web-2").connect_timeout == 60

    def test_the_options_are_part_of_the_nodes_description(self, fleet):
        fleet.added()
        fleet.manager.set_ssh_options("web-2", connect_timeout=45, bastion=bastion(), test=False)

        described = fleet.manager.describe_ssh("web-2")

        assert described["connect_timeout"] == 45
        assert described["bastion"]["address"] == "hop@bastion.example.com:2222"
        assert described["bastion"]["fingerprint"] == fingerprint(BASTION_KEY)
        assert "host_key" not in described["bastion"], "the key itself is not needed to display it"


def test_the_table_is_declared_in_the_fleet_fragment():
    from noust.core import schema_v14

    assert "node_ssh_options" in schema_v14.FLEET_SQL
    connection = sqlite3.connect(":memory:")
    connection.executescript("CREATE TABLE nodes (name TEXT PRIMARY KEY);" + schema_v14.FLEET_SQL)


class TestResolveBastion:
    def scan_output(self) -> str:
        return f"bastion.example.com {BASTION_KEY}\n"

    def test_a_key_given_is_pinned_as_it_is(self, fleet):
        resolved = fleet.manager.resolve_bastion("hop@bastion.example.com", host_key=BASTION_KEY)

        assert resolved.host_key == BASTION_KEY
        assert fleet.runner.calls_to("ssh-keyscan") == []

    def test_a_fingerprint_is_checked_against_what_answers(self, fleet):
        fleet.runner.script(["ssh-keyscan"], stdout=self.scan_output())

        resolved = fleet.manager.resolve_bastion(
            "hop@bastion.example.com:2200", fingerprint=fingerprint(BASTION_KEY)
        )

        assert resolved.host_key == BASTION_KEY and resolved.port == 2200
        scan = fleet.runner.calls_to("ssh-keyscan")[0]
        assert scan[-3:] == ("-p", "2200", "bastion.example.com") or "2200" in scan

    def test_another_key_answering_is_refused_with_both_fingerprints(self, fleet):
        fleet.runner.script(["ssh-keyscan"], stdout=f"bastion.example.com {ed25519_line(8)}\n")

        with pytest.raises(NodeError) as caught:
            fleet.manager.resolve_bastion(
                "hop@bastion.example.com", fingerprint=fingerprint(BASTION_KEY)
            )

        assert fingerprint(ed25519_line(8)) in caught.value.details
        assert fingerprint(BASTION_KEY) in caught.value.details

    def test_neither_a_key_nor_a_fingerprint_is_never_learnt_on_first_use(self, fleet):
        with pytest.raises(NodeError) as caught:
            fleet.manager.resolve_bastion("hop@bastion.example.com")

        assert "first use" in caught.value.details
        assert fleet.runner.calls_to("ssh-keyscan") == []
