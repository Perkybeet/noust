"""
Tests for the runner's sandboxed execution, its strict read-only classification
and its execution listeners.

The sandbox's argv is asserted exactly with the fake runner, which records what
the real one would execute. The real runner is exercised against a stand-in
``systemd-run`` that honours the same contract (environment file, ``$$``
escaping, the result marker its ``ExecStopPost`` writes), because the real one
needs systemd as PID 1 and root; tests/integration/run.py covers that.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

from noust.core import paths
from noust.core import runner as runner_module
from noust.core.runner import (
    EXIT_NOT_FOUND,
    EXIT_SANDBOX_FAILED,
    EXIT_TIMEOUT,
    SANDBOX_TIMEOUT_MARGIN,
    CommandCancelled,
    CommandExecution,
    CommandResult,
    CommandRunner,
    DryRunRunner,
    FakeRunner,
    SandboxSpec,
    SubprocessRunner,
    add_execution_listener,
    cancellable,
    clean_environment,
    environment_file_text,
    escape_systemd_argv,
    interpret_sandbox_result,
    is_read_only,
    matches_shape,
    read_sandbox_marker,
    remove_execution_listener,
    sandbox_environment,
    sandbox_prefix,
    sandbox_unit_name,
)

RELEASE = Path("/var/www/apps/shop-example-com/releases/20260929-120000-abcdef0")
SHARED = Path("/var/www/apps/shop-example-com/shared")
APPS = Path("/var/www/apps")
CACHE_ROOT = Path("/var/cache/noust/build")
CACHE = CACHE_ROOT / "shop-example-com"


def build_spec(**overrides: object) -> SandboxSpec:
    """A spec shaped like a release build's."""
    values: dict[str, object] = {
        "user": "noust-build",
        "writable_paths": (RELEASE, CACHE),
        "read_only_paths": (SHARED,),
        "inaccessible_paths": (Path("/etc/noust"), Path("/var/lib/noust")),
        "hidden_paths": (APPS, CACHE_ROOT),
        "memory_max_mb": 2048,
        "cpu_quota_percent": 200,
        "name": "shop-example-com",
    }
    values.update(overrides)
    return SandboxSpec(**values)  # type: ignore[arg-type]


def properties(argv: tuple[str, ...] | list[str]) -> list[str]:
    """The unit properties of a systemd-run argv, in order."""
    head = list(argv[: list(argv).index("--")])
    return [arg.removeprefix("--property=") for arg in head if arg.startswith("--property=")]


# ---------------------------------------------------------------------------
# is_read_only: exact shapes only
# ---------------------------------------------------------------------------


