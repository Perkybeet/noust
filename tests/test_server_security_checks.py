# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the hardening checks, accepted risks and fail2ban.

The checks are one list shared by the console, ``noust health`` and the ENS
check, so what is pinned is the contract: every id of the catalog comes back,
a probe that fails is ``unknown`` with its output (never a pass, never an
exception that empties the list), an automatic fix whose guard cannot be
proven now is offered as guided with the reason, and an accepted risk shows
as accepted until it ends - then the finding is back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import SecurityError, ValidationError
from noust.core.fs import RecordingFileSystem
from noust.core.store import NoustStore
from noust.managers.server import host as host_module
from noust.managers.server import security_checks
from noust.managers.server.errors import ConfirmationRequiredError
from noust.managers.server.security_catalog import CATALOG
from noust.managers.server.security_checks import HardeningChecks, run_checks, summarize
from noust.managers.server.security_fail2ban import Fail2ban, parse_jail, parse_jail_list
from noust.managers.server.security_pending import FAIL2BAN_JAIL
from noust.managers.server.security_probe import SecurityProbe
from noust.managers.server.security_risks import AcceptedRisks
from tests.server_security_support import ED_FP, NOW, FakeHost, FakeSshd, accepted

NOW_DT = datetime.fromtimestamp(NOW, tz=timezone.utc)

F2B_STATUS = "Status\n|- Number of jail:\t2\n`- Jail list:\tnginx-http-auth, sshd\n"
F2B_SSHD = (
    "Status for the jail: sshd\n"
    "|- Filter\n"
    "|  |- Currently failed:\t1\n"
    "|  |- Total failed:\t23\n"
    "|  `- Journal matches:\t_SYSTEMD_UNIT=sshd.service + _COMM=sshd\n"
    "`- Actions\n"
    "   |- Currently banned:\t2\n"
    "   |- Total banned:\t5\n"
    "   `- Banned IP list:\t192.0.2.1 198.51.100.2\n"
)


@pytest.fixture(autouse=True)
def fresh_platform() -> None:
    host_module.reset_platform_cache()
    security_checks.forget_report()
    yield
    host_module.reset_platform_cache()
    security_checks.forget_report()


@pytest.fixture
def host(tmp_path: Path) -> FakeHost:
    fake = FakeHost(tmp_path / "root")
    fake.write("/etc/os-release", 'ID=debian\nVERSION_ID="12"\nPRETTY_NAME="Debian GNU/Linux 12"\n')
    return fake


@pytest.fixture
def runner(host: FakeHost) -> FakeSshd:
    fake = FakeSshd(host)
    fake.only_knows("apt-get", "fail2ban-client", "ufw", "systemctl", "sshd")
    fake.script(["ufw", "status", "verbose"], stdout="Status: inactive\n")
    fake.script(["ufw", "show", "added"], stdout="")
    fake.script(["systemctl", "is-system-running"], stdout="running\n")
    fake.script(["systemctl", "is-enabled"], stdout="enabled\n")
    fake.script(["systemctl", "is-active"], stdout="inactive\ninactive\n", exit_code=3)
    fake.script(["fail2ban-client", "ping"], stdout="Server replied: pong\n")
    fake.script(["fail2ban-client", "status"], stdout=F2B_STATUS)
    fake.script(["fail2ban-client", "status", "sshd"], stdout=F2B_SSHD)
    fake.script(["fail2ban-client", "status", "nginx-http-auth"], stdout="Status\n")
    return fake


@pytest.fixture
def store(tmp_path: Path) -> NoustStore:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db", fs=RecordingFileSystem())
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def risks(store: NoustStore) -> AcceptedRisks:
    return AcceptedRisks(store, clock=lambda: NOW_DT)


def _probe(runner: FakeSshd, host: FakeHost) -> SecurityProbe:
    return SecurityProbe(runner=runner, host=host.paths, clock=lambda: NOW)


def _run(runner: FakeSshd, host: FakeHost, risks: AcceptedRisks, **kwargs: Any):
    return HardeningChecks(_probe(runner, host), risks=risks, console_port=8080).run(**kwargs)


# Accepted risks ---------------------------------------------------------------


