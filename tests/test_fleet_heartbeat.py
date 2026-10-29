# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A central that keeps watch says so, and a server notes when it last heard from it.

The monitor of a central probes every server every half minute for as long as it runs, and says so
in the call (``X-Noust-Heartbeat: 30``); the console probes only while somebody has it open and says
nothing. A server notes the time of every call of a fleet token and what the central said, and
expects to hear again only from one that said it keeps watch. What is pinned: what the central's
client sends and what only the monitor's probe claims; that the server notes the call whichever
route it was for; that it takes the claim as advice and never refuses a call over it; and that a
token of the server's own is not a central.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.config import Config
from noust.core.exceptions import NodeError
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.fleet import probe
from noust.fleet.aggregate import Asker
from noust.fleet.central_calls import CentralCalls
from noust.fleet.client import HEARTBEAT_HEADER
from noust.fleet.settings.signature import SyncSignature
from noust.web import auth
from noust.web.auth import FLEET_ACTOR_HEADER, FLEET_ELEVATED_HEADER
from noust.web.server import create_app, get_token_manager
from tests.support.fleet_node_security import client_from, fleet_headers
from tests.support.fleet_support import build_fleet
from tests.support.web_auth import make_config


@pytest.fixture
def fleet(tmp_path: Path):
    built = build_fleet(tmp_path)
    built.added()
    yield built
    NoustStore.reset_instance()


class TestWhatTheCentralSends:
    def test_a_client_that_keeps_watch_says_how_often_it_calls(self, fleet) -> None:
        headers = fleet.manager.client("web-2").auth_headers(
            "noust-probe", actor_scope="read", heartbeat=30
        )

        assert headers[HEARTBEAT_HEADER] == "30"
        assert HEARTBEAT_HEADER == auth.FLEET_HEARTBEAT_HEADER == "X-Noust-Heartbeat"

    def test_an_ordinary_call_says_nothing_of_it(self, fleet) -> None:
        headers = fleet.manager.client("web-2").auth_headers("ana", actor_scope="read")

        assert HEARTBEAT_HEADER not in headers

    @pytest.mark.parametrize("bad", [0, -1, 3601, True, "30", 2.5])
    def test_a_claim_that_is_not_a_number_of_seconds_is_not_sent(self, fleet, bad: Any) -> None:
        with pytest.raises(NodeError):
            fleet.manager.client("web-2").auth_headers("ana", actor_scope="read", heartbeat=bad)

    def test_the_asker_carries_it_without_changing_what_an_answer_depends_on(self) -> None:
        keeping = Asker(actor="noust-probe", scope="read", heartbeat=30)
        plain = Asker(actor="noust-probe", scope="read")

        assert keeping.headers()["heartbeat"] == 30
        assert plain.headers()["heartbeat"] is None
        assert keeping.audience == plain.audience


class TestWhoClaims:
    @pytest.fixture
    def asked(self, monkeypatch: pytest.MonkeyPatch, store: NoustStore) -> list[Asker]:
        from noust.core.store import NodeRecord

        store.save_node(
            NodeRecord(
                name="web-1", ssh_host="h", ssh_port=22, ssh_user="u", host_key="k", console_port=1
            )
        )
        seen: list[Asker] = []
        monkeypatch.setattr(
            "noust.fleet.aggregate.gather", lambda *a, asker, **kw: seen.append(asker)
        )
        monkeypatch.setattr(probe, "get_store", lambda: store)
        return seen

    def test_the_probe_of_the_monitor_claims_to_call_every_half_minute(
        self, asked: list[Asker]
    ) -> None:
        probe._gather_round(heartbeat=30)

        assert [a.heartbeat for a in asked] == [30]
        assert asked[0].actor == probe.PROBE_ACTOR

    def test_the_probe_of_a_console_claims_nothing(self, asked: list[Asker]) -> None:
        probe._gather_round()

        assert [a.heartbeat for a in asked] == [None]

    def test_the_monitors_round_is_the_one_that_claims(
        self, asked: list[Asker], store: NoustStore, tmp_path: Path
    ) -> None:
        for daemon, claim in ((True, 30), (False, None)):
            asked.clear()
            round_ = probe.ReachabilityProbe(
                daemon=daemon, store=store, lock_path=tmp_path / f"lock-{daemon}"
            )
            round_._gather()
            assert [a.heartbeat for a in asked] == [claim]


# -- What the server does with it ----------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def app(
    sandbox: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch, store: NoustStore
) -> Any:
    monkeypatch.setattr(
        "noust.core.config.DEFAULT_CONFIG_PATH", sandbox / "etc" / "noust" / "config.yaml"
    )
    Config.reset_instance()
    return create_app(make_config(sandbox))