class TestStrictReadOnly:
    """A command is a probe only when its exact shape is declared."""

    @pytest.mark.parametrize(
        "argv",
        [
            # The finding: any argument matching made this "read-only".
            ["ufw", "allow", "22", "comment", "status"],
            ["git", "commit", "-m", "status"],
            ["git", "-c", "protocol.ext.allow=never", "push", "origin", "log"],
            ["docker", "run", "--name", "ps", "alpine"],
            ["docker", "compose", "-p", "x", "-f", "c.yml", "up", "-d", "ps"],
            ["systemctl", "restart", "show"],
            ["systemctl", "stop", "is-active"],
            ["certbot", "renew", "certificates"],
            ["apt-get", "install", "-y", "nginx", "--version"],
            ["rm", "-rf", "/var/www/apps/x", "--version"],
            ["dnf", "install", "-y", "info"],
            ["sshd", "-f", "/tmp/x", "-T"],
            # Options that turn a declared probe into a change.
            ["journalctl", "--vacuum-size=100M"],
            ["journalctl", "--vacuum-time=7d"],
            ["journalctl", "--rotate"],
            ["openssl", "x509", "-in", "a.pem", "-noout", "-out", "/etc/noust/x"],
            ["git", "ls-remote", "--exit-code", "--upload-pack=touch /tmp/x"],
            ["git", "log", "-1", "--format=%s", "--output=/etc/passwd"],
            # A bare wildcard is an operand, never an option.
            ["ssh", "-Q", "-oProxyCommand=x"],
            ["fail2ban-client", "status", "--async"],
            ["hostname", "attacker"],
            ["ufw", "enable"],
            ["firewall-cmd", "--reload"],
            ["nft", "flush", "ruleset"],
            ["systemd-run", "--wait", "--", "cat", "/etc/shadow"],
        ],
    )
    def test_these_change_something(self, argv: list[str]) -> None:
        assert not is_read_only(argv)

    @pytest.mark.parametrize(
        "argv",
        [
            # Requested by the server's security checks and the fleet.
            ["ss", "-Hltnup"],
            ["ss", "-Htnp", "state", "established"],
            ["sshd", "-t"],
            ["sshd", "-T"],
            ["sshd", "-T", "-C", "user=noust-tunnel,host=x,addr=127.0.0.1"],
            ["ufw", "status"],
            ["ufw", "status", "numbered"],
            ["ufw", "status", "verbose"],
            ["ufw", "show", "added"],
            ["firewall-cmd", "--state"],
            ["firewall-cmd", "--get-default-zone"],
            ["firewall-cmd", "--list-all"],
            ["firewall-cmd", "--zone=public", "--list-all"],
            ["firewall-cmd", "--permanent", "--zone=public", "--list-all"],
            ["fail2ban-client", "ping"],
            ["fail2ban-client", "status"],
            ["fail2ban-client", "status", "sshd"],
            ["fail2ban-client", "-t"],
            ["nft", "-j", "list", "ruleset"],
            ["getenforce"],
            ["systemctl", "is-system-running"],
            ["systemctl", "list-units", "--failed", "--plain", "--no-legend"],
            ["ssh", "-Q", "kex"],
            ["ssh", "-Q", "cipher"],
            # What Noust ran as a probe before, still a probe.
            ["systemctl", "is-active", "--quiet", "noust-web.service"],
            ["journalctl", "-u", "shop-example-com.service", "-n", "50", "--no-pager"],
            [
                "git",
                "-c",
                "protocol.ext.allow=never",
                "-c",
                "protocol.file.allow=never",
                "rev-parse",
                "HEAD",
            ],
            [
                "git",
                "-c",
                "safe.directory=/var/www/apps/x",
                "--no-optional-locks",
                "status",
                "--porcelain=v1",
            ],
            ["git", "log", "-1", "--format=%s", "0123456789abcdef"],
            ["docker", "compose", "-p", "stack", "-f", "/x/docker-compose.yml", "ps", "-q"],
            [
                "docker",
                "compose",
                "-p",
                "s",
                "-f",
                "/x/c.yml",
                "--profile",
                "web",
                "logs",
                "--tail",
                "5",
            ],
            ["/usr/bin/whoami"],
            ["node", "--version"],
            ["dnf", "--cacheonly", "info", "--available", "noust"],
            ["openssl", "enc", "-d", "-aes-256-cbc", "-pass", "env:K", "-a", "-A"],
        ],
    )
    def test_these_only_look(self, argv: list[str]) -> None:
        assert is_read_only(argv)

    def test_a_managers_own_declarations_count(self) -> None:
        from noust.managers.server.probes import READ_ONLY_PROBES

        concrete = [
            [str(arg).replace("*", "x") for arg in probe if arg is not ...]
            for probe in READ_ONLY_PROBES
        ]
        assert concrete, "the server managers declare probes"
        for argv in concrete:
            if argv[0] == "journalctl":
                continue
            assert is_read_only(argv), argv

    def test_a_wildcard_program_only_ever_means_a_version(self) -> None:
        assert matches_shape(["npm", "--version"], ("*", "--version"))
        assert not matches_shape(["npm", "--version", "x"], ("*", "--version"))


# ---------------------------------------------------------------------------
# The systemd-run argv
# ---------------------------------------------------------------------------


