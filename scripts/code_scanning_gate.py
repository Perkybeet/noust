# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Fail while the repository has an open code scanning alert of high or critical severity.

A finding of CodeQL or Trivy is either fixed or dismissed in GitHub with the reason written down
(docs/code-scanning.md); what is not allowed is to leave it open and unread, which is how 434
alerts reached a release without anyone having decided one. ``security.yml`` runs this after the
analyses are uploaded, on the branch they ran on:

    gh api --paginate "repos/OWNER/REPO/code-scanning/alerts?state=open&ref=refs/heads/main" \\
        > alerts.json
    python scripts/code_scanning_gate.py alerts.json

Exit status: 0 when nothing of that severity is open, 1 when something is, 2 when the answer is
not a list of alerts (an expired token or a missing permission must not look like a clean run).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

#: Security severities that block, worst first.
BLOCKING = ("critical", "high")

#: What a rule with no security severity (a lint-like query) blocks by: its own level.
BLOCKING_LEVELS = ("error",)


def load_alerts(text: str) -> list[dict[str, Any]]:
    """
    Read the alerts out of what ``gh api --paginate`` printed.

    Args:
        text: One JSON array, or several one after the other (one per page).

    Returns:
        Every alert.

    Raises:
        ValueError: There is JSON, but not a list of alerts (an error message of the API).
    """
    decoder = json.JSONDecoder()
    alerts: list[dict[str, Any]] = []
    position = 0
    while True:
        while position < len(text) and text[position].isspace():
            position += 1
        if position >= len(text):
            return alerts
        value, position = decoder.raw_decode(text, position)
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise ValueError(f"Not a list of alerts: {str(value)[:200]}")
        alerts.extend(value)


def severity_of(alert: dict[str, Any]) -> str:
    """
    Say how serious an alert is.

    Args:
        alert: One alert of the API.

    Returns:
        Its security severity, or its rule's level when the rule has none.
    """
    rule = alert.get("rule") or {}
    return str(rule.get("security_severity_level") or rule.get("severity") or "none")


def blocking(alerts: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Pick the alerts that keep a branch from being called decided.

    Args:
        alerts: Alerts of the API.

    Returns:
        The open ones of high or critical severity (or, for a rule with no security
        severity, of level error), worst first.
    """
    found = []
    for alert in alerts:
        if alert.get("state") != "open":
            continue
        rule = alert.get("rule") or {}
        level = severity_of(alert)
        if level in BLOCKING or (
            not rule.get("security_severity_level") and level in BLOCKING_LEVELS
        ):
            found.append(alert)
    return sorted(found, key=lambda a: (rank(severity_of(a)), a.get("number", 0)))


def rank(severity: str) -> int:
    """
    Order severities, the worst first.

    Args:
        severity: A severity as :func:`severity_of` returns it.

    Returns:
        A smaller number for a worse severity.
    """
    order = (*BLOCKING, *BLOCKING_LEVELS)
    return order.index(severity) if severity in order else len(order)


def report(alerts: Sequence[dict[str, Any]]) -> str:
    """
    Describe the alerts that block, one line each.

    Args:
        alerts: The result of :func:`blocking`.

    Returns:
        A table: number, severity, rule, place and where to decide it.
    """
    lines = []
    for alert in sorted(alerts, key=lambda a: (rank(severity_of(a)), a.get("number", 0))):
        place = (alert.get("most_recent_instance") or {}).get("location") or {}
        where = f"{place.get('path', '?')}:{place.get('start_line', '?')}"
        rule = (alert.get("rule") or {}).get("id", "?")
        lines.append(
            f"#{alert.get('number')} {severity_of(alert):8s} {rule}  {where}  {alert.get('html_url', '')}"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """
    Read the alerts file and judge it.

    Args:
        argv: The arguments after the program name: the path of the alerts file.

    Returns:
        0, 1 or 2 as described in the module's docstring.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("usage: code_scanning_gate.py ALERTS.json", file=sys.stderr)
        return 2
    try:
        alerts = load_alerts(Path(arguments[0]).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Could not read the alerts: {exc}", file=sys.stderr)
        return 2
    found = blocking(alerts)
    if not found:
        print(f"No open alert of high or critical severity ({len(alerts)} open in all).")
        return 0
    print(f"{len(found)} open alert(s) of high or critical severity, none decided:\n")
    print(report(found))
    print(
        "\nFix each one, or dismiss it in GitHub with the reason (false positive, used in tests, "
        "won't fix): docs/code-scanning.md says which and how."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
