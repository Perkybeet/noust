"""
Tests for ``noust fleet`` and ``noust node``: the shells over the fleet modules.

The modules are tested on their own; these pin what the commands add - root
checks, the join code read from stdin and never echoed, the audit entries,
the console kept on loopback, and the ``--json`` shapes.
"""

from __future__ import annotations

import functools
import io
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
from noust.fleet.authorize import TUNNEL_USER
from noust.fleet.policy import FleetAccess, current_access
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
    """The fleet's own entries in the audit log (the log may hold others, such as its chain's start)."""
    path = state_dir / "web-audit.log"
    if not path.exists():
        return []
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    return [e for e in entries if str(e.get("action", "")).startswith("fleet.")]


class TestAuthorizeCommand:
    @pytest.fixture
    def node(self, tmp_path: Path, state_dir: Path, monkeypatch: pytest.MonkeyPatch):
        from noust.cli.web_state import token_manager

        node = Node(tmp_path, token_manager())
        node.console_outcome = fleet_cli.ConsoleOutcome(port=8080)
        monkeypatch.setattr(fleet_cli, "check_root", lambda: True)
        monkeypatch.setattr(
            fleet_cli, "ensure_console_on_loopback", lambda verbose, dry, **kw: node.console_outcome
        )
        monkeypatch.setattr("noust.core.store.get_store", lambda *a, **k: node.store)
        monkeypatch.setattr(authorize_module, "issued_here", lambda key, central: None)
        monkeypatch.setattr(
            authorize_module,
            "authorize",
            functools.partial(
                authorize_module.authorize,
                runner=node.runner,
                host_key_file=node.host_key_file,
                sshd_config=node.sshd_config,
                sshd_dropin_dir=node.dropin_dir,
                tunnel_keys_dir=node.tunnel_keys_dir,
                passwd=node.passwd,
                store=node.store,
            ),
        )
        monkeypatch.setattr(
            authorize_module,
            "deauthorize",
            functools.partial(
                authorize_module.deauthorize,
                runner=node.runner,
                sshd_config=node.sshd_config,
                tunnel_keys_dir=node.tunnel_keys_dir,
                passwd=node.passwd,
            ),
        )
        yield node
        NoustStore.reset_instance()

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
        payload = json.loads(result.stdout)
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
            "access",
            "tunnel_account_created",
            "sshd_policy",
            "moved_from",
            "warnings",
            "console_token",
            "console_adopted_pid",
        }
        assert payload["join_code"].startswith("noust-join:v1:")
        assert (payload["central"], payload["console_port"]) == ("nas", 8080)
        assert payload["ssh_user"] == TUNNEL_USER
        assert payload["access"] == {"level": "admin", "host_access": False}
        assert payload["console_token"] is None

    def test_a_second_authorize_asks_before_replacing(self, node, state_dir):
        args = ["fleet", "authorize", "--central-key", CENTRAL_KEY, "--name", "nas"]
        CliRunner().invoke(root_cli, args)

        declined = CliRunner().invoke(root_cli, args, input="n\n")
        assert isinstance(declined.exception, NodeError)
        accepted = CliRunner().invoke(root_cli, [*args, "--yes", "--json"])
        assert json.loads(accepted.stdout)["replaced_tokens"] == ["fleet-nas"]
        results = [e["result"] for e in _audit(state_dir)]
        assert results == ["success", "failure", "success"]

    def test_deauthorize(self, node, state_dir):
        CliRunner().invoke(
            root_cli, ["fleet", "authorize", "--central-key", CENTRAL_KEY, "--name", "nas"]
        )

        result = CliRunner().invoke(root_cli, ["fleet", "deauthorize", "--name", "nas", "--json"])

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == {
            "central": "nas",
            "authorized_keys": [str(node.keys_file())],
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

    def test_progress_goes_to_stderr_and_the_code_alone_to_stdout(self, node, state_dir):
        result = CliRunner().invoke(
            root_cli, ["fleet", "authorize", "--central-key", CENTRAL_KEY, "--name", "nas"]
        )

        assert result.exit_code == 0, result.output
        assert f"[1/6] Checking sshd for {TUNNEL_USER}..." in result.stderr
        assert "[6/6] Issuing the token fleet-nas..." in result.stderr
        assert "[1/6]" not in result.stdout
        codes = [line for line in result.stdout.splitlines() if line.startswith("noust-join:")]
        assert len(codes) == 1

    def test_no_banner_and_no_new_console_token(self, node, state_dir):
        result = CliRunner().invoke(
            root_cli, ["fleet", "authorize", "--central-key", CENTRAL_KEY, "--name", "nas"]
        )

        assert result.exit_code == 0, result.output
        assert "Access Token" not in result.output
        assert "Console access token" not in result.output
        assert "admin; host access off" in result.stdout

    def test_a_first_console_token_is_shown_after_the_join_code(self, node, state_dir):
        node.console_outcome = fleet_cli.ConsoleOutcome(
            port=8080, token="noust_first", started=True
        )

        result = CliRunner().invoke(
            root_cli, ["fleet", "authorize", "--central-key", CENTRAL_KEY, "--name", "nas"]
        )

        lines = result.stdout.splitlines()
        code_at = next(i for i, line in enumerate(lines) if line.startswith("noust-join:"))
        assert lines.index("noust_first") > code_at

    def test_root_needs_i_understand(self, node, state_dir):
        args = ["fleet", "authorize", "--central-key", CENTRAL_KEY, "--name", "nas"]

        refused = CliRunner().invoke(root_cli, [*args, "--ssh-user", "root"])
        assert isinstance(refused.exception, NodeError)
        assert "--i-understand" in refused.exception.message

        accepted = CliRunner().invoke(root_cli, [*args, "--ssh-user", "root", "--i-understand"])
        assert accepted.exit_code == 0, accepted.output
        assert "Unix sockets as root" in accepted.output

    def test_access_is_stored_and_stated(self, node, state_dir):
        result = CliRunner().invoke(
            root_cli,
            [
                "fleet",
                "authorize",
                "--central-key",
                CENTRAL_KEY,
                "--name",
                "nas",
                "--access",
                "read",
                "--json",
            ],
        )

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["access"] == {"level": "read", "host_access": False}
        assert current_access(node.store) == FleetAccess("read")
        assert "access read" in _audit(state_dir)[-1]["detail"]

    def test_host_access_alone_keeps_the_level(self, node, state_dir):
        node.store.set_fleet_access("deploy", False)
        CliRunner().invoke(
            root_cli,
            [
                "fleet",
                "authorize",
                "--central-key",
                CENTRAL_KEY,
                "--name",
                "nas",
                "--allow-host-access",
            ],
        )
        assert current_access(node.store) == FleetAccess("deploy", True)

    def test_the_issuing_central_is_refused_unless_allowed(self, node, state_dir, monkeypatch):
        monkeypatch.setattr(authorize_module, "issued_here", lambda key, central: "it is 'nas'")
        args = ["fleet", "authorize", "--central-key", CENTRAL_KEY, "--name", "nas"]

        refused = CliRunner().invoke(root_cli, args)
        assert isinstance(refused.exception, NodeError)
        assert "central that issued this key" in refused.exception.message
        assert _audit(state_dir)[-1]["result"] == "failure"

        allowed = CliRunner().invoke(root_cli, [*args, "--allow-self"])
        assert allowed.exit_code == 0, allowed.output


class TestSteps:
    def test_plain_lines_off_a_terminal(self):
        stream = io.StringIO()
        steps = fleet_cli.Steps(stream, animate=False)

        steps(1, 2, "One")
        steps.note("detail")
        steps(2, 2, "Two")
        steps.finish()

        assert stream.getvalue() == "[1/2] One...\n      detail\n[2/2] Two...\n"

    def test_a_terminal_gets_a_spinner_ended_by_the_outcome(self):
        stream = io.StringIO()
        steps = fleet_cli.Steps(stream, animate=True)

        steps(1, 2, "One")
        with steps.paused():
            stream.write("question?\n")
        steps(2, 2, "Two")
        steps.finish(ok=False)

        out = stream.getvalue()
        assert "question?\n" in out
        assert "[1/2] One... done\n" in out
        assert out.endswith("[2/2] Two... failed\n")

    def test_not_a_terminal_by_default_in_tests(self):
        assert fleet_cli.Steps(io.StringIO()).animate is False


class TestAccessCommand:
    @pytest.fixture
    def store(self, tmp_path: Path, state_dir: Path, monkeypatch: pytest.MonkeyPatch):
        from noust.core.fs import RecordingFileSystem

        NoustStore.reset_instance()
        store = NoustStore(tmp_path / "noust.db", fs=RecordingFileSystem())
        monkeypatch.setattr("noust.core.store.get_store", lambda *a, **k: store)
        monkeypatch.setattr(fleet_cli, "check_root", lambda: True)
        yield store
        NoustStore.reset_instance()

    def test_shows_the_default(self, store):
        result = CliRunner().invoke(root_cli, ["fleet", "access", "--json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == {"level": "admin", "host_access": False}

    def test_sets_audits_and_explains(self, store, state_dir):
        result = CliRunner().invoke(root_cli, ["fleet", "access", "--level", "deploy"])

        assert result.exit_code == 0, result.output
        assert "is now: deploy; host access off" in result.output
        assert current_access(store) == FleetAccess("deploy")
        entry = _audit(state_dir)[-1]
        assert (entry["action"], entry["detail"]) == (
            "fleet.access",
            "admin; host access off -> deploy; host access off",
        )
        assert store.get_fleet_access()["updated_by"].startswith("cli:")

    def test_host_access(self, store):
        CliRunner().invoke(root_cli, ["fleet", "access", "--host-access", "on"])
        assert current_access(store) == FleetAccess("admin", True)
        CliRunner().invoke(root_cli, ["fleet", "access", "--host-access", "off"])
        assert current_access(store) == FleetAccess("admin", False)

    def test_changing_it_needs_root(self, store, monkeypatch):
        monkeypatch.setattr(fleet_cli, "check_root", lambda: False)

        shown = CliRunner().invoke(root_cli, ["fleet", "access"])
        assert shown.exit_code == 0

        refused = CliRunner().invoke(root_cli, ["fleet", "access", "--level", "read"])
        assert isinstance(refused.exception, NodeError)
        assert current_access(store) == FleetAccess("admin")

    def test_a_rehearsal_changes_nothing(self, store):
        result = CliRunner().invoke(root_cli, ["--dry-run", "fleet", "access", "--level", "read"])
        assert result.exit_code == 0, result.output
        assert current_access(store) == FleetAccess("admin")


class TestConsoleOnLoopback:
    @pytest.fixture
    def web(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from noust.cli.commands import web

        unit = tmp_path / "noust-web.service"
        state: dict[str, Any] = {
            "active": False,
            "enabled_with": None,
            "daemon": None,
            "calls": [],
            "fail": None,
        }
        monkeypatch.setattr(web, "_service_unit_path", lambda: unit)
        monkeypatch.setattr(
            web,
            "_service_status",
            lambda verbose: {"active": state["active"]} if unit.exists() else None,
        )
        monkeypatch.setattr(web, "running_daemon", lambda: state["daemon"])

        def enable(port, verbose, *, dry_run=False, issue_token=False, port_just_freed=False):
            state["calls"].append(("enable", port, dry_run))
            state["port_just_freed"] = port_just_freed
            if state["fail"] is not None:
                raise state["fail"]
            state["enabled_with"] = port
            return None if dry_run else web.ConsoleService(unit=unit, token=None, port=port)

        monkeypatch.setattr(web, "enable_loopback_service", enable)
        monkeypatch.setattr(web, "stop_daemon", lambda pid: state["calls"].append(("stop", pid)))
        monkeypatch.setattr(
            web, "restart_daemon", lambda options: state["calls"].append(("restart", options))
        )
        monkeypatch.setattr(web, "_disable", lambda verbose: state["calls"].append(("disable",)))
        return unit, state

    def _unit(self, unit: Path, *flags: str) -> None:
        unit.write_text(
            "[Service]\nExecStart=/usr/bin/noust web start --under-systemd "
            + " ".join(flags)
            + "\n"
        )

    def _daemon(self, *argv: str, pid: int = 4321):
        from noust.cli.commands import web

        return web.DaemonConsole(pid=pid, options=web.options_from_argv(list(argv)))

    def test_a_running_loopback_console_is_left_alone(self, web):
        unit, state = web
        self._unit(unit, "--host", "127.0.0.1", "--port", "8123")
        state["active"] = True

        assert fleet_cli.ensure_console_on_loopback(False, False).port == 8123
        assert state["enabled_with"] is None

    def test_no_console_is_enabled_on_loopback(self, web):
        _, state = web

        outcome = fleet_cli.ensure_console_on_loopback(False, False)

        assert (outcome.port, outcome.started, outcome.token) == (8080, True, None)
        assert state["enabled_with"] == 8080

    def test_a_stopped_loopback_console_is_started_on_its_port(self, web):
        unit, state = web
        self._unit(unit, "--host", "127.0.0.1", "--port", "9000")

        assert fleet_cli.ensure_console_on_loopback(False, False).port == 9000
        assert state["enabled_with"] == 9000

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

    def test_a_console_that_fails_to_start_is_an_error(self, web):
        from noust.core.exceptions import ServiceError

        _, state = web
        state["fail"] = ServiceError("noust-web.service failed to start", details="journal")

        with pytest.raises(NodeError) as caught:
            fleet_cli.ensure_console_on_loopback(False, False)
        assert "noust-web.service failed to start" in caught.value.details

    def test_a_background_console_is_adopted_on_its_port_once_confirmed(self, web):
        _, state = web
        state["daemon"] = self._daemon("noust", "web", "start", "-d", "--port", "9100")
        asked: list[Any] = []

        outcome = fleet_cli.ensure_console_on_loopback(
            False, False, confirm_adopt=lambda d, changes: asked.append((d.pid, changes)) or True
        )

        assert asked == [(4321, [])]
        assert state["calls"] == [("stop", 4321), ("enable", 9100, False)]
        # The daemon's connections linger in TIME_WAIT; the port is the service's now.
        assert state["port_just_freed"] is True
        assert (outcome.port, outcome.adopted) == (9100, 4321)

    def test_declining_leaves_the_background_console_running(self, web):
        _, state = web
        state["daemon"] = self._daemon("noust", "web", "start", "-d")

        with pytest.raises(NodeError) as caught:
            fleet_cli.ensure_console_on_loopback(False, False)

        assert "nothing was changed" in caught.value.message
        assert "noust web stop" in caught.value.details
        assert state["calls"] == []

    def test_what_the_service_would_no_longer_do_is_said_before(self, web):
        _, state = web
        everywhere = ".".join(["0"] * 4)
        state["daemon"] = self._daemon(
            "noust",
            "web",
            "start",
            "-d",
            f"--host={everywhere}",
            "--self-signed",
            "--allow-ip",
            "10.0.0.0/8",
        )
        seen: list[list[str]] = []

        fleet_cli.ensure_console_on_loopback(
            False, False, confirm_adopt=lambda d, changes: seen.append(changes) or True
        )

        (changes,) = seen
        assert any(everywhere in change for change in changes)
        assert any("TLS" in change for change in changes)
        assert any("10.0.0.0/8" in change for change in changes)

    def test_a_service_that_does_not_come_up_puts_the_background_console_back(self, web):
        from noust.core.exceptions import ServiceError

        _, state = web
        state["daemon"] = self._daemon("noust", "web", "start", "-d", "--port", "9100")
        state["fail"] = ServiceError("noust-web.service failed to start", details="journal")

        with pytest.raises(NodeError) as caught:
            fleet_cli.ensure_console_on_loopback(False, False, confirm_adopt=lambda d, c: True)

        assert "started again as it was" in caught.value.message
        kinds = [call[0] for call in state["calls"]]
        assert kinds == ["stop", "enable", "disable", "restart"]
        assert state["calls"][-1][1].port == 9100

    def test_a_rehearsal_stops_nothing(self, web):
        _, state = web
        state["daemon"] = self._daemon("noust", "web", "start", "-d")

        outcome = fleet_cli.ensure_console_on_loopback(False, True, confirm_adopt=lambda d, c: True)

        assert outcome.adopted == 4321
        assert state["calls"] == []


class TestLoopbackService:
    """noust.cli.commands.web's side: no banner, and the operator's token survives."""

    @pytest.fixture
    def web(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state_dir: Path):
        from noust.cli.commands import web

        captured: dict[str, Any] = {}
        monkeypatch.setattr(web, "_check_dependencies", lambda: (True, [], []))
        monkeypatch.setattr(web, "_installed_web_unit", lambda: web.WEB_UNIT)
        monkeypatch.setattr(web, "_running_daemon_pid", lambda: None)
        monkeypatch.setattr(web, "_service_status", lambda verbose: None)
        monkeypatch.setattr(web, "_port_in_use", lambda host, port: False)

        def install(options, config, verbose, logger, *, dry_run=False, issue_token=True):
            captured.update(options=options, issue_token=issue_token)
            return web.ConsoleService(
                unit=tmp_path / "unit", token="new" if issue_token else None, port=options.port
            )

        monkeypatch.setattr(web, "_install_and_start", install)
        return web, captured

    def test_the_token_on_disk_is_kept(self, web):
        module, captured = web
        from noust.web.auth import SecurityConfig, TokenManager

        TokenManager(SecurityConfig()).generate_master_token()

        service = module.enable_loopback_service(8080, False)

        assert captured["issue_token"] is False
        assert service.token is None
        assert (captured["options"].host, captured["options"].port) == ("127.0.0.1", 8080)

    def test_a_first_token_is_issued_when_there_was_none(self, web):
        module, captured = web

        service = module.enable_loopback_service(8080, False)

        assert captured["issue_token"] is True
        assert service.token == "new"

    def test_a_taken_port_is_an_error_with_what_to_do(self, web, monkeypatch):
        module, _ = web
        from noust.core.exceptions import ServiceError

        monkeypatch.setattr(module, "_port_in_use", lambda host, port: port == 8080)

        with pytest.raises(ServiceError) as caught:
            module.enable_loopback_service(8080, False)
        assert "ss -ltnp" in caught.value.details
        assert "8081" in caught.value.details

    def test_a_port_just_freed_skips_the_taken_port_check(self, web, monkeypatch):
        module, captured = web
        monkeypatch.setattr(module, "_port_in_use", lambda host, port: True)

        module.enable_loopback_service(8080, False, port_just_freed=True)

        assert captured["options"].port == 8080

    def test_a_background_console_is_refused(self, web, monkeypatch):
        module, _ = web
        from noust.core.exceptions import ServiceError

        monkeypatch.setattr(module, "_running_daemon_pid", lambda: 77)
        with pytest.raises(ServiceError) as caught:
            module.enable_loopback_service(8080, False)
        assert "77" in caught.value.message


class TestDaemonConsole:
    def test_reads_how_it_was_started_from_proc(self, tmp_path, monkeypatch):
        from noust.cli.commands import web

        (tmp_path / "55").mkdir()
        (tmp_path / "55" / "cmdline").write_bytes(
            b"/usr/bin/python3\0/usr/bin/noust\0web\0start\0-d\0-p\0"
            b"9200\0--allow-ip\0"
            b"10.0.0.0/8\0--allow-ip=192.168.0.0/16\0"
        )
        monkeypatch.setattr(web, "_running_daemon_pid", lambda: 55)

        found = web.running_daemon(proc=tmp_path)

        assert found is not None
        assert (found.pid, found.options.port, found.options.host) == (55, 9200, "127.0.0.1")
        assert found.options.allow_ip == ("10.0.0.0/8", "192.168.0.0/16")

    def test_none_when_nothing_runs(self, monkeypatch):
        from noust.cli.commands import web

        monkeypatch.setattr(web, "_running_daemon_pid", lambda: None)
        assert web.running_daemon() is None

    def test_put_back_with_its_options_and_its_token(self, monkeypatch):
        from noust.cli.commands import web
        from noust.core.runner import FakeRunner

        runner = FakeRunner()
        monkeypatch.setattr(web, "_noust_executable", lambda: "/usr/bin/noust")

        web.restart_daemon(web.StartOptions(port=9200), runner=runner)

        assert runner.calls == [
            (
                "/usr/bin/noust",
                "web",
                "start",
                "--daemon",
                "--keep-token",
                "--host",
                "127.0.0.1",
                "--port",
                "9200",
            )
        ]

    def test_stopping_waits_for_it_to_be_gone(self, monkeypatch, tmp_path):
        from noust.cli.commands import web

        signals: list[tuple[int, int]] = []
        pid_file = tmp_path / "noust-web.pid"
        pid_file.write_text("12")
        monkeypatch.setattr(web, "get_pid_file", lambda: pid_file)

        def kill(pid: int, sig: int) -> None:
            signals.append((pid, sig))
            if sig == 0 and len(signals) > 2:
                raise ProcessLookupError

        monkeypatch.setattr(web.os, "kill", kill)
        monkeypatch.setattr(web.time, "sleep", lambda seconds: None)

        web.stop_daemon(12)

        assert signals[0] == (12, web.signal.SIGTERM)
        assert not pid_file.exists()

    def test_one_that_will_not_stop_is_an_error(self, monkeypatch):
        from noust.cli.commands import web
        from noust.core.exceptions import ServiceError

        monkeypatch.setattr(web.os, "kill", lambda pid, sig: None)
        with pytest.raises(ServiceError) as caught:
            web.stop_daemon(12, timeout=0)
        assert "kill 12" in caught.value.details

    def test_keep_token_needs_daemon(self):
        from noust.cli.commands import web

        result = CliRunner().invoke(web.cli, ["start", "--keep-token"])
        assert result.exit_code == 2
        assert "--keep-token" in result.output


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
            "access_level",
            "host_access",
            "access_read_at",
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
