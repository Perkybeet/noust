# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``GET /api/databases/backups/{name}/data``: a dump read by ranges, for a central carrying it away (C-21).

A dump of a database is gigabytes and a request through a tunnel is cut after minutes, so a central
that moves a database to another server reads the dump in pieces, each one a request of its own and
each able to be asked again. What these tests pin is what makes that safe: it is the download's
power (sudo mode, a name and never a path), it reads exactly the range asked and nothing past the
end, and the audit trail records the reading once and not once per piece.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.managers.database.base import BaseDatabaseManager
from tests.database_backup_support import PG_ARCHIVE
from tests.test_web_database_backups import (  # noqa: F401
    _reset_store,
    audited,
    engine,
    runner,
    systemd_dir,
    take_dump,
)
from tests.test_web_databases_api import (  # noqa: F401
    _capture_queued_jobs,
    anonymous,
    app,
    client,
    elevate,
    wire,
)

# The fixtures above are the databases API's and the backups tests' own, so there is one
# definition of "a signed-in client" and of "an engine with a dump".
# ruff: noqa: F811

BASE = "/api/databases"


def data(client: TestClient, name: str, **params: Any) -> Any:
    query = {"engine": "postgresql", **params}
    return client.get(f"{BASE}/backups/{name}/data", params=query)


class TestReadingByRanges:
    def test_it_needs_sudo_mode_like_the_download(
        self, client: TestClient, engine: type[BaseDatabaseManager]
    ) -> None:
        name = take_dump(engine)

        response = data(client, name, offset=0, length=10)

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"

    def test_it_answers_the_bytes_asked_for(
        self, client: TestClient, engine: type[BaseDatabaseManager]
    ) -> None:
        name = take_dump(engine)
        elevate(client)

        response = data(client, name, offset=3, length=7)

        assert response.status_code == 200
        assert response.content == PG_ARCHIVE[3:10]
        assert response.headers["content-type"] == "application/octet-stream"

    def test_pieces_put_together_are_the_dump(
        self, client: TestClient, engine: type[BaseDatabaseManager]
    ) -> None:
        name = take_dump(engine)
        elevate(client)

        got = b""
        while len(got) < len(PG_ARCHIVE):
            piece = data(client, name, offset=len(got), length=5).content
            assert piece
            got += piece

        assert got == PG_ARCHIVE

    def test_the_last_piece_is_short_and_past_the_end_is_nothing(
        self, client: TestClient, engine: type[BaseDatabaseManager]
    ) -> None:
        name = take_dump(engine)
        elevate(client)
        size = len(PG_ARCHIVE)

        assert data(client, name, offset=size - 2, length=100).content == PG_ARCHIVE[-2:]
        past = data(client, name, offset=size + 50, length=100)

        assert past.status_code == 200 and past.content == b""

    @pytest.mark.parametrize("length", [0, -1, 64 * 1024 * 1024])
    def test_a_length_outside_what_one_piece_may_carry_is_refused(
        self, client: TestClient, engine: type[BaseDatabaseManager], length: int
    ) -> None:
        name = take_dump(engine)
        elevate(client)

        assert data(client, name, offset=0, length=length).status_code == 422

    def test_a_negative_offset_is_refused(
        self, client: TestClient, engine: type[BaseDatabaseManager]
    ) -> None:
        name = take_dump(engine)
        elevate(client)

        assert data(client, name, offset=-1, length=10).status_code == 422


class TestOnlyADump:
    @pytest.mark.parametrize(
        "name", ["..%2F..%2Fetc%2Fpasswd", "postgresql-x-20250101_010101.dump", "%2Fetc%2Fpasswd"]
    )
    def test_a_name_is_never_a_path(
        self, client: TestClient, engine: type[BaseDatabaseManager], name: str
    ) -> None:
        elevate(client)

        response = data(client, name, offset=0, length=100)

        assert response.status_code in (400, 404)
        assert b"root:" not in response.content


class TestTheAuditTrail:
    def test_the_reading_is_recorded_once_at_its_start(
        self,
        client: TestClient,
        engine: type[BaseDatabaseManager],
        audited: list[dict[str, Any]],
    ) -> None:
        name = take_dump(engine)
        elevate(client)

        for offset in range(0, len(PG_ARCHIVE), 4):
            data(client, name, offset=offset, length=4)

        events = [e for e in audited if e["event"] == "db.backup.download"]
        assert len(events) == 1
        assert events[0]["target"] == "db:postgresql"
        assert events[0]["details"] == {"file": name, "size": len(PG_ARCHIVE), "ranged": True}

    def test_a_piece_in_the_middle_alone_records_nothing(
        self,
        client: TestClient,
        engine: type[BaseDatabaseManager],
        audited: list[dict[str, Any]],
    ) -> None:
        # A retry resumes in the middle; the reading it belongs to was recorded where it began.
        name = take_dump(engine)
        elevate(client)

        data(client, name, offset=4, length=4)

        assert [e for e in audited if e["event"] == "db.backup.download"] == []


class TestInTheMaps:
    def test_the_route_asks_for_sudo_mode_and_has_a_permission(self, app: Any) -> None:
        operation = app.openapi()["paths"][f"{BASE}/backups/{{name}}/data"]["get"]

        assert operation["x-noust-requires-elevation"] is True
        assert operation["x-noust-permission"] == "databases.manage"
