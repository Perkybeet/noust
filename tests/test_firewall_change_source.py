# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Changing where a firewall rule admits from, in one step (item 164).

It used to be two: delete the rule and add the one that says the new origin, with the confirmation
of the SSH login between them, so a person who wanted "this port only from the office" was left
with the port open to everyone or closed to everyone for as long as it took to confirm twice.
Now one operation adds the new rule, removes the old one and waits ONCE, on the same safety net as
every change of access: nobody confirms and the old rule comes back. What is pinned: the two rules
are made in the order that never leaves the port without one, a single change is armed, the revert
puts the old rule back and removes the new one, the guard that keeps SSH reachable says what it
always said, and a rule Noust cannot spell is refused with the reason.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import SecurityError, ValidationError
from noust.managers.server.security_access import AccessGuardError
from noust.managers.server.security_firewall import Firewall, RuleRequest
from noust.managers.server.security_pending import ChangeLedger
from noust.managers.server.security_probe import SecurityProbe
from tests.support.server_security_support import NOW, FakeHost, FakeSshd

UFW_ACTIVE = """Status: active
Default: deny (incoming), allow (outgoing), disabled (routed)
"""

UFW_ADDED = """Added user rules (see 'ufw status' for running firewall):
ufw allow 22/tcp
ufw allow 80,443/tcp
ufw allow from 10.0.0.0/8 to any port 5432 proto tcp comment 'noust: office db'
ufw deny 3306/tcp
ufw limit 2222/tcp
ufw allow OpenSSH
"""

FIREWALLD_ZONE = """public (active)
  target: default
  interfaces: eth0
  services: ssh
  ports: 8080/tcp
  rich rules:
	rule family="ipv4" source address="10.0.0.0/8" port port="5432" protocol="tcp" accept
"""

SS_LISTEN = (
    'tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=900,fd=3))\n'
    'tcp LISTEN 0 244 0.0.0.0:5432 0.0.0.0:* users:(("postgres",pid=77,fd=5))\n'
)

#: A session open now, from 198.51.100.7.
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


def _rule(firewall: Firewall, spec: str):
    return next(rule for rule in firewall.state().rules if rule.spec == spec)


def _ufw_calls(runner: FakeSshd) -> list[tuple[str, ...]]:
    return [
        call
        for call in runner.calls
        if call[0] == "ufw" and call[1:3] not in (("status", "verbose"), ("show", "added"))
    ]


OFFICE_DB = "allow from 10.0.0.0/8 to any port 5432 proto tcp"


class TestOneStepOnUfw:
    def test_the_new_rule_is_made_before_the_old_one_goes(self, runner, host, ledger):
        firewall = _firewall(runner, host, ledger)

        firewall.change_source(_rule(firewall, OFFICE_DB).id, "192.0.2.0/24")

        assert _ufw_calls(runner) == [
            (
                "ufw",
                "allow",
                "from",
                "192.0.2.0/24",
                "to",
                "any",
                "port",
                "5432",
                "proto",
                "tcp",
                "comment",
                "noust: office db",
            ),
            (
                "ufw",
                "delete",
                "allow",
                "from",
                "10.0.0.0/8",
                "to",
                "any",
                "port",
                "5432",
                "proto",
                "tcp",
            ),
        ]

    def test_it_waits_once_for_a_single_change(self, runner, host, ledger):
        firewall = _firewall(runner, host, ledger)

        change = firewall.change_source(_rule(firewall, OFFICE_DB).id, "192.0.2.0/24")

        assert change.status == "pending"
        assert [pending.id for pending in ledger.pending()] == [change.id]
        assert "192.0.2.0/24" in change.title and "5432" in change.title

    def test_the_revert_puts_the_old_rule_back_before_it_removes_the_new_one(
        self, runner, host, ledger
    ):
        firewall = _firewall(runner, host, ledger)

        change = firewall.change_source(_rule(firewall, OFFICE_DB).id, "192.0.2.0/24")

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
            ],
            [
                "ufw",
                "delete",
                "allow",
                "from",
                "192.0.2.0/24",
                "to",
                "any",
                "port",
                "5432",
                "proto",
                "tcp",
            ],
        ]

    def test_another_change_waiting_stops_it_like_any_other(self, runner, host, ledger):
        firewall = _firewall(runner, host, ledger)
        firewall.add_rule(RuleRequest("allow", 8443))

        with pytest.raises(SecurityError, match="waiting for confirmation"):
            firewall.change_source(_rule(firewall, OFFICE_DB).id, "192.0.2.0/24")

    def test_a_command_the_firewall_refuses_undoes_the_whole_change(self, runner, host, ledger):
        runner.script(
            ["ufw", "delete", "allow", "from", "10.0.0.0/8"],
            stderr="ERROR: no such rule",
            exit_code=1,
        )
        firewall = _firewall(runner, host, ledger)

        with pytest.raises(SecurityError, match="undone"):
            firewall.change_source(_rule(firewall, OFFICE_DB).id, "192.0.2.0/24")

        assert ledger.pending() == []

    def test_a_rule_for_everyone_can_be_narrowed_to_a_network(self, runner, host, ledger):
        firewall = _firewall(runner, host, ledger)
        https = _rule(firewall, "allow 80,443/tcp")

        # Two ports: Noust spells a rule of one.
        with pytest.raises(SecurityError, match="one port"):
            firewall.change_source(https.id, "192.0.2.0/24")

    def test_a_deny_rule_keeps_its_action(self, runner, host, ledger):
        firewall = _firewall(runner, host, ledger)

        firewall.change_source(_rule(firewall, "deny 3306/tcp").id, "203.0.113.0/24")

        assert _ufw_calls(runner)[0][:3] == ("ufw", "deny", "from")


