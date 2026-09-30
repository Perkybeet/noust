"""
Tests for the node's side of enrollment: ``authorize`` and ``deauthorize``.

What is pinned: the exact restricted key line, that it is never duplicated
and never written through a symlink, the key file's mode and owner; the
tunnel account (created with ``*`` as its password, reused, refused when it
can log in) and its ``Match User`` block in sshd - validated with ``sshd -t``,
checked with ``sshd -T -C`` and put back when sshd refuses it or applies
something else; that sshd settings which would break the tunnel are refused
up front; that root needs ``allow_root``; that a central moving to the tunnel
account leaves root's file; the access ceiling; that authorizing on the
central that issued the key is refused; that a central authorized again gets
a new token and the old one is revoked; and that the join code carries what
the central needs.
"""

from __future__ import annotations

import pwd
import stat
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import NodeError, SecurityError
from noust.core.fs import RecordingFileSystem
from noust.core.runner import CommandResult, DryRunRunner, FakeRunner
from noust.core.store import NodeRecord, NoustStore
from noust.fleet import authorize as authorize_module
from noust.fleet.authorize import (
    FORCED_COMMAND,
    NO_LISTEN,
    POLICY_BEGIN,
    POLICY_END,
    SSHD_DROPIN_NAME,
    TUNNEL_USER,
    AuthorizedKeys,
    SshdSettings,
    authorize,
    authorized_key_line,
    authorized_keys_path,
    deauthorize,
    issued_here,
    read_sshd_settings,
    sshd_blockers,
    tunnel_policy_block,
)
from noust.fleet.joincode import JoinCode
from noust.fleet.models import parse_public_key
from noust.fleet.policy import FleetAccess, current_access
from noust.web.auth import STATE_DIR_ENV, SecurityConfig, TokenManager
from tests.fleet_support import HOST_KEY, ed25519_line, fingerprint

CENTRAL_KEY = ed25519_line(7, "noust-central@nas")
OTHER_CENTRAL_KEY = ed25519_line(8, "noust-central@nas")

SSHD_T = """port 22
allowtcpforwarding yes
disableforwarding no
permitrootlogin without-password
authorizedkeysfile .ssh/authorized_keys .ssh/authorized_keys2
"""

EVALUATED = "user=noust-tunnel,host=localhost,addr=127.0.0.1"


@pytest.fixture
def tokens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TokenManager:
    directory = tmp_path / "state"
    directory.mkdir()
    monkeypatch.setenv(STATE_DIR_ENV, str(directory))
    return TokenManager(SecurityConfig())


class SshdRunner(FakeRunner):
    """
    sshd, systemctl and useradd as a node has them.

    ``sshd -T`` prints :attr:`config`; evaluated for the tunnel account, the
    settings of Noust's ``Match`` block come first, as sshd applies them -
    unless :attr:`ignore_policy` says another block wins. ``useradd`` adds
    the account to the node. Anything a test scripts wins over both.
    """

    def __init__(self, node: Node) -> None:
        super().__init__()
        self.node = node
        self.config = SSHD_T
        self.ignore_policy = False
        self.script(["systemctl", "is-active", "ssh.service"], stdout="active\n")

    def _scripted_for(self, argv: list[str]) -> bool:
        return any(tuple(argv[: len(s.match)]) == s.match for s in self._scripted)

    def run(self, argv, **kwargs: Any) -> CommandResult:  # type: ignore[override]
        check = kwargs.pop("check", False)
        result = super().run(argv, **kwargs)
        args = [str(a) for a in argv]
        if args[:2] == ["sshd", "-T"] and not self._scripted_for(args):
            output = self.config
            if "-C" in args and f"user={TUNNEL_USER}," in args[args.index("-C") + 1]:
                if not self.ignore_policy:
                    output = self.node.policy_lines() + output
            result = replace(result, stdout=output)
        if args and args[0] == "useradd" and result.success:
            self.node.add_account(args[-1], login=args[args.index("--shell") + 1])
        return result.check() if check else result


