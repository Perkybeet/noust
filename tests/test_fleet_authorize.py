"""
Tests for the node's side of enrollment: ``authorize`` and ``deauthorize``.

What is pinned: the exact restricted ``authorized_keys`` line, that it is never
duplicated and never written through a symlink, the file's mode and owner,
that sshd settings which would break the tunnel are refused up front, that a
central authorized again gets a new token and the old one is revoked, and
that the join code carries what the central needs.
"""

from __future__ import annotations

import pwd
import stat
import threading
from pathlib import Path

import pytest

from noust.core.exceptions import NodeError, SecurityError
from noust.core.runner import FakeRunner
from noust.fleet.authorize import (
    FORCED_COMMAND,
    NO_LISTEN,
    AuthorizedKeys,
    SshdSettings,
    authorize,
    authorized_key_line,
    authorized_keys_path,
    deauthorize,
    read_sshd_settings,
)
from noust.fleet.joincode import JoinCode
from noust.fleet.models import parse_public_key
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


@pytest.fixture
def tokens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TokenManager:
    directory = tmp_path / "state"
    directory.mkdir()
    monkeypatch.setenv(STATE_DIR_ENV, str(directory))
    return TokenManager(SecurityConfig())


class Node:
    """A node's accounts, sshd and host key, inside the test's directory."""

    def __init__(self, tmp_path: Path, tokens: TokenManager) -> None:
        self.tmp_path = tmp_path
        self.tokens = tokens
        self.runner = FakeRunner().script(["sshd", "-T"], stdout=SSHD_T)
        self.host_key_file = tmp_path / "ssh_host_ed25519_key.pub"
        self.host_key_file.write_text(HOST_KEY + " root@node\n")
        self.sshd_config = tmp_path / "sshd_config"
        self.homes = {
            "root": (0, tmp_path / "root"),
            "deploy": (1001, tmp_path / "home" / "deploy"),
        }
        for _, home in self.homes.values():
            home.mkdir(parents=True)
        self.console_calls = 0

    def passwd(self, user: str) -> pwd.struct_passwd:
        if user not in self.homes:
            raise KeyError(user)
        uid, home = self.homes[user]
        return pwd.struct_passwd((user, "x", uid, uid, "", str(home), "/bin/bash"))

    def keys_file(self, user: str = "root") -> Path:
        return self.homes[user][1] / ".ssh" / "authorized_keys"

    def console(self) -> int:
        self.console_calls += 1
        return 8080

    def authorize(self, key: str = CENTRAL_KEY, *, user: str = "root", confirm=None):
        return authorize(
            central_key=key,
            central="nas",
            tokens=self.tokens,
            ensure_console=self.console,
            confirm_replace=confirm or (lambda names: True),
            ssh_user=user,
            runner=self.runner,
            host_key_file=self.host_key_file,
            sshd_config=self.sshd_config,
            passwd=self.passwd,
        )

    def deauthorize(self, *, user: str = "root"):
        return deauthorize(
            central="nas",
            tokens=self.tokens,
            ssh_user=user,
            runner=self.runner,
            sshd_config=self.sshd_config,
            passwd=self.passwd,
        )


@pytest.fixture
def node(tmp_path: Path, tokens: TokenManager) -> Node:
    return Node(tmp_path, tokens)


def _expected_line(key: str = CENTRAL_KEY, port: int = 8080) -> str:
    bare = " ".join(key.split()[:2])
    return (
        f'restrict,port-forwarding,permitopen="127.0.0.1:{port}",'
        f'permitlisten="127.0.0.1:1",command="/usr/bin/false" {bare} noust-central:nas'
    )


class TestAuthorizedKeys:
    def test_the_restricted_line_mode_and_directory(self, node):
        result = node.authorize()

        path = node.keys_file()
        assert path.read_text() == _expected_line() + "\n"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert result.authorized_keys == str(path)
        assert result.key_changed
        assert FORCED_COMMAND == "/usr/bin/false"
        # root owns root's file already: nothing to chown.
        assert node.runner.calls_to("chown") == []

    def test_other_lines_are_kept_and_ours_is_never_duplicated(self, node):
        path = node.keys_file()
        path.parent.mkdir(mode=0o700)
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
        path = node.keys_file()
        path.parent.mkdir(mode=0o700)
        target = node.tmp_path / "shadow"
        target.write_text("secret\n")
        path.symlink_to(target)

        with pytest.raises(NodeError) as caught:
            node.authorize()

        assert "symlink" in caught.value.message
        assert target.read_text() == "secret\n"
        assert node.tokens.list_api_tokens() == []

    def test_a_symlinked_directory_is_refused(self, node):
        elsewhere = node.tmp_path / "elsewhere"
        elsewhere.mkdir()
        (node.homes["root"][1] / ".ssh").symlink_to(elsewhere)

        with pytest.raises(NodeError):
            node.authorize()
        assert list(elsewhere.iterdir()) == []

    def test_the_line_s_shape(self):
        key = parse_public_key(CENTRAL_KEY)
        assert authorized_key_line("nas", key, 9000) == _expected_line(port=9000)

    def test_remote_forwarding_is_pinned_to_a_port_nothing_uses(self):
        # port-forwarding turns -R back on too; permitlisten is what keeps the
        # central's key from listening on the node. OpenSSH has no "none" for
        # it (the key line would be rejected whole), and port 0 is refused, so
        # the tightest value is loopback port 1: sshd refuses a privileged
        # listen to any account but root, and nothing on the node dials it.
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
        path.parent.mkdir(mode=0o700)
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


