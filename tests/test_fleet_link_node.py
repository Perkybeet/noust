"""
Tests for the server's side of a link: ``link`` and ``unlink``.

What is pinned: that every check runs before anything changes; the key is generated
for this link alone, readable by root, with the central's host key pinned beside it;
the unit runs one ssh that forwards this server's console to the central's loopback
and nothing else, with no credential in it; the fleet token is issued only once the
rest is in place and revoked again when starting fails; the link code carries what the
central needs and nothing it should not; and a link made again replaces the old one.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from noust.core.exceptions import NodeError
from noust.core.fs import RecordingFileSystem
from noust.core.store import NoustStore
from noust.fleet.link.code import LinkCode
from noust.fleet.link.listener import LINK_USER
from noust.fleet.link.node import (
    KEY_NAME,
    KNOWN_HOSTS_NAME,
    link,
    link_argv,
    link_dir,
    render_unit,
    unit_name,
    unlink,
)
from noust.fleet.tunnels import PINNED_ALGORITHMS
from noust.web.auth import STATE_DIR_ENV, SecurityConfig, TokenManager
from tests.fleet_support import HOST_KEY, TOKEN, KeygenRunner, fingerprint
from tests.link_support import NONCE

CENTRAL_HOST_KEY = HOST_KEY


@pytest.fixture
def tokens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TokenManager:
    directory = tmp_path / "state"
    directory.mkdir()
    monkeypatch.setenv(STATE_DIR_ENV, str(directory))
    return TokenManager(SecurityConfig())


class Server:
    """A server's directories, runner and token manager, for one test."""

    def __init__(self, tmp_path: Path, tokens: TokenManager) -> None:
        self.tokens = tokens
        self.link_root = tmp_path / "link"
        self.unit_dir = tmp_path / "systemd"
        self.unit_dir.mkdir()
        self.runner = KeygenRunner()
        self.runner.script(["systemctl", "is-active"], stdout="active\n")
        self.console_calls = 0
        NoustStore.reset_instance()
        self.store = NoustStore(tmp_path / "noust.db", fs=RecordingFileSystem())

    def console(self) -> int:
        self.console_calls += 1
        return 8080

    def link(self, **overrides):
        values = {
            "central": "nas",
            "host": "central.example.com",
            "ssh_port": 2222,
            "host_key": CENTRAL_HOST_KEY,
            "remote_port": 24001,
            "nonce": NONCE,
            "node_name": "web-5",
            "tokens": self.tokens,
            "ensure_console": self.console,
            "confirm_replace": lambda names: True,
            "runner": self.runner,
            "link_root": self.link_root,
            "unit_dir": self.unit_dir,
            "store": self.store,
            "ssh_program": "/usr/bin/ssh",
        }
        values.update(overrides)
        return link(**values)

    def unlink(self, **overrides):
        values = {
            "central": "nas",
            "tokens": self.tokens,
            "runner": self.runner,
            "link_root": self.link_root,
            "unit_dir": self.unit_dir,
        }
        values.update(overrides)
        return unlink(**values)

    @property
    def unit(self) -> Path:
        return self.unit_dir / unit_name("nas")


@pytest.fixture
def server(tmp_path: Path, tokens: TokenManager):
    built = Server(tmp_path, tokens)
    yield built
    NoustStore.reset_instance()


def _options(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, arg in enumerate(argv) if arg == "-o"]