class TestSandboxArgv:
    """What a build's transient unit is given, property by property."""

    def argv(self, spec: SandboxSpec | None = None, timeout: int = 1800) -> list[str]:
        return sandbox_prefix(
            spec or build_spec(),
            unit="noust-build-shop-example-com-0a1b2c3d",
            env_file=Path("/run/noust/sandbox/noust-build-shop-example-com-0a1b2c3d.env"),
            cwd=RELEASE,
            timeout=timeout,
        )

    def test_runs_as_a_transient_unit_that_waits_pipes_and_collects(self) -> None:
        argv = self.argv()

        assert argv[0] == "systemd-run"
        for flag in ("--wait", "--collect", "--quiet", "--pipe"):
            assert flag in argv
        assert "--unit=noust-build-shop-example-com-0a1b2c3d" in argv
        assert argv[-1] == "--"

    def test_every_level_one_property_is_there(self) -> None:
        props = properties(self.argv())

        for expected in (
            "User=noust-build",
            "Group=noust-build",
            f"WorkingDirectory={RELEASE}",
            "NoNewPrivileges=yes",
            "PrivateTmp=yes",
            "PrivateDevices=yes",
            "ProtectSystem=strict",
            "ProtectHome=yes",
            "ProtectKernelTunables=yes",
            "ProtectKernelModules=yes",
            "ProtectKernelLogs=yes",
            "ProtectControlGroups=yes",
            "ProtectClock=yes",
            "ProtectHostname=yes",
            "RestrictSUIDSGID=yes",
            "RestrictNamespaces=yes",
            "LockPersonality=yes",
            "RestrictRealtime=yes",
            "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK",
            "CapabilityBoundingSet=",
            "MemoryMax=2048M",
            "CPUQuota=200%",
            f"RuntimeMaxSec={1800 + SANDBOX_TIMEOUT_MARGIN}",
        ):
            assert expected in props, expected

    def test_the_other_applications_are_not_there_and_its_own_paths_are_bound_back(self) -> None:
        props = properties(self.argv())

        assert f"TemporaryFileSystem={APPS}:ro" in props
        assert f"TemporaryFileSystem={CACHE_ROOT}:ro" in props
        assert f"BindPaths={RELEASE}" in props
        assert f"BindPaths={CACHE}" in props
        assert f"BindReadOnlyPaths=-{SHARED}" in props
        assert "InaccessiblePaths=-/etc/noust" in props
        assert "InaccessiblePaths=-/var/lib/noust" in props

    def test_a_writable_path_outside_the_hidden_ones_is_opened_in_protect_system(self) -> None:
        props = properties(self.argv(build_spec(hidden_paths=(), writable_paths=(RELEASE,))))

        assert f"ReadWritePaths={RELEASE}" in props
        assert not any(p.startswith("BindPaths=") for p in props)

    def test_no_network_is_a_private_network(self) -> None:
        assert "PrivateNetwork=yes" in properties(self.argv(build_spec(network="none")))
        assert "PrivateNetwork=yes" not in properties(self.argv())

    def test_the_environment_arrives_in_a_file_never_on_the_command_line(self) -> None:
        argv = self.argv()
        props = properties(argv)

        assert props[-2] == (
            "EnvironmentFile=/run/noust/sandbox/noust-build-shop-example-com-0a1b2c3d.env"
        )
        assert not any(arg in ("-E", "--setenv") or arg.startswith("--setenv") for arg in argv)
        assert not any(p.startswith("Environment=") for p in props)

    def test_an_applications_env_file_is_read_by_systemd_and_may_be_missing(self) -> None:
        props = properties(self.argv(build_spec(env_files=(SHARED / ".env",))))

        assert f"EnvironmentFile=-{SHARED}/.env" in props
        # The runner's own file comes after, so what it composed wins.
        assert props.index(f"EnvironmentFile=-{SHARED}/.env") < props.index(
            "EnvironmentFile=/run/noust/sandbox/noust-build-shop-example-com-0a1b2c3d.env"
        )

    def test_the_result_marker_is_written_by_root_after_the_unit_stops(self) -> None:
        stop_post = properties(self.argv())[-1]

        assert stop_post.startswith("ExecStopPost=+/")
        assert stop_post.endswith(
            f"{paths.SANDBOX_RUNTIME_DIR}/noust-build-shop-example-com-0a1b2c3d.result"
            ".${SERVICE_RESULT}.${EXIT_CODE}.${EXIT_STATUS}"
        )

    def test_the_compatibility_mode_uses_a_terminal(self) -> None:
        argv = self.argv(build_spec(pty=True))

        assert "--pty" in argv
        assert "--pipe" not in argv

    def test_a_migration_keeps_only_what_the_application_unit_has(self) -> None:
        spec = SandboxSpec(user="www-data", group="www-data", strict=False, name="shop")
        props = properties(self.argv(spec))

        assert "User=www-data" in props
        assert "NoNewPrivileges=yes" in props
        assert "PrivateTmp=yes" in props
        assert "ProtectSystem=strict" not in props

    def test_home_can_stay_visible_for_an_application_that_lives_there(self) -> None:
        props = properties(self.argv(build_spec(protect_home=False)))

        assert "ProtectHome=yes" not in props
        assert "ProtectSystem=strict" in props

    def test_dollar_is_doubled_and_percent_is_not(self) -> None:
        assert escape_systemd_argv(["echo", "$HOME", "${X}", "%h", "a$$b", ";", "-x"]) == [
            "echo",
            "$$HOME",
            "$${X}",
            "%h",
            "a$$$$b",
            ";",
            "-x",
        ]

    def test_unit_names_are_prefixed_and_distinct(self) -> None:
        first = sandbox_unit_name(build_spec())
        second = sandbox_unit_name(build_spec())

        assert first.startswith("noust-build-shop-example-com-")
        assert first != second

    @pytest.mark.parametrize(
        "path",
        ["relative/path", "/with space", "/a:b", "/a%h", "/a$HOME", "/a\nb", '/a"b'],
    )
    def test_a_path_systemd_would_misread_is_refused(self, path: str) -> None:
        with pytest.raises(ValueError, match="path"):
            build_spec(writable_paths=(Path(path),))

    def test_an_account_or_network_it_cannot_express_is_refused(self) -> None:
        with pytest.raises(ValueError, match="account"):
            SandboxSpec(user="root; rm")
        with pytest.raises(ValueError, match="network"):
            SandboxSpec(user="noust-build", network="host")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="positive"):
            SandboxSpec(user="noust-build", memory_max_mb=0)


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