class Node:
    """A node's accounts, sshd and host key, inside the test's directory."""

    def __init__(self, tmp_path: Path, tokens: TokenManager) -> None:
        self.tmp_path = tmp_path
        self.tokens = tokens
        self.runner = SshdRunner(self)
        self.host_key_file = tmp_path / "ssh_host_ed25519_key.pub"
        self.host_key_file.write_text(HOST_KEY + " root@node\n")
        self.dropin_dir = tmp_path / "sshd_config.d"
        self.dropin_dir.mkdir()
        self.sshd_config = tmp_path / "sshd_config"
        self.sshd_config.write_text(f"Include {self.dropin_dir}/*.conf\nPort 22\n")
        self.tunnel_keys_dir = tmp_path / "etc-ssh-noust"
        self.homes: dict[str, tuple[int, Path, str]] = {
            "root": (0, tmp_path / "root", "/bin/bash"),
            "deploy": (1001, tmp_path / "home" / "deploy", "/bin/bash"),
        }
        for _, home, _ in self.homes.values():
            home.mkdir(parents=True)
        self.console_calls = 0
        NoustStore.reset_instance()
        self.store = NoustStore(tmp_path / "noust.db", fs=RecordingFileSystem())

    def add_account(self, user: str, *, login: str = "/usr/sbin/nologin") -> None:
        self.homes[user] = (999, Path("/nonexistent"), login)

    def passwd(self, user: str) -> pwd.struct_passwd:
        if user not in self.homes:
            raise KeyError(user)
        uid, home, shell = self.homes[user]
        return pwd.struct_passwd((user, "x", uid, uid, "", str(home), shell))

    def keys_file(self, user: str = TUNNEL_USER) -> Path:
        if user == TUNNEL_USER:
            return self.tunnel_keys_dir / f"{TUNNEL_USER}.keys"
        return self.homes[user][1] / ".ssh" / "authorized_keys"

    @property
    def dropin(self) -> Path:
        return self.dropin_dir / SSHD_DROPIN_NAME

    def policy_lines(self) -> str:
        """What sshd -T -C prints for the tunnel account from Noust's block, if it is there."""
        if self.dropin.exists():
            text = self.dropin.read_text()
        elif self.sshd_config.exists():
            text = self.sshd_config.read_text()
        else:
            return ""
        lines: list[str] = []
        inside = False
        for raw in text.splitlines():
            if raw.startswith(f"Match User {TUNNEL_USER}"):
                inside = True
                continue
            if inside and raw.startswith("    "):
                keyword, _, value = raw.strip().partition(" ")
                lines.append(f"{keyword.lower()} {value}")
            elif inside:
                break
        return "\n".join(lines) + "\n" if lines else ""

    def console(self) -> int:
        self.console_calls += 1
        return 8080

    def arguments(self, **overrides: Any) -> dict[str, Any]:
        values: dict[str, Any] = {
            "central_key": CENTRAL_KEY,
            "central": "nas",
            "tokens": self.tokens,
            "ensure_console": self.console,
            "confirm_replace": lambda names: True,
            "runner": self.runner,
            "host_key_file": self.host_key_file,
            "sshd_config": self.sshd_config,
            "sshd_dropin_dir": self.dropin_dir,
            "tunnel_keys_dir": self.tunnel_keys_dir,
            "passwd": self.passwd,
            "store": self.store,
            "self_check": None,
        }
        values.update(overrides)
        return values

    def authorize(self, key: str = CENTRAL_KEY, *, user: str = TUNNEL_USER, confirm=None, **kw):
        return authorize(
            **self.arguments(
                central_key=key,
                ssh_user=user,
                allow_root=user == "root",
                confirm_replace=confirm or (lambda names: True),
                **kw,
            )
        )

    def deauthorize(self, *, user: str | None = None, dry_run: bool = False):
        return deauthorize(
            central="nas",
            tokens=self.tokens,
            ssh_user=user,
            runner=self.runner,
            sshd_config=self.sshd_config,
            tunnel_keys_dir=self.tunnel_keys_dir,
            passwd=self.passwd,
            dry_run=dry_run,
        )


@pytest.fixture
def node(tmp_path: Path, tokens: TokenManager):
    built = Node(tmp_path, tokens)
    yield built
    NoustStore.reset_instance()


def _expected_line(key: str = CENTRAL_KEY, port: int = 8080) -> str:
    bare = " ".join(key.split()[:2])
    return (
        f'restrict,port-forwarding,permitopen="127.0.0.1:{port}",'
        f'permitlisten="127.0.0.1:1",command="/usr/bin/false" {bare} noust-central:nas'
    )