class TestLink:
    def test_it_dials_the_central_and_prints_a_code_for_it(self, server: Server):
        result = server.link()

        assert (result.central, result.node_name, result.unit) == (
            "nas",
            "web-5",
            "noust-link-nas.service",
        )
        assert (result.host, result.ssh_port, result.remote_port) == (
            "central.example.com",
            2222,
            24001,
        )
        assert result.console_port == 8080
        assert result.active
        assert result.host_key_fingerprint == fingerprint(HOST_KEY)
        assert server.console_calls == 1
        code = LinkCode.decode(result.link_code)
        assert (code.node_name, code.central, code.console_port, code.nonce) == (
            "web-5",
            "nas",
            8080,
            NONCE,
        )
        assert code.token_name == "fleet-nas"
        public = (link_dir("nas", server.link_root) / f"{KEY_NAME}.pub").read_text()
        assert public.split()[:2] == code.link_key.split()[:2]

    def test_the_token_is_a_fleet_token_of_this_server(self, server: Server):
        result = server.link()

        record = next(r for r in server.tokens.list_api_tokens() if r["name"] == "fleet-nas")
        assert record["scope"] == "fleet"
        assert record["revoked_at"] is None
        assert LinkCode.decode(result.link_code).token.startswith("noust_tok_")

    def test_the_key_is_roots_alone_and_the_central_s_host_key_is_pinned(self, server: Server):
        server.link()

        directory = link_dir("nas", server.link_root)
        private = directory / KEY_NAME
        assert stat.S_IMODE(private.stat().st_mode) == 0o600
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        known = (directory / KNOWN_HOSTS_NAME).read_text()
        assert known == f"[central.example.com]:2222 {HOST_KEY}\n"
        assert stat.S_IMODE((directory / KNOWN_HOSTS_NAME).stat().st_mode) == 0o600

    def test_port_22_is_pinned_by_the_host_alone(self, server: Server):
        server.link(ssh_port=22)
        known = (link_dir("nas", server.link_root) / KNOWN_HOSTS_NAME).read_text()
        assert known == f"central.example.com {HOST_KEY}\n"

    def test_the_unit_runs_one_ssh_that_forwards_the_console_and_nothing_else(self, server: Server):
        server.link()

        text = server.unit.read_text()
        directory = link_dir("nas", server.link_root)
        line = next(line for line in text.splitlines() if line.startswith("ExecStart="))
        assert line.startswith("ExecStart=/usr/bin/ssh -N -T -F /dev/null ")
        assert f"-i {directory / KEY_NAME}" in line
        assert "-p 2222" in line
        assert f"-l {LINK_USER}" in line
        assert "-R 127.0.0.1:24001:127.0.0.1:8080" in line
        assert "-L " not in line
        assert line.endswith(" -- central.example.com")
        assert "Restart=always" in text
        assert "StartLimitIntervalSec=0" in text
        assert "Generated by Noust" in text
        # What identifies the central is pinned, and strictly.
        assert "StrictHostKeyChecking=yes" in line
        assert f"UserKnownHostsFile={directory / KNOWN_HOSTS_NAME}" in line
        assert "ExitOnForwardFailure=yes" in line

    def test_no_credential_is_written_into_the_unit(self, server: Server):
        result = server.link()
        token = LinkCode.decode(result.link_code).token
        assert token not in server.unit.read_text()
        assert TOKEN not in server.unit.read_text()

    def test_the_unit_is_started_in_order(self, server: Server):
        server.link()

        systemctl = [call for call in server.runner.calls if call[0] == "systemctl"]
        assert systemctl[:3] == [
            ("systemctl", "daemon-reload"),
            ("systemctl", "enable", "noust-link-nas.service"),
            ("systemctl", "restart", "noust-link-nas.service"),
        ]

    def test_ipv6_central_is_dialled_without_brackets_and_pinned_with_them(self, server: Server):
        server.link(host="[2001:db8::7]")

        argv_line = next(
            line for line in server.unit.read_text().splitlines() if line.startswith("ExecStart=")
        )
        assert argv_line.endswith(" -- 2001:db8::7")
        known = (link_dir("nas", server.link_root) / KNOWN_HOSTS_NAME).read_text()
        assert known == f"[2001:db8::7]:2222 {HOST_KEY}\n"

    def test_a_server_without_ssh_cannot_dial(self, server: Server):
        server.runner.only_knows("systemctl", "ssh-keygen")

        with pytest.raises(NodeError) as caught:
            server.link()

        assert "ssh client is not installed" in caught.value.message
        assert not server.unit.exists()
        assert server.tokens.list_api_tokens() == []

    @pytest.mark.parametrize(
        "overrides",
        [
            {"remote_port": 443},
            {"nonce": "nope"},
            {"host_key": "ssh-rsa AAAA nope"},
            {"host": "a b"},
            {"host": "x; reboot"},
            {"central": "Not A Name"},
            {"ssh_port": 70000},
        ],
    )
    def test_what_is_wrong_is_refused_before_anything_changes(self, server: Server, overrides):
        with pytest.raises(NodeError):
            server.link(**overrides)

        assert not server.unit.exists()
        assert not server.link_root.exists()
        assert server.tokens.list_api_tokens() == []
        assert server.console_calls == 0

    def test_a_link_made_again_revokes_the_old_token_after_asking(self, server: Server):
        server.link()
        asked: list[list[str]] = []

        def confirm(names: list[str]) -> bool:
            asked.append(names)
            return True

        second = server.link(confirm_replace=confirm)

        assert asked == [["fleet-nas"]]
        assert second.replaced_tokens == ["fleet-nas"]
        live = [r["name"] for r in server.tokens.list_api_tokens() if r["revoked_at"] is None]
        assert live == ["fleet-nas.2"]

    def test_declining_to_replace_the_token_changes_nothing(self, server: Server):
        server.link()
        before = server.unit.read_text()

        with pytest.raises(NodeError) as caught:
            server.link(confirm_replace=lambda names: False, nonce="f" * 32)

        assert "Cancelled" in caught.value.message
        assert server.unit.read_text() == before
        assert [r["name"] for r in server.tokens.list_api_tokens() if r["revoked_at"] is None] == [
            "fleet-nas"
        ]

    def test_a_unit_that_cannot_start_leaves_nothing_behind(self, server: Server):
        server.runner.script(["systemctl", "restart"], exit_code=1, stderr="Unit not found")

        with pytest.raises(NodeError) as caught:
            server.link()

        assert "could not be started" in caught.value.message
        assert not server.unit.exists()
        assert not link_dir("nas", server.link_root).exists()
        assert [r for r in server.tokens.list_api_tokens() if r["revoked_at"] is None] == []
        assert ("systemctl", "disable", "--now", "noust-link-nas.service") in server.runner.calls

    def test_a_rehearsal_writes_nothing_and_issues_nothing(self, server: Server):
        result = server.link(dry_run=True)

        assert result.link_code == ""
        assert result.token_name == "fleet-nas"
        assert not server.unit.exists()
        assert not server.link_root.exists()
        assert server.tokens.list_api_tokens() == []
        assert server.runner.calls_to("systemctl") == []