class TestSandboxEnvironment:
    """A build sees the allow-list and what it was given, nothing else."""

    PARENT = {
        "PATH": "/root/.nvm/versions/node/v22/bin:/usr/bin",
        "HOME": "/root",
        "LANG": "en_US.UTF-8",
        "LC_ALL": "C.UTF-8",
        "https_proxy": "http://proxy:3128",
        "NOUST_SECRET_SENTINEL": "x",
        "SSH_AUTH_SOCK": "/tmp/ssh-agent",
        "AWS_SECRET_ACCESS_KEY": "hunter2",
    }

    def test_only_the_allow_list_crosses(self) -> None:
        env = sandbox_environment(build_spec(), {"DATABASE_URL": "postgres://x"}, self.PARENT)

        assert env["LANG"] == "en_US.UTF-8"
        assert env["LC_ALL"] == "C.UTF-8"
        assert env["https_proxy"] == "http://proxy:3128"
        assert env["DATABASE_URL"] == "postgres://x"
        for leaked in ("NOUST_SECRET_SENTINEL", "SSH_AUTH_SOCK", "AWS_SECRET_ACCESS_KEY", "HOME"):
            assert leaked not in env

    def test_the_path_is_fixed_and_system_wide(self) -> None:
        env = sandbox_environment(build_spec(), None, self.PARENT)

        assert env["PATH"] == runner_module.SANDBOX_PATH
        assert "/root" not in env["PATH"]

    def test_the_compatibility_mode_quiets_the_terminal(self) -> None:
        env = sandbox_environment(build_spec(pty=True), None, {})

        assert env["CI"] == "1"
        assert env["NO_COLOR"] == "1"
        assert env["TERM"] == "dumb"

    def test_a_clean_environment_keeps_the_account_and_drops_the_rest(self) -> None:
        env = clean_environment(self.PARENT)

        assert env["HOME"] == "/root"
        assert env["PATH"] == self.PARENT["PATH"]
        assert "NOUST_SECRET_SENTINEL" not in env
        assert "AWS_SECRET_ACCESS_KEY" not in env

    def test_the_file_quotes_every_value_so_it_comes_back_byte_for_byte(self) -> None:
        text = environment_file_text(
            {"A": 'a"b\\c`d$e', "MULTI": "one\ntwo", "EMPTY": "", "SPACE": "  x  "}
        )

        assert 'A="a\\"b\\\\c\\`d\\$e"' in text
        assert 'MULTI="one\ntwo"' in text
        assert 'EMPTY=""' in text
        assert 'SPACE="  x  "' in text
        assert _parse_systemd_env(text) == {
            "A": 'a"b\\c`d$e',
            "MULTI": "one\ntwo",
            "EMPTY": "",
            "SPACE": "  x  ",
        }

    def test_the_file_refuses_what_it_cannot_carry(self) -> None:
        with pytest.raises(ValueError, match="variable name"):
            environment_file_text({"BAD-NAME": "x"})
        with pytest.raises(ValueError, match="NUL"):
            environment_file_text({"X": "a\x00b"})


def _parse_systemd_env(text: str) -> dict[str, str]:
    """
    Parse an environment file the way systemd parses double-quoted values.

    Args:
        text: The file.

    Returns:
        Name to value.
    """
    out: dict[str, str] = {}
    index = 0
    while index < len(text):
        if text[index] == "\n":
            index += 1
            continue
        equals = text.index("=", index)
        name = text[index:equals]
        assert text[equals + 1] == '"'
        index = equals + 2
        value = []
        while text[index] != '"':
            char = text[index]
            if char == "\\":
                nxt = text[index + 1]
                value.append(nxt if nxt in '"\\`$' else "\\" + nxt)
                index += 2
                continue
            value.append(char)
            index += 1
        out[name] = "".join(value)
        index += 1
    return out


# ---------------------------------------------------------------------------
# Result: a deadline and a memory kill say so
# ---------------------------------------------------------------------------


