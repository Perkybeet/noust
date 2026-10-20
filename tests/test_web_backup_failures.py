# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The whole output of a failed backup over the API (checklist H-10).

The notice links to ``/api/backups/failures/{id}/output``, which a signed-in operator opens in a
browser: it answers plain text, exactly what the tool said, and never anything that could be taken
for a page. The routes read, so they need the read permission and write nothing; an unknown record
is the same 404 the rest of the backups API gives, and the fixed path is not mistaken for a backup's
id.
"""

# The fixtures are imported from the tests of the thing they set up.
# ruff: noqa: F811

from __future__ import annotations

from fastapi.testclient import TestClient

from noust.core.store import get_store
from noust.managers.backup_failures import BackupFailureLog
from tests.test_web_backup_schedules import anonymous, app, client  # noqa: F401


def record(kind: str = "backup", subject: str = "shop.example.com", text: str = "boom") -> int:
    return BackupFailureLog(get_store()).record(kind, subject, text, destination=None)


class TestListing:
    def test_the_newest_come_first_without_their_output(self, client: TestClient) -> None:
        first = record(text="one")
        second = record(kind="upload", subject="b.example.com", text="two")

        response = client.get("/api/backups/failures")

        assert response.status_code == 200, response.text
        listed = response.json()["failures"]
        assert [entry["id"] for entry in listed] == [second, first]
        assert "output" not in listed[0]
        assert listed[0]["command"] == f"noust backup failures show {second}"
        assert listed[0]["size"] == 3

    def test_a_subject_and_a_limit_narrow_it(self, client: TestClient) -> None:
        record(subject="a.example.com")
        wanted = record(subject="b.example.com")
        record(subject="b.example.com")

        by_subject = client.get("/api/backups/failures", params={"subject": "b.example.com"})
        limited = client.get("/api/backups/failures", params={"limit": 1})

        assert len(by_subject.json()["failures"]) == 2
        assert wanted in [entry["id"] for entry in by_subject.json()["failures"]]
        assert len(limited.json()["failures"]) == 1

    def test_an_absurd_limit_is_refused(self, client: TestClient) -> None:
        assert client.get("/api/backups/failures", params={"limit": 100000}).status_code == 422

    def test_the_fixed_path_is_not_taken_for_a_backups_id(self, client: TestClient) -> None:
        assert client.get("/api/backups/failures").status_code == 200


class TestOneRecord:
    def test_it_says_everything_including_the_output(self, client: TestClient) -> None:
        text = "\n".join(f"line {n}" for n in range(200))
        failure_id = record(text=text)

        response = client.get(f"/api/backups/failures/{failure_id}")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["output"] == text
        assert (body["id"], body["kind"], body["subject"]) == (
            failure_id,
            "backup",
            "shop.example.com",
        )
        assert body["truncated"] is False

    def test_the_output_is_plain_text_and_nothing_else(self, client: TestClient) -> None:
        failure_id = record(text="<script>alert(1)</script>\nsecond line")

        response = client.get(f"/api/backups/failures/{failure_id}/output")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.text == "<script>alert(1)</script>\nsecond line\n"

    def test_an_unknown_record_is_a_404(self, client: TestClient) -> None:
        assert client.get("/api/backups/failures/999").status_code == 404
        assert client.get("/api/backups/failures/999/output").status_code == 404

    def test_a_record_needs_a_session(self, anonymous: TestClient) -> None:
        failure_id = record()

        assert anonymous.get(f"/api/backups/failures/{failure_id}/output").status_code == 401
        assert anonymous.get("/api/backups/failures").status_code == 401
