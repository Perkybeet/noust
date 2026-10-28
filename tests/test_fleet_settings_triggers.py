# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What makes a central look again at a server's settings, and what it forgets with the server.

Three things start a reconciliation: saving a setting of the fleet (the route's job), a server
answering after it was silent (here), and a person pressing "apply" (the action). The second is
noted by whichever process saw the server come back - the console's or the monitor's - in the
store, and acted on by the console's watcher, so that it is not lost when the monitor is the one
that saw it. A server that is added gets the fleet's settings the same way, and one that is
removed leaves nothing of the fleet behind on the central.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.store import NoustStore
from noust.fleet.aggregate import gather, set_aggregator
from noust.fleet.liveness import UNREACHABLE_GRACE_SECONDS, Liveness
from noust.fleet.settings.desired import FleetSettings
from tests.support.fleet_views_support import Central, build_central

T0 = 1_000_000.0
MANAGE = frozenset({"settings.manage"})


@pytest.fixture
def central(tmp_path: Path):
    built = build_central(tmp_path)
    # Adding a server notes it for a first look: these tests are about what comes after.
    for name in built.nodes:
        FleetSettings(built.store).clear_resync(name)
    yield built
    set_aggregator(None)
    NoustStore.reset_instance()


def look(central: Central) -> None:
    gather("servers", manager=central.manager, refresh=True)


class TestTheVerdict:
    def test_a_server_that_answers_after_a_silence_came_back(self, store: NoustStore) -> None:
        liveness = Liveness(store)
        liveness.failed("web-2", T0)

        assert liveness.answered("web-2", T0 + 10).came_back is True

    def test_a_server_that_was_never_silent_did_not_come_back(self, store: NoustStore) -> None:
        assert Liveness(store).answered("web-2", T0).came_back is False

    def test_it_comes_back_once_per_silence(self, store: NoustStore) -> None:
        liveness = Liveness(store)
        liveness.failed("web-2", T0)
        liveness.answered("web-2", T0 + 10)

        assert liveness.answered("web-2", T0 + 20).came_back is False

    def test_a_paused_server_does_not_come_back_until_its_operator_resumes_it(
        self, store: NoustStore
    ) -> None:
        liveness = Liveness(store)
        liveness.pause("web-2")

        assert liveness.answered("web-2", T0).came_back is False


class TestThroughTheAggregator:
    def test_a_server_seen_answering_after_it_was_down_is_noted_for_a_new_look(
        self, central: Central
    ) -> None:
        settings = FleetSettings(central.store)
        central.nodes["web-3"].down = True
        look(central)
        central.fleet.clock.advance(UNREACHABLE_GRACE_SECONDS + 1)
        look(central)
        assert settings.pending_resync() == []

        central.nodes["web-3"].down = False
        central.fleet.clock.advance(40)
        look(central)

        assert settings.pending_resync() == ["web-3"]

    def test_a_blip_that_ended_before_it_was_an_outage_is_noted_too(self, central: Central) -> None:
        # The server may have missed a push in the blip: what was missed is only known by asking.
        central.nodes["web-3"].down = True
        look(central)
        central.nodes["web-3"].down = False
        central.fleet.clock.advance(40)
        look(central)

        assert FleetSettings(central.store).pending_resync() == ["web-3"]

    def test_a_server_that_never_failed_is_not_noted(self, central: Central) -> None:
        look(central)
        look(central)

        assert FleetSettings(central.store).pending_resync() == []

    def test_a_store_that_cannot_be_written_never_stops_a_look(
        self, central: Central, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sqlite3

        def broken(self: FleetSettings, node: str) -> None:
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(FleetSettings, "request_resync", broken)
        central.nodes["web-3"].down = True
        look(central)
        central.nodes["web-3"].down = False
        central.fleet.clock.advance(40)

        result = gather("servers", manager=central.manager, refresh=True)

        outcome = result.outcome("web-3")
        assert outcome is not None and outcome.status == "ok"


class TestTheFleetsMembership:
    def test_a_server_that_is_removed_leaves_nothing_of_the_fleet_behind(
        self, central: Central
    ) -> None:
        settings = FleetSettings(central.store)
        settings.save(
            "variable",
            "REGION",
            {"value": "eu"},
            scope="node:web-3",
            person="ana",
            held=MANAGE,
        )
        settings.set_mode("web-3", "independent", person="ana")
        settings.request_resync("web-3")

        central.manager.remove("web-3", revoke=False)

        assert settings.get("variable", "REGION", "node:web-3") is None
        assert settings.mode("web-3") == "managed"
        assert settings.pending_resync() == []

    def test_a_server_that_is_added_is_noted_so_it_gets_the_fleets_settings(
        self, central: Central
    ) -> None:
        central.fleet.added("web-9")

        assert "web-9" in FleetSettings(central.store).pending_resync()
