# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the firewall: rules against real sockets, Docker's bypass, and the anti-lockout guard.

The outputs are what ufw, firewall-cmd, ss and docker print. What is pinned:
every listening port gets the verdict a stranger would see (Docker's ports are
reported as going around the firewall, whatever ufw says); nothing closes SSH
or a public console, nor deletes the last rule that opens them; turning ufw on
allows SSH before the default becomes deny; every change arms its revert
before its first command, is undone on the spot when a command fails, and is
undone by the timer when nobody confirms it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import SecurityError, ValidationError
from noust.managers.server.security_access import AccessGuardError
from noust.managers.server.security_firewall import (
    Firewall,
    RuleRequest,
    firewalld_rules,
    parse_docker_ps,
    parse_firewalld_zone,
    parse_ufw_added,
    parse_ufw_status,
)
from noust.managers.server.security_pending import ChangeLedger
from noust.managers.server.security_probe import SecurityProbe
from noust.managers.server.security_ssh import SshSecurity
from tests.server_security_support import CENTRAL_FP, NOW, FakeHost, FakeSshd, accepted

UFW_ACTIVE = """Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW IN    Anywhere
80,443/tcp                 ALLOW IN    Anywhere
22/tcp (v6)                ALLOW IN    Anywhere (v6)
80,443/tcp (v6)            ALLOW IN    Anywhere (v6)
"""

UFW_INACTIVE = "Status: inactive\n"

UFW_ADDED = """Added user rules (see 'ufw status' for running firewall):
ufw allow 22/tcp
ufw allow 80,443/tcp
ufw allow from 10.0.0.0/8 to any port 5432 proto tcp comment 'noust: office db'
ufw limit 2222/tcp
ufw allow out 53
ufw route allow in on eth1 out on eth0
"""

FIREWALLD_ZONE = """public (active)
  target: default
  icmp-block-inversion: no
  interfaces: eth0
  sources:
  services: cockpit dhcpv6-client ssh
  ports: 8080/tcp
  protocols:
  forward: yes
  masquerade: no
  forward-ports:
  source-ports:
  icmp-blocks:
  rich rules:
	rule family="ipv4" source address="10.0.0.0/8" port port="5432" protocol="tcp" accept
"""

SS_LISTEN = (
    'tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=900,fd=3))\n'
    'tcp LISTEN 0 128 [::]:22 [::]:* users:(("sshd",pid=900,fd=4))\n'
    'tcp LISTEN 0 511 0.0.0.0:443 0.0.0.0:* users:(("nginx",pid=10,fd=6))\n'
    'tcp LISTEN 0 244 0.0.0.0:5432 0.0.0.0:* users:(("postgres",pid=77,fd=5))\n'
    'tcp LISTEN 0 511 0.0.0.0:6379 0.0.0.0:* users:(("redis-server",pid=78,fd=6))\n'
    'tcp LISTEN 0 80 127.0.0.1:3306 0.0.0.0:* users:(("mariadbd",pid=79,fd=20))\n'
    'tcp LISTEN 0 2048 127.0.0.1:8080 0.0.0.0:* users:(("noust",pid=80,fd=7))\n'
)

DOCKER_PS = (
    '{"ID":"abc","Image":"postgres:16","Labels":"com.docker.compose.project=arenna,'
    'com.docker.compose.service=db","Names":"arenna_postgres",'
    '"Ports":"0.0.0.0:5435->5432/tcp, [::]:5435->5432/tcp","State":"running"}\n'
    '{"ID":"def","Image":"nginx","Labels":"","Names":"web","Ports":"127.0.0.1:8081->80/tcp, '
    '443/tcp","State":"running"}\n'
)

#: What ss and docker print for "every interface"; nothing here binds it.
ANY = "0.0.0.0"  # noqa: S104

SESSION = '0 0 10.0.0.5:22 198.51.100.7:51234 users:(("sshd",pid=1,fd=4))\n'


@pytest.fixture
def host(tmp_path: Path) -> FakeHost:
    return FakeHost(tmp_path / "root")


@pytest.fixture
def runner(host: FakeHost) -> FakeSshd:
    fake = FakeSshd(host)
    fake.only_knows("ufw", "sshd", "systemctl", "ss", "journalctl", "systemd-run")
    fake.script(["ufw", "status", "verbose"], stdout=UFW_ACTIVE)
    fake.script(["ufw", "show", "added"], stdout=UFW_ADDED)
    fake.script(["ss", "-Hltnup"], stdout=SS_LISTEN)
    fake.script(["ss", "-Htnp"], stdout=SESSION)
    return fake