class TestSshd:
    def test_reads_sshd_t(self, node):
        node.runner.script(["sshd", "-T"], stdout="port 2222\nport 22\nallowtcpforwarding local\n")
        settings = read_sshd_settings(node.runner, node.sshd_config)
        assert (settings.port, settings.allow_tcp_forwarding, settings.source) == (
            2222,
            "local",
            "sshd -T",
        )

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
        "config",
        [
            "allowtcpforwarding no\n",
            "allowtcpforwarding remote\n",
            "disableforwarding yes\n",
            "permitrootlogin no\n",
        ],
    )
    def test_settings_that_break_the_tunnel_are_refused_before_any_change(self, node, config):
        node.runner.script(["sshd", "-T"], stdout=config)

        with pytest.raises(NodeError) as caught:
            node.authorize()

        assert "sshd would refuse" in caught.value.message
        assert not node.keys_file().exists()
        assert node.console_calls == 0
        assert node.tokens.list_api_tokens() == []

    def test_the_join_code_carries_the_sshd_port(self, node):
        node.runner.script(["sshd", "-T"], stdout="port 2222\n")
        result = node.authorize()
        assert JoinCode.decode(result.join_code).ssh_port == 2222


class TestTokenAndJoinCode:
    def test_the_join_code_carries_a_working_fleet_token(self, node):
        result = node.authorize()

        code = JoinCode.decode(result.join_code)
        assert code.ssh_host_key == HOST_KEY
        assert (code.ssh_user, code.console_port) == ("root", 8080)
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

    def test_a_token_that_cannot_be_issued_puts_authorized_keys_back(self, node, monkeypatch):
        path = node.keys_file()
        path.parent.mkdir(mode=0o700)
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
            ("ssh-rsa AAAAB3Nza user", "nas", "root"),
            (CENTRAL_KEY + "\nssh-ed25519 AAAA evil", "nas", "root"),
            (CENTRAL_KEY, "NAS central", "root"),
            (CENTRAL_KEY, "nas", "nobody-here"),
        ],
    )
    def test_bad_inputs_change_nothing(self, node, key, central, user):
        with pytest.raises(NodeError):
            authorize(
                central_key=key,
                central=central,
                tokens=node.tokens,
                ensure_console=node.console,
                confirm_replace=lambda names: True,
                ssh_user=user,
                runner=node.runner,
                host_key_file=node.host_key_file,
                sshd_config=node.sshd_config,
                passwd=node.passwd,
            )
        assert not node.keys_file().exists()
        assert node.tokens.list_api_tokens() == []

    def test_a_missing_host_key_says_how_to_make_one(self, node):
        node.host_key_file.unlink()
        with pytest.raises(NodeError) as caught:
            node.authorize()
        assert "ssh-keygen -A" in caught.value.details


class TestDryRun:
    def test_authorize_creates_no_token_and_leaves_authorized_keys_alone(self, node):
        path = node.keys_file()
        path.parent.mkdir(mode=0o700)
        path.write_text("ssh-ed25519 AAAAexisting me@laptop\n")
        before = path.read_text()

        result = authorize(
            central_key=CENTRAL_KEY,
            central="nas",
            tokens=node.tokens,
            ensure_console=node.console,
            confirm_replace=lambda names: True,
            runner=node.runner,
            host_key_file=node.host_key_file,
            sshd_config=node.sshd_config,
            passwd=node.passwd,
            dry_run=True,
        )

        assert node.tokens.list_api_tokens() == []
        assert path.read_text() == before
        assert result.key_changed
        assert result.token_name == "fleet-nas"
        code = JoinCode.decode(result.join_code)
        assert node.tokens.verify_api_token(code.token, "127.0.0.1") is None

    def test_authorize_dry_run_does_not_revoke_an_older_token(self, node):
        node.authorize()
        before_tokens = node.tokens.list_api_tokens()

        result = authorize(
            central_key=OTHER_CENTRAL_KEY,
            central="nas",
            tokens=node.tokens,
            ensure_console=node.console,
            confirm_replace=lambda names: True,
            runner=node.runner,
            host_key_file=node.host_key_file,
            sshd_config=node.sshd_config,
            passwd=node.passwd,
            dry_run=True,
        )

        assert node.tokens.list_api_tokens() == before_tokens
        assert result.replaced_tokens == ["fleet-nas"]

    def test_deauthorize_removes_nothing_and_revokes_nothing(self, node):
        node.authorize()
        path = node.keys_file()
        before = path.read_text()
        before_tokens = node.tokens.list_api_tokens()

        result = deauthorize(
            central="nas",
            tokens=node.tokens,
            runner=node.runner,
            sshd_config=node.sshd_config,
            passwd=node.passwd,
            dry_run=True,
        )

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
        path.parent.mkdir(mode=0o700)
        path.write_text("ssh-ed25519 AAAAexisting me@laptop\n")
        first = JoinCode.decode(node.authorize().join_code)
        second = JoinCode.decode(node.authorize().join_code)
        node.tokens.create_api_token("ci", "deploy")

        result = node.deauthorize()

        assert result.removed_keys == 1
        assert result.revoked_tokens == ["fleet-nas.2"]
        assert path.read_text() == "ssh-ed25519 AAAAexisting me@laptop\n"
        for code in (first, second):
            assert node.tokens.verify_api_token(code.token, "127.0.0.1") is None
        live = [t["name"] for t in node.tokens.list_api_tokens() if t["revoked_at"] is None]
        assert live == ["ci"]

    def test_nothing_to_remove(self, node):
        result = node.deauthorize()
        assert (result.removed_keys, result.revoked_tokens) == (0, [])
