# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Allow rules without a confirmation window (:meth:`Firewall.allow_direct`).

The confirm-or-revert window exists because a rule can cut the operator off;
an allow rule cannot, and a database made public needs one now. These are the
same plan the confirmed path builds, run immediately, by the one firewall
manager: only an allow rule is ever made this way, a rule that is already there
is never counted as made (so it is never removed later), and removing takes
away exactly what was made.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from noust.core.exceptions import SecurityError
from noust.core.runner import FakeRunner, set_runner
from noust.managers.server.security_firewall import Firewall, RuleRequest
from noust.managers.server.security_probe import SecurityProbe
from tests.database_public_support import PublicHostRunner

ALLOW = RuleRequest("allow", 5433, "tcp", "203.0.113.7", "db test")


def firewall(runner: FakeRunner) -> Firewall:
    """A firewall manager with no confirmation ledger."""
    return Firewall(SecurityProbe(runner=runner), None, actor="test")


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    yield
    set_runner(None)


class TestUfw:
    """ufw, the way a server usually has it."""

    def test_an_allow_rule_is_made_now(self) -> None:
        runner = PublicHostRunner()
        change = firewall(runner).allow_direct(ALLOW)

        assert (change.backend, change.active, change.changed) == ("ufw", True, True)
        assert (
            "ufw allow from 203.0.113.7/32 to any port 5433 proto tcp comment 'noust: db test'"
            in runner.rules
        )

    def test_no_timer_is_armed_and_nothing_waits_for_a_login(self) -> None:
        runner = PublicHostRunner()
        firewall(runner).allow_direct(ALLOW)
        assert not [call for call in runner.calls if call[0] == "systemd-run"]

    def test_a_rule_that_is_already_there_is_not_made_again(self) -> None:
        runner = PublicHostRunner()
        runner.rules.append("ufw allow from 203.0.113.7/32 to any port 5433 proto tcp")
        change = firewall(runner).allow_direct(ALLOW)
        assert change.changed is False and "already allowed" in change.detail
        assert not [call for call in runner.calls if call[:2] == ("ufw", "allow")]

    def test_only_an_allow_rule_goes_this_way(self) -> None:
        with pytest.raises(SecurityError, match="Only an allow rule"):
            firewall(PublicHostRunner()).allow_direct(RuleRequest("deny", 22, "tcp", "any"))

    def test_an_inactive_ufw_still_keeps_the_rule(self) -> None:
        runner = PublicHostRunner(ufw="inactive")
        change = firewall(runner).allow_direct(ALLOW)
        assert change.active is False and change.changed is True

    def test_a_refused_command_is_an_error_with_ufw_s_words(self) -> None:
        runner = PublicHostRunner()
        runner.fail[("ufw", "allow")] = "ERROR: Bad port"
        with pytest.raises(SecurityError, match="refused") as caught:
            firewall(runner).allow_direct(ALLOW)
        assert caught.value.output == "ERROR: Bad port"

    def test_a_bad_field_is_a_validation_error_and_changes_nothing(self) -> None:
        from noust.core.exceptions import ValidationError

        runner = PublicHostRunner()
        with pytest.raises(ValidationError):
            firewall(runner).allow_direct(RuleRequest("allow", 70000, "tcp", "any"))
        with pytest.raises(ValidationError):
            firewall(runner).allow_direct(RuleRequest("allow", 5433, "tcp", "not-an-address"))
        assert runner.rules == ["ufw allow 22/tcp"]

    def test_it_is_audited(self) -> None:
        from noust.core import audit

        firewall(PublicHostRunner()).allow_direct(ALLOW)
        names = [entry["action"] for entry in audit.get_log().read(limit=50)]
        assert "server.firewall" in names


