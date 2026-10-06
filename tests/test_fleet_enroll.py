# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Guided enrollment: a central brings a server in with a credential used once.

The server is a scripted stand-in behind the runner (what ssh would run there,
and what comes back); the central is the real registry wired to a fake node API,
so the join at the end is the real one. What is pinned:

- the host key is the one the operator confirmed, and the join code must carry it;
- Noust is installed with the central's own copy of the installer, only when
  missing and allowed, and removed from ``/run`` afterwards;
- the result is the one the manual join gives (the tunnel account, the token,
  the pinned key), and a failure past the authorization withdraws it;
- the credential is in no argument list, no log line, no audit event, no error
  and no file that outlives the call.
"""

from __future__ import annotations

import hashlib
import shlex
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from noust.core.config import Config
from noust.core.exceptions import NodeError
from noust.core.runner import CommandResult, FakeRunner
from noust.fleet import enroll as enroll_module
from noust.fleet.enroll import Credential, EnrollRequest, NodeEnroller, find_installer
from noust.fleet.joincode import JoinCode
from tests.fleet_support import HOST_KEY, Fleet, build_fleet, fingerprint, join_code

PASSWORD = "correct-horse-battery-1234"
PASSPHRASE = "key-passphrase-5678"
PRIVATE_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\n"
    "-----END OPENSSH PRIVATE KEY-----\n"
)
INSTALLER = "#!/bin/sh\necho installing noust\n"
SERVER_FINGERPRINT = fingerprint(HOST_KEY)


class FakeServer:
    """
    What the server answers, command by command, and what it was asked.

    Attributes:
        installed: Whether noust is on it.
        version: What ``noust --version`` says.
        uid: What ``id -u`` says for the account the credential is for.
        sudo_works: Whether ``sudo -n`` runs.
        authorize_exit: The exit status of ``noust fleet authorize``.
        authorize_output: What it prints when it fails.
        code_fields: Overrides of what the join code it prints holds.
        installer_exit: The exit status of the installer.
        commands: Every remote command, split, in order.
        uploaded: What was written to the installer's path.
        deauthorized: Whether the central's authorization was withdrawn.
    """

    def __init__(self) -> None:
        self.installed = True
        self.version = "3.3.1"
        self.uid = "0"
        self.sudo_works = True
        self.authorize_exit = 0
        self.authorize_output = ""
        self.code_fields: dict[str, Any] = {}
        self.installer_exit = 0
        self.commands: list[list[str]] = []
        self.uploaded: str | None = None
        self.deauthorized = False
        self.removed: list[str] = []

    def installer_lines(self) -> list[str]:
        return ["Noust installer: Ubuntu 24.04 LTS, amd64 (supported)", "Installed: Noust 3.3.1"]

    def answer(self, remote: str, input_text: str | None) -> tuple[int, str, str]:
        tokens = shlex.split(remote)
        self.commands.append(tokens)
        if tokens[:2] == ["sudo", "-n"]:
            if not self.sudo_works:
                return 1, "", "sudo: a password is required"
            tokens = tokens[2:]
        if tokens[:2] == ["sh", "-c"]:
            script = tokens[2]
            if script.startswith("id -u"):
                version = (
                    f"Noust {self.version} (formerly WASM)" if self.installed else "NOUST_MISSING"
                )
                return 0, f"{self.uid}\n{version}\n", ""
            if script.startswith("umask 077 && cat > "):
                self.uploaded = input_text
                return 0, "", ""
        if tokens == ["true"]:
            return 0, "", ""
        if tokens[:2] == ["sh", tokens[1]] and tokens[1].startswith("/run/noust-install-"):
            if self.installer_exit == 0:
                self.installed = True
            return self.installer_exit, "\n".join(self.installer_lines()) + "\n", ""
        if tokens[:2] == ["rm", "-f"]:
            self.removed.append(tokens[2])
            return 0, "", ""
        if tokens[:3] == ["noust", "fleet", "authorize"]:
            if self.authorize_exit != 0:
                return self.authorize_exit, self.authorize_output, "Aborted!"
            key = tokens[tokens.index("--central-key") + 1]
            fields: dict[str, Any] = {
                "ssh_user": "noust-tunnel",
                "ssh_host_key": HOST_KEY,
                "noust_version": self.version,
                "node_name": "web-2",
                "central": tokens[tokens.index("--name") + 1],
                **self.code_fields,
            }
            code = join_code(key, **fields)
            return 0, '{"join_code": "' + code + '", "warnings": ["a warning of its own"]}\n', ""
        if tokens[:3] == ["noust", "fleet", "deauthorize"]:
            self.deauthorized = True
            return 0, "", ""
        return 1, "", f"unexpected command: {remote}"


class SshRunner(FakeRunner):
    """The runner of the enrollment: ssh goes to the scripted server, the rest is recorded."""

    def __init__(self, server: FakeServer) -> None:
        super().__init__()
        self.server = server
        self.inputs_sent: list[str | None] = []
        self.streamed: list[str] = []

    def run(self, argv: Sequence[str], **kwargs: Any) -> CommandResult:  # type: ignore[override]
        result = super().run(argv, **kwargs)
        if argv[0] != "ssh" or argv[1] == "-Q":
            return result
        self.inputs_sent.append(kwargs.get("input"))
        code, out, err = self.server.answer(argv[-1], kwargs.get("input"))
        return replace(result, exit_code=code, stdout=out, stderr=err)

    def stream(
        self, argv: Sequence[str], *, on_line: Callable[[str], None], **kwargs: Any
    ) -> CommandResult:  # type: ignore[override]
        result = super().stream(argv, on_line=lambda line: None, **kwargs)
        if argv[0] != "ssh":
            return result
        code, out, err = self.server.answer(argv[-1], None)
        for line in out.splitlines():
            self.streamed.append(line)
            on_line(line)
        return replace(result, exit_code=code, stdout=out, stderr=err)

    def ssh_calls(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if call[0] == "ssh" and call[1] != "-Q"]


@pytest.fixture(autouse=True)
def runtime_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private, memory-backed directory stand-in for the credential's files."""
    directory = tmp_path / "run"
    directory.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(directory))
    return directory