class TestAcceptedRisks:
    def test_an_acceptance_records_who_why_and_until(self, risks):
        risk = risks.accept(
            "ssh.password_auth",
            reason="Client staging box, passwords until they get keys",
            by="token:ops",
            expires_at=NOW_DT + timedelta(days=30),
        )

        assert risk.accepted_by == "token:ops"
        assert risks.active()["ssh.password_auth"].id == risk.id

    @pytest.mark.parametrize(
        ("check_id", "reason", "days", "field"),
        [
            ("ssh.nope", "a good enough reason", 30, "check_id"),
            ("ssh.password_auth", "short", 30, "reason"),
            ("ssh.password_auth", "a good enough reason", -1, "expires_at"),
            ("ssh.password_auth", "a good enough reason", 400, "expires_at"),
        ],
    )
    def test_what_cannot_be_accepted(self, risks, check_id, reason, days, field):
        with pytest.raises(ValidationError) as caught:
            risks.accept(check_id, reason=reason, by="me", expires_at=NOW_DT + timedelta(days=days))

        assert caught.value.field == field

    def test_accepting_again_supersedes_and_keeps_the_history(self, risks):
        first = risks.accept(
            "fw.inactive",
            reason="behind the provider firewall",
            by="a",
            expires_at=NOW_DT + timedelta(days=5),
        )
        second = risks.accept(
            "fw.inactive",
            reason="behind the provider firewall, renewed",
            by="b",
            expires_at=NOW_DT + timedelta(days=50),
        )

        history = risks.history("fw.inactive")
        assert [risk.id for risk in history] == [second.id, first.id]
        assert history[1].revoked_by == "b"
        assert risks.active()["fw.inactive"].id == second.id

    def test_an_expired_acceptance_no_longer_holds(self, store):
        early = AcceptedRisks(store, clock=lambda: NOW_DT)
        early.accept(
            "fw.inactive", reason="for a week only", by="a", expires_at=NOW_DT + timedelta(days=7)
        )
        later = AcceptedRisks(store, clock=lambda: NOW_DT + timedelta(days=8))

        assert later.active() == {}

    def test_revoking_brings_the_finding_back(self, risks):
        risks.accept(
            "fw.inactive", reason="for a week only", by="a", expires_at=NOW_DT + timedelta(days=7)
        )

        revoked = risks.revoke("fw.inactive", by="b")

        assert revoked is not None and revoked.revoked_by == "b"
        assert risks.active() == {}
        assert risks.revoke("fw.inactive", by="b") is None


# The checks ---------------------------------------------------------------------