class TestTheGuardKeepsSshReachable:
    @pytest.fixture(autouse=True)
    def only_one_rule_opens_ssh(self, runner: FakeSshd) -> None:
        """The profile rule that also opens SSH would make every move harmless."""
        runner.script(["ufw", "show", "added"], stdout=UFW_ADDED.replace("ufw allow OpenSSH\n", ""))

    def test_another_rule_that_opens_ssh_to_everyone_makes_the_move_harmless(
        self, runner, host, ledger
    ):
        runner.script(["ufw", "show", "added"], stdout=UFW_ADDED)
        firewall = _firewall(runner, host, ledger)

        change = firewall.change_source(_rule(firewall, "allow 22/tcp").id, "192.0.2.0/24")

        assert change.status == "pending"

    def test_narrowing_ssh_to_a_network_that_holds_the_open_sessions_is_allowed(
        self, runner, host, ledger
    ):
        firewall = _firewall(runner, host, ledger)

        change = firewall.change_source(_rule(firewall, "allow 22/tcp").id, "198.51.100.0/24")

        assert change.status == "pending"
        assert _ufw_calls(runner)[-1] == ("ufw", "delete", "allow", "22/tcp")

    def test_narrowing_ssh_to_a_network_that_cuts_a_session_off_is_the_usual_refusal(
        self, runner, host, ledger
    ):
        firewall = _firewall(runner, host, ledger)

        with pytest.raises(AccessGuardError, match="port 22") as refused:
            firewall.change_source(_rule(firewall, "allow 22/tcp").id, "192.0.2.0/24")

        assert "198.51.100.7" in refused.value.details
        assert _ufw_calls(runner) == []
        assert ledger.pending() == []

    def test_a_rule_for_ssh_from_somewhere_can_move_to_everyone(self, runner, host, ledger):
        runner.script(
            ["ufw", "show", "added"],
            stdout="ufw allow from 198.51.100.0/24 to any port 22 proto tcp\n",
        )
        firewall = _firewall(runner, host, ledger)
        rule = _rule(firewall, "allow from 198.51.100.0/24 to any port 22 proto tcp")

        change = firewall.change_source(rule.id, "any")

        assert change.status == "pending"
        assert _ufw_calls(runner)[0] == (
            "ufw",
            "allow",
            "from",
            "any",
            "to",
            "any",
            "port",
            "22",
            "proto",
            "tcp",
            "comment",
            "noust:",
        )

    def test_closing_a_protected_port_by_changing_the_origin_of_a_deny_is_refused(
        self, runner, host, ledger
    ):
        runner.script(
            ["ufw", "show", "added"], stdout="ufw deny from 192.0.2.0/24 to any port 22 proto tcp\n"
        )
        firewall = _firewall(runner, host, ledger)
        rule = _rule(firewall, "deny from 192.0.2.0/24 to any port 22 proto tcp")

        with pytest.raises(AccessGuardError):
            firewall.change_source(rule.id, "198.51.100.0/24")


