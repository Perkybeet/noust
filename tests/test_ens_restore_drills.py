# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A restore that was tried is evidence, in ``noust ens check`` and in ``noust ens report`` (item 174).

``ENS-BAK-01`` judges that a backup was taken, was verified (the archive reads) and left the server.
ENS MEDIA (mp.info.6.r1) asks that the procedures of backup *and restoration* are tested, and
``noust backup drill`` does it: the archive is verified in depth, unpacked, and every database dump
it carries is loaded into a temporary database. The drill was kept in ``restore_drills`` and no
check read it. ``ENS-BAK-03`` does, with the date, the result and how long it took, and the report
says what that time is (observed in a rehearsal) and what it does not prove (that a new server would
start with the data).
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from noust.core.ens import checks, report
from noust.core.ens.checks import Facts, Sources, evaluate, gather, indicators
from noust.core.restore_drills import DrillRecords, DrillReport, DrillStep
from noust.core.store import App, NoustStore
from tests.support.ens_checks import NOW, good_facts


def by_id(findings: list[checks.Finding]) -> dict[str, checks.Finding]:
    return {finding.id: finding for finding in findings}


def drill(
    domain: str = "shop.example.com",
    *,
    days_ago: float = 3,
    seconds: int = 252,
    ok: bool = True,
    databases: int = 2,
    backup_id: str = "shop-2026-09-27",
) -> dict[str, Any]:
    """One application's drill facts, as gathering makes them."""
    finished = NOW - timedelta(days=days_ago)
    return {
        "domain": domain,
        "last_ok_at": finished.isoformat() if ok else None,
        "duration_s": float(seconds) if ok else None,
        "databases": databases if ok else 0,
        "backup_id": backup_id if ok else None,
        "last_at": finished.isoformat(),
        "last_ok": ok,
        "last_summary": "" if ok else "postgresql/shop: password authentication failed",
    }


def facts_with(*drills: dict[str, Any]) -> Facts:
    facts = good_facts()
    facts.restore_drills = list(drills)
    return facts


class TestTheCheck:
    def test_a_restore_tried_within_the_month_passes_with_its_date_result_and_duration(
        self,
    ) -> None:
        finding = by_id(evaluate(facts_with(drill())))["ENS-BAK-03"]

        assert finding.status == "ok"
        assert finding.measures == ("mp.info.6.r1",)
        assert "within 30 days" in finding.summary
        assert (
            "shop.example.com: restore tested 2026-09-27, completed in 4 min 12 s, "
            "databases loaded: 2" in finding.evidence
        )

    def test_an_application_never_tested_is_a_warning_with_the_command_to_do_it(self) -> None:
        never = {
            **drill(),
            "last_ok_at": None,
            "duration_s": None,
            "last_ok": None,
            "last_at": None,
        }

        finding = by_id(evaluate(facts_with(never)))["ENS-BAK-03"]

        assert finding.status == "warning"
        assert "shop.example.com: never tested" in finding.evidence
        assert "noust backup drill shop.example.com" in finding.remediation

    def test_a_restore_older_than_the_limit_is_a_warning(self) -> None:
        finding = by_id(evaluate(facts_with(drill(days_ago=31))))["ENS-BAK-03"]

        assert finding.status == "warning"
        assert any("last good restore test 2026-08-30" in line for line in finding.evidence)

    def test_the_limit_is_a_whole_30_days(self) -> None:
        assert by_id(evaluate(facts_with(drill(days_ago=30))))["ENS-BAK-03"].status == "ok"

    def test_a_drill_that_failed_after_the_last_good_one_is_said(self) -> None:
        facts = facts_with(
            {
                **drill(days_ago=20),
                "last_at": (NOW - timedelta(days=1)).isoformat(),
                "last_ok": False,
                "last_summary": "postgresql/shop: password authentication failed",
            }
        )

        finding = by_id(evaluate(facts))["ENS-BAK-03"]

        assert finding.status == "warning"
        assert any(
            "the last attempt (2026-09-29) failed: postgresql/shop: password authentication failed"
            in line
            for line in finding.evidence
        )

    def test_a_failing_attempt_with_no_good_one_before_it_is_a_warning_too(self) -> None:
        finding = by_id(evaluate(facts_with(drill(ok=False, days_ago=2))))["ENS-BAK-03"]

        assert finding.status == "warning"
        assert any("never tested" in line or "failed" in line for line in finding.evidence)

    def test_no_application_is_not_a_finding(self) -> None:
        facts = good_facts()
        facts.backups = []
        facts.restore_drills = []

        assert by_id(evaluate(facts))["ENS-BAK-03"].status == "n/a"

    def test_a_server_that_cannot_read_its_drills_says_so_and_does_not_guess(self) -> None:
        facts = good_facts()
        facts.restore_drills = None
        facts.errors["restore_drills"] = "OperationalError: no such table"

        finding = by_id(evaluate(facts))["ENS-BAK-03"]

        assert finding.status == "warning"
        assert "restore_drills" in finding.summary

    def test_a_passing_server_still_passes_every_check(self) -> None:
        findings = evaluate(facts_with(drill()))

        assert {f.status for f in findings} <= {"ok", "n/a"}


class TestTheFigures:
    def test_the_share_of_applications_whose_restore_was_tried_in_time(self) -> None:
        facts = facts_with(drill("a.example.com"), drill("b.example.com", days_ago=60))
        facts.backups = [
            {"domain": "a.example.com", "newest": NOW.isoformat(), "verified": NOW.isoformat()},
            {"domain": "b.example.com", "newest": NOW.isoformat(), "verified": NOW.isoformat()},
        ]

        figures = indicators(facts)

        assert figures["restores_tested_percent"] == 50.0
        assert figures["restore_time_observed_seconds"] == 252.0

    def test_no_drills_have_no_observed_time(self) -> None:
        figures = indicators(facts_with())

        assert figures["restore_time_observed_seconds"] is None