class TestAuthorizedKeys:
    def test_the_tunnel_account_s_file_is_root_s_and_readable_by_sshd(self, node):
        result = node.authorize()

        path = node.keys_file()
        assert path.read_text() == _expected_line() + "\n"
        # sshd reads it as the account: root's, 0644, in a 0755 directory.
        assert stat.S_IMODE(path.stat().st_mode) == 0o644
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o755
        assert result.authorized_keys == str(path)
        assert result.key_changed
        assert FORCED_COMMAND == "/usr/bin/false"
        assert node.runner.calls_to("chown") == []

    def test_a_home_file_is_0600_in_a_0700_directory(self, node):
        node.authorize(user="root")

        path = node.keys_file("root")
        assert path.read_text() == _expected_line() + "\n"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    def test_other_lines_are_kept_and_ours_is_never_duplicated(self, node):
        path = node.keys_file()
        path.parent.mkdir(mode=0o755)
        path.write_text("# mine\nssh-ed25519 AAAAexisting me@laptop\n")

        node.authorize()
        second = node.authorize()

        assert path.read_text() == (
            "# mine\nssh-ed25519 AAAAexisting me@laptop\n" + _expected_line() + "\n"
        )
        assert not second.key_changed

    def test_a_new_key_for_the_same_central_replaces_the_old_line(self, node):
        node.authorize()
        node.authorize(OTHER_CENTRAL_KEY)

        assert node.keys_file().read_text() == _expected_line(OTHER_CENTRAL_KEY) + "\n"

    def test_a_non_root_account_owns_its_file(self, node):
        node.authorize(user="deploy")

        chowns = node.runner.calls_to("chown")
        home = node.homes["deploy"][1]
        assert chowns == [
            ("chown", "-h", "1001:1001", str(home / ".ssh")),
            ("chown", "-h", "1001:1001", str(home / ".ssh" / "authorized_keys")),
        ]

    def test_a_symlinked_file_is_refused(self, node):
        path = node.keys_file("deploy")
        path.parent.mkdir(mode=0o700)
        target = node.tmp_path / "shadow"
        target.write_text("secret\n")
        path.symlink_to(target)

        with pytest.raises(NodeError) as caught:
            node.authorize(user="deploy")

        assert "symlink" in caught.value.message
        assert target.read_text() == "secret\n"
        assert node.tokens.list_api_tokens() == []

    def test_a_symlinked_directory_is_refused(self, node):
        elsewhere = node.tmp_path / "elsewhere"
        elsewhere.mkdir()
        node.tunnel_keys_dir.symlink_to(elsewhere)

        with pytest.raises(NodeError):
            node.authorize()
        assert list(elsewhere.iterdir()) == []

    def test_the_line_s_shape(self):
        key = parse_public_key(CENTRAL_KEY)
        assert authorized_key_line("nas", key, 9000) == _expected_line(port=9000)

    def test_remote_forwarding_is_pinned_to_a_port_nothing_uses(self):
        # port-forwarding turns -R back on too; permitlisten is what keeps the
        # central's key from listening on the node over TCP. OpenSSH has no
        # "none" for it (the key line would be rejected whole), and port 0 is
        # refused, so the tightest value is loopback port 1: sshd refuses a
        # privileged listen to any account but root, and nothing on the node
        # dials it. Unix sockets are the tunnel account's Match block's job.
        key = parse_public_key(CENTRAL_KEY)
        options = authorized_key_line("nas", key, 9000).split(" ", 1)[0]
        assert options.count("permitlisten=") == 1
        assert f'permitlisten="{NO_LISTEN}"' in options
        host, _, port = NO_LISTEN.partition(":")
        assert host == "127.0.0.1"
        assert 0 < int(port) < 1024
        assert options.count("permitopen=") == 1
        assert options.startswith("restrict,")

    def test_a_line_from_before_permitlisten_is_upgraded(self, node):
        path = node.keys_file()
        path.parent.mkdir(mode=0o755)
        bare = " ".join(CENTRAL_KEY.split()[:2])
        old = (
            'restrict,port-forwarding,permitopen="127.0.0.1:8080",'
            f'command="/usr/bin/false" {bare} noust-central:nas'
        )
        path.write_text("ssh-ed25519 AAAAexisting me@laptop\n" + old + "\n")

        result = node.authorize()

        assert result.key_changed
        assert path.read_text() == (
            "ssh-ed25519 AAAAexisting me@laptop\n" + _expected_line() + "\n"
        )

    def test_authorized_keys_file_expansion(self):
        account = pwd.struct_passwd(("deploy", "x", 1001, 1001, "", "/home/deploy", "/bin/sh"))
        settings = SshdSettings(
            authorized_keys_files=(
                "none",
                "/etc/ssh/keys/%u_%U%%",
            )
        )
        assert authorized_keys_path(settings, account) == Path("/etc/ssh/keys/deploy_1001%")
        relative = SshdSettings(authorized_keys_files=(".ssh/authorized_keys",))
        assert authorized_keys_path(relative, account) == Path("/home/deploy/.ssh/authorized_keys")
        with pytest.raises(NodeError):
            authorized_keys_path(SshdSettings(authorized_keys_files=("none",)), account)


