"""
Tests for the central's SSH side: per-node keys, the pinned host key, the tunnels.

The ssh command line is the whole truth about how the central talks to a node,
so it is asserted option by option.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from noust.core.exceptions import NodeError, NodeUnreachableError
from noust.core.store import NodeRecord
from noust.fleet.keys import host_key_alias, known_hosts_line
from noust.fleet.tunnels import BACKOFF_MAX, explain_ssh_failure, ssh_argv
from tests.fleet_support import HOST_KEY, build_fleet


@pytest.fixture(autouse=True)
def _central_named_nas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")


@pytest.fixture
def fleet(tmp_path: Path):
    return build_fleet(tmp_path)


def _register(fleet, name: str = "web-2", **fields) -> NodeRecord:
    values = {
        "name": name,
        "ssh_host": "web2.example.com",
        "ssh_port": 2222,
        "ssh_user": "root",
        "host_key": known_hosts_line(name, HOST_KEY),
        "console_port": 8080,
    }
    values.update(fields)
    return fleet.store.save_node(NodeRecord(**values))


class TestKeys:
    def test_keygen_argv_and_modes(self, fleet):
        public = fleet.manager.central_public_key("web-2")

        calls = fleet.runner.calls_to("ssh-keygen")
        assert len(calls) == 1
        private = fleet.keys.private_key_path("web-2")
        assert calls[0] == (
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            "noust-central@nas",
            "-f",
            str(private),
        )
        assert private == fleet.secrets.root / "fleet" / "nodes" / "web-2" / "id_ed25519"
        assert stat.S_IMODE(private.stat().st_mode) == 0o600
        assert stat.S_IMODE(private.parent.stat().st_mode) == 0o700
        assert public.startswith("ssh-ed25519 ") and public.endswith("noust-central@nas")

    def test_the_pair_is_generated_once(self, fleet):
        first = fleet.manager.central_public_key("web-2")
        second = fleet.manager.central_public_key("web-2")

        assert first == second
        assert len(fleet.runner.calls_to("ssh-keygen")) == 1

    def test_a_half_pair_is_regenerated_whole(self, fleet):
        fleet.manager.central_public_key("web-2")
        fleet.keys.private_key_path("web-2").unlink()

        fleet.manager.central_public_key("web-2")

        assert len(fleet.runner.calls_to("ssh-keygen")) == 2

    def test_a_failing_keygen_is_an_error_with_its_words(self, fleet):
        fleet.runner.script(["ssh-keygen"], exit_code=1, stderr="Saving key failed")

        with pytest.raises(NodeError) as caught:
            fleet.manager.central_public_key("web-2")

        assert "Saving key failed" in caught.value.details

    def test_the_authorize_command_quotes_the_key(self, fleet):
        command = fleet.manager.authorize_command("web-2")
        key = fleet.manager.central_public_key("web-2")

        assert command == f"noust fleet authorize --central-key '{key}' --name nas"

    def test_known_hosts_line_uses_the_alias(self):
        assert host_key_alias("web-2") == "noust-node-web-2"
        assert known_hosts_line("web-2", HOST_KEY + " root@node") == f"noust-node-web-2 {HOST_KEY}"


class TestSSHArgv:
    def test_every_option(self, fleet):
        record = _register(fleet)
        argv = ssh_argv(record, fleet.keys, 50123)

        assert argv[:14] == [
            "ssh",
            "-N",
            "-T",
            "-F",
            "/dev/null",
            "-i",
            str(fleet.keys.private_key_path("web-2")),
            "-p",
            "2222",
            "-l",
            "root",
            "-L",
            "127.0.0.1:50123:127.0.0.1:8080",
            "-o",
        ]
        assert argv[-2:] == ["--", "web2.example.com"]
        options = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-o"]
        assert options == [
            "BatchMode=yes",
            "StrictHostKeyChecking=yes",
            f"UserKnownHostsFile={fleet.keys.known_hosts_path('web-2')}",
            "GlobalKnownHostsFile=/dev/null",
            "HostKeyAlias=noust-node-web-2",
            "CheckHostIP=no",
            "UpdateHostKeys=no",
            "IdentitiesOnly=yes",
            "IdentityAgent=none",
            "PasswordAuthentication=no",
            "KbdInteractiveAuthentication=no",
            "ExitOnForwardFailure=yes",
            "ServerAliveInterval=15",
            "ServerAliveCountMax=3",
            "ConnectTimeout=10",
            "ForwardAgent=no",
            "ForwardX11=no",
            "PermitLocalCommand=no",
            "LogLevel=ERROR",
        ]


class TestTunnels:
    def test_opens_on_demand_and_pins_the_host_key(self, fleet):
        _register(fleet)

        host, port = fleet.tunnels.endpoint("web-2")

        assert (host, port) == ("127.0.0.1", 50000)
        assert fleet.runner.processes[0].argv[0] == "ssh"
        assert fleet.keys.known_hosts_path("web-2").read_text() == (
            f"noust-node-web-2 {HOST_KEY}\n"
        )
        status = fleet.tunnels.status("web-2")
        assert status["open"] and status["local_port"] == 50000 and status["since"]

    def test_reuses_a_live_tunnel(self, fleet):
        _register(fleet)
        first = fleet.tunnels.endpoint("web-2")

        assert fleet.tunnels.endpoint("web-2") == first
        assert len(fleet.runner.processes) == 1

    def test_the_pin_is_rewritten_from_the_store_on_every_open(self, fleet):
        _register(fleet)
        fleet.keys.pin_host_key("web-2", "noust-node-web-2 ssh-ed25519 AAAAsomethingelse")

        fleet.tunnels.endpoint("web-2")

        assert HOST_KEY in fleet.keys.known_hosts_path("web-2").read_text()

    def test_an_unknown_node_is_a_node_error(self, fleet):
        with pytest.raises(NodeError) as caught:
            fleet.tunnels.endpoint("ghost")
        assert not isinstance(caught.value, NodeUnreachableError)

    def test_a_changed_host_key_is_refused_with_ssh_s_words(self, fleet):
        _register(fleet)
        words = (
            "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
            "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\n"
            "Host key verification failed."
        )
        fleet.runner.script(["ssh"], exit_code=255, stderr=words)

        with pytest.raises(NodeUnreachableError) as caught:
            fleet.tunnels.endpoint("web-2")

        assert "does not match the one pinned" in caught.value.message
        assert caught.value.details == words

    def test_reopens_after_the_tunnel_dies(self, fleet):
        _register(fleet)
        fleet.tunnels.endpoint("web-2")
        fleet.runner.processes[0].die(255, "Connection reset by peer")

        host, port = fleet.tunnels.endpoint("web-2")

        assert port == 50001
        assert len(fleet.runner.processes) == 2
        assert fleet.tunnels.status("web-2")["last_error"] == "Connection reset by peer"

    def test_backs_off_after_failures(self, fleet):
        _register(fleet)
        fleet.runner.script(
            ["ssh"], exit_code=255, stderr="ssh: connect to host: Connection refused"
        )

        with pytest.raises(NodeUnreachableError) as first:
            fleet.tunnels.endpoint("web-2")
        assert "Nothing accepts SSH connections" in first.value.message

        # Within the backoff window: refused without starting ssh again.
        with pytest.raises(NodeUnreachableError) as waiting:
            fleet.tunnels.endpoint("web-2")
        assert "next attempt" in waiting.value.message
        assert "Connection refused" in waiting.value.details
        assert len(fleet.runner.processes) == 1

        fleet.clock.advance(1.0)
        with pytest.raises(NodeUnreachableError):
            fleet.tunnels.endpoint("web-2")
        assert len(fleet.runner.processes) == 2
        assert fleet.tunnels.status("web-2")["failures"] == 2

        # The wait doubles, up to a ceiling.
        fleet.clock.advance(1.5)
        with pytest.raises(NodeUnreachableError) as doubled:
            fleet.tunnels.endpoint("web-2")
        assert "next attempt" in doubled.value.message
        for _ in range(10):
            fleet.clock.advance(BACKOFF_MAX)
            with pytest.raises(NodeUnreachableError):
                fleet.tunnels.endpoint("web-2")
        tunnel = fleet.tunnels._tunnels["web-2"]
        assert tunnel.retry_at - fleet.clock.now <= BACKOFF_MAX

        # Once ssh works again, the counter resets.
        fleet.runner.script(["ssh"], exit_code=0)
        fleet.clock.advance(BACKOFF_MAX)
        fleet.tunnels.endpoint("web-2")
        assert fleet.tunnels.status("web-2")["failures"] == 0

    def test_a_tunnel_that_never_listens_times_out_and_is_stopped(self, fleet):
        _register(fleet)
        fleet.probe_answers[50000] = False

        with pytest.raises(NodeUnreachableError) as caught:
            fleet.tunnels.endpoint("web-2")

        assert "did not come up" in caught.value.message
        assert fleet.runner.processes[0].terminated

    def test_idle_tunnels_are_closed(self, fleet):
        _register(fleet)
        _register(fleet, "web-3")
        fleet.tunnels.endpoint("web-2")
        fleet.clock.advance(300)
        fleet.tunnels.endpoint("web-3")
        fleet.clock.advance(fleet.tunnels.idle_seconds - 100)

        assert fleet.tunnels.reap_idle() == ["web-2"]
        assert fleet.runner.processes[0].terminated
        assert not fleet.runner.processes[1].terminated
        assert not fleet.tunnels.status("web-2")["open"]

    def test_a_leased_tunnel_is_not_reaped(self, fleet):
        _register(fleet)
        with fleet.tunnels.lease("web-2"):
            fleet.clock.advance(fleet.tunnels.idle_seconds * 3)
            assert fleet.tunnels.reap_idle() == []
        fleet.clock.advance(fleet.tunnels.idle_seconds + 1)
        assert fleet.tunnels.reap_idle() == ["web-2"]

    def test_close_and_close_all_stop_the_processes(self, fleet):
        _register(fleet)
        _register(fleet, "web-3")
        fleet.tunnels.endpoint("web-2")
        fleet.tunnels.endpoint("web-3")

        fleet.tunnels.close("web-2")
        assert fleet.runner.processes[0].terminated
        assert not fleet.runner.processes[1].terminated

        fleet.tunnels.close_all()
        assert fleet.runner.processes[1].terminated
        assert fleet.tunnels.status("web-3") == {
            "open": False,
            "local_port": None,
            "since": None,
            "last_error": None,
            "failures": 0,
        }


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("root@web2: Permission denied (publickey).", "refused this central's key"),
        ("channel 2: open failed: administratively prohibited: open failed", "refused to forward"),
        ("ssh: Could not resolve hostname web2: Name or service not known", "Could not resolve"),
        ("ssh: connect to host web2 port 22: Connection timed out", "timed out"),
        ("bind [127.0.0.1]:50000: Address already in use", "local port was taken"),
        ("something new", "failed (exit 255)"),
    ],
)
def test_ssh_failures_are_explained(stderr, expected):
    record = NodeRecord(
        name="web-2",
        ssh_host="web2",
        ssh_port=22,
        ssh_user="root",
        host_key="x",
        console_port=8080,
    )
    assert expected in explain_ssh_failure(stderr, record, 255)