class TestGathering:
    @pytest.fixture
    def store(self, tmp_path: Path) -> Any:
        NoustStore.reset_instance()
        instance = NoustStore(tmp_path / "noust.db")
        yield instance
        NoustStore.reset_instance()

    def _save(
        self, store: NoustStore, domain: str, *, started: str, finished: str, ok: bool
    ) -> None:
        steps = [
            DrillStep("backup", True, "shop-2026-09-27 (5 MB)"),
            DrillStep("archive", True, "checksum and signature verified"),
            DrillStep("unpack", True, "211 files unpacked"),
            DrillStep("postgresql/shop", ok, "loaded" if ok else "password authentication failed"),
            DrillStep("mysql/blog", True, "loaded"),
            DrillStep("redis/cache", True, "cannot be tried", skipped=True),
        ]
        DrillRecords(store).save(
            DrillReport(
                domain=domain,
                backup_id="shop-2026-09-27",
                trigger="schedule",
                started_at=started,
                finished_at=finished,
                steps=steps,
            )
        )

    def test_the_newest_good_drill_and_the_newest_attempt_of_each_application_are_read(
        self, store: NoustStore
    ) -> None:
        store.create_app(App(domain="shop.example.com", app_type="nodejs", port=3000))
        self._save(
            store,
            "shop.example.com",
            started="2026-09-20T03:00:00+00:00",
            finished="2026-09-20T03:04:12+00:00",
            ok=True,
        )
        self._save(
            store,
            "shop.example.com",
            started="2026-09-29T03:00:00+00:00",
            finished="2026-09-29T03:00:40+00:00",
            ok=False,
        )

        facts = gather(
            Sources(store=store, config=_Config(), hardening=lambda: [], clock=lambda: NOW)
        )

        [entry] = facts.restore_drills or []
        assert entry["domain"] == "shop.example.com"
        assert entry["last_ok_at"] == "2026-09-20T03:04:12+00:00"
        assert entry["duration_s"] == 252.0
        assert entry["databases"] == 2, "the skipped one was not loaded"
        assert entry["last_ok"] is False
        assert entry["last_at"] == "2026-09-29T03:00:40+00:00"
        assert "password authentication failed" in entry["last_summary"]

    def test_an_application_with_no_drill_is_listed_as_never_tested(
        self, store: NoustStore
    ) -> None:
        store.create_app(App(domain="blog.example.com", app_type="nodejs", port=3001))

        facts = gather(
            Sources(store=store, config=_Config(), hardening=lambda: [], clock=lambda: NOW)
        )

        [entry] = facts.restore_drills or []
        assert entry["last_ok_at"] is None and entry["last_at"] is None

    def test_a_preview_is_not_an_application_to_protect(self, store: NoustStore) -> None:
        store.create_app(App(domain="shop.example.com", app_type="nodejs", port=3000))
        store.create_app(
            App(
                domain="pr-1.example.com",
                app_type="nodejs",
                port=3001,
                preview_parent="shop.example.com",
            )
        )

        facts = gather(
            Sources(store=store, config=_Config(), hardening=lambda: [], clock=lambda: NOW)
        )

        assert [e["domain"] for e in facts.restore_drills or []] == ["shop.example.com"]


class _Config:
    """What ``gather`` asks of the configuration: nothing set, so every default."""

    def get(self, key: str, default: Any = None) -> Any:
        return default


class TestTheReport:
    def build(self, *drills: dict[str, Any]) -> dict[str, Any]:
        facts = facts_with(*drills)
        check = checks.ComplianceCheck(
            profile="ens-medium",
            checked_at=NOW.isoformat(),
            host="web-1",
            version="4.3.0",
            findings=evaluate(facts),
            indicators=indicators(facts),
        )
        return report.build_report(check, facts)

    def test_the_json_lists_each_application_with_its_date_result_and_time(self) -> None:
        built = self.build(drill())

        [entry] = built["backups"]["restore_drills"]["applications"]
        assert entry["domain"] == "shop.example.com"
        assert entry["last_ok_at"] == "2026-09-27T12:00:00+00:00"
        assert entry["duration_s"] == 252.0

    def test_the_time_is_called_what_it_is_and_says_what_it_proves_and_what_it_does_not(
        self,
    ) -> None:
        notes = self.build(drill())["backups"]["restore_drills"]["scope"]

        assert "tiempo de restauración observado" in notes["observed_time"].lower()
        assert "no es un tiempo de recuperación garantizado" in notes["observed_time"].lower()
        assert "los datos se cargan" in notes["proves"]
        assert "no prueba que un servidor nuevo arranque" in notes["does_not_prove"]
        assert "noust restore-server --plan" in notes["does_not_prove"]

    def test_the_markdown_has_a_section_for_it_with_the_measure(self) -> None:
        text = report.render_markdown(self.build(drill()))

        assert "## Restauraciones probadas (mp.info.6.r1)" in text
        assert "| shop.example.com | 2026-09-27 | correcta | 4 min 12 s | 2 |" in text
        assert "Tiempo de restauración observado" in text
        assert "no prueba que un servidor nuevo arranque" in text

    def test_an_application_never_tested_is_in_the_table_as_never(self) -> None:
        never = {
            **drill(),
            "last_ok_at": None,
            "duration_s": None,
            "last_ok": None,
            "last_at": None,
        }

        text = report.render_markdown(self.build(never))

        assert "| shop.example.com | nunca | - | - | - |" in text

    def test_the_report_is_still_canonical_json(self) -> None:
        built = self.build(drill())

        assert built["sha256"] == report.report_digest(built)
        json.dumps(built, default=str)