class TestTunnelAccount:
    def test_a_missing_account_is_created_without_a_home_a_shell_or_a_password(
        self, node, monkeypatch
    ):
        monkeypatch.setattr(authorize_module, "_nologin_shell", lambda: "/usr/sbin/nologin")

        result = node.authorize()

        assert node.runner.calls_to("useradd") == [
            (
                "useradd",
                "--system",
                "--user-group",
                "--no-create-home",
                "--home-dir",
                "/nonexistent",
                "--shell",
                "/usr/sbin/nologin",
                "--comment",
                "Noust fleet tunnel",
                TUNNEL_USER,
            )
        ]
        # '*', not the '!' useradd leaves: with UsePAM no, sshd reads '!' as a
        # locked account and refuses even a valid key.
        assert node.runner.calls_to("usermod") == [("usermod", "--password", "*", TUNNEL_USER)]
        assert result.tunnel_account_created
        assert result.ssh_user == TUNNEL_USER
        assert JoinCode.decode(result.join_code).ssh_user == TUNNEL_USER

    def test_an_existing_account_is_reused(self, node):
        node.add_account(TUNNEL_USER)

        result = node.authorize()

        assert node.runner.calls_to("useradd") == []
        assert node.runner.calls_to("usermod") == [("usermod", "--password", "*", TUNNEL_USER)]
        assert not result.tunnel_account_created

    def test_an_account_that_can_log_in_is_refused_before_anything_changes(self, node):
        node.add_account(TUNNEL_USER, login="/bin/bash")

        with pytest.raises(NodeError) as caught:
            node.authorize()

        assert "can log in" in caught.value.message
        assert node.console_calls == 0
        assert node.runner.calls_to("usermod") == []
        assert not node.dropin.exists()
        assert node.tokens.list_api_tokens() == []


class TestSshdPolicy:
    def test_the_match_block_goes_in_its_own_fragment(self, node):
        result = node.authorize()

        assert result.sshd_policy == str(node.dropin)
        text = node.dropin.read_text()
        assert text.endswith(tunnel_policy_block(TUNNEL_USER, 8080, node.tunnel_keys_dir))
        # Root's alone, as hardening benchmarks want sshd's configuration.
        assert stat.S_IMODE(node.dropin.stat().st_mode) == 0o600
        block = tunnel_policy_block(TUNNEL_USER, 8080, node.tunnel_keys_dir)
        for directive in (
            f"Match User {TUNNEL_USER}",
            f"AuthorizedKeysFile {node.tunnel_keys_dir}/%u.keys",
            "AllowTcpForwarding local",
            "AllowStreamLocalForwarding no",
            "PermitOpen 127.0.0.1:8080",
            "PermitListen none",
            "PermitTTY no",
            "X11Forwarding no",
            "AllowAgentForwarding no",
            "ForceCommand /usr/bin/false",
        ):
            assert directive in block
        # The main file is not touched.
        assert POLICY_BEGIN not in node.sshd_config.read_text()

    def test_validated_reloaded_and_verified_in_that_order(self, node):
        node.authorize()

        relevant = [
            call
            for call in node.runner.calls
            if call[0] == "sshd" or call[:2] == ("systemctl", "reload")
        ]
        assert relevant == [
            ("sshd", "-T", "-C", EVALUATED),
            ("sshd", "-t"),
            ("systemctl", "reload", "ssh.service"),
            ("sshd", "-T", "-C", EVALUATED),
            # Where root's authorized_keys is, to take an older line of this central out.
            ("sshd", "-T", "-C", "user=root,host=localhost,addr=127.0.0.1"),
        ]

    def test_without_an_include_the_block_is_appended_last_and_marked(self, node):
        node.sshd_config.write_text("Port 22\nPasswordAuthentication no\n")
        node.sshd_config.chmod(0o640)

        first = node.authorize()
        node.authorize(OTHER_CENTRAL_KEY)

        text = node.sshd_config.read_text()
        assert first.sshd_policy == str(node.sshd_config)
        assert text.startswith("Port 22\nPasswordAuthentication no\n")
        assert text.count(POLICY_BEGIN) == 1
        assert text.rstrip().endswith(POLICY_END)
        assert not node.dropin.exists()
        # sshd_config keeps the mode it had.
        assert stat.S_IMODE(node.sshd_config.stat().st_mode) == 0o640

    def test_a_vendor_configuration_under_usr_is_never_replaced(self, node):
        # openSUSE keeps sshd_config in /usr/etc/ssh; /etc/ssh/sshd_config.d is
        # included from there. Creating /etc/ssh/sshd_config would override it.
        node.sshd_config.unlink()

        result = node.authorize()

        assert result.sshd_policy == str(node.dropin)
        assert not node.sshd_config.exists()

    def test_no_configuration_anywhere_is_an_error(self, node):
        node.sshd_config.unlink()
        node.dropin_dir.rmdir()

        with pytest.raises(NodeError) as caught:
            node.authorize()

        assert "openssh-server" in caught.value.details
        assert not node.sshd_config.exists()

    def test_sshd_refusing_it_puts_everything_back(self, node):
        node.runner.script(["sshd", "-t"], exit_code=255, stderr="line 3: Bad configuration")

        with pytest.raises(NodeError) as caught:
            node.authorize()

        assert "sshd refused" in caught.value.message
        assert caught.value.output == "line 3: Bad configuration"
        assert not node.dropin.exists()
        assert node.runner.calls_to("systemctl") == []
        assert not node.keys_file().exists()
        assert node.tokens.list_api_tokens() == []

    def test_a_block_sshd_does_not_apply_is_put_back_and_explained(self, node):
        node.runner.ignore_policy = True

        with pytest.raises(NodeError) as caught:
            node.authorize()

        assert "does not apply" in caught.value.message
        assert "AllowStreamLocalForwarding" in caught.value.details
        assert not node.dropin.exists()
        # Reloaded with the block, then again without it.
        reloads = [c for c in node.runner.calls if c[:2] == ("systemctl", "reload")]
        assert len(reloads) == 2
        assert node.tokens.list_api_tokens() == []

    def test_nothing_is_reloaded_when_sshd_is_not_running(self, node):
        node.runner.script(["systemctl", "is-active"], stdout="inactive\n")

        node.authorize()

        assert [c for c in node.runner.calls if c[:2] == ("systemctl", "reload")] == []