@pytest.fixture(autouse=True)
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    Config.reset_instance()
    yield
    Config.reset_instance()


@pytest.fixture
def fleet(tmp_path: Path) -> Iterator[Fleet]:
    built = build_fleet(tmp_path)
    yield built
    from noust.core.store import NoustStore

    NoustStore.reset_instance()


@pytest.fixture
def server() -> FakeServer:
    return FakeServer()


@pytest.fixture
def ssh(server: FakeServer) -> SshRunner:
    runner = SshRunner(server)
    runner.script(
        ["ssh-keyscan"],
        stdout=f"# web2.example.com:22 SSH-2.0-OpenSSH_9.6\nweb2.example.com {HOST_KEY}\n",
    )
    return runner


@pytest.fixture
def installer_file(tmp_path: Path) -> Path:
    path = tmp_path / "install.sh"
    path.write_text(INSTALLER, encoding="utf-8")
    return path


@pytest.fixture
def enroller(fleet: Fleet, ssh: SshRunner, installer_file: Path) -> NodeEnroller:
    return NodeEnroller(fleet.manager, runner=ssh, installer=installer_file)


def request(**overrides: Any) -> EnrollRequest:
    values: dict[str, Any] = {
        "name": "web-2",
        "host": "web2.example.com",
        "host_key_fingerprint": SERVER_FINGERPRINT,
        "credential": Credential(password=PASSWORD),
    }
    values.update(overrides)
    return EnrollRequest(**values)


