# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust compliance`` at the terminal.

The commands hold no logic: they call :mod:`noust.core.compliance` and translate the
result. What is pinned: nothing is on until it is chosen; ``enable`` says which controls move
and the clause each serves before it asks; ``--yes`` and ``--json`` go ahead; a control cannot
be set looser than an enabled framework asks; ``check`` exits like a monitoring check and
names the clause of each finding; and the old ``noust ens`` still works.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli import audit_policy
from noust.cli.app import cli
from noust.core.audit import Actor, get_log
from noust.core.compliance import resolve
from noust.core.config import Config
from noust.core.ens import checks
from noust.core.store import NoustStore, get_store

OPERATOR = Actor(kind="cli", id="0", name="root", via="sudo", source=None)


@pytest.fixture(autouse=True)
def operator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit_policy.audit, "cli_actor", lambda: OPERATOR)


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = get_store()
    yield instance
    NoustStore.reset_instance()


def events(action: str) -> list[dict[str, Any]]:
    # The log reads newest first; a test reads what happened in order.
    return list(reversed([e for e in get_log().read(limit=1000) if e["action"] == action]))


def run(*args: str, input: str | None = None) -> Any:
    return CliRunner().invoke(cli, ["compliance", *args], input=input)


def facts_with(**values: Any) -> checks.Facts:
    facts = checks.Facts(profile="standard", now=datetime.now(timezone.utc))
    facts.audit_health = {"sinks": []}
    facts.accounts = []
    facts.backups = []
    for key, value in values.items():
        setattr(facts, key, value)
    return facts


class TestReading:
    def test_status_with_nothing_chosen(self, real_config: Config) -> None:
        result = run("status")

        assert result.exit_code == 0, result.output
        assert "none" in result.output

    def test_status_as_json(self, real_config: Config) -> None:
        body = json.loads(run("status", "--json").output)

        assert not any(f["enabled"] for f in body["frameworks"])
        assert len(body["controls"]) > 30

    def test_the_frameworks_are_listed_with_their_reference(self, real_config: Config) -> None:
        output = run("frameworks").output

        for name in (
            "ENS BÁSICA",
            "ENS MEDIA",
            "ENS ALTA",
            "ISO/IEC 27001",
            "NIS2",
            "SOC 2",
            "GDPR",
        ):
            assert name in output

    def test_show_names_the_clause_and_what_stays_organisational(self, real_config: Config) -> None:
        result = run("show", "ISO-27001")

        assert result.exit_code == 0, result.output
        assert "A.5.3" in result.output and "Segregation of duties" in result.output
        assert "organisational" in result.output

    def test_show_as_json_carries_the_whole_mapping(self, real_config: Config) -> None:
        body = json.loads(run("show", "cis-ig2", "--json").output)

        assert body["includes"] == "cis-ig1"
        assert body["requirements"]["stale_account_days"]["clauses"][0]["id"] == "5.3"
        assert body["organisational"]

    def test_an_unknown_framework_is_a_usage_error(self, real_config: Config) -> None:
        result = run("show", "iso-27002")

        assert result.exit_code == 2 and "is not a framework" in result.output

    def test_controls_say_who_forces_them(self, real_config: Config) -> None:
        run("enable", "ens-media", "--yes")

        body = json.loads(run("controls", "--json").output)

        approvals = next(c for c in body if c["id"] == "approvals")
        assert approvals["forced_by"] == ["ens-media"] and approvals["locked"]


class TestEnabling:
    def test_it_shows_what_moves_and_the_clause_before_it_asks(self, real_config: Config) -> None:
        result = run("enable", "iso-27001", input="n\n")

        assert result.exit_code != 0
        assert "separation_of_duties" in result.output and "iso-27001: A.5.3" in result.output
        assert "Turn on iso-27001?" in result.output
        assert resolve.enabled_ids(real_config) == []

    def test_yes_goes_ahead_and_records_it(self, real_config: Config) -> None:
        result = run("enable", "iso-27001", "nis2", "--yes")

        assert result.exit_code == 0, result.output
        assert resolve.enabled_ids(real_config) == ["iso-27001", "nis2"]
        recorded = events("security.framework.change")
        assert recorded and recorded[-1]["details"]["enabled"] == ["iso-27001", "nis2"]

    def test_dry_run_changes_nothing(self, real_config: Config) -> None:
        result = CliRunner().invoke(
            cli, ["--dry-run", "compliance", "enable", "iso-27001", "--yes"]
        )

        assert result.exit_code == 0, result.output
        assert resolve.enabled_ids(real_config) == []

    def test_json_applies_and_prints_the_plan(self, real_config: Config) -> None:
        result = run("enable", "gdpr-32", "--yes", "--json")

        body = json.loads(result.output)
        assert body["applied"] and body["plan"]["enabled_after"] == ["gdpr-32"]

    def test_the_old_profile_setting_still_means_ens_media(self, real_config: Config) -> None:
        real_config.set("security.profile", "ens-medium")

        body = json.loads(run("status", "--json").output)

        assert [f["id"] for f in body["frameworks"] if f["enabled"]] == ["ens-media"]

    def test_disabling_turns_a_framework_off(self, real_config: Config) -> None:
        run("enable", "nis2", "--yes")

        result = run("disable", "nis2", "--yes")

        assert result.exit_code == 0, result.output
        assert resolve.enabled_ids(real_config) == []
        assert events("security.framework.change")[-1]["details"]["disabled"] == ["nis2"]


