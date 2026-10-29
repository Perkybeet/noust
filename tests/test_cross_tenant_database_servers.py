# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A member of one team counts only the databases of its own teams on a database server (4.1).

The list of database servers is read by every team, like the engines' list it joins with the
instances': where databases run is a catalogue. What it adds to that catalogue is data, how many
databases each server holds, and that is the caller's: another team's databases are not counted,
and one an engine holds that Noust never recorded belongs to nobody. Choosing a server as the
default decides where every team's next database goes, so it is not a team's to do.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.store import NoustStore
from noust.managers.database.servers import DatabaseServers
from noust.managers.database.service import DatabaseListing, DatabaseService, DatabaseView
from tests.support.teams_support import World, build_world


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Any:
    built = build_world(tmp_path, monkeypatch)
    # The engines are not running here: they report these three, one of which (orphan) the store
    # never recorded and so belongs to nobody.
    monkeypatch.setattr(
        DatabaseService,
        "listing",
        lambda self, engine=None: DatabaseListing(
            [
                DatabaseView(name="shop_db", engine="postgresql", tracked=True),
                DatabaseView(name="blog_db", engine="postgresql", tracked=True),
                DatabaseView(name="orphan", engine="postgresql"),
            ],
            [],
        ),
    )
    monkeypatch.setattr(
        DatabaseServers,
        "_system",
        lambda self: [
            {
                "key": "postgresql",
                "engine": "postgresql",
                "flavour": "postgresql",
                "display_name": "PostgreSQL",
                "version": "16.3",
                "runs_in": "system",
                "name": None,
                "state": "running",
                "running": True,
                "port": 5432,
                "memory_limit_mb": None,
                "image": None,
                "digest": None,
                "custom_image": False,
                "app": None,
                "support": "supported",
                "end_of_life": "2028-11-09",
                "created_at": None,
            }
        ],
    )
    monkeypatch.setattr(DatabaseServers, "_containers", lambda self, problems: [])
    try:
        yield built
    finally:
        NoustStore.reset_instance()


def count(world: World, headers: dict[str, str]) -> int | None:
    response = world.client.get("/api/databases/servers", headers=headers)
    assert response.status_code == 200, response.text
    [server] = response.json()["servers"]
    return server["databases"]


class TestWhatEachCallerCounts:
    def test_a_member_counts_its_teams_databases_and_nobody_elses(self, world: World) -> None:
        assert count(world, world.as_("alice")) == 1
        assert count(world, world.as_("bob")) == 1

    def test_whoever_sees_every_team_counts_them_all(self, world: World) -> None:
        for headers in (world.as_("admin"), world.master):
            assert count(world, headers) == 3

    def test_one_server_has_the_same_count(self, world: World) -> None:
        one = world.client.get("/api/databases/servers/postgresql", headers=world.as_("alice"))

        assert one.status_code == 200, one.text
        assert one.json()["databases"] == 1


class TestChoosingIsTheServers:
    @pytest.mark.parametrize("method", ["PUT", "DELETE"])
    def test_a_member_of_a_team_cannot_choose_the_default(self, world: World, method: str) -> None:
        response = world.client.request(
            method, "/api/databases/servers/postgresql/default", headers=world.as_("alice")
        )

        assert response.status_code in (403, 404), response.text

    def test_an_administrator_can(self, world: World) -> None:
        response = world.client.put(
            "/api/databases/servers/postgresql/default", headers=world.as_("admin")
        )

        assert response.status_code == 200, response.text
        assert response.json()["default_source"] == "chosen"