@pytest.fixture
def fleet_token(app: Any) -> str:
    return str(get_token_manager().create_fleet_token("fleet-nas")["token"])


def call(app: Any, token: str, path: str = "/api/system/version", **extra: str) -> Any:
    return client_from(app).get(path, headers=fleet_headers(token, **extra))


class TestWhatTheServerNotes:
    def test_a_call_with_a_fleet_token_is_noted_as_its_centrals(
        self, app: Any, fleet_token: str, store: NoustStore
    ) -> None:
        response = call(app, fleet_token)

        assert response.status_code == 200, response.text
        [noted] = CentralCalls(store).list()
        assert noted.central == "nas"
        assert noted.heartbeat_s is None

    def test_what_the_central_says_of_how_often_it_calls_is_noted(
        self, app: Any, fleet_token: str, store: NoustStore
    ) -> None:
        call(app, fleet_token, **{HEARTBEAT_HEADER: "30"})

        assert CentralCalls(store).list()[0].heartbeat_s == 30

    @pytest.mark.parametrize("claim", ["soon", "0", "-3", "9999", "30 60"])
    def test_a_claim_that_is_nonsense_is_no_claim_and_the_call_still_works(
        self, app: Any, fleet_token: str, store: NoustStore, claim: str
    ) -> None:
        response = call(app, fleet_token, **{HEARTBEAT_HEADER: claim})

        assert response.status_code == 200
        assert CentralCalls(store).list()[0].heartbeat_s is None

    def test_a_push_of_the_fleets_settings_is_a_call_too(
        self, app: Any, fleet_token: str, store: NoustStore
    ) -> None:
        signature = SyncSignature(person="ana", change="c0123456789ab")

        client_from(app).get(
            "/api/managed-settings",
            headers=fleet_headers(
                fleet_token,
                **{FLEET_ACTOR_HEADER: signature.actor, FLEET_ELEVATED_HEADER: signature.elevation},
            ),
        )

        assert [c.central for c in CentralCalls(store).list()] == ["nas"]

    def test_a_token_of_the_servers_own_is_not_a_central(self, app: Any, store: NoustStore) -> None:
        issued = get_token_manager().create_api_token("ci", "read")

        response = client_from(app).get(
            "/api/system/version", headers={"Authorization": f"Bearer {issued['token']}"}
        )

        assert response.status_code == 200
        assert CentralCalls(store).list() == []

    def test_a_refused_call_is_not_noted(self, app: Any, store: NoustStore) -> None:
        response = call(app, "not-a-token")

        assert response.status_code == 401
        assert CentralCalls(store).list() == []

    def test_the_server_says_when_each_central_last_called_and_whether_it_keeps_watch(
        self, app: Any, fleet_token: str
    ) -> None:
        call(app, fleet_token, **{HEARTBEAT_HEADER: "30"})

        response = client_from(app).get("/api/managed-settings", headers=fleet_headers(fleet_token))

        assert response.status_code == 200, response.text
        [central] = response.json()["centrals"]
        assert central["central"] == "nas"
        assert central["keeps_watch"] is True and central["heartbeat_s"] == 30
        assert central["last_call_at"].endswith("+00:00")
        assert 0 <= central["quiet_for_s"] < 60

    def test_a_central_that_did_not_say_it_keeps_watch_is_listed_as_not_doing_so(
        self, app: Any, fleet_token: str
    ) -> None:
        call(app, fleet_token)

        response = client_from(app).get("/api/managed-settings", headers=fleet_headers(fleet_token))

        [central] = response.json()["centrals"]
        assert central["keeps_watch"] is False and central["heartbeat_s"] is None

    def test_a_server_that_has_not_been_called_lists_no_central(self, app: Any) -> None:
        token = str(get_token_manager().create_api_token("reader", "read")["token"])

        response = client_from(app).get(
            "/api/managed-settings", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200, response.text
        assert response.json()["centrals"] == []

    def test_a_central_that_revokes_itself_is_forgotten_with_what_it_managed(
        self, app: Any, fleet_token: str, store: NoustStore
    ) -> None:
        call(app, fleet_token, **{HEARTBEAT_HEADER: "30"})
        assert [c.central for c in CentralCalls(store).list()] == ["nas"]

        response = client_from(app).post(
            "/api/auth/fleet/revoke", headers=fleet_headers(fleet_token)
        )

        assert response.status_code == 200, response.text
        assert CentralCalls(store).list() == []

    def test_a_call_does_not_wait_on_the_note_when_the_store_cannot_be_written(
        self, app: Any, fleet_token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sqlite3

        def broken(*a: Any, **kw: Any) -> bool:
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(CentralCalls, "note", broken)

        assert call(app, fleet_token).status_code == 200