@pytest.fixture
def ledger(tmp_path: Path, runner: FakeSshd, host: FakeHost) -> ChangeLedger:
    return ChangeLedger(
        tmp_path / "changes",
        runner=runner,
        host=host.paths,
        clock=lambda: NOW,
        python="/usr/bin/python3",
    )


def _firewall(runner: FakeSshd, host: FakeHost, ledger: ChangeLedger | None = None) -> Firewall:
    probe = SecurityProbe(runner=runner, host=host.paths, clock=lambda: NOW)
    return Firewall(probe, ledger, actor="tester", console_port=8080)


def _ufw_calls(runner: FakeSshd) -> list[tuple[str, ...]]:
    return [
        call
        for call in runner.calls
        if call[0] == "ufw" and call[1:3] not in (("status", "verbose"), ("show", "added"))
    ]


class TestParsing:
    def test_ufw_status_gives_state_and_default(self):
        assert parse_ufw_status(UFW_ACTIVE) == (True, "deny")
        assert parse_ufw_status(UFW_INACTIVE) == (False, "")

    def test_ufw_rules_are_read_from_show_added(self):
        rules = parse_ufw_added(UFW_ADDED)

        assert [(r.action, r.ports, r.proto, r.source) for r in rules] == [
            ("allow", ((22, 22),), "tcp", "any"),
            ("allow", ((80, 80), (443, 443)), "tcp", "any"),
            ("allow", ((5432, 5432),), "tcp", "10.0.0.0/8"),
            ("limit", ((2222, 2222),), "tcp", "any"),
        ]
        office = rules[2]
        assert office.noust and office.comment == "noust: office db"
        assert office.spec == "allow from 10.0.0.0/8 to any port 5432 proto tcp"
        assert len({rule.id for rule in rules}) == 4

    def test_an_application_profile_covers_its_ports(self):
        [rule] = parse_ufw_added("ufw allow OpenSSH\n")

        assert rule.covers(22) and rule.service == "OpenSSH"

    def test_an_unknown_profile_covers_nothing_the_guard_can_count_on(self):
        [rule] = parse_ufw_added("ufw allow 'My App'\n")

        assert not rule.known and not rule.covers(22)

    def test_firewalld_services_ports_and_rich_rules(self):
        rules = firewalld_rules(parse_firewalld_zone(FIREWALLD_ZONE))

        summary = {(rule.spec.split()[0], rule.source): rule for rule in rules}
        assert rules[0].service == "cockpit" and rules[0].covers(9090)
        assert any(rule.service == "ssh" and rule.covers(22) for rule in rules)
        assert any(rule.spec == "port 8080/tcp" and rule.covers(8080) for rule in rules)
        rich = summary[("rich", "10.0.0.0/8")]
        assert rich.covers(5432) and rich.action == "allow"

    def test_docker_ports_published_on_every_interface(self):
        ports = parse_docker_ps(DOCKER_PS)

        public = [port for port in ports if port.public]
        assert {(p.host_address, p.host_port, p.container_port, p.project) for p in public} == {
            (ANY, 5435, 5432, "arenna"),
            ("::", 5435, 5432, "arenna"),
        }
        assert any(port.host_address == "127.0.0.1" and not port.public for port in ports)


class TestExposures:
    def test_every_port_gets_the_verdict_a_stranger_sees(self, runner, host):
        runner.only_knows("ufw", "docker")
        runner.script(["docker", "ps"], stdout=DOCKER_PS)

        exposures, error = _firewall(runner, host).exposures()

        verdicts = {(e.address, e.port): (e.verdict, e.risky, e.baseline) for e in exposures}
        assert error == ""
        assert verdicts[(ANY, 22)] == ("open", "", True)
        assert verdicts[(ANY, 5432)] == ("open_to", "PostgreSQL", False)
        assert verdicts[(ANY, 6379)] == ("blocked", "Redis or Valkey", False)
        assert verdicts[("127.0.0.1", 3306)][0] == "local"
        assert verdicts[(ANY, 5435)] == ("docker_bypass", "PostgreSQL", False)

    def test_without_a_firewall_every_public_port_is_open(self, runner, host):
        runner.script(["ufw", "status", "verbose"], stdout=UFW_INACTIVE)

        exposures, _ = _firewall(runner, host).exposures()

        assert {e.port: e.verdict for e in exposures}[6379] == "no_firewall"