class TestWhatIsRefused:
    def test_an_unknown_rule(self, runner, host, ledger):
        with pytest.raises(SecurityError, match="no firewall rule"):
            _firewall(runner, host, ledger).change_source("0123456789ab", "192.0.2.0/24")

    def test_the_same_origin_is_not_a_change(self, runner, host, ledger):
        firewall = _firewall(runner, host, ledger)

        with pytest.raises(ValidationError, match="already"):
            firewall.change_source(_rule(firewall, OFFICE_DB).id, "10.0.0.0/8")

    def test_an_origin_that_is_not_an_address_is_refused_before_anything_runs(
        self, runner, host, ledger
    ):
        firewall = _firewall(runner, host, ledger)

        with pytest.raises(ValidationError):
            firewall.change_source(_rule(firewall, OFFICE_DB).id, "10.0.0.0/8; reboot")
        assert _ufw_calls(runner) == []

    def test_an_origin_a_rule_already_has(self, runner, host, ledger):
        runner.script(
            ["ufw", "show", "added"],
            stdout=UFW_ADDED + "ufw allow from 192.0.2.0/24 to any port 5432 proto tcp\n",
        )
        firewall = _firewall(runner, host, ledger)

        with pytest.raises(SecurityError, match="already a rule"):
            firewall.change_source(_rule(firewall, OFFICE_DB).id, "192.0.2.0/24")

    @pytest.mark.parametrize("spec", ["limit 2222/tcp", "allow OpenSSH"])
    def test_a_rule_noust_cannot_spell_says_why(self, runner, host, ledger, spec):
        firewall = _firewall(runner, host, ledger)

        with pytest.raises(SecurityError, match="one port") as refused:
            firewall.change_source(_rule(firewall, spec).id, "192.0.2.0/24")

        assert "add the rule you want" in refused.value.details

    def test_each_rule_says_whether_its_origin_can_be_changed(self, runner, host, ledger):
        rules = {rule.spec: rule for rule in _firewall(runner, host, ledger).state().rules}

        assert rules[OFFICE_DB].source_changeable
        assert rules["allow 22/tcp"].source_changeable
        assert rules["deny 3306/tcp"].source_changeable
        assert not rules["allow 80,443/tcp"].source_changeable
        assert not rules["limit 2222/tcp"].source_changeable
        assert not rules["allow OpenSSH"].source_changeable
        assert rules[OFFICE_DB].to_dict()["source_changeable"] is True

    def test_the_firewall_opened_for_reading_cannot_change(self, runner, host):
        firewall = _firewall(runner, host)

        with pytest.raises(SecurityError, match="reading only"):
            firewall.change_source(_rule(firewall, OFFICE_DB).id, "192.0.2.0/24")


class TestOneStepOnFirewalld:
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

    def test_a_port_for_everyone_becomes_a_rich_rule_and_is_permanent_once_confirmed(
        self, firewalld, host, ledger
    ):
        firewall = _firewall(firewalld, host, ledger)

        change = firewall.change_source(_rule(firewall, "port 8080/tcp").id, "192.0.2.0/24")

        rich = 'rule family="ipv4" source address="192.0.2.0/24" port port="8080" protocol="tcp" accept'
        assert firewalld.ran("firewall-cmd", "--zone=public", f"--add-rich-rule={rich}")
        assert firewalld.ran("firewall-cmd", "--zone=public", "--remove-port=8080/tcp")
        assert change.commit == [
            ["firewall-cmd", "--permanent", "--zone=public", f"--add-rich-rule={rich}"],
            ["firewall-cmd", "--permanent", "--zone=public", "--remove-port=8080/tcp"],
        ]
        assert change.undo == [
            ["firewall-cmd", "--zone=public", "--add-port=8080/tcp"],
            ["firewall-cmd", "--zone=public", f"--remove-rich-rule={rich}"],
        ]

    def test_a_rich_rule_moves_to_another_network(self, firewalld, host, ledger):
        firewall = _firewall(firewalld, host, ledger)
        rule = next(rule for rule in firewall.state().rules if rule.spec.startswith("rich rule"))

        firewall.change_source(rule.id, "203.0.113.0/24")

        assert firewalld.ran(
            "firewall-cmd",
            "--zone=public",
            '--add-rich-rule=rule family="ipv4" source address="203.0.113.0/24" '
            'port port="5432" protocol="tcp" accept',
        )
        assert firewalld.ran(
            "firewall-cmd",
            "--zone=public",
            '--remove-rich-rule=rule family="ipv4" source address="10.0.0.0/8" '
            'port port="5432" protocol="tcp" accept',
        )

    def test_the_ssh_service_is_a_rule_noust_cannot_move(self, firewalld, host, ledger):
        firewall = _firewall(firewalld, host, ledger)
        ssh = next(rule for rule in firewall.state().rules if rule.service == "ssh")

        with pytest.raises(SecurityError, match="one port"):
            firewall.change_source(ssh.id, "198.51.100.0/24")