class TestSshd:
    def test_reads_sshd_t(self, node):
        node.runner.script(["sshd", "-T"], stdout="port 2222\nport 22\nallowtcpforwarding local\n")
        settings = read_sshd_settings(node.runner, node.sshd_config)
        assert (settings.port, settings.allow_tcp_forwarding, settings.source) == (
            2222,
            "local",
            "sshd -T",
        )

    def test_evaluates_an_account_s_match_blocks(self, node):
        node.runner.script(
            ["sshd", "-T", "-C"],
            stdout="allowusers alice\nallowusers bob@10.0.0.1\npermitopen 127.0.0.1:8080\n"
            "forcecommand /usr/bin/false\n",
        )
        settings = read_sshd_settings(node.runner, node.sshd_config, user=TUNNEL_USER)
        assert node.runner.calls[-1] == ("sshd", "-T", "-C", EVALUATED)
        assert settings.allow_users == ("alice", "bob@10.0.0.1")
        assert settings.permit_open == ("127.0.0.1:8080",)
        assert settings.force_command == "/usr/bin/false"

    def test_falls_back_to_the_config_file(self, node):
        node.runner.script(["sshd", "-T"], exit_code=127, stderr="Command not found")
        node.sshd_config.write_text(
            "# comment\nPort 2200\nPermitRootLogin no\nMatch User git\n  Port 9\n"
        )
        settings = read_sshd_settings(node.runner, node.sshd_config)
        assert (settings.port, settings.permit_root_login) == (2200, "no")

    def test_defaults_when_nothing_is_readable(self, node):
        node.runner.script(["sshd", "-T"], exit_code=1)
        assert read_sshd_settings(node.runner, node.tmp_path / "missing") == SshdSettings()

    @pytest.mark.parametrize(
        ("config", "user"),
        [
            ("allowtcpforwarding no\n", "deploy"),
            ("allowtcpforwarding remote\n", "deploy"),
            ("disableforwarding yes\n", "deploy"),
            ("permitrootlogin no\n", "root"),
            ("allowusers alice\n", TUNNEL_USER),
            ("denyusers noust-*\n", TUNNEL_USER),
        ],
    )
    def test_settings_that_break_the_tunnel_are_refused_before_any_change(self, node, config, user):
        node.runner.config = config

        with pytest.raises(NodeError) as caught:
            node.authorize(user=user)

        assert "sshd would refuse" in caught.value.message
        assert not node.keys_file(user).exists()
        assert node.console_calls == 0
        assert node.tokens.list_api_tokens() == []
        assert node.runner.calls_to("useradd") == []

    def test_a_global_ban_on_forwarding_does_not_stop_the_tunnel_account(self, node):
        # Its own Match block turns forwarding back on for it alone, which is
        # how a node with DisableForwarding yes can still be enrolled.
        node.runner.config = "disableforwarding yes\nallowtcpforwarding no\npermitrootlogin no\n"

        result = node.authorize()

        assert result.ssh_user == TUNNEL_USER

    def test_allow_groups_is_held_against_the_account_s_groups(self):
        settings = SshdSettings(allow_groups=("sshusers",))
        assert sshd_blockers(settings, TUNNEL_USER, (TUNNEL_USER,), tunnel=True)
        assert not sshd_blockers(settings, TUNNEL_USER, ("sshusers",), tunnel=True)
        denied = SshdSettings(deny_groups=("noust-*",))
        assert sshd_blockers(denied, TUNNEL_USER, (TUNNEL_USER,), tunnel=True)

    def test_the_join_code_carries_the_sshd_port(self, node):
        node.runner.config = "port 2222\n"
        result = node.authorize()
        assert JoinCode.decode(result.join_code).ssh_port == 2222