class TestGuard:
    def test_denying_ssh_is_refused_before_anything_runs(self, runner, host, ledger):
        with pytest.raises(AccessGuardError, match="SSH listens there"):
            _firewall(runner, host, ledger).add_rule(RuleRequest("deny", 22))

        assert not runner.ran("systemd-run") and _ufw_calls(runner) == []

    def test_denying_ssh_to_the_network_of_a_session_open_now_is_refused(
        self, runner, host, ledger
    ):
        with pytest.raises(AccessGuardError):
            _firewall(runner, host, ledger).add_rule(
                RuleRequest("deny", 22, source="198.51.100.0/24")
            )

    def test_denying_ssh_to_somebody_else_is_allowed(self, runner, host, ledger):
        change = _firewall(runner, host, ledger).add_rule(
            RuleRequest("deny", 22, source="192.0.2.0/24")
        )

        assert change.status == "pending"
        assert _ufw_calls(runner) == [
            (
                "ufw",
                "deny",
                "from",
                "192.0.2.0/24",
                "to",
                "any",
                "port",
                "22",
                "proto",
                "tcp",
                "comment",
                "noust:",
            )
        ]

    def test_the_last_rule_opening_ssh_is_not_deleted(self, runner, host, ledger):
        firewall = _firewall(runner, host, ledger)
        ssh_rule = next(rule for rule in firewall.state().rules if rule.spec == "allow 22/tcp")

        with pytest.raises(AccessGuardError, match="port 22"):
            firewall.delete_rule(ssh_rule.id)

    def test_a_second_rule_for_ssh_makes_the_first_deletable(self, runner, host, ledger):
        runner.script(["ufw", "show", "added"], stdout=UFW_ADDED + "ufw allow OpenSSH\n")
        firewall = _firewall(runner, host, ledger)
        ssh_rule = next(rule for rule in firewall.state().rules if rule.spec == "allow 22/tcp")

        change = firewall.delete_rule(ssh_rule.id)

        assert _ufw_calls(runner) == [("ufw", "delete", "allow", "22/tcp")]
        assert change.undo == [["ufw", "allow", "22/tcp"]]

    def test_a_noust_rule_is_put_back_with_its_comment(self, runner, host, ledger):
        firewall = _firewall(runner, host, ledger)
        office = next(rule for rule in firewall.state().rules if rule.noust)

        change = firewall.delete_rule(office.id)

        assert change.undo == [
            [
                "ufw",
                "allow",
                "from",
                "10.0.0.0/8",
                "to",
                "any",
                "port",
                "5432",
                "proto",
                "tcp",
                "comment",
                "noust: office db",
            ]
        ]

    def test_fields_that_are_not_what_a_rule_carries_are_refused(self, runner, host, ledger):
        firewall = _firewall(runner, host, ledger)

        with pytest.raises(ValidationError):
            firewall.add_rule(RuleRequest("allow", 70000))
        with pytest.raises(ValidationError):
            firewall.add_rule(RuleRequest("allow", 80, source="10.0.0.0/8; reboot"))
        with pytest.raises(ValidationError):
            firewall.add_rule(RuleRequest("limit", 80))


class TestEnable:
    def test_ssh_is_allowed_before_the_default_becomes_deny(self, runner, host, ledger):
        runner.script(["ufw", "status", "verbose"], stdout=UFW_INACTIVE)
        runner.script(["ufw", "show", "added"], stdout="Added user rules:\n(None)\n")
        runner.configured = {"port": "2222"}
        runner.script(
            ["ss", "-Hltnup"],
            stdout='tcp LISTEN 0 128 0.0.0.0:2222 0.0.0.0:* users:(("sshd",pid=900,fd=3))\n',
        )

        change = _firewall(runner, host, ledger).enable()

        calls = _ufw_calls(runner)
        assert calls == [
            (
                "ufw",
                "allow",
                "from",
                "any",
                "to",
                "any",
                "port",
                "2222",
                "proto",
                "tcp",
                "comment",
                "noust: SSH",
            ),
            ("ufw", "default", "deny", "incoming"),
            ("ufw", "default", "allow", "outgoing"),
            ("ufw", "--force", "enable"),
        ]
        timer = next(i for i, call in enumerate(runner.calls) if call[0] == "systemd-run")
        first_ufw = runner.calls.index(calls[0])
        assert timer < first_ufw
        assert change.undo[0] == ["ufw", "disable"]
        assert [
            "ufw",
            "delete",
            "allow",
            "from",
            "any",
            "to",
            "any",
            "port",
            "2222",
            "proto",
            "tcp",
        ] in change.undo

    def test_a_public_console_is_allowed_too(self, runner, host, ledger):
        runner.script(["ufw", "status", "verbose"], stdout=UFW_INACTIVE)
        runner.script(["ufw", "show", "added"], stdout="ufw allow 22/tcp\n")
        runner.script(
            ["ss", "-Hltnup"],
            stdout=SS_LISTEN.replace("127.0.0.1:8080", "0.0.0.0:8080"),
        )

        _firewall(runner, host, ledger).enable()

        assert _ufw_calls(runner)[0][:8] == (
            "ufw",
            "allow",
            "from",
            "any",
            "to",
            "any",
            "port",
            "8080",
        )
        assert _ufw_calls(runner)[0][-1] == "noust: the console"

    def test_enabling_what_is_on_is_refused(self, runner, host, ledger):
        with pytest.raises(SecurityError, match="already active"):
            _firewall(runner, host, ledger).enable()


