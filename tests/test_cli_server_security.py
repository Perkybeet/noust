# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``noust server security``: the command line client of ServerSecurity.

Parity with the console is by construction (both call ``preflight`` then
``execute``); what is pinned here is what an operator at a terminal sees: a
refused change says why and how, exits 1 and changes nothing; an applied one
says when it undoes itself and which command keeps it; ``confirm`` without a
change id picks the only pending one; the checks exit 1 while a critical
finding is open; and ``--dry-run`` writes nothing and arms nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from noust.cli.app import cli as root_cli
from noust.cli.commands import server_security
from noust.core.fs import DryRunFileSystem, set_fs
from noust.core.runner import DryRunRunner
from noust.managers.server import host as host_module
from noust.managers.server import security_checks
from noust.managers.server.security import ServerSecurity
from noust.managers.server.security_sshd import DROPIN
from tests.server_security_support import ED_FP, NOW, FakeHost, FakeSshd, accepted


@pytest.fixture(autouse=True)
def fresh() -> None:
    host_module.reset_platform_cache()
    security_checks.forget_report()
    yield
    host_module.reset_platform_cache()
    security_checks.forget_report()


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    host = FakeHost(tmp_path / "root")
    host.write("/etc/os-release", 'ID=debian\nVERSION_ID="12"\n')
    runner = FakeSshd(host)
    runner.only_knows("apt-get", "ufw", "systemctl", "sshd")
    runner.script(["ufw", "status", "verbose"], stdout="Status: inactive\n")
    runner.script(["ufw", "show", "added"], stdout="")
    state: dict[str, Any] = {"runner": runner}

    def build(*, actor: str, on_output: Any = None) -> ServerSecurity:
        return ServerSecurity(
            actor=actor,
            runner=state["runner"],
            host=host.paths,
            changes=tmp_path / "changes",
            on_output=on_output,
            console_port=8080,
            clock=lambda: NOW,
            python="/usr/bin/python3",
        )

    monkeypatch.setattr(server_security, "ServerSecurity", build)
    monkeypatch.setattr("noust.cli.commands.server.check_root", lambda: True)
    return type("Machine", (), {"host": host, "runner": runner, "state": state})


def invoke(*args: str) -> Result:
    return CliRunner().invoke(root_cli, ["server", "security", *args])


class TestSsh:
    def test_a_refused_fix_says_how_and_changes_nothing(self, machine):
        result = invoke("ssh", "harden", "disable-passwords", "-y")

        assert result.exit_code == 1
        assert "ssh-keygen -t ed25519" in result.output
        assert machine.host.read(DROPIN) is None

    def test_an_applied_fix_says_how_to_keep_it(self, machine):
        machine.runner.script(["journalctl"], stdout=accepted("root", ED_FP, at=NOW - 3600) + "\n")

        result = invoke("ssh", "harden", "disable-passwords", "-y")

        assert result.exit_code == 0, result.output
        assert "noust server security confirm" in result.output
        assert "PasswordAuthentication no" in (machine.host.read(DROPIN) or "")

    def test_confirm_picks_the_only_pending_change_and_needs_a_new_login(self, machine):
        machine.runner.script(["journalctl"], stdout=accepted("root", ED_FP, at=NOW - 3600) + "\n")
        invoke("ssh", "harden", "verbose-logging", "-y")

        early = invoke("confirm")
        machine.runner.script(
            ["journalctl"], stdout=accepted("root", ED_FP, at=NOW + 30, port=60001) + "\n"
        )
        later = invoke("confirm")

        assert early.exit_code == 1 and "No new SSH login" in early.output
        assert later.exit_code == 0, later.output
        assert "port 60001" in later.output

    def test_the_plan_shows_before_and_after_as_json(self, machine):
        result = invoke("ssh", "plan", "sensible-defaults", "--json")

        plan = json.loads(result.output)
        assert {"directive": "X11Forwarding", "before": "yes", "after": "no"} in plan["changes"]

    def test_remove_key_refuses_the_central_key(self, machine):
        from tests.server_security_support import CENTRAL_FP, CENTRAL_LINE, ED_KEY

        machine.host.write("/root/.ssh/authorized_keys", ED_KEY + "\n" + CENTRAL_LINE + "\n")

        result = invoke("ssh", "remove-key", CENTRAL_FP, "--user", "root")

        assert result.exit_code == 1
        assert "noust fleet deauthorize" in result.output


class TestChecks:
    def test_checks_exit_1_while_a_critical_finding_is_open(self, machine):
        machine.runner.script(
            ["ss", "-Hltnup"],
            stdout='tcp LISTEN 0 244 0.0.0.0:6379 0.0.0.0:* users:(("redis-server",pid=7,fd=5))\n',
        )

        result = invoke("checks", "--quick", "--json")

        assert result.exit_code == 1
        report = json.loads(result.output)
        public = next(check for check in report["checks"] if check["id"] == "fw.public_listener")
        assert public["status"] == "fail"

    def test_an_accepted_risk_is_listed(self, machine):
        result = invoke(
            "accept", "fw.inactive", "--why", "The provider filters this network", "--days", "30"
        )
        listed = invoke("risks", "--json")

        assert result.exit_code == 0, result.output
        assert [risk["check_id"] for risk in json.loads(listed.output)] == ["fw.inactive"]

    def test_accept_needs_an_end(self, machine):
        result = invoke("accept", "fw.inactive", "--why", "The provider filters this network")

        assert result.exit_code == 2

    def test_status_summarises(self, machine):
        result = invoke("status", "--json")

        data = json.loads(result.output)
        assert data["counts"]["warning"] >= 1
        assert data["pending"] == []


class TestFirewall:
    def test_closing_ssh_is_refused(self, machine):
        result = invoke("firewall", "deny", "22")

        assert result.exit_code == 1
        assert "SSH listens there" in result.output


class TestDryRun:
    def test_a_rehearsal_writes_nothing_and_arms_nothing(self, machine):
        machine.runner.script(["journalctl"], stdout=accepted("root", ED_FP, at=NOW - 3600) + "\n")
        rehearsal = DryRunRunner(machine.runner)
        machine.state["runner"] = rehearsal
        set_fs(DryRunFileSystem())

        security = server_security.ServerSecurity(actor="cli:test")
        change = security.execute("ssh.fix", {"fix": "disable-passwords"})

        assert change["change"]["status"] == "pending"
        assert machine.host.read(DROPIN) is None
        skipped = [argv[0] for argv in rehearsal.skipped]
        assert "systemd-run" in skipped and "systemctl" in skipped
        assert not machine.runner.ran("systemd-run")
        assert not (machine.host.root.parent / "changes").exists()