class TestChecks:
    def test_every_catalog_id_comes_back_in_order(self, runner, host, risks, monkeypatch):
        _stub_host_managers(monkeypatch)

        report = _run(runner, host, risks)

        assert [check.id for check in report.checks] == list(CATALOG)
        assert len(CATALOG) == 31

    def test_debian_defaults_show_the_expected_findings(self, runner, host, risks):
        report = _run(runner, host, risks, host_checks=False)

        assert report.get("ssh.password_auth").status == "warn"
        assert report.get("ssh.root_login").status == "warn"
        assert report.get("ssh.root_password").status == "pass"
        assert report.get("ssh.defaults").status == "warn"
        assert report.get("ssh.loglevel").status == "warn"
        assert report.get("fw.inactive").status in ("warn", "fail")
        assert report.get("f2b.missing").status == "pass"
        assert report.get("f2b.no_sshd_jail").status == "pass"
        evidence = report.get("ssh.password_auth").evidence
        assert "passwordauthentication yes" in evidence

    def test_a_fix_whose_proof_is_missing_is_offered_guided_with_the_reason(
        self, runner, host, risks
    ):
        report = _run(runner, host, risks, host_checks=False)

        fix = report.get("ssh.password_auth").fix
        assert fix is not None and fix.kind == "guided"
        assert "30 days" in fix.blocked
        assert any("ssh-keygen" in step for step in fix.steps)

    def test_with_proof_the_same_fix_is_automatic_and_reverts(self, runner, host, risks):
        runner.script(["journalctl"], stdout=accepted("root", ED_FP, at=NOW - 3600) + "\n")

        fix = _run(runner, host, risks, host_checks=False).get("ssh.password_auth").fix

        assert fix is not None
        assert (fix.kind, fix.action, fix.reverts) == ("automatic", "ssh:disable-passwords", True)
        assert "Proven" in fix.summary

    def test_an_sshd_that_cannot_answer_makes_ssh_checks_unknown_not_passed(
        self, runner, host, risks
    ):
        def broken(*args, **kwargs):
            raise AssertionError("not reached")

        from noust.core.runner import CommandResult

        original = runner.run

        def run(argv, **kwargs):
            if list(argv)[:2] == ["sshd", "-T"]:
                return CommandResult(
                    tuple(argv), 255, "", "sshd: no hostkeys available -- exiting."
                )
            return original(argv, **kwargs)

        runner.run = run  # type: ignore[method-assign]

        report = _run(runner, host, risks, host_checks=False)

        for check in report.checks:
            if check.id.startswith("ssh."):
                assert check.status == "unknown"
                assert check.evidence == ("sshd: no hostkeys available -- exiting.",)

    def test_an_accepted_risk_shows_accepted_until_it_ends(self, runner, host, store):
        risks = AcceptedRisks(store, clock=lambda: NOW_DT)
        risks.accept(
            "ssh.password_auth",
            reason="Client staging box, passwords until they get keys",
            by="me",
            expires_at=NOW_DT + timedelta(days=3),
        )

        now = _run(runner, host, risks, host_checks=False)
        later = _run(
            runner,
            host,
            AcceptedRisks(store, clock=lambda: NOW_DT + timedelta(days=4)),
            host_checks=False,
        )

        assert now.get("ssh.password_auth").status == "accepted"
        assert now.get("ssh.password_auth").accepted.accepted_by == "me"
        assert now.counts()["accepted"] == 1
        assert later.get("ssh.password_auth").status == "warn"

    def test_a_publicly_reachable_database_is_critical(self, runner, host, risks):
        runner.script(
            ["ss", "-Hltnup"],
            stdout='tcp LISTEN 0 244 0.0.0.0:5432 0.0.0.0:* users:(("postgres",pid=77,fd=5))\n',
        )

        report = _run(runner, host, risks, host_checks=False)

        check = report.get("fw.public_listener")
        assert check.status == "fail"
        assert check.evidence == ("0.0.0.0:5432/tcp postgres (PostgreSQL): no firewall",)
        assert report.get("fw.inactive").severity == "critical"

    def test_a_console_on_the_network_without_tls_is_critical(self, runner, host, risks):
        runner.script(
            ["ss", "-Hltnup"],
            stdout='tcp LISTEN 0 2048 0.0.0.0:8080 0.0.0.0:* users:(("noust",pid=80,fd=7))\n',
        )
        runner.script(
            ["systemctl", "show", "noust-web.service"],
            stdout="ExecStart={ path=/usr/bin/noust ; argv[]=/usr/bin/noust web start --host 0.0.0.0 }\n",
        )

        assert (
            _run(runner, host, risks, host_checks=False).get("fw.console_public").status == "fail"
        )

    def test_a_central_key_in_root_is_reported(self, runner, host, risks):
        from tests.server_security_support import CENTRAL_LINE, ED_KEY

        host.write("/root/.ssh/authorized_keys", ED_KEY + "\n" + CENTRAL_LINE + "\n")

        check = _run(runner, host, risks, host_checks=False).get("noust.fleet_key_root")

        assert check.status == "warn"
        assert check.evidence == (
            "noust-central:hub-1: SHA256:4yDNqv1O3P66iRait+oMWrVGjJ40Le05sg2tu2RmSj0",
        )

    def test_a_central_key_in_root_comes_with_the_exact_line_to_remove_it(
        self, runner, host, risks
    ):
        from tests.server_security_support import CENTRAL_KEY_BARE, CENTRAL_LINE

        # Any comment: a 3.0 line named its central otherwise than 3.1 does,
        # and 'migrate-tunnel' alone left it here.
        line = CENTRAL_LINE.replace("noust-central:hub-1", "noust-central@arennalabs.com")
        host.write("/root/.ssh/authorized_keys", line + "\n")

        check = _run(runner, host, risks, host_checks=False).get("noust.fleet_key_root")

        assert check.status == "warn"
        blob = CENTRAL_KEY_BARE.split()[1]
        assert f"sed -i '\\#{blob}#d' /root/.ssh/authorized_keys" in check.fix.steps
        assert any("--replace-root-key" in step for step in check.fix.steps)

    def test_a_line_with_noust_s_forwarding_prefix_is_a_central_key(self, runner, host, risks):
        from tests.server_security_support import CENTRAL_KEY_BARE

        # Neither the comment nor the permitlisten marker: the options' prefix.
        host.write(
            "/root/.ssh/authorized_keys",
            f'restrict,port-forwarding,permitopen="127.0.0.1:8080" {CENTRAL_KEY_BARE} x\n',
        )

        check = _run(runner, host, risks, host_checks=False).get("noust.fleet_key_root")

        assert check.status == "warn"

    def test_a_manager_that_fails_leaves_its_checks_unknown_with_its_words(
        self, runner, host, risks, monkeypatch
    ):
        _stub_host_managers(
            monkeypatch,
            pending_error=SecurityError("apt-get -s failed", output="E: Could not get lock"),
        )

        report = _run(runner, host, risks)

        check = report.get("upd.security_pending")
        assert check.status == "unknown"
        assert "E: Could not get lock" in check.reason

    def test_pending_security_updates_point_at_the_updates_action(
        self, runner, host, risks, monkeypatch
    ):
        _stub_host_managers(monkeypatch, security=2)

        check = _run(runner, host, risks).get("upd.security_pending")

        assert check.status == "warn"
        assert check.fix.kind == "action"
        assert check.fix.endpoint == "POST /api/server/updates/apply"

    def test_the_last_report_is_remembered_for_health(self, runner, host, risks):
        report = run_checks(_probe(runner, host), risks=risks, host_checks=False, console_port=8080)

        summary = summarize(security_checks.cached_report())

        assert security_checks.cached_report() is report
        assert "SSH accepts passwords" in summary.warnings
        assert summarize(None).checked_at is None


