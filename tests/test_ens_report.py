# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust ens report``: the check grouped by RD 311/2022 measure, in JSON and Markdown, hashed.
"""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

from noust.core.ens import checks, report
from tests.test_ens_checks import NOW, good_facts


def make() -> dict:
    facts = good_facts()
    facts.accounts[0]["passkeys"] = 1
    result = checks.ComplianceCheck(
        profile=facts.profile,
        checked_at=NOW.isoformat(),
        host="central-1",
        version="3.1.0",
        findings=checks.evaluate(facts),
        indicators=checks.indicators(facts),
    )
    return report.build_report(result, facts)


class TestTheReport:
    def test_findings_are_grouped_by_measure(self) -> None:
        built = make()

        measures = {entry["measure"]: entry for entry in built["measures"]}
        assert measures["op.acc.6"]["name"].startswith("Mecanismo de autenticación")
        assert "ENS-ACC-02" in measures["op.acc.6"]["findings"]
        assert "ENS-BAK-02" in measures["mp.si.2"]["findings"]
        assert all(entry["status"] in ("ok", "n/a") for entry in built["measures"])

    def test_it_carries_the_evidence_an_auditor_asks_for(self) -> None:
        built = make()

        assert built["regulation"].startswith("Real Decreto 311/2022")
        assert built["indicators"]["mfa_percent"] == 100.0
        assert {item["key"] for item in built["baseline"]} >= {"auth.session.idle_minutes"}
        assert built["accounts"][0] == {
            "username": "sofia",
            "role": "security",
            "status": "active",
            "mfa": True,
            "passkeys": 1,
            "last_login_at": built["accounts"][0]["last_login_at"],
            "created_at": built["accounts"][0]["created_at"],
        }
        assert "token_hash" not in json.dumps(built)

    def test_its_digest_covers_everything_but_itself(self) -> None:
        built = make()

        assert built["sha256"] == report.report_digest(built)
        built["verdict"] = "ok-edited"
        assert built["sha256"] != report.report_digest(built)

    def test_the_markdown_lists_measures_and_findings(self) -> None:
        text = report.render_markdown(make())

        assert text.startswith("# Informe ENS (categoría MEDIA) - central-1")
        assert "| op.acc.3 | Segregación de funciones y tareas |" in text
        assert "### ENS-LOG-01 - Audit log intact: OK" in text

    def test_the_bundle_is_owner_only_with_sums(self, tmp_path: Path) -> None:
        built = make()

        written = report.write_bundle(built, tmp_path / "evidence")

        assert stat.S_IMODE((tmp_path / "evidence").stat().st_mode) == 0o700
        for path in written.values():
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
        for line in written["sums"].read_text().splitlines():
            digest, name = line.split("  ")
            data = (tmp_path / "evidence" / name).read_bytes()
            assert hashlib.sha256(data).hexdigest() == digest

    def test_base_measures(self) -> None:
        assert report.base_measure("op.acc.6.r2") == "op.acc.6"
        assert report.base_measure("mp.eq.2") == "mp.eq.2"
        assert report.base_measure("org.1.3") == "org.1"
        assert report.base_measure("op.exp.10") == "op.exp.10"