class TestRoot:
    def test_root_needs_an_explicit_yes(self, node):
        with pytest.raises(NodeError) as caught:
            authorize(**node.arguments(ssh_user="root"))

        assert "--i-understand" in caught.value.message
        assert "Unix socket" in caught.value.details
        assert node.console_calls == 0

    def test_root_with_it_works_as_before(self, node):
        result = node.authorize(user="root")

        assert result.ssh_user == "root"
        assert result.sshd_policy is None
        assert node.runner.calls_to("useradd") == []

    def test_moving_to_the_tunnel_account_takes_the_line_out_of_root_s_file(self, node):
        root_file = node.keys_file("root")
        node.authorize(user="root")
        root_file.write_text("ssh-ed25519 AAAAmine me@laptop\n" + root_file.read_text())

        result = node.authorize()

        assert root_file.read_text() == "ssh-ed25519 AAAAmine me@laptop\n"
        assert result.moved_from == [str(root_file)]
        assert node.keys_file().read_text() == _expected_line() + "\n"

    def test_and_back(self, node):
        node.authorize()
        result = node.authorize(user="root")
        assert node.keys_file().read_text() == ""
        assert result.moved_from == [str(node.keys_file())]


class TestAccess:
    def test_the_default_keeps_what_is_in_force(self, node):
        node.store.set_fleet_access("read", False)

        result = node.authorize()

        assert result.access == {"level": "read", "host_access": False}
        assert current_access(node.store) == FleetAccess("read")

    def test_a_ceiling_given_is_stored_with_who_set_it(self, node):
        result = node.authorize(access=FleetAccess("deploy", True), actor="cli:root")

        assert result.access == {"level": "deploy", "host_access": True}
        assert current_access(node.store) == FleetAccess("deploy", True)
        assert node.store.get_fleet_access()["updated_by"] == "cli:root"

    def test_a_never_configured_server_is_admin_without_host_access(self, node):
        assert node.authorize().access == {"level": "admin", "host_access": False}


class TestIssuedHere:
    @pytest.fixture
    def central(self, tmp_path, monkeypatch):
        from noust.core.secrets import SecretStore
        from noust.fleet.keys import NodeKeys
        from tests.fleet_support import KeygenRunner

        NoustStore.reset_instance()
        store = NoustStore(tmp_path / "central" / "noust.db", fs=RecordingFileSystem())
        monkeypatch.setattr("noust.core.store.get_store", lambda *a, **k: store)
        monkeypatch.setattr(authorize_module, "central_name", lambda: "nas")
        keys = NodeKeys(SecretStore(root=tmp_path / "central" / "secrets"), KeygenRunner())
        yield store, keys
        NoustStore.reset_instance()

    def test_holding_the_very_key_is_proof(self, central):
        _, keys = central
        held = keys.ensure_keypair("web-2", "nas")

        assert issued_here(held, "other-name") == "it holds this very key, for its node web-2"

    def test_a_central_by_that_name_with_nodes(self, central):
        store, _ = central
        store.save_node(
            NodeRecord(
                name="web-3",
                ssh_host="w3",
                ssh_port=22,
                ssh_user=TUNNEL_USER,
                host_key="noust-node-web-3 " + HOST_KEY,
                console_port=8080,
            )
        )
        stranger = parse_public_key(ed25519_line(55, "noust-central@nas"))

        assert issued_here(stranger, "nas") == "it is the central 'nas' and manages nodes"
        assert issued_here(stranger, "elsewhere") == "it is the central 'nas' and manages nodes"
        assert issued_here(parse_public_key(ed25519_line(55, "x@y")), "other") is None

    def test_a_plain_server_is_not_a_central(self, central):
        assert issued_here(parse_public_key(CENTRAL_KEY), "nas") is None

    def test_authorize_refuses_with_where_it_ran(self, node):
        with pytest.raises(NodeError) as caught:
            authorize(**node.arguments(self_check=lambda key, name: "it holds this very key"))

        assert "central that issued this key" in caught.value.message
        assert "--allow-self" in caught.value.details
        assert node.console_calls == 0


class TestProgress:
    def test_each_step_is_announced_before_it_runs(self, node):
        steps: list[tuple[int, int, str]] = []

        node.authorize(progress=lambda step, total, what: steps.append((step, total, what)))

        assert [(step, total) for step, total, _ in steps] == [(n, 6) for n in range(1, 7)]
        assert "Checking sshd" in steps[0][2]
        assert "console" in steps[1][2]
        assert "fleet-nas" in steps[5][2]

    def test_another_account_has_fewer_steps(self, node):
        steps: list[int] = []
        node.authorize(user="deploy", progress=lambda step, total, what: steps.append(total))
        assert set(steps) == {4}