class TestSettingAControl:
    def test_it_sets_a_flag(self, real_config: Config) -> None:
        result = run("set", "change_reasons", "on", "--yes")

        assert result.exit_code == 0, result.output
        assert real_config.get("security.change_reasons") is True
        assert events("security.control.change")[-1]["resource"] == "control:change_reasons"

    def test_it_sets_a_number_and_a_choice(self, real_config: Config) -> None:
        run("set", "session_idle_minutes", "10", "--yes")
        run("set", "backup_encryption", "required", "--yes")

        assert real_config.get("auth.session.idle_minutes") == 10
        assert real_config.get("backup.encryption") == "required"

    def test_a_control_id_may_be_written_with_dashes(self, real_config: Config) -> None:
        assert run("set", "change-reasons", "on", "--yes").exit_code == 0

    def test_it_cannot_loosen_below_an_enabled_framework(self, real_config: Config) -> None:
        run("enable", "ens-media", "--yes")

        # ENS MEDIA also asks for a reason with every change.
        result = run("set", "session_idle_minutes", "60", "--yes", "--reason", "test")

        assert result.exit_code == 2 and "ens-media" in result.output
        assert real_config.get("auth.session.idle_minutes") != 60

    def test_nonsense_is_refused(self, real_config: Config) -> None:
        assert run("set", "nothing", "on").exit_code == 2
        assert run("set", "session_idle_minutes", "soon", "--yes").exit_code == 2


class TestChecking:
    def canned(self, monkeypatch: pytest.MonkeyPatch, facts: checks.Facts) -> None:
        monkeypatch.setattr(checks, "gather", lambda sources=None: facts)

    def test_with_no_framework_it_says_so(self, real_config: Config, monkeypatch) -> None:
        self.canned(monkeypatch, facts_with())

        result = run("check")

        assert result.exit_code == 0 and "No framework is on" in result.output

    def test_a_framework_that_is_on_and_in_force_passes(
        self, real_config: Config, monkeypatch
    ) -> None:
        run("enable", "cis-ig1", "--yes")
        self.canned(
            monkeypatch,
            facts_with(
                backups=[{"domain": "shop.example.com"}],
                schedules=[{"domain": "shop.example.com", "destinations": ["nas"]}],
                accounts=[],
                audit_events={"last_access_review": datetime.now(timezone.utc).isoformat()},
            ),
        )

        result = run("check")

        assert result.exit_code == 0, result.output
        assert "does not mean the server" in result.output

    def test_a_framework_that_is_off_fails_with_the_clause_and_how_to_turn_it_on(
        self, real_config: Config, monkeypatch
    ) -> None:
        self.canned(monkeypatch, facts_with())

        result = run("check", "--framework", "iso-27001", "--json")

        body = json.loads(result.output)
        assert result.exit_code == 2 and body["verdict"] == "fail"
        finding = next(
            f for f in body["frameworks"][0]["findings"] if f["control"] == "separation_of_duties"
        )
        assert finding["status"] == "fail"
        assert finding["clauses"] == ["A.5.3: Segregation of duties"]
        assert "noust compliance enable iso-27001" in finding["remediation"]
        assert body["frameworks"][0]["organisational"]

    def test_a_missing_audit_destination_fails_the_control_that_needs_one(
        self, real_config: Config, monkeypatch
    ) -> None:
        run("enable", "cis-ig2", "--yes")
        self.canned(monkeypatch, facts_with(audit_syslog=0, audit_journald=False))

        body = json.loads(run("check", "--json").output)

        sink = next(
            f for f in body["frameworks"][0]["findings"] if f["control"] == "external_audit_sink"
        )
        assert sink["status"] == "fail" and "8.9" in sink["clauses"][0]

    def test_a_shipped_audit_trail_passes_it(self, real_config: Config, monkeypatch) -> None:
        run("enable", "cis-ig2", "--yes")
        self.canned(monkeypatch, facts_with(audit_syslog=1, audit_journald=True))

        body = json.loads(run("check", "--json").output)

        sink = next(
            f for f in body["frameworks"][0]["findings"] if f["control"] == "external_audit_sink"
        )
        assert sink["status"] == "ok"

    def test_the_result_never_says_the_server_complies(
        self, real_config: Config, monkeypatch
    ) -> None:
        self.canned(monkeypatch, facts_with())

        body = json.loads(run("check", "--framework", "nis2", "--json").output)

        assert "does not mean the server" in body["disclaimer"]

    def test_the_check_is_a_read_not_a_change(self, real_config: Config, monkeypatch) -> None:
        self.canned(monkeypatch, facts_with())

        run("check", "--framework", "nis2")

        assert events("compliance.read")
        assert not events("cli.command")


class TestTheOldCommandsStillWork:
    def test_ens_profile_still_names_the_profile(self, real_config: Config) -> None:
        real_config.set("security.profile", "ens-medium")

        result = CliRunner().invoke(cli, ["ens", "profile", "--json"])

        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["profile"] == "ens-medium"
