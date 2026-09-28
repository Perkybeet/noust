# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``POST /api/apps/import`` answers the whole plan, not only a count.

The console confirms an import in sudo mode; what it confirms must be what
runs. The queued job carries the plan in its metadata (every cron job with
its user, directory and command, what previews copy, why it needs a yes)
and the response's message spells the steps out, and the job knows who
asked, for the audit record of each cron job it creates.
"""

# ruff: noqa: F811

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.test_web_app_export import (  # noqa: F401  (pytest resolves fixtures by name)
    STRIPE,
    app,
    elevated,
    exported,
    master_token,
    queued,
    session,
    store,
)


def test_the_queued_import_carries_the_detailed_plan(
    elevated: TestClient, queued: list[dict[str, Any]]
) -> None:
    document = exported(elevated)
    document["cron"] = [
        {
            "name": "shop-example-com-sync",
            "schedule": "hourly",
            "command": "/usr/bin/true",
            "user": "root",
            "working_directory": "/etc",
        }
    ]
    document["previews"] = {"base_domain": "pr.example.com", "allow_bots": True}

    response = elevated.post(
        "/api/apps/import",
        json={"document": document, "domain": "store.example.org", "env": {"STRIPE_KEY": STRIPE}},
    )

    assert response.status_code == 202, response.text
    [job] = queued
    plan = job["metadata"]["plan"]
    cron_line = (
        "cron store-example-org-sync [hourly] as root (root: the whole server) in /etc "
        "(outside the application): /usr/bin/true"
    )
    assert cron_line in plan["steps"]
    assert any("secrets included" in step for step in plan["steps"])
    assert len(plan["confirm"]) == 2
    assert job["kwargs"]["actor"] == job["actor"], "the job audits under the caller's name"
    assert cron_line in response.json()["message"]
    assert STRIPE not in response.text