class TestTokenAndJoinCode:
    def test_the_join_code_carries_a_working_fleet_token(self, node):
        result = node.authorize()

        code = JoinCode.decode(result.join_code)
        assert code.ssh_host_key == HOST_KEY
        assert (code.ssh_user, code.console_port) == (TUNNEL_USER, 8080)
        assert code.central_key_fp == fingerprint(CENTRAL_KEY)
        assert (code.central, code.token_name) == ("nas", "fleet-nas")
        payload = node.tokens.verify_api_token(code.token, "127.0.0.1")
        assert payload is not None and payload["scope"] == "fleet"
        assert result.token_name == "fleet-nas"
        assert node.console_calls == 1

    def test_authorizing_again_replaces_the_token_after_confirmation(self, node):
        first = JoinCode.decode(node.authorize().join_code)
        asked: list[list[str]] = []

        second = node.authorize(confirm=lambda names: asked.append(names) or True)

        assert asked == [["fleet-nas"]]
        assert second.token_name == "fleet-nas.2"
        assert second.replaced_tokens == ["fleet-nas"]
        assert node.tokens.verify_api_token(first.token, "127.0.0.1") is None
        new = JoinCode.decode(second.join_code)
        assert node.tokens.verify_api_token(new.token, "127.0.0.1") is not None

    def test_declining_the_replacement_changes_nothing(self, node):
        node.authorize()
        before = node.keys_file().read_text()

        with pytest.raises(NodeError) as caught:
            node.authorize(OTHER_CENTRAL_KEY, confirm=lambda names: False)

        assert "nothing was changed" in caught.value.message
        assert node.keys_file().read_text() == before
        assert [t["name"] for t in node.tokens.list_api_tokens()] == ["fleet-nas"]
        assert node.console_calls == 1

    def test_a_token_that_cannot_be_issued_puts_the_key_file_back(self, node, monkeypatch):
        path = node.keys_file()
        path.parent.mkdir(mode=0o755)
        path.write_text("ssh-ed25519 AAAAexisting me@laptop\n")

        def refuse(name: str) -> dict:
            raise SecurityError("disk full")

        monkeypatch.setattr(node.tokens, "create_fleet_token", refuse)

        with pytest.raises(NodeError):
            node.authorize()

        assert path.read_text() == "ssh-ed25519 AAAAexisting me@laptop\n"

    @pytest.mark.parametrize(
        ("key", "central", "user"),
        [
            ("ssh-rsa AAAAB3Nza user", "nas", TUNNEL_USER),
            (CENTRAL_KEY + "\nssh-ed25519 AAAA evil", "nas", TUNNEL_USER),
            (CENTRAL_KEY, "NAS central", TUNNEL_USER),
            (CENTRAL_KEY, "nas", "nobody-here"),
            (CENTRAL_KEY, "nas", "Bad User"),
        ],
    )
    def test_bad_inputs_change_nothing(self, node, key, central, user):
        with pytest.raises(NodeError):
            authorize(**node.arguments(central_key=key, central=central, ssh_user=user))
        assert not node.keys_file().exists()
        assert node.tokens.list_api_tokens() == []
        assert node.runner.calls_to("useradd") == []

    def test_a_missing_host_key_says_how_to_make_one(self, node):
        node.host_key_file.unlink()
        with pytest.raises(NodeError) as caught:
            node.authorize()
        assert "ssh-keygen -A" in caught.value.details


class TestDryRun:
    def test_authorize_changes_nothing_on_the_machine(self, node):
        rehearsal = DryRunRunner(node.runner)

        result = authorize(**node.arguments(runner=rehearsal, dry_run=True))

        assert node.tokens.list_api_tokens() == []
        assert not node.keys_file().exists()
        assert not node.dropin.exists()
        assert TUNNEL_USER not in node.homes
        assert ("useradd",) not in {c[:1] for c in node.runner.calls}
        assert result.key_changed
        assert result.token_name == "fleet-nas"
        assert result.ssh_user == TUNNEL_USER
        code = JoinCode.decode(result.join_code)
        assert node.tokens.verify_api_token(code.token, "127.0.0.1") is None

    def test_authorize_dry_run_does_not_revoke_an_older_token_nor_set_access(self, node):
        node.authorize()
        before_tokens = node.tokens.list_api_tokens()

        result = authorize(
            **node.arguments(
                central_key=OTHER_CENTRAL_KEY,
                access=FleetAccess("read"),
                runner=DryRunRunner(node.runner),
                dry_run=True,
            )
        )

        assert node.tokens.list_api_tokens() == before_tokens
        assert result.replaced_tokens == ["fleet-nas"]
        assert result.access == {"level": "read", "host_access": False}
        assert current_access(node.store) == FleetAccess("admin")

    def test_a_fresh_server_without_a_store_can_rehearse(self, node, monkeypatch):
        # --dry-run never creates the store, so on a server that never ran
        # Noust for real there is none to read the ceiling or fleet state from.
        from noust.core.store import StoreError

        class NoStore:
            def get_fleet_access(self):
                raise StoreError("Cannot open the Noust database")

        def no_store(*args, **kwargs):
            raise StoreError("Cannot open the Noust database")

        monkeypatch.setattr("noust.core.store.get_store", no_store)
        monkeypatch.setattr("noust.core.secrets.get_store", no_store, raising=False)

        result = authorize(
            **node.arguments(
                runner=DryRunRunner(node.runner),
                dry_run=True,
                store=NoStore(),
                self_check=issued_here,
            )
        )

        assert result.access == {"level": "admin", "host_access": False}

    def test_deauthorize_removes_nothing_and_revokes_nothing(self, node):
        node.authorize()
        path = node.keys_file()
        before = path.read_text()
        before_tokens = node.tokens.list_api_tokens()

        result = node.deauthorize(dry_run=True)

        assert path.read_text() == before
        assert node.tokens.list_api_tokens() == before_tokens
        assert result.removed_keys == 1
        assert result.revoked_tokens == ["fleet-nas"]


