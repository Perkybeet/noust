# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The report of the frameworks that are on: the document a check leaves behind.

``noust ens report`` was a check plus a document to hand to an auditor, for one
framework. The generic check (:mod:`noust.core.compliance.check`) judges any
framework clause by clause; this is its document, the same for every one: what was
judged, the clause of each finding, what stays organisational, and a digest so that a
copy attached to a file can be checked against what the server said when it was made.

It says nothing the check did not read, and it repeats the check's own warning: a pass
is a technical control in force on this server, never a statement that the server or the
organisation complies. The ENS evidence bundle (:mod:`noust.core.ens.report`) is the
richer document for that one framework; this is what every other one has.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from noust.core.compliance.check import DISCLAIMER, STATUSES, CheckResult

_LABEL = {"ok": "OK", "warning": "WARNING", "fail": "FAIL", "n/a": "N/A"}


def build(result: CheckResult) -> dict[str, Any]:
    """
    Assemble the report of a check.

    Args:
        result: The check, run for the frameworks the report is about.

    Returns:
        The report, JSON-ready. ``sha256`` is the digest of the report without that
        field, canonically encoded.
    """
    built: dict[str, Any] = {
        "format": "noust-compliance-report",
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": result.host,
        "noust_version": result.version,
        "checked_at": result.checked_at,
        "verdict": result.verdict,
        "enabled": list(result.enabled),
        "frameworks": [entry.to_dict() for entry in result.results],
        "errors": dict(result.errors),
        "disclaimer": DISCLAIMER,
    }
    built["sha256"] = digest(built)
    return built


def digest(report: dict[str, Any]) -> str:
    """
    Args:
        report: A report.

    Returns:
        The SHA-256 of the report without its ``sha256`` field, as canonical JSON.
    """
    body = {key: value for key, value in report.items() if key != "sha256"}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _cell(text: Any) -> str:
    """
    Args:
        text: A value for a table cell.

    Returns:
        It on one line, with the table's own separator escaped.
    """
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict[str, Any]) -> str:
    """
    Render the report for people.

    Args:
        report: What :func:`build` returned.

    Returns:
        Markdown: the result per framework, then every finding that is not a plain
        pass with its clauses, evidence and what to do, then what stays organisational.
    """
    on = ", ".join(report["enabled"]) or "none"
    lines = [
        f"# Compliance report - {report['host']}",
        "",
        f"- Checked: {report['checked_at']} (report made {report['generated_at']})",
        f"- Noust {report['noust_version']}; frameworks on: {on}",
        f"- Result: **{_LABEL.get(report['verdict'], report['verdict'])}**",
        f"- SHA-256 of the report (canonical JSON): `{report['sha256']}`",
        "",
        report["disclaimer"],
        "",
    ]
    if not report["frameworks"]:
        lines += [
            "No framework is on, so nothing was judged. Turn one on "
            "(`noust compliance enable FRAMEWORK`) or ask for one anyway "
            "(`noust compliance check --framework FRAMEWORK`).",
            "",
        ]
    for entry in report["frameworks"]:
        state = "on" if entry["enabled"] else "off"
        counts = ", ".join(f"{_LABEL[s]} {entry['counts'].get(s, 0)}" for s in STATUSES)
        lines += [
            f"## {entry['name']} ({state}): {_LABEL.get(entry['verdict'], entry['verdict'])}",
            "",
            counts,
            "",
            "| Control | Clauses | Status | Found |",
            "|---|---|:-:|---|",
        ]
        for finding in entry["findings"]:
            lines.append(
                f"| {finding['control']} | {_cell('; '.join(finding['clauses']))} | "
                f"{_LABEL.get(finding['status'], finding['status'])} | {_cell(finding['summary'])} |"
            )
        lines.append("")
        for finding in entry["findings"]:
            if finding["status"] == "ok" or not (finding["evidence"] or finding["remediation"]):
                continue
            lines += [f"### {finding['control']}: {_LABEL.get(finding['status'])}", ""]
            if finding["evidence"]:
                lines += ["```", *[str(item) for item in finding["evidence"]], "```", ""]
            if finding["remediation"]:
                lines += [f"What to do: {finding['remediation']}", ""]
        if entry["organisational"]:
            lines += ["Stays organisational:", ""]
            lines += [f"- {item}" for item in entry["organisational"]]
            lines.append("")
        if entry["not_covered"]:
            lines += ["Technical requirements Noust does not enforce:", ""]
            lines += [f"- {item}" for item in entry["not_covered"]]
            lines.append("")
    if report["errors"]:
        lines += ["## Areas that could not be read", ""]
        lines += [f"- {area}: {error}" for area, error in report["errors"].items()]
        lines.append("")
    return "\n".join(lines)