class TestApply:
    def test_a_command_the_firewall_refuses_undoes_the_change(self, runner, host, ledger):
        runner.script(["ufw", "allow"], stderr="ERROR: Bad port", exit_code=1)

        with pytest.raises(SecurityError) as caught:
            _firewall(runner, host, ledger).add_rule(RuleRequest("allow", 8443))

        assert caught.value.output == "ERROR: Bad port"
        assert runner.ran("ufw", "delete", "allow", "from", "any")
        assert [change.status for change in ledger.changes()] == ["reverted"]

    def test_the_timer_undoes_a_rule_nobody_confirmed(self, runner, host, ledger):
        change = _firewall(runner, host, ledger).add_rule(RuleRequest("allow", 8443))

        ledger.revert(change.id, by="timer", expired=True)

        assert runner.calls[-1] == (
            "ufw",
            "delete",
            "allow",
            "from",
            "any",
            "to",
            "any",
            "port",
            "8443",
            "proto",
            "tcp",
        )
        assert ledger.load(change.id).status == "expired"

    def test_the_central_reconnecting_confirms_a_firewall_change(self, runner, host, ledger):
        change = _firewall(runner, host, ledger).add_rule(RuleRequest("allow", 8443))
        runner.script(
            ["journalctl"], stdout=accepted("noust-tunnel", CENTRAL_FP, at=NOW + 20) + "\n"
        )
        probe = SecurityProbe(runner=runner, host=host.paths, clock=lambda: NOW)

        confirmed = SshSecurity(probe, ledger, actor="tester").confirm(change.id)

        assert confirmed.status == "confirmed"


class TestFirewalld:
    @pytest.fixture
    def firewalld(self, runner: FakeSshd) -> FakeSshd:
        runner.only_knows("firewall-cmd")
        runner.script(["firewall-cmd", "--state"], stdout="running\n")
        runner.script(["firewall-cmd", "--get-default-zone"], stdout="public\n")
        runner.script(["firewall-cmd", "--zone=public", "--list-all"], stdout=FIREWALLD_ZONE)
        runner.script(
            ["firewall-cmd", "--permanent", "--zone=public", "--list-all"], stdout=FIREWALLD_ZONE
        )
        return runner

    def test_a_rule_is_runtime_until_confirmed_then_permanent(self, firewalld, host, ledger):
        change = _firewall(firewalld, host, ledger).add_rule(RuleRequest("allow", 8443))

        assert firewalld.ran("firewall-cmd", "--zone=public", "--add-port=8443/tcp")
        assert not firewalld.ran(
            "firewall-cmd", "--permanent", "--zone=public", "--add-port=8443/tcp"
        )
        assert change.commit == [
            ["firewall-cmd", "--permanent", "--zone=public", "--add-port=8443/tcp"]
        ]
        firewalld.script(["journalctl"], stdout=accepted("root", "SHA256:x", at=NOW + 5) + "\n")
        probe = SecurityProbe(runner=firewalld, host=host.paths, clock=lambda: NOW)

        SshSecurity(probe, ledger, actor="tester").confirm(change.id)

        assert firewalld.ran("firewall-cmd", "--permanent", "--zone=public", "--add-port=8443/tcp")

    def test_a_restricted_rule_is_a_rich_rule(self, firewalld, host, ledger):
        _firewall(firewalld, host, ledger).add_rule(
            RuleRequest("allow", 5432, source="203.0.113.0/24")
        )

        assert firewalld.ran(
            "firewall-cmd",
            "--zone=public",
            '--add-rich-rule=rule family="ipv4" source address="203.0.113.0/24" '
            'port port="5432" protocol="tcp" accept',
        )

    def test_the_ssh_service_is_protected_like_a_port(self, firewalld, host, ledger):
        firewall = _firewall(firewalld, host, ledger)
        ssh = next(rule for rule in firewall.state().rules if rule.service == "ssh")

        with pytest.raises(AccessGuardError):
            firewall.delete_rule(ssh.id)

    def test_turning_firewalld_on_is_guided(self, firewalld, host, ledger):
        with pytest.raises(SecurityError, match="ufw only"):
            _firewall(firewalld, host, ledger).enable()
