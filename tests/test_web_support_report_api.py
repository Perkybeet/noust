# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``GET /api/system/support-report`` (checklist N-33): the console's copy of ``noust support report``.

The report itself is pinned in ``tests/test_support_report.py``. What this module owns is the
translation: the route builds the same document, the journal excerpt is only there for a
credential that may reveal secrets, and the options reach the reporter.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.managers.support_report import ReportSection, SupportReport
from noust.web.api import system as system_api
from noust.web.api.auth import get_current_session
from noust.web.permissions import Permission


class FakeReporter:
    """Stands in for the reporter and keeps how it was built."""

    built: list[dict[str, Any]] = []

    def __init__(self, **options: Any) -> None:
        self.options = options
        FakeReporter.built.append(options)

    def build(self) -> SupportReport:
        sections = (
            ReportSection("noust", "Noust", "Noust: 3.4.0"),
            ReportSection("journal", "Journal", "(left out)", collected=False),
        )
        return SupportReport(
            "3.4.0", "2026-10-13T10:00:00Z", self.options["hide_domains"], sections
        )


def client(monkeypatch: pytest.MonkeyPatch, permissions: list[str]) -> TestClient:
    FakeReporter.built = []
    monkeypatch.setattr(system_api, "SupportReporter", FakeReporter)
    app = FastAPI()
    app.include_router(system_api.router, prefix="/api/system")
    app.dependency_overrides[get_current_session] = lambda: {
        "session_id": "t",
        "permissions": permissions,
    }
    return TestClient(app)


def test_it_answers_the_report_as_markdown_and_as_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    api = client(monkeypatch, [Permission.SERVER_READ])

    response = api.get("/api/system/support-report")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["markdown"].startswith("# Noust support report")
    assert [s["key"] for s in body["sections"]] == ["noust", "journal"]
    assert body["sections"][1]["collected"] is False
    assert body["version"] == "3.4.0"


def test_the_journal_is_left_out_for_a_credential_that_may_not_reveal_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = client(monkeypatch, [Permission.SERVER_READ])

    api.get("/api/system/support-report")

    assert FakeReporter.built[-1]["journal_left_out"] == (
        "reading the journal needs the right to reveal secrets"
    )


def test_the_journal_is_read_for_a_credential_that_may(monkeypatch: pytest.MonkeyPatch) -> None:
    api = client(monkeypatch, [Permission.SERVER_READ, Permission.SECRETS_REVEAL])

    api.get("/api/system/support-report?lines=30&hide_domains=true")

    options = FakeReporter.built[-1]
    assert options["journal_left_out"] is None
    assert options["journal_lines"] == 30
    assert options["hide_domains"] is True


def test_asking_for_no_lines_leaves_the_journal_out_and_says_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = client(monkeypatch, [Permission.SERVER_READ, Permission.SECRETS_REVEAL])

    api.get("/api/system/support-report?lines=0")

    assert FakeReporter.built[-1]["journal_left_out"] == "--lines 0 was given"


@pytest.mark.parametrize("lines", ["-1", "501", "many"])
def test_a_number_of_lines_outside_the_range_is_refused(
    monkeypatch: pytest.MonkeyPatch, lines: str
) -> None:
    api = client(monkeypatch, [Permission.SERVER_READ])

    response = api.get(f"/api/system/support-report?lines={lines}")

    assert response.status_code == 422
    assert FakeReporter.built == []