class TestConcurrentWrites:
    def test_concurrent_installs_for_different_centrals_lose_no_line(self, tmp_path):
        path = tmp_path / "home" / "authorized_keys"
        path.parent.mkdir(mode=0o700)
        key = parse_public_key(CENTRAL_KEY)
        centrals = [f"central-{i}" for i in range(30)]
        barrier = threading.Barrier(len(centrals))

        def worker(name: str) -> None:
            barrier.wait()
            AuthorizedKeys(path, None).install(authorized_key_line(name, key, 8080), name)

        threads = [threading.Thread(target=worker, args=(name,)) for name in centrals]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        content = path.read_text()
        for name in centrals:
            assert f"noust-central:{name}" in content, f"lost the line for {name}"
        assert content.count("noust-central:") == len(centrals)

    def test_concurrent_install_and_remove_serialise(self, tmp_path):
        path = tmp_path / "home" / "authorized_keys"
        path.parent.mkdir(mode=0o700)
        key = parse_public_key(CENTRAL_KEY)
        AuthorizedKeys(path, None).install(authorized_key_line("steady", key, 8080), "steady")
        barrier = threading.Barrier(2)

        def install_many() -> None:
            for i in range(15):
                barrier.wait() if i == 0 else None
                AuthorizedKeys(path, None).install(
                    authorized_key_line(f"c-{i}", key, 8080), f"c-{i}"
                )

        def remove_and_reinstall() -> None:
            for i in range(15):
                barrier.wait() if i == 0 else None
                AuthorizedKeys(path, None).remove("steady")
                AuthorizedKeys(path, None).install(
                    authorized_key_line("steady", key, 8080), "steady"
                )

        threads = [
            threading.Thread(target=install_many),
            threading.Thread(target=remove_and_reinstall),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        content = path.read_text()
        assert "noust-central:steady" in content
        for i in range(15):
            assert f"noust-central:c-{i}" in content


class TestDeauthorize:
    def test_removes_the_line_and_revokes_every_token_of_the_central(self, node):
        path = node.keys_file()
        path.parent.mkdir(mode=0o755)
        path.write_text("ssh-ed25519 AAAAexisting me@laptop\n")
        first = JoinCode.decode(node.authorize().join_code)
        second = JoinCode.decode(node.authorize().join_code)
        node.tokens.create_api_token("ci", "deploy")

        result = node.deauthorize()

        assert result.removed_keys == 1
        assert result.revoked_tokens == ["fleet-nas.2"]
        assert result.authorized_keys == [str(path)]
        assert path.read_text() == "ssh-ed25519 AAAAexisting me@laptop\n"
        for code in (first, second):
            assert node.tokens.verify_api_token(code.token, "127.0.0.1") is None
        live = [t["name"] for t in node.tokens.list_api_tokens() if t["revoked_at"] is None]
        assert live == ["ci"]

    def test_looks_in_root_s_file_too(self, node):
        node.authorize(user="root")
        root_file = node.keys_file("root")

        result = node.deauthorize()

        assert result.removed_keys == 1
        assert result.authorized_keys == [str(root_file)]
        assert root_file.read_text() == ""

    def test_one_account_only(self, node):
        node.authorize(user="deploy")

        result = node.deauthorize(user="deploy")

        assert result.authorized_keys == [str(node.keys_file("deploy"))]
        assert result.removed_keys == 1

    def test_nothing_to_remove(self, node):
        result = node.deauthorize()
        assert (result.removed_keys, result.revoked_tokens) == (0, [])
