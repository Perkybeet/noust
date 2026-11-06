# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The report of the frameworks that are on: what the ENS report gave, for every framework.

The ENS report is the check, its verdict and a document to hand to an auditor. The generic
check (:mod:`noust.core.compliance.check`) had the first two for any framework and no
document. What is pinned here is the document: it names the frameworks it judges, the clause
of each finding and what stays organisational, carries the digest that lets a copy be checked
later, and says plainly that a pass is a technical control in force and not compliance.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.core.compliance import check as compliance_check
from noust.core.compliance import report
from noust.core.config import Config


@pytest.fixture
def result(real_config: Config) -> compliance_check.CheckResult:
    real_config.set("security.frameworks", ["nis2"])
    return compliance_check.run(gather_facts=False)


class TestTheResultSaysWhenAndWhere:
    def test_it_carries_the_time_the_host_and_the_version(
        self, result: compliance_check.CheckResult
    ) -> None:
        body = result.to_dict()

        assert body["checked_at"].endswith("+00:00") or body["checked_at"].endswith("Z")
        assert body["host"] and body["version"]

    def test_it_lists_the_frameworks_that_are_on(
        self, result: compliance_check.CheckResult
    ) -> None:
        assert result.to_dict()["enabled"] == ["nis2"]

    def test_with_nothing_on_it_says_so_and_judges_nothing(self, real_config: Config) -> None:
        body = compliance_check.run(gather_facts=False).to_dict()

        assert body["enabled"] == [] and body["frameworks"] == []
        assert body["verdict"] == "ok"

    def test_a_framework_judged_while_off_is_not_listed_as_on(self, real_config: Config) -> None:
        body = compliance_check.run(["iso-27001"], gather_facts=False).to_dict()

        assert body["enabled"] == []
        assert body["frameworks"][0]["framework"] == "iso-27001"
        assert body["frameworks"][0]["enabled"] is False


class TestTheDocument:
    def test_it_names_what_it_judges(self, result: compliance_check.CheckResult) -> None:
        built = report.build(result)

        assert built["format"] == "noust-compliance-report"
        assert built["verdict"] == result.verdict
        assert [entry["framework"] for entry in built["frameworks"]] == ["nis2"]
        assert built["enabled"] == ["nis2"]
        assert built["host"] and built["noust_version"] and built["generated_at"]
        assert "does not mean the server" in built["disclaimer"]

    def test_its_digest_is_the_one_of_everything_but_itself(
        self, result: compliance_check.CheckResult
    ) -> None:
        built = report.build(result)

        assert len(built["sha256"]) == 64
        assert built["sha256"] == report.digest(built)
        tampered = {**built, "verdict": "ok" if built["verdict"] != "ok" else "fail"}
        assert report.digest(tampered) != built["sha256"]

    def test_every_finding_keeps_its_clauses(self, result: compliance_check.CheckResult) -> None:
        built = report.build(result)

        findings: list[dict[str, Any]] = built["frameworks"][0]["findings"]
        assert findings and all(item["clauses"] for item in findings)
        assert built["frameworks"][0]["organisational"]

    def test_the_markdown_reads_for_a_person(self, result: compliance_check.CheckResult) -> None:
        text = report.render_markdown(report.build(result))

        assert text.startswith("# Compliance report")
        assert "NIS2" in text
        assert "Art. 21(2)" in text
        assert "Stays organisational" in text
        assert "SHA-256" in text
        assert "does not mean the server" in text

    def test_a_pipe_in_a_cell_does_not_break_the_table(
        self, result: compliance_check.CheckResult
    ) -> None:
        built = report.build(result)
        built["frameworks"][0]["findings"][0]["summary"] = "a | b\nc"

        text = report.render_markdown(built)

        assert "a \\| b c" in text

    def test_with_nothing_on_the_document_says_no_framework_is_on(
        self, real_config: Config
    ) -> None:
        built = report.build(compliance_check.run(gather_facts=False))

        assert built["frameworks"] == []
        assert "No framework is on" in report.render_markdown(built)
