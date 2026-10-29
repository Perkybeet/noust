# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
When a central last spoke to this server, and whether it said it keeps watch.

A server cannot call its central, so the only way it can tell that the central is gone is that the
central stops calling. It notes, for each central that presents a fleet token, when it last did and
how often the central said it would (the central's monitor probes every server every half minute
for as long as it runs, and says so; a console probes only while someone has it open and does not).
What is pinned: the note is made once a minute and not at every request, because a central that
calls twice a second must not write that often; what the central said of itself is kept when a
later call says nothing; a malformed claim is no claim; a central the server forgets is gone from
the note; and a central's name is the one its token carries.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.store import NoustStore
from noust.fleet.central_calls import (
    MAX_HEARTBEAT,
    WRITE_EVERY,
    CentralCalls,
    central_of_token,
    heartbeat_of,
)


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def calls(store: NoustStore, clock: Clock) -> CentralCalls:
    return CentralCalls(store, clock=clock)


class TestNotingACall:
    def test_the_first_call_is_noted_with_when(self, calls: CentralCalls, clock: Clock) -> None:
        calls.note("nas")

        [call] = calls.list()
        assert call.central == "nas"
        assert call.first_call_at == call.last_call_at == clock.now
        assert call.heartbeat_s is None

    def test_a_central_that_says_it_keeps_watch_is_noted_as_doing_so(
        self, calls: CentralCalls
    ) -> None:
        calls.note("nas", heartbeat_s=30)

        assert calls.list()[0].heartbeat_s == 30

    def test_the_note_is_made_once_a_minute_and_not_at_every_request(
        self, calls: CentralCalls, clock: Clock
    ) -> None:
        calls.note("nas", heartbeat_s=30)
        first = clock.now

        clock.now += WRITE_EVERY - 1
        assert calls.note("nas", heartbeat_s=30) is False
        assert calls.list()[0].last_call_at == first

        clock.now += 2
        assert calls.note("nas", heartbeat_s=30) is True
        assert calls.list()[0].last_call_at == clock.now
        assert calls.list()[0].first_call_at == first

    def test_a_central_that_starts_to_say_it_keeps_watch_is_noted_at_once(
        self, calls: CentralCalls, clock: Clock
    ) -> None:
        calls.note("nas")
        clock.now += 1

        assert calls.note("nas", heartbeat_s=30) is True
        assert calls.list()[0].heartbeat_s == 30

    def test_what_it_said_of_itself_is_kept_when_a_later_call_says_nothing(
        self, calls: CentralCalls, clock: Clock
    ) -> None:
        # A console probes only while somebody has it open, and does not say it keeps watch.
        calls.note("nas", heartbeat_s=30)
        clock.now += WRITE_EVERY + 1

        calls.note("nas")

        assert calls.list()[0].heartbeat_s == 30

    def test_each_central_has_its_own_note(self, calls: CentralCalls, clock: Clock) -> None:
        calls.note("nas", heartbeat_s=30)
        clock.now += 5
        calls.note("backup")

        assert {c.central: c.heartbeat_s for c in calls.list()} == {"backup": None, "nas": 30}
        assert [c.central for c in calls.list()] == ["backup", "nas"]

    def test_the_throttle_is_the_servers_and_not_the_object_that_asks(
        self, store: NoustStore, clock: Clock
    ) -> None:
        # The admission of a request makes a new one at every request: if the memory of the last
        # note were the object's, a central that calls twice a second would be written twice a
        # second.
        assert CentralCalls(store, clock=clock).note("nas", heartbeat_s=30) is True
        clock.now += 1

        assert CentralCalls(store, clock=clock).note("nas", heartbeat_s=30) is False
        assert CentralCalls(store, clock=clock).note("nas") is False

        clock.now += WRITE_EVERY
        assert CentralCalls(store, clock=clock).note("nas") is True

    def test_two_servers_that_share_a_name_for_their_central_do_not_share_a_throttle(
        self, tmp_path: Path, clock: Clock
    ) -> None:
        NoustStore.reset_instance()
        first = NoustStore(tmp_path / "a.db")
        assert CentralCalls(first, clock=clock).note("nas") is True
        NoustStore.reset_instance()
        second = NoustStore(tmp_path / "b.db")

        assert CentralCalls(second, clock=clock).note("nas") is True
        assert [c.central for c in CentralCalls(second, clock=clock).list()] == ["nas"]
        NoustStore.reset_instance()

    def test_it_is_kept_across_processes_because_it_is_in_the_store(
        self, store: NoustStore, clock: Clock
    ) -> None:
        CentralCalls(store, clock=clock).note("nas", heartbeat_s=30)

        assert CentralCalls(store, clock=clock).list()[0].central == "nas"


class TestForgetting:
    def test_a_central_the_server_forgets_is_gone(self, calls: CentralCalls) -> None:
        calls.note("nas", heartbeat_s=30)
        calls.note("backup")

        assert calls.forget("nas") is True

        assert [c.central for c in calls.list()] == ["backup"]
        assert calls.forget("nas") is False

    def test_it_is_noted_again_if_it_calls_after_that(
        self, calls: CentralCalls, clock: Clock
    ) -> None:
        calls.note("nas", heartbeat_s=30)
        calls.forget("nas")
        clock.now += 1

        assert calls.note("nas") is True


class TestWhatACentralClaims:
    @pytest.mark.parametrize(("header", "seconds"), [("30", 30), ("1", 1), ("300", 300)])
    def test_a_number_of_seconds_is_the_claim(self, header: str, seconds: int) -> None:
        assert heartbeat_of(header) == seconds

    @pytest.mark.parametrize(
        "header",
        [
            None,
            "",
            "abc",
            "0",
            "-5",
            "3.5",
            "1e3",
            str(MAX_HEARTBEAT + 1),
            "30 60",
            "30\n",
            " 30",
            "٣٠",
        ],
    )
    def test_anything_else_is_no_claim_and_not_an_error(self, header: str | None) -> None:
        assert heartbeat_of(header) is None


class TestTheCentralsName:
    @pytest.mark.parametrize(
        ("token", "central"),
        [("fleet-nas", "nas"), ("fleet-nas.2", "nas"), ("fleet-web-hub.14", "web-hub")],
    )
    def test_it_is_what_the_fleet_token_carries(self, token: str, central: str) -> None:
        assert central_of_token(token) == central

    @pytest.mark.parametrize("token", ["", None, "ci", "fleet-", "fleet-A", "fleet-nas.x"])
    def test_a_token_that_is_not_a_fleet_token_names_no_central(self, token: str | None) -> None:
        assert central_of_token(token) is None
