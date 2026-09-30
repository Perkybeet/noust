# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust ens``, ``noust incident`` and ``noust central backup`` at the terminal.

The commands hold no logic: they call :mod:`noust.core.ens` and translate the
result. What is pinned: ``ens check`` exits 0, 1 or 2 like a monitoring check
and prints JSON; ``ens report`` writes the bundle and records its hash;
``ens inventory`` sets and exports; ``incident freeze`` needs a reason and
locks the console; the read-only commands record no command event.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli import audit_policy
from noust.cli.app import cli
from noust.core.audit import Actor, get_log
from noust.core.ens import checks, incident
from noust.core.store import App, NoustStore, get_store

OPERATOR = Actor(kind="cli", id="0", name="root", via="sudo", source=None)


@pytest.fixture(autouse=True)
def operator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit_policy.audit, "cli_actor", lambda: OPERATOR)
    monkeypatch.setattr(audit_policy, "security_profile", lambda: "standard")


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = get_store()
    yield instance
    NoustStore.reset_instance()


def events(action: str) -> list[dict[str, Any]]:
    return [entry for entry in get_log().read(limit=1000) if entry["action"] == action]


def canned(*statuses: str) -> Any:
    def run(sources: Any = None) -> tuple[checks.ComplianceCheck, checks.Facts]:
        from datetime import datetime, timezone

        facts = checks.Facts(profile="ens-medium", now=datetime.now(timezone.utc))
        result = checks.ComplianceCheck(
            profile="ens-medium",
            checked_at="2026-09-30T12:00:00+00:00",
            host="central-1",
            version="3.1.0",
            findings=[
                checks.Finding(
                    f"ENS-X-{i:02d}", "A check", status, ("op.exp.2",), "Found.", ("e",), "Fix."
                )
                for i, status in enumerate(statuses)
            ],
        )
        return result, facts

    return run


class TestEnsCheck:
    @pytest.mark.parametrize(
        ("statuses", "code"), [(("ok",), 0), (("ok", "warning"), 1), (("fail",), 2)]
    )
    def test_the_exit_code_is_the_verdict(self, monkeypatch, statuses, code) -> None:
        monkeypatch.setattr(checks, "run_check", canned(*statuses))

        result = CliRunner().invoke(cli, ["ens", "check"])

        assert result.exit_code == code, result.output

    def test_json_carries_every_finding(self, monkeypatch) -> None:
        monkeypatch.setattr(checks, "run_check", canned("ok", "fail"))

        result = CliRunner().invoke(cli, ["ens", "check", "--json"])

        body = json.loads(result.output)
        assert body["verdict"] == "fail" and len(body["findings"]) == 2
        assert body["findings"][1]["measures"] == ["op.exp.2"]

    def test_it_is_a_sensitive_read_not_a_change(self, monkeypatch) -> None:
        monkeypatch.setattr(checks, "run_check", canned("ok"))

        CliRunner().invoke(cli, ["ens", "check"])

        assert events("compliance.read")
        assert not events("cli.command")


class TestEnsReport:
    def test_the_bundle_is_written_and_its_hash_recorded(
        self, monkeypatch, tmp_path, store
    ) -> None:
        monkeypatch.setattr(checks, "run_check", canned("ok", "warning"))

        result = CliRunner().invoke(cli, ["ens", "report", "--output", str(tmp_path / "ev")])

        assert result.exit_code == 0, result.output
        written = sorted(path.name for path in (tmp_path / "ev").iterdir())
        assert [name.rsplit(".", 1)[-1] for name in written] == ["SHA256SUMS", "json", "md"]
        recorded = events("compliance.read")[0]
        body = json.loads(next((tmp_path / "ev").glob("*.json")).read_text())
        assert recorded["details"]["sha256"] == body["sha256"]


class TestEnsInventory:
    def test_set_then_export(self, store, tmp_path) -> None:
        store.create_app(
            App(domain="shop.example.com", app_type="nodejs", source="x", app_path="/x")
        )

        setting = CliRunner().invoke(
            cli,
            [
                "ens",
                "inventory",
                "set",
                "shop.example.com",
                "--owner",
                "Ventas",
                "--criticality",
                "high",
            ],
        )
        exported = CliRunner().invoke(cli, ["ens", "inventory", "list", "--format", "csv"])

        assert setting.exit_code == 0, setting.output
        assert "shop.example.com,nodejs" in exported.output and ",Ventas,high," in exported.output
        assert events("apps.inventory")

    def test_set_needs_something_to_set(self, store) -> None:
        result = CliRunner().invoke(cli, ["ens", "inventory", "set", "shop.example.com"])

        assert result.exit_code == 2


class TestIncident:
    def test_freeze_needs_a_reason(self, store) -> None:
        result = CliRunner().invoke(cli, ["incident", "freeze"])

        assert result.exit_code == 2 and "--reason" in result.output

    def test_freeze_status_unfreeze(self, store, monkeypatch) -> None:
        taken: dict[str, Any] = {}

        def fake_freeze(**kwargs: Any) -> incident.FreezeResult:
            taken.update(kwargs)
            incident.lock_down(actor=kwargs["actor"], reason=kwargs["reason"])
            return incident.FreezeResult(directory=Path("/x"), manifest_sha256="ab", locked=True)

        monkeypatch.setattr(incident, "freeze", fake_freeze)

        frozen = CliRunner().invoke(cli, ["incident", "freeze", "--reason", "INC-7", "--json"])
        status = CliRunner().invoke(cli, ["incident", "status", "--json"])
        lifted = CliRunner().invoke(cli, ["incident", "unfreeze", "--reason", "INC-7 closed"])

        assert frozen.exit_code == 0, frozen.output
        assert taken["reason"] == "INC-7" and taken["lock"] is True
        assert json.loads(status.output)["locked"] is True
        assert lifted.exit_code == 0 and "lifted" in lifted.output.lower()
        assert incident.lockdown_state() is None