# fail2ban -------------------------------------------------------------------------


class TestFail2ban:
    def test_the_status_tree_is_read(self):
        assert parse_jail_list(F2B_STATUS) == ["nginx-http-auth", "sshd"]
        jail = parse_jail("sshd", F2B_SSHD)
        assert (jail.currently_banned, jail.total_failed, jail.banned) == (
            2,
            23,
            ("192.0.2.1", "198.51.100.2"),
        )
        assert jail.reads.startswith("_SYSTEMD_UNIT=sshd.service")

    def test_unban_names_the_jail_and_validates_the_address(self, runner, host):
        fail2ban = Fail2ban(_probe(runner, host))

        result = fail2ban.unban("192.0.2.1")

        assert result == {"address": "192.0.2.1", "jails": ["sshd"]}
        assert runner.ran("fail2ban-client", "set", "sshd", "unbanip", "192.0.2.1")
        with pytest.raises(ValidationError):
            fail2ban.unban("192.0.2.1; reboot")
        with pytest.raises(ValidationError):
            fail2ban.unban("192.0.2.1", jail="nope")

    def test_install_on_debian_ignores_whoever_is_connected(self, runner, host):
        runner.only_knows("apt-get", "systemctl", "sshd")
        runner.script(
            ["ss", "-Htnp"],
            stdout='0 0 10.0.0.5:22 198.51.100.7:51234 users:(("sshd",pid=1,fd=4))\n',
        )

        result = Fail2ban(_probe(runner, host)).install(ignore=["203.0.113.50", "127.0.0.1"])

        assert runner.ran("apt-get", "install", "-y", "fail2ban", "python3-systemd")
        jail = host.read(FAIL2BAN_JAIL) or ""
        assert "ignoreip = 127.0.0.1/8 ::1 198.51.100.7 203.0.113.50" in jail
        assert "backend = systemd" in jail and "port = 22" in jail
        assert "banaction = iptables-multiport" in jail
        assert result["ignored"] == ["198.51.100.7", "203.0.113.50"]
        steps = [call for call in runner.calls if call[0] in ("fail2ban-client", "systemctl")]
        assert ("fail2ban-client", "-t") in steps
        assert ("systemctl", "enable", "--now", "fail2ban") in steps

    def test_a_jail_fail2ban_refuses_is_put_back(self, runner, host):
        runner.only_knows("apt-get", "systemctl", "sshd")
        host.write(FAIL2BAN_JAIL, "# Generated by Noust. old\n")
        runner.script(["fail2ban-client", "-t"], stderr="ERROR  No file(s) found", exit_code=255)

        with pytest.raises(SecurityError) as caught:
            Fail2ban(_probe(runner, host)).install()

        assert caught.value.output == "ERROR  No file(s) found"
        assert host.read(FAIL2BAN_JAIL) == "# Generated by Noust. old\n"

    def test_epel_is_a_separate_yes(self, runner, host):
        host.write(
            "/etc/os-release", 'ID="almalinux"\nID_LIKE="rhel centos fedora"\nVERSION_ID="9.4"\n'
        )
        runner.only_knows("dnf", "systemctl", "sshd")

        with pytest.raises(ConfirmationRequiredError) as caught:
            Fail2ban(_probe(runner, host)).install()

        assert caught.value.required == {"epel": True, "packages": ["epel-release", "fail2ban"]}
        assert not runner.ran("dnf", "install")

        Fail2ban(_probe(runner, host)).install(epel=True)

        installs = [call for call in runner.calls if call[:2] == ("dnf", "install")]
        assert installs == [
            ("dnf", "install", "-y", "epel-release"),
            ("dnf", "install", "-y", "fail2ban"),
        ]

    def test_rhel_itself_is_guided(self, runner, host):
        host.write("/etc/os-release", 'ID="rhel"\nVERSION_ID="9.4"\n')
        runner.only_knows("dnf", "systemctl")

        state = Fail2ban(_probe(runner, host)).state()

        assert not state.install_supported
        assert "codeready-builder" in state.install_hint