class TestSandboxResult:
    """systemd-run exits 1 for both a deadline and the memory limit; the marker tells them apart."""

    def interpret(
        self, marker: tuple[str, str, str] | None, *, exit_code: int = 1, timed_out: bool = False
    ) -> CommandResult:
        raw = CommandResult(argv=("systemd-run",), exit_code=exit_code, timed_out=timed_out)
        return interpret_sandbox_result(
            raw, marker, build_spec(), argv=("npm", "ci"), unit="noust-build-x-1", timeout=1800
        )

    def test_success(self) -> None:
        result = self.interpret(("success", "exited", "0"), exit_code=0)

        assert result.success
        assert result.argv == ("npm", "ci")
        assert result.sandbox_unit == "noust-build-x-1"

    def test_an_exit_code_is_the_commands(self) -> None:
        assert self.interpret(("exit-code", "exited", "3"), exit_code=3).exit_code == 3

    def test_a_memory_kill_says_so_and_reads_as_the_oom_killer(self) -> None:
        result = self.interpret(("oom-kill", "killed", "KILL"))

        assert result.exit_code == 137
        assert result.out_of_memory
        assert "ran out of memory" in result.stderr
        assert "MemoryMax=2048M" in result.stderr

    def test_the_units_own_deadline_says_so(self) -> None:
        result = self.interpret(("timeout", "killed", "TERM"))

        assert result.timed_out
        assert result.exit_code == EXIT_TIMEOUT
        assert "RuntimeMaxSec" in result.stderr

    def test_the_runners_deadline_says_so(self) -> None:
        result = self.interpret(None, exit_code=EXIT_TIMEOUT, timed_out=True)

        assert result.timed_out
        assert "1800s deadline" in result.stderr
        assert "noust-build-x-1" in result.stderr

    def test_a_signal_is_128_plus_its_number(self) -> None:
        assert self.interpret(("signal", "killed", "TERM")).exit_code == 143

    def test_a_program_the_sandbox_cannot_execute_reads_as_not_found(self) -> None:
        result = self.interpret(("exit-code", "exited", "203"), exit_code=203)

        assert result.exit_code == EXIT_NOT_FOUND
        assert "/root" in result.stderr

    def test_a_unit_that_never_started_keeps_systemd_runs_words(self) -> None:
        raw = CommandResult(argv=("systemd-run",), exit_code=1, stderr="Failed to start transient")
        result = interpret_sandbox_result(
            raw, None, build_spec(), argv=("npm", "ci"), unit="u", timeout=10
        )

        assert result.exit_code == 1
        assert result.stderr.startswith("Failed to start transient")
        assert "could not be started" in result.stderr

    def test_the_marker_is_read_and_removed(self, tmp_path: Path) -> None:
        (tmp_path / "noust-build-x-1.result.oom-kill.killed.KILL").touch()
        (tmp_path / "noust-build-other-2.result.success.exited.0").touch()

        assert read_sandbox_marker("noust-build-x-1", tmp_path) == ("oom-kill", "killed", "KILL")
        assert read_sandbox_marker("noust-build-x-1", tmp_path) is None
        assert (tmp_path / "noust-build-other-2.result.success.exited.0").exists()


# ---------------------------------------------------------------------------
# The fake and the rehearsal
# ---------------------------------------------------------------------------


class TestFakeRunnerSandbox:
    """The fake records what the real runner would execute."""

    def test_records_the_wrapped_argv_and_the_command_inside(self) -> None:
        fake = FakeRunner()

        result = fake.run(["npm", "ci"], cwd=RELEASE, sandbox=build_spec(), timeout=1800)

        assert fake.calls[0][0] == "systemd-run"
        assert fake.calls[0][-2:] == ("npm", "ci")
        assert fake.sandboxed == [(("npm", "ci"), build_spec())]
        assert result.argv == ("npm", "ci")
        assert result.sandbox_unit is not None
        assert result.sandbox_unit.startswith("noust-build-shop-example-com-")

    def test_records_the_whole_environment_the_file_would_hold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOUST_SECRET_SENTINEL", "x")
        fake = FakeRunner()

        fake.stream(["npm", "ci"], on_line=lambda _l: None, env={"A": "1"}, sandbox=build_spec())

        env = fake.envs[0]
        assert env is not None
        assert env["A"] == "1"
        assert "NOUST_SECRET_SENTINEL" not in env

    def test_a_script_matches_the_command_inside_the_sandbox(self) -> None:
        fake = FakeRunner().script(["npm", "ci"], exit_code=1, stderr="EUSAGE")

        result = fake.run(["npm", "ci"], sandbox=build_spec())

        assert result.exit_code == 1
        assert result.stderr == "EUSAGE"

    def test_a_dollar_reaches_the_recorded_argv_escaped(self) -> None:
        fake = FakeRunner()

        fake.run(["node", "-e", "console.log($HOME)"], sandbox=build_spec())

        assert fake.calls[0][-1] == "console.log($$HOME)"

    def test_user_and_sandbox_are_exclusive(self) -> None:
        with pytest.raises(ValueError, match="exclusive"):
            FakeRunner().run(["npm", "ci"], user="www-data", sandbox=build_spec())

    def test_a_clean_environment_is_recorded(self) -> None:
        fake = FakeRunner()

        fake.run(["npm", "ci"], clean_env=True)
        fake.run(["npm", "ci"])

        assert fake.clean_envs == [True, False]