class TestUnitAndArguments:
    def test_the_ssh_options_are_the_ones_every_noust_connection_has(self):
        argv = link_argv(
            host="central.example.com",
            ssh_port=22,
            remote_port=24001,
            console_port=8080,
            key=Path("/var/lib/noust/link/nas/id_ed25519"),
            known_hosts=Path("/var/lib/noust/link/nas/known_hosts"),
            algorithms={name: ",".join(values) for name, values in PINNED_ALGORITHMS.items()},
        )

        options = _options(argv)
        assert "BatchMode=yes" in options
        assert "StrictHostKeyChecking=yes" in options
        assert "PasswordAuthentication=no" in options
        assert "ForwardAgent=no" in options
        assert "HostKeyAlgorithms=ssh-ed25519" in options
        assert argv[:5] == ["ssh", "-N", "-T", "-F", "/dev/null"]
        assert "-R" in argv and "-L" not in argv

    def test_the_unit_name_is_the_central_s(self):
        assert unit_name("nas") == "noust-link-nas.service"

    def test_a_name_that_could_add_directives_is_refused(self):
        with pytest.raises(NodeError):
            render_unit("nas\nExecStart=/bin/sh", ["ssh"])


class TestUnlink:
    def test_it_stops_the_link_and_removes_what_it_made(self, server: Server):
        server.link()

        result = server.unlink()

        assert result.stopped and result.removed_files
        assert result.revoked_tokens == ["fleet-nas"]
        assert not server.unit.exists()
        assert not link_dir("nas", server.link_root).exists()
        assert [r for r in server.tokens.list_api_tokens() if r["revoked_at"] is None] == []
        assert ("systemctl", "disable", "--now", "noust-link-nas.service") in server.runner.calls

    def test_a_server_that_never_linked_has_nothing_to_remove(self, server: Server):
        result = server.unlink()

        assert not result.stopped and not result.removed_files
        assert result.revoked_tokens == []

    def test_a_rehearsal_reports_without_touching(self, server: Server):
        server.link()

        result = server.unlink(dry_run=True)

        assert result.stopped and result.revoked_tokens == ["fleet-nas"]
        assert server.unit.exists()
        assert link_dir("nas", server.link_root).exists()
        assert [r["name"] for r in server.tokens.list_api_tokens() if r["revoked_at"] is None] == [
            "fleet-nas"
        ]