class Lines:
    """What the enrollment said, as ``(level, message)``."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, str]] = []

    def __call__(self, message: str, level: str = "info") -> None:
        self.entries.append((level, message))

    @property
    def text(self) -> str:
        return "\n".join(message for _, message in self.entries)


# ---------------------------------------------------------------------------
# The result is the manual join's
# ---------------------------------------------------------------------------


def test_a_server_with_noust_is_authorized_and_joined(
    enroller: NodeEnroller, fleet: Fleet, server: FakeServer, ssh: SshRunner
) -> None:
    node = enroller.enroll(request(access="deploy"), actor="web:maria")

    assert node.name == "web-2"
    assert node.ssh_user == "noust-tunnel"
    assert node.status == "reachable"
    assert fleet.store.get_node("web-2") is not None
    # The pinned host key is the one that was confirmed, as the manual join pins it.
    assert node.host_key.endswith(HOST_KEY.split()[0] + " " + HOST_KEY.split()[1])
    authorize = next(c for c in server.commands if c[:3] == ["noust", "fleet", "authorize"])
    assert authorize[authorize.index("--access") + 1] == "deploy"
    assert "--no-host-access" in authorize and "--json" in authorize
    assert "--yes" not in authorize
    key = fleet.manager.central_public_key("web-2")
    assert authorize[authorize.index("--central-key") + 1] == key
    assert server.uploaded is None, "Noust was there: nothing is installed"
    assert not server.deauthorized


def test_the_host_key_is_checked_against_the_confirmed_fingerprint_before_anything_is_sent(
    enroller: NodeEnroller, fleet: Fleet, server: FakeServer, ssh: SshRunner
) -> None:
    ssh.script(
        ["ssh-keyscan"], stdout="web2.example.com " + HOST_KEY.replace("AAAA", "BBBB", 1) + "\n"
    )

    with pytest.raises(NodeError):
        enroller.enroll(request())

    assert ssh.ssh_calls() == []
    assert fleet.store.get_node("web-2") is None


def test_a_different_server_than_the_one_confirmed_is_named(
    enroller: NodeEnroller, ssh: SshRunner
) -> None:
    other = "SHA256:" + "A" * 43

    with pytest.raises(NodeError) as raised:
        enroller.enroll(request(host_key_fingerprint=other))

    assert "not the one you confirmed" in raised.value.message
    assert SERVER_FINGERPRINT in (raised.value.details or "")
    assert ssh.ssh_calls() == []


def test_the_connection_uses_the_confirmed_key_and_no_other_trust(
    enroller: NodeEnroller, ssh: SshRunner
) -> None:
    enroller.enroll(request())

    argv = ssh.ssh_calls()[0]
    options = {
        argv[i + 1].split("=", 1)[0]: argv[i + 1].split("=", 1)[1]
        for i, a in enumerate(argv)
        if a == "-o"
    }
    assert options["StrictHostKeyChecking"] == "yes"
    assert options["GlobalKnownHostsFile"] == "/dev/null"
    assert options["HostKeyAlias"] == "noust-enroll-web-2"
    assert options["HostKeyAlgorithms"] == "ssh-ed25519"
    assert options["IdentityAgent"] == "none"
    assert options["ForwardAgent"] == "no"
    assert options["PreferredAuthentications"] == "password,keyboard-interactive"
    assert options["NumberOfPasswordPrompts"] == "1"
    assert argv[-2] == "web2.example.com"
    assert "-F" in argv and argv[argv.index("-F") + 1] == "/dev/null"
    assert argv[argv.index("-l") + 1] == "root"


def test_the_join_code_must_carry_the_host_key_that_was_confirmed(
    enroller: NodeEnroller,
    fleet: Fleet,
    server: FakeServer,
) -> None:
    from tests.fleet_support import ed25519_line

    server.code_fields = {"ssh_host_key": ed25519_line(77)}

    with pytest.raises(NodeError) as raised:
        enroller.enroll(request())

    assert "web-2 could not be added" in raised.value.message
    assert "not the one that was scanned" in raised.value.message
    assert fleet.store.get_node("web-2") is None
    assert server.deauthorized, "what was authorized is taken back"


def test_a_failed_join_withdraws_the_authorization_and_registers_nothing(
    enroller: NodeEnroller, fleet: Fleet, server: FakeServer
) -> None:
    fleet.node.revoked = True  # the node refuses the token it was just given

    with pytest.raises(NodeError) as raised:
        enroller.enroll(request())

    assert "web-2 could not be added" in raised.value.message
    assert "withdrawn" in (raised.value.details or "")
    assert server.deauthorized
    assert fleet.store.get_node("web-2") is None


# ---------------------------------------------------------------------------
# Installing Noust
# ---------------------------------------------------------------------------


def test_noust_is_installed_with_the_centrals_own_installer_when_it_is_missing(
    enroller: NodeEnroller, server: FakeServer, ssh: SshRunner
) -> None:
    server.installed = False
    log = Lines()

    enroller.enroll(request(), log=log)

    assert server.uploaded == INSTALLER
    run = next(
        c for c in server.commands if c[0] == "sh" and c[1].startswith("/run/noust-install-")
    )
    # The console is authorize's to make a service; the server downloads no script.
    assert run[2:] == ["--yes", "--no-console"]
    assert server.removed == [run[1]], "the installer does not stay on the server"
    assert "Installed: Noust 3.3.1" in log.text
    assert hashlib.sha256(INSTALLER.encode()).hexdigest()[:16] in log.text
    assert ssh.streamed == server.installer_lines()


def test_a_failing_installer_shows_its_own_output(
    enroller: NodeEnroller, server: FakeServer, fleet: Fleet
) -> None:
    server.installed = False
    server.installer_exit = 3

    with pytest.raises(NodeError) as raised:
        enroller.enroll(request())

    assert "installer failed on the server (exit 3)" in raised.value.message
    assert "Ubuntu 24.04 LTS" in (raised.value.details or "")
    assert fleet.store.get_node("web-2") is None
    assert not any(c[:3] == ["noust", "fleet", "authorize"] for c in server.commands)
    assert server.removed, "the installer is removed even when it fails"


def test_without_permission_to_install_it_stops_before_anything_changes(
    enroller: NodeEnroller, server: FakeServer
) -> None:
    server.installed = False

    with pytest.raises(NodeError) as raised:
        enroller.enroll(request(install=False))

    assert "not installed" in raised.value.message
    assert server.uploaded is None


def test_a_central_without_an_installer_says_how_to_go_on(
    fleet: Fleet, ssh: SshRunner, server: FakeServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    server.installed = False
    monkeypatch.setattr(enroll_module, "INSTALLER_PATHS", ())
    enroller = NodeEnroller(fleet.manager, runner=ssh)

    with pytest.raises(NodeError) as raised:
        enroller.enroll(request())

    assert "no installer" in raised.value.message
    assert "install.sh" in (raised.value.details or "")


def test_find_installer_looks_where_the_package_puts_it(tmp_path: Path) -> None:
    present = tmp_path / "install.sh"
    present.write_text("#!/bin/sh\n", encoding="utf-8")

    assert find_installer((tmp_path / "nowhere.sh", present)) == present
    assert find_installer((tmp_path / "nowhere.sh",)) is None


def test_a_noust_too_old_to_be_a_node_is_not_touched(
    enroller: NodeEnroller, server: FakeServer
) -> None:
    server.version = "3.0.2"

    with pytest.raises(NodeError) as raised:
        enroller.enroll(request())

    assert "too old" in raised.value.message
    assert not any(c[:3] == ["noust", "fleet", "authorize"] for c in server.commands)


# ---------------------------------------------------------------------------
# The account the credential is for
# ---------------------------------------------------------------------------


def test_an_account_that_is_not_root_acts_through_passwordless_sudo(
    enroller: NodeEnroller, server: FakeServer
) -> None:
    server.uid = "1000"

    enroller.enroll(request(ssh_user="deploy"))

    authorize = [c for c in server.commands if c[:2] == ["sudo", "-n"] and "authorize" in c]
    assert authorize, "authorize ran through sudo -n"


def test_an_account_without_sudo_is_refused_with_the_other_ways_in(
    enroller: NodeEnroller, server: FakeServer
) -> None:
    server.uid = "1000"
    server.sudo_works = False

    with pytest.raises(NodeError) as raised:
        enroller.enroll(request(ssh_user="deploy"))

    assert "no passwordless sudo" in raised.value.message
    assert "NOPASSWD" in (raised.value.details or "")
    assert not any("authorize" in c for c in server.commands)


# ---------------------------------------------------------------------------
# What authorize says when it does not work
# ---------------------------------------------------------------------------


def test_an_existing_token_asks_for_it_to_be_replaced(
    enroller: NodeEnroller, server: FakeServer
) -> None:
    server.authorize_exit = 1
    server.authorize_output = "Central nas already holds fleet-nas on this server. Revoke it and issue a new token? [y/N]: "

    with pytest.raises(NodeError) as raised:
        enroller.enroll(request())

    assert "already holds a token" in raised.value.message
    assert "--replace-token" in (raised.value.details or "")


def test_replacing_the_token_passes_yes_to_authorize(
    enroller: NodeEnroller, server: FakeServer
) -> None:
    enroller.enroll(request(replace_token=True))

    authorize = next(c for c in server.commands if c[:3] == ["noust", "fleet", "authorize"])
    assert "--yes" in authorize


def test_any_other_failure_of_authorize_is_shown_as_it_was_printed(
    enroller: NodeEnroller, server: FakeServer
) -> None:
    server.authorize_exit = 1
    server.authorize_output = "error: sshd would refuse the central's tunnel"

    with pytest.raises(NodeError) as raised:
        enroller.enroll(request())

    assert "authorize failed on the server (exit 1)" in raised.value.message
    assert "sshd would refuse" in (raised.value.details or "")


def test_a_warning_authorize_gave_reaches_the_log(
    enroller: NodeEnroller, server: FakeServer
) -> None:
    log = Lines()

    enroller.enroll(request(), log=log)

    assert ("warning", "a warning of its own") in log.entries


# ---------------------------------------------------------------------------
# Refused before connecting
# ---------------------------------------------------------------------------


def test_a_central_that_may_not_add_servers_stops_before_connecting(
    tmp_path: Path, ssh: SshRunner, installer_file: Path
) -> None:
    fleet = build_fleet(tmp_path, blockers=lambda: ["Two-factor sign-in is not enabled."])
    enroller = NodeEnroller(fleet.manager, runner=ssh, installer=installer_file)

    with pytest.raises(NodeError) as raised:
        enroller.enroll(request())

    assert "may not register servers yet" in raised.value.message
    assert "Two-factor" in (raised.value.details or "")
    assert ssh.calls == []


def test_a_name_that_is_taken_stops_before_connecting(
    enroller: NodeEnroller, fleet: Fleet, ssh: SshRunner
) -> None:
    fleet.added("web-2")
    ssh.calls.clear()

    with pytest.raises(NodeError) as raised:
        enroller.enroll(request())

    assert "already registered" in raised.value.message
    assert ssh.calls == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "Web 2"},
        {"name": ""},
        {"ssh_user": "root; id"},
        {"host": "web2.example.com; rm -rf /"},
        {"host": "root@web2.example.com"},
        {"host": "web2.example.com:2222"},
        {"port": 0},
        {"port": 70000},
        {"access": "owner"},
        {"host_key_fingerprint": ""},
        {"host_key_fingerprint": "MD5:00:11"},
        {"credential": Credential()},
        {"credential": Credential(password="a", private_key=PRIVATE_KEY)},
        {"credential": Credential(private_key="not a key")},
        {"credential": Credential(password="a", passphrase="b")},
    ],
)
def test_nothing_malformed_reaches_a_command_line(
    enroller: NodeEnroller, ssh: SshRunner, overrides: dict[str, Any]
) -> None:
    with pytest.raises(Exception) as raised:
        enroller.enroll(request(**overrides))

    assert type(raised.value).__name__ in ("NodeError", "ValidationError")
    assert ssh.calls == []


# ---------------------------------------------------------------------------
# The credential
# ---------------------------------------------------------------------------


def everything_recorded(ssh: SshRunner) -> str:
    return "\n".join(" ".join(call) for call in ssh.calls)


def test_a_password_goes_through_the_environment_of_ssh_alone(
    enroller: NodeEnroller, ssh: SshRunner
) -> None:
    enroller.enroll(request())

    assert PASSWORD not in everything_recorded(ssh)
    ssh_envs = [
        env
        for call, env in zip(ssh.calls, ssh.envs, strict=True)
        if call[0] == "ssh" and call[1] != "-Q"
    ]
    assert ssh_envs and all(env is not None for env in ssh_envs)
    for env in ssh_envs:
        assert env is not None
        assert env["NOUST_ENROLL_SECRET"] == PASSWORD
        assert env["SSH_ASKPASS_REQUIRE"] == "force"
        assert Path(env["SSH_ASKPASS"]).name == "askpass.sh"


def test_the_helper_ssh_asks_holds_no_secret(enroller: NodeEnroller, ssh: SshRunner) -> None:
    enroller.enroll(request())

    env = next(
        e for c, e in zip(ssh.calls, ssh.envs, strict=True) if c[0] == "ssh" and c[1] != "-Q"
    )
    assert env is not None
    script = Path(env["SSH_ASKPASS"])
    text = script.read_text(encoding="utf-8")
    assert PASSWORD not in text
    assert "NOUST_ENROLL_SECRET" in text
    assert script.stat().st_mode & 0o777 == 0o700


def test_a_key_is_a_file_in_a_private_directory_that_is_gone_afterwards(
    enroller: NodeEnroller, ssh: SshRunner, runtime_dir: Path
) -> None:
    seen: dict[str, Any] = {}
    real_stream = ssh.run

    def spying_run(argv: Sequence[str], **kwargs: Any) -> CommandResult:
        if argv[0] == "ssh" and argv[1] != "-Q" and "identity" not in seen:
            path = Path(argv[argv.index("-i") + 1])
            seen["identity"] = path
            seen["text"] = path.read_text(encoding="utf-8")
            seen["mode"] = path.stat().st_mode & 0o777
            seen["dir_mode"] = path.parent.stat().st_mode & 0o777
        return real_stream(argv, **kwargs)

    ssh.run = spying_run  # type: ignore[method-assign]

    enroller.enroll(request(credential=Credential(private_key=PRIVATE_KEY)))

    assert seen["text"] == PRIVATE_KEY
    assert seen["mode"] == 0o600 and seen["dir_mode"] == 0o700
    assert not seen["identity"].exists(), "the key does not outlive the call"
    assert not seen["identity"].parent.exists()
    assert PRIVATE_KEY not in everything_recorded(ssh)
    argv = ssh.ssh_calls()[0]
    options = {
        argv[i + 1].split("=", 1)[0]: argv[i + 1].split("=", 1)[1]
        for i, a in enumerate(argv)
        if a == "-o"
    }
    assert options["BatchMode"] == "yes"
    assert options["PreferredAuthentications"] == "publickey"
    assert options["PasswordAuthentication"] == "no"


def test_a_keys_passphrase_goes_the_way_a_password_does(
    enroller: NodeEnroller, ssh: SshRunner
) -> None:
    enroller.enroll(request(credential=Credential(private_key=PRIVATE_KEY, passphrase=PASSPHRASE)))

    assert PASSPHRASE not in everything_recorded(ssh)
    env = next(
        e for c, e in zip(ssh.calls, ssh.envs, strict=True) if c[0] == "ssh" and c[1] != "-Q"
    )
    assert env is not None and env["NOUST_ENROLL_SECRET"] == PASSPHRASE


def test_the_private_directory_goes_when_the_enrollment_fails_too(
    enroller: NodeEnroller, server: FakeServer, runtime_dir: Path
) -> None:
    server.authorize_exit = 1

    with pytest.raises(NodeError):
        enroller.enroll(request(credential=Credential(private_key=PRIVATE_KEY)))

    leftovers = [p for p in runtime_dir.rglob("*") if p.is_file() and p.name != "askpass.sh"]
    assert leftovers == []


def test_no_log_line_error_or_result_holds_the_credential(
    enroller: NodeEnroller, server: FakeServer
) -> None:
    log = Lines()
    server.installed = False
    enroller.enroll(request(), log=log)
    assert PASSWORD not in log.text
    assert "noust_tok_" not in log.text, "the join code's token is never logged"
    assert "noust-join:" not in log.text

    server.authorize_exit = 1
    server.authorize_output = "refused"
    failing = Lines()
    with pytest.raises(NodeError) as raised:
        NodeEnroller(
            enroller.manager, runner=enroller.runner, installer=enroller._installer
        ).enroll(request(name="web-3"), log=failing)
    text = f"{raised.value.message} {raised.value.details}"
    assert PASSWORD not in text and PASSWORD not in failing.text


def test_the_credential_describes_itself_without_itself() -> None:
    credential = Credential(password=PASSWORD, passphrase=PASSPHRASE)

    assert PASSWORD not in repr(credential) and PASSWORD not in str(credential)
    assert PASSPHRASE not in repr(credential)
    assert PASSWORD not in repr(request(credential=credential))
    assert credential.secrets() == [PASSWORD, PASSPHRASE]


def test_the_audit_trail_records_who_and_where_and_never_the_credential(
    enroller: NodeEnroller, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        enroll_module.audit,
        "record",
        lambda event, **kwargs: events.append((event, {**kwargs, **(kwargs.get("details") or {})})),
    )

    enroller.enroll(request(access="read"))

    assert [name for name, _ in events] == ["fleet.enroll.start", "fleet.enroll.finish"]
    start, finish = events[0][1], events[1][1]
    assert start["host"] == "web2.example.com" and start["access"] == "read"
    assert start["host_key"] == SERVER_FINGERPRINT
    assert finish["outcome"] == "ok" and finish["version"]
    assert PASSWORD not in repr(events)


def test_a_failure_is_audited_as_one(
    enroller: NodeEnroller, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        enroll_module.audit,
        "record",
        lambda event, **kwargs: events.append((event, {**kwargs, **(kwargs.get("details") or {})})),
    )

    with pytest.raises(NodeError):
        enroller.enroll(request(host_key_fingerprint="SHA256:" + "A" * 43))

    assert events[-1][0] == "fleet.enroll.finish"
    assert events[-1][1]["outcome"] == "failure"
    assert "confirmed" in events[-1][1]["error"]


# ---------------------------------------------------------------------------
# The host key scan
# ---------------------------------------------------------------------------


def test_scanning_shows_the_fingerprint_and_trusts_nothing(
    enroller: NodeEnroller, ssh: SshRunner
) -> None:
    scan = enroller.scan_host_key("web2.example.com", 2222)

    assert scan.fingerprint == SERVER_FINGERPRINT
    assert scan.to_dict() == {
        "host": "web2.example.com",
        "port": 2222,
        "key_type": "ssh-ed25519",
        "fingerprint": SERVER_FINGERPRINT,
    }
    argv = ssh.calls_to("ssh-keyscan")[0]
    assert argv[argv.index("-p") + 1] == "2222" and argv[-1] == "web2.example.com"


def test_a_server_that_answers_nothing_is_named(enroller: NodeEnroller, ssh: SshRunner) -> None:
    ssh.script(
        ["ssh-keyscan"], stdout="", exit_code=1, stderr="connect to host: Connection refused"
    )

    with pytest.raises(NodeError) as raised:
        enroller.scan_host_key("web2.example.com")

    assert "gave no ed25519 host key" in raised.value.message
    assert "Connection refused" in (raised.value.details or "")


def test_an_address_with_an_account_or_a_port_in_it_is_not_scanned(
    enroller: NodeEnroller, ssh: SshRunner
) -> None:
    for host in ("root@web2.example.com", "web2.example.com:22", "web2.example.com; id"):
        with pytest.raises(NodeError):
            enroller.scan_host_key(host)
    assert ssh.calls == []


def test_a_failure_to_log_in_explains_itself_after_ssh_s_own_words(
    enroller: NodeEnroller, server: FakeServer, ssh: SshRunner
) -> None:
    original = server.answer

    def refuse(remote: str, input_text: str | None) -> tuple[int, str, str]:
        return 255, "", "root@web2.example.com: Permission denied (publickey,password)."

    server.answer = refuse  # type: ignore[method-assign]

    with pytest.raises(NodeError) as raised:
        enroller.enroll(request())

    assert "Permission denied (publickey,password)." in (raised.value.details or "")
    assert "refused the credential" in (raised.value.details or "")
    server.answer = original  # type: ignore[method-assign]


def test_the_join_code_it_builds_the_registration_on_is_a_real_one(
    enroller: NodeEnroller, fleet: Fleet
) -> None:
    node = enroller.enroll(request())

    code = JoinCode.decode(join_code(fleet.manager.central_public_key("web-2")))
    assert node.console_port == code.console_port


def test_a_server_that_does_not_answer_in_time_says_so(
    enroller: NodeEnroller, server: FakeServer
) -> None:
    from noust.core.runner import EXIT_TIMEOUT

    def silent(remote: str, input_text: str | None) -> tuple[int, str, str]:
        return EXIT_TIMEOUT, "", ""

    server.answer = silent  # type: ignore[method-assign]
    original = enroller.runner.run

    def timed_out(argv: Sequence[str], **kwargs: Any) -> CommandResult:
        result = original(argv, **kwargs)
        return replace(result, timed_out=result.exit_code == EXIT_TIMEOUT)

    enroller.runner.run = timed_out  # type: ignore[method-assign]

    with pytest.raises(NodeError) as raised:
        enroller.enroll(request())

    assert "Could not connect" in raised.value.message
    assert "did not finish within" in (raised.value.details or "")