# Helpers ---------------------------------------------------------------------------


@dataclass
class _Package:
    name: str
    security: bool = True
    kernel: bool = False
    severity: str | None = None
    installed: str = "1"
    candidate: str = "2"


@dataclass
class _Pending:
    packages: list[Any] = field(default_factory=list)
    broken: bool = False
    lists_age_seconds: int | None = 3600
    notes: list[str] = field(default_factory=list)


def _stub_host_managers(
    monkeypatch: pytest.MonkeyPatch, *, security: int = 0, pending_error: Exception | None = None
) -> None:
    """Stand in for B3a's managers, which have tests of their own."""
    from types import SimpleNamespace

    from noust.managers.server import clock, storage, swap, updates

    class Updates:
        def __init__(self, **kwargs: Any) -> None:
            self.platform = SimpleNamespace(
                updates_supported=True, why_updates_unsupported=lambda: ""
            )
            self.backend = SimpleNamespace(
                auto_status=lambda: SimpleNamespace(
                    supported=True,
                    installed=True,
                    enabled=True,
                    mechanism="unattended-upgrades",
                    detail="",
                )
            )

        def pending(self) -> _Pending:
            if pending_error is not None:
                raise pending_error
            return _Pending([_Package(f"pkg{i}") for i in range(security)])

        def restart_probe(self) -> Any:
            return SimpleNamespace(
                reboot=SimpleNamespace(required=False, since=None, reasons=(), packages=()),
                services=(),
            )

    monkeypatch.setattr(updates, "UpdatesManager", Updates)
    monkeypatch.setattr(
        clock,
        "ClockManager",
        lambda **_: SimpleNamespace(
            status=lambda: SimpleNamespace(synchronized=True, ntp_enabled=True)
        ),
    )
    monkeypatch.setattr(
        swap,
        "SwapManager",
        lambda **_: SimpleNamespace(
            status=lambda: SimpleNamespace(recommended=False, memory_bytes=2**32, total_bytes=0)
        ),
    )
    monkeypatch.setattr(storage, "StorageManager", lambda **_: SimpleNamespace(mounts=lambda: []))
    monkeypatch.setattr("noust.managers.server.power.unenabled_app_units", lambda runner: [])


# noust health ---------------------------------------------------------------------


class TestHealth:
    def test_the_console_reads_the_last_checks_and_never_probes(
        self, runner, host, risks, monkeypatch
    ):
        from noust.managers import health

        monkeypatch.setattr(health, "run_checks", lambda **_: pytest.fail("probed on a request"))
        warnings: list[str] = []

        before = health._check_hardening("cached", warnings)
        run_checks(_probe(runner, host), risks=risks, host_checks=False, console_port=8080)
        after = health._check_hardening("cached", warnings)

        assert before.status == "info" and "Not checked yet" in before.value
        assert after.status == "warning"
        assert "Security: SSH accepts passwords" in warnings

    def test_findings_are_warnings_so_the_exit_code_keeps_meaning_down(
        self, runner, host, risks, monkeypatch
    ):
        from noust.managers import health

        report = run_checks(_probe(runner, host), risks=risks, host_checks=False, console_port=8080)
        monkeypatch.setattr(health, "run_checks", lambda **_: report)
        warnings: list[str] = []

        check = health._check_hardening("live", warnings)

        assert check.name == "Security hardening"
        assert all(line.startswith("Security") for line in warnings)
        assert health._check_hardening("off", warnings) is None