class TestRemoving:
    """Exactly what was made, and not an operator's own."""

    def test_the_rule_noust_made_is_removed(self) -> None:
        runner = PublicHostRunner()
        manager = firewall(runner)
        manager.allow_direct(ALLOW)
        change = manager.remove_direct(ALLOW)
        assert change.changed is True
        assert runner.rules == ["ufw allow 22/tcp"]

    def test_an_operators_own_identical_rule_is_not_removed_on_ufw(self) -> None:
        runner = PublicHostRunner()
        runner.rules.append("ufw allow from 203.0.113.7/32 to any port 5433 proto tcp")
        change = firewall(runner).remove_direct(ALLOW)
        assert change.changed is False
        assert "ufw allow from 203.0.113.7/32 to any port 5433 proto tcp" in runner.rules

    def test_a_rule_that_is_not_there_is_not_an_error(self) -> None:
        change = firewall(PublicHostRunner()).remove_direct(ALLOW)
        assert change.changed is False and change.detail == "no such rule"

    def test_another_port_s_rule_is_left_alone(self) -> None:
        runner = PublicHostRunner()
        manager = firewall(runner)
        manager.allow_direct(ALLOW)
        manager.allow_direct(RuleRequest("allow", 5434, "tcp", "203.0.113.7", "db other"))
        manager.remove_direct(ALLOW)
        assert any("5434" in rule for rule in runner.rules)
        assert not any("5433" in rule for rule in runner.rules)


FIREWALLD_ZONE = """public (active)
  target: default
  interfaces: eth0
  services: ssh
  ports:
  rich rules:
"""


class TestFirewalld:
    """firewalld: the runtime rule, then the saved one."""

    def runner(self, *, running: bool = True) -> FakeRunner:
        runner = FakeRunner()
        runner.only_knows("firewall-cmd")
        if running:
            runner.script(["firewall-cmd", "--state"], stdout="running\n")
            runner.script(["firewall-cmd", "--get-default-zone"], stdout="public\n")
            runner.script(["firewall-cmd", "--zone=public", "--list-all"], stdout=FIREWALLD_ZONE)
            runner.script(
                ["firewall-cmd", "--permanent", "--zone=public", "--list-all"],
                stdout=FIREWALLD_ZONE,
            )
        else:
            runner.script(["firewall-cmd", "--state"], stderr="not running", exit_code=252)
        return runner

    def test_a_source_makes_a_rich_rule_and_makes_it_permanent(self) -> None:
        runner = self.runner()
        change = firewall(runner).allow_direct(ALLOW)
        assert change.changed is True and change.backend == "firewalld"
        adds = [call for call in runner.calls if any("--add-rich-rule" in part for part in call)]
        assert len(adds) == 2  # runtime, then --permanent
        assert "--permanent" in adds[1]
        assert 'source address="203.0.113.7/32"' in adds[0][-1]

    def test_anyone_is_a_plain_port(self) -> None:
        runner = self.runner()
        firewall(runner).allow_direct(RuleRequest("allow", 5433, "tcp", "any"))
        assert ("firewall-cmd", "--zone=public", "--add-port=5433/tcp") in runner.calls
        assert (
            "firewall-cmd",
            "--permanent",
            "--zone=public",
            "--add-port=5433/tcp",
        ) in runner.calls

    def test_firewalld_that_is_not_running_is_not_asked(self) -> None:
        runner = self.runner(running=False)
        change = firewall(runner).allow_direct(ALLOW)
        assert change.changed is False and change.active is False
        assert not [call for call in runner.calls if "--add-rich-rule" in " ".join(call)]

    def test_a_permanent_failure_undoes_the_runtime_rule(self) -> None:
        runner = self.runner()
        runner.script(
            ["firewall-cmd", "--permanent", "--zone=public", "--add-port=5433/tcp"],
            exit_code=1,
            stderr="no",
        )
        with pytest.raises(SecurityError, match="could not make"):
            firewall(runner).allow_direct(RuleRequest("allow", 5433, "tcp", "any"))
        assert ("firewall-cmd", "--zone=public", "--remove-port=5433/tcp") in runner.calls


class TestNoFirewallAtAll:
    """Nothing Noust changes."""

    def test_it_says_so_and_changes_nothing(self) -> None:
        runner = FakeRunner()
        runner.only_knows("ss")
        change = firewall(runner).allow_direct(ALLOW)
        assert change.changed is False and "neither is in use" in change.detail
        assert firewall(runner).remove_direct(ALLOW).changed is False