class TestDryRunSandbox:
    """A sandboxed command is a build, and a rehearsal never runs one."""

    def test_a_sandboxed_command_is_skipped_even_when_it_looks_like_a_probe(self) -> None:
        inner = FakeRunner()
        dry = DryRunRunner(inner)

        dry.run(["cat", "/etc/hostname"], sandbox=build_spec())

        assert inner.calls == []
        assert dry.skipped == [("cat", "/etc/hostname")]

    def test_a_probe_keeps_its_clean_environment(self) -> None:
        inner = FakeRunner()

        DryRunRunner(inner).run(["systemctl", "is-active", "x"], clean_env=True)

        assert inner.clean_envs == [True]


# ---------------------------------------------------------------------------
# Execution listeners
# ---------------------------------------------------------------------------


class TestExecutionListeners:
    """Every execution reaches the listeners, whichever runner ran it."""

    @pytest.fixture
    def heard(self) -> list[CommandExecution]:
        events: list[CommandExecution] = []
        add_execution_listener(events.append)
        yield events
        remove_execution_listener(events.append)

    def test_a_command_is_reported_with_how_it_ended(self, heard: list[CommandExecution]) -> None:
        FakeRunner().script(["systemctl", "restart"], exit_code=3).run(
            ["systemctl", "restart", "x"], cwd=Path("/srv")
        )

        assert len(heard) == 1
        event = heard[0]
        assert event.argv == ("systemctl", "restart", "x")
        assert event.cwd == Path("/srv")
        assert event.exit_code == 3
        assert event.read_only is False
        assert event.duration is not None

    def test_a_probe_is_marked_as_one(self, heard: list[CommandExecution]) -> None:
        FakeRunner().run(["systemctl", "is-active", "x"])

        assert heard[0].read_only is True

    def test_a_sandboxed_build_names_its_unit_and_account(
        self, heard: list[CommandExecution]
    ) -> None:
        FakeRunner().run(["npm", "ci"], sandbox=build_spec())

        assert heard[0].argv == ("npm", "ci")
        assert heard[0].user == "noust-build"
        assert heard[0].sandbox_unit is not None
        assert heard[0].read_only is False

    def test_secrets_are_redacted_for_the_listener(self, heard: list[CommandExecution]) -> None:
        FakeRunner().run(["mysql", "-psecret"], secrets=["secret"])

        assert heard[0].argv == ("mysql", "-p***")

    def test_a_rehearsal_reports_nothing_it_did_not_run(
        self, heard: list[CommandExecution]
    ) -> None:
        DryRunRunner(FakeRunner()).run(["systemctl", "restart", "x"])

        assert heard == []

    def test_a_failing_listener_is_logged_and_the_command_stands(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        def broken(_event: CommandExecution) -> None:
            raise RuntimeError("ledger is down")

        add_execution_listener(broken)
        try:
            with caplog.at_level(logging.ERROR, logger="noust.core.runner"):
                result = FakeRunner().run(["true"])
        finally:
            remove_execution_listener(broken)

        assert result.success
        assert "execution listener failed" in caplog.text

    def test_the_class_exposes_the_same_registry(self, heard: list[CommandExecution]) -> None:
        other: list[CommandExecution] = []
        CommandRunner.add_execution_listener(other.append)
        try:
            FakeRunner().run(["true"])
        finally:
            CommandRunner.remove_execution_listener(other.append)

        assert len(other) == 1
        assert len(heard) == 1

    def test_a_listener_is_added_once(self, heard: list[CommandExecution]) -> None:
        add_execution_listener(heard.append)

        FakeRunner().run(["true"])

        assert len(heard) == 1


# ---------------------------------------------------------------------------
# The real runner
# ---------------------------------------------------------------------------

FAKE_SYSTEMD_RUN = r'''#!{python}
"""A stand-in for systemd-run --wait --pipe: runs the command, writes the marker."""
import json, os, re, subprocess, sys, time
from pathlib import Path

args = sys.argv[1:]
split = args.index("--")
options, command = args[:split], [a.replace("$$", "$") for a in args[split + 1:]]
props = [o[len("--property="):] for o in options if o.startswith("--property=")]
unit = next(o.split("=", 1)[1] for o in options if o.startswith("--unit="))
state = Path(os.environ.get("FAKE_STATE") or "{state}")
env = {{}}
for prop in props:
    if prop.startswith("EnvironmentFile=") and not prop.startswith("EnvironmentFile=-"):
        path = Path(prop.split("=", 1)[1])
        mode = oct(path.stat().st_mode & 0o777)
        text = path.read_text()
        for match in re.finditer(r'^([A-Za-z_][A-Za-z0-9_]*)="((?:[^"\\]|\\.)*)"$', text, re.M):
            env[match.group(1)] = re.sub(r'\\(.)', r'\1', match.group(2))
        (state / f"{{unit}}.seen.json").write_text(json.dumps({{"env": env, "mode": mode, "path": str(path)}}))
marker = next(p for p in props if p.startswith("ExecStopPost="))
target = marker.split(" ", 1)[1]
(state / f"{{unit}}.pid").write_text(str(os.getpid()))
if env.get("FAKE_RESULT") == "oom-kill":
    Path(target.replace("${{SERVICE_RESULT}}", "oom-kill").replace("${{EXIT_CODE}}", "killed").replace("${{EXIT_STATUS}}", "KILL")).touch()
    sys.exit(1)
code = subprocess.run(command, env=env).returncode
Path(target.replace("${{SERVICE_RESULT}}", "success" if code == 0 else "exit-code").replace("${{EXIT_CODE}}", "exited").replace("${{EXIT_STATUS}}", str(code))).touch()
sys.exit(code)
'''

FAKE_SYSTEMCTL = r"""#!{python}
import sys
from pathlib import Path
with open("{state}/systemctl.log", "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\n")
"""


@pytest.fixture
def fake_systemd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Put a stand-in systemd-run and systemctl first on every PATH the runner uses.

    Returns:
        The directory where they leave what they saw.
    """
    bin_dir = tmp_path / "bin"
    state = tmp_path / "state"
    runtime = tmp_path / "run" / "sandbox"
    bin_dir.mkdir()
    state.mkdir()
    for name, template in (("systemd-run", FAKE_SYSTEMD_RUN), ("systemctl", FAKE_SYSTEMCTL)):
        script = bin_dir / name
        script.write_text(template.format(python=sys.executable, state=state))
        script.chmod(0o755)
    sandbox_path = f"{bin_dir}:{runner_module.SANDBOX_PATH}"
    monkeypatch.setattr(runner_module, "SANDBOX_PATH", sandbox_path)
    monkeypatch.setattr(paths, "SANDBOX_RUNTIME_DIR", runtime)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
    return state


def plain_spec(**overrides: object) -> SandboxSpec:
    """A spec with nothing path-specific, for the stand-in."""
    values: dict[str, object] = {"user": "noust-build", "name": "unit-test"}
    values.update(overrides)
    return SandboxSpec(**values)  # type: ignore[arg-type]


@pytest.mark.allow_subprocess
class TestRealRunnerSandbox:
    """The real runner against a stand-in systemd-run."""

    def seen(self, state: Path) -> dict[str, object]:
        [seen] = list(state.glob("*.seen.json"))
        return json.loads(seen.read_text())

    def test_output_exit_code_and_unit_come_back(self, fake_systemd: Path) -> None:
        result = SubprocessRunner().run(
            ["sh", "-c", "echo out; echo err >&2; exit 3"], sandbox=plain_spec()
        )

        assert result.exit_code == 3
        assert result.stdout.strip() == "out"
        assert result.stderr.strip() == "err"
        assert result.argv == ("sh", "-c", "echo out; echo err >&2; exit 3")
        assert result.sandbox_unit is not None
        assert result.sandbox_result == "exit-code"

    def test_a_dollar_reaches_the_program_literally(self, fake_systemd: Path) -> None:
        result = SubprocessRunner().run(["echo", "$HOME", "${X}", "%h"], sandbox=plain_spec())

        assert result.stdout.strip() == "$HOME ${X} %h"

    def test_the_environment_file_is_private_holds_only_the_allow_list_and_is_removed(
        self, fake_systemd: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOUST_SECRET_SENTINEL", "leak")
        monkeypatch.setenv("LANG", "C.UTF-8")

        result = SubprocessRunner().run(
            ["env"], sandbox=plain_spec(), env={"DATABASE_URL": "postgres://u:p@h/d"}
        )

        seen = self.seen(fake_systemd)
        assert seen["mode"] == "0o600"
        env = seen["env"]
        assert isinstance(env, dict)
        assert env["DATABASE_URL"] == "postgres://u:p@h/d"
        assert env["LANG"] == "C.UTF-8"
        assert "NOUST_SECRET_SENTINEL" not in env
        assert "NOUST_SECRET_SENTINEL" not in result.stdout
        assert not Path(str(seen["path"])).exists()
        assert stat.S_IMODE(paths.SANDBOX_RUNTIME_DIR.stat().st_mode) == 0o700

    def test_a_memory_kill_is_an_out_of_memory_result(self, fake_systemd: Path) -> None:
        result = SubprocessRunner().stream(
            ["true"],
            on_line=lambda _l: None,
            sandbox=plain_spec(memory_max_mb=64),
            env={"FAKE_RESULT": "oom-kill"},
        )

        assert result.exit_code == 137
        assert result.out_of_memory
        assert "MemoryMax=64M" in result.stderr
        assert not list(paths.SANDBOX_RUNTIME_DIR.glob("*"))

    def test_streaming_delivers_lines_as_they_come(self, fake_systemd: Path) -> None:
        lines: list[str] = []

        result = SubprocessRunner().stream(
            ["sh", "-c", "echo one; echo two >&2"], on_line=lines.append, sandbox=plain_spec()
        )

        assert result.success
        assert sorted(lines) == ["one", "two"]

    def test_the_deadline_stops_the_unit_and_says_so(self, fake_systemd: Path) -> None:
        started = time.monotonic()

        result = SubprocessRunner().stream(
            ["sleep", "30"], on_line=lambda _l: None, sandbox=plain_spec(), timeout=1
        )

        assert time.monotonic() - started < 15
        assert result.timed_out
        assert "deadline" in result.stderr
        log = (fake_systemd / "systemctl.log").read_text()
        assert f"stop {result.sandbox_unit}.service" in log

    def test_a_cancellation_stops_the_unit(self, fake_systemd: Path) -> None:
        event = threading.Event()
        threading.Timer(0.5, event.set).start()

        with cancellable(event), pytest.raises(CommandCancelled):
            SubprocessRunner().stream(
                ["sleep", "30"], on_line=lambda _l: None, sandbox=plain_spec(), timeout=60
            )

        log = (fake_systemd / "systemctl.log").read_text()
        assert log.startswith("stop noust-build-unit-test-")

    def test_a_program_outside_the_sandboxs_path_is_not_found(self, fake_systemd: Path) -> None:
        result = SubprocessRunner().run(["no-such-build-tool-xyz"], sandbox=plain_spec())

        assert result.exit_code == EXIT_NOT_FOUND
        assert "sandbox's PATH" in result.stderr

    def test_an_environment_it_cannot_write_fails_before_anything_runs(
        self, fake_systemd: Path
    ) -> None:
        result = SubprocessRunner().run(["true"], sandbox=plain_spec(), env={"BAD-NAME": "x"})

        assert result.exit_code == EXIT_SANDBOX_FAILED
        assert "could not be prepared" in result.stderr
        assert not list(fake_systemd.glob("*.seen.json"))

    def test_a_listener_hears_the_command_not_the_wrapping(self, fake_systemd: Path) -> None:
        heard: list[CommandExecution] = []
        add_execution_listener(heard.append)
        try:
            SubprocessRunner().run(["true"], sandbox=plain_spec())
        finally:
            remove_execution_listener(heard.append)

        assert [event.argv for event in heard] == [("true",)]
        assert heard[0].sandbox_unit is not None


@pytest.mark.allow_subprocess
class TestRealRunnerCleanEnvironment:
    """A build that is not sandboxed still does not inherit Noust's secrets."""

    def test_a_clean_environment_drops_what_is_not_allowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOUST_SECRET_SENTINEL", "leak")
        monkeypatch.setenv("LANG", "C.UTF-8")

        result = SubprocessRunner().run(["env"], clean_env=True, env={"GIVEN": "1"})

        assert "NOUST_SECRET_SENTINEL" not in result.stdout
        assert "GIVEN=1" in result.stdout
        assert "LANG=C.UTF-8" in result.stdout

    def test_without_it_the_environment_is_inherited_as_before(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOUST_TEST_INHERITED", "yes")

        assert "NOUST_TEST_INHERITED=yes" in SubprocessRunner().run(["env"]).stdout

    def test_a_listener_hears_a_real_command(self) -> None:
        heard: list[CommandExecution] = []
        add_execution_listener(heard.append)
        try:
            SubprocessRunner().run(["true"], cwd=Path("/"))
        finally:
            remove_execution_listener(heard.append)

        assert heard[0].argv == ("true",)
        assert heard[0].exit_code == 0
        assert heard[0].cwd == Path("/")
