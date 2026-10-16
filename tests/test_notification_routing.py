# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Routes, quiet hours and the one decision that uses them.

Checklist K-25 (a channel's own events), K-26 (routing by application, project,
environment or tag) and K-28 (quiet hours). Pure: no store, no clock but the
one passed in.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import pytest

from noust.core.notifications.model import OFF_BY_DEFAULT, Notification, State
from noust.core.notifications.routing import (
    HOLD_FLOOD,
    HOLD_QUIET,
    HOLD_SUMMARY,
    Flood,
    Placement,
    QuietHours,
    Route,
    Routing,
    Summary,
    decide,
    severity_of,
)


def note(
    kind: str = "deploy_failed",
    state: State = State.FAILED,
    domain: str | None = "shop.example.com",
) -> Notification:
    return Notification(
        kind=kind,
        code=f"{kind}.x",
        state=state,
        locale="en",
        title="T",
        subject=domain or "",
        summary="",
        server="web-1",
        domain=domain,
    )


NOON = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
NIGHT = datetime(2026, 10, 13, 23, 30, tzinfo=timezone.utc)
SHOP = Placement(project="shop", environment="production", tags=frozenset({"critical", "web"}))


def choose(
    routing: Routing,
    notification: Notification,
    *,
    now: datetime = NOON,
    events: dict[str, bool] | None = None,
    placement: Placement | None = SHOP,
    sent_recently=None,
):
    return decide(
        routing,
        notification,
        now=now,
        server_events=events or {},
        default_on=lambda kind: kind not in OFF_BY_DEFAULT,
        placement_of=lambda _domain: placement,
        sent_recently=sent_recently,
    )


class TestSeverity:
    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            (State.OK, "info"),
            (State.PROGRESS, "info"),
            (State.INFO, "info"),
            (State.WARNING, "warning"),
            (State.FAILED, "failed"),
        ],
    )
    def test_a_state_ranks_as_a_severity(self, state: State, expected: str) -> None:
        assert severity_of(note(state=state)) == expected


class TestRouteParsing:
    def test_no_route_reaches_everything(self) -> None:
        route = Route.from_mapping(None)

        assert not route.narrows
        assert route.allows(note(), None)
        assert route.allows(note(domain=None), None)

    def test_a_comma_separated_string_is_a_list(self) -> None:
        route = Route.from_mapping({"apps": "a.example.com, b.example.com"})

        assert route.apps == ("a.example.com", "b.example.com")

    def test_it_writes_back_only_what_is_set(self) -> None:
        route = Route.from_mapping({"apps": ["a.example.com"], "min_severity": "failed"})

        assert route.to_dict() == {"apps": ["a.example.com"], "min_severity": "failed"}
        assert Route.from_mapping(route.to_dict()) == route

    @pytest.mark.parametrize(
        "bad",
        [
            "a string",
            {"apps": "has space"},
            {"apps": ["ok", "bad name"]},
            {"apps": 5},
            {"min_severity": "info"},
            {"min_severity": "catastrophic"},
            {"server_events": "yes"},
            {"colour": "red"},
            {"apps": ["x" * 300]},
            {"tags": [f"t{i}" for i in range(100)]},
        ],
    )
    def test_a_route_that_means_nothing_is_refused(self, bad: object) -> None:
        with pytest.raises(ValueError):
            Route.from_mapping(bad)


class TestRoute:
    def test_apps_match_by_domain_and_pattern(self) -> None:
        route = Route.from_mapping({"apps": ["*.example.com"]})

        assert route.allows(note(domain="shop.example.com"), None)
        assert not route.allows(note(domain="shop.other.org"), None)

    def test_a_narrowing_route_hears_nothing_about_the_server(self) -> None:
        route = Route.from_mapping({"apps": ["shop.example.com"]})

        assert not route.allows(note(kind="disk_threshold", domain=None), None)

    def test_server_events_can_be_asked_for(self) -> None:
        route = Route.from_mapping({"apps": ["shop.example.com"], "server_events": True})

        assert route.allows(note(kind="disk_threshold", domain=None), None)
        assert not route.allows(note(domain="other.example.com"), None)

    def test_projects_and_environments_use_the_placement(self) -> None:
        route = Route.from_mapping({"projects": ["shop"], "environments": ["production"]})

        assert route.allows(note(), SHOP)
        assert not route.allows(note(), Placement(project="blog", environment="production"))
        assert not route.allows(note(), Placement(project="shop", environment="staging"))

    def test_an_environment_may_be_named_with_its_project(self) -> None:
        route = Route.from_mapping({"environments": ["shop/production"]})

        assert route.allows(note(), SHOP)
        assert not route.allows(note(), Placement(project="blog", environment="production"))

    def test_tags_match_any_listed(self) -> None:
        route = Route.from_mapping({"tags": ["critical", "db"]})

        assert route.allows(note(), SHOP)
        assert not route.allows(note(), Placement(project="x", tags=frozenset({"web"})))

    def test_an_unknown_placement_cannot_prove_a_project(self) -> None:
        route = Route.from_mapping({"projects": ["shop"]})

        assert not route.allows(note(), None)

    def test_every_list_that_is_set_must_match(self) -> None:
        route = Route.from_mapping({"apps": ["shop.example.com"], "tags": ["db"]})

        assert not route.allows(note(), SHOP)

    def test_a_minimum_severity_drops_the_lower_ones(self) -> None:
        route = Route.from_mapping({"min_severity": "warning"})

        assert route.allows(note(state=State.FAILED), None)
        assert route.allows(note(state=State.WARNING), None)
        assert not route.allows(note(state=State.OK), None)

    def test_only_failures(self) -> None:
        route = Route.from_mapping({"min_severity": "failed"})

        assert not route.allows(note(state=State.WARNING), None)


class TestQuietParsing:
    def test_none_is_none(self) -> None:
        assert QuietHours.from_mapping(None) is None
        assert QuietHours.from_mapping({}) is None

    def test_a_window_round_trips(self) -> None:
        quiet = QuietHours.from_mapping(
            {"start": "22:00", "end": "7:30", "timezone": "Europe/Madrid", "allow": "warning"}
        )

        assert quiet is not None
        assert quiet.to_dict() == {
            "start": "22:00",
            "end": "07:30",
            "timezone": "Europe/Madrid",
            "allow": "warning",
            "digest": True,
        }

    @pytest.mark.parametrize(
        "bad",
        [
            {"start": "22:00"},
            {"start": "25:00", "end": "07:00"},
            {"start": "22:00", "end": "22:00"},
            {"start": "22:00", "end": "07:00", "timezone": "Mars/Olympus"},
            {"start": "22:00", "end": "07:00", "allow": "everything"},
            {"start": "22:00", "end": "07:00", "digest": "yes"},
            {"start": "22:00", "end": "07:00", "sleep": True},
            "22:00-07:00",
        ],
    )
    def test_a_window_that_cannot_be_kept_is_refused(self, bad: object) -> None:
        with pytest.raises(ValueError):
            QuietHours.from_mapping(bad)


class TestQuietHours:
    def test_a_window_across_midnight(self) -> None:
        quiet = QuietHours.from_mapping({"start": "22:00", "end": "07:00"})
        assert quiet is not None

        assert quiet.active(NIGHT)
        assert quiet.active(datetime(2026, 10, 14, 6, 59, tzinfo=timezone.utc))
        assert not quiet.active(datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc))
        assert not quiet.active(NOON)

    def test_a_window_inside_a_day(self) -> None:
        quiet = QuietHours.from_mapping({"start": "13:00", "end": "15:00"})
        assert quiet is not None

        assert quiet.active(datetime(2026, 10, 13, 14, 0, tzinfo=timezone.utc))
        assert not quiet.active(NOON)

    def test_the_zone_decides_what_night_is(self) -> None:
        # 23:30 UTC is 01:30 in Madrid (CEST): inside 00:00-06:00 there, not in UTC.
        madrid = QuietHours.from_mapping(
            {"start": "00:00", "end": "06:00", "timezone": "Europe/Madrid"}
        )
        utc = QuietHours.from_mapping({"start": "00:00", "end": "06:00"})
        assert madrid is not None and utc is not None

        assert madrid.active(NIGHT)
        assert not utc.active(NIGHT)

    def test_it_ends_at_the_next_close(self) -> None:
        quiet = QuietHours.from_mapping({"start": "22:00", "end": "07:00"})
        assert quiet is not None

        assert quiet.ends_at(NIGHT) == datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc)
        early = datetime(2026, 10, 14, 3, 0, tzinfo=timezone.utc)
        assert quiet.ends_at(early) == datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc)

    def test_a_failure_gets_through_by_default(self) -> None:
        quiet = QuietHours.from_mapping({"start": "22:00", "end": "07:00"})
        assert quiet is not None

        assert quiet.lets_through(note(state=State.FAILED))
        assert not quiet.lets_through(note(state=State.WARNING))

    def test_none_holds_everything(self) -> None:
        quiet = QuietHours.from_mapping({"start": "22:00", "end": "07:00", "allow": "none"})
        assert quiet is not None

        assert not quiet.lets_through(note(state=State.FAILED))


class TestDecision:
    def test_a_plain_channel_sends(self) -> None:
        assert choose(Routing(), note()).action == "send"

    def test_the_server_wide_switch_applies_without_a_channel_one(self) -> None:
        assert choose(Routing(), note(), events={"deploy_failed": False}).action == "skip"

    def test_a_kind_that_ships_off_is_off_without_a_switch(self) -> None:
        assert choose(Routing(), note(kind="deploy_started", state=State.PROGRESS)).action == "skip"
        assert choose(Routing(), note(kind="cron_success", state=State.OK)).action == "skip"

    def test_the_channels_own_switch_beats_the_server_wide_one(self) -> None:
        routing = Routing(events={"deploy_started": True, "deploy_failed": False})

        started = note(kind="deploy_started", state=State.PROGRESS)
        assert choose(routing, started).action == "send"
        assert choose(routing, note(), events={"deploy_failed": True}).action == "skip"

    def test_a_route_skips_what_is_outside_it(self) -> None:
        routing = Routing(route=Route.from_mapping({"apps": ["blog.example.com"]}))

        decision = choose(routing, note(domain="shop.example.com"))

        assert decision.action == "skip"
        assert "route" in decision.reason

    def test_the_placement_is_asked_only_when_a_route_needs_it(self) -> None:
        asked: list[str] = []

        def placement_of(domain: str) -> Placement | None:
            asked.append(domain)
            return SHOP

        def run(routing: Routing) -> str:
            return decide(
                routing,
                note(),
                now=NOON,
                server_events={},
                default_on=lambda _kind: True,
                placement_of=placement_of,
            ).action

        assert run(Routing()) == "send"
        assert run(Routing(route=Route.from_mapping({"apps": ["shop.example.com"]}))) == "send"
        assert asked == []
        assert run(Routing(route=Route.from_mapping({"projects": ["shop"]}))) == "send"
        assert asked == ["shop.example.com"]

    def test_quiet_hours_hold_what_is_not_urgent_until_they_end(self) -> None:
        routing = Routing(quiet=QuietHours.from_mapping({"start": "22:00", "end": "07:00"}))

        decision = choose(routing, note(state=State.WARNING), now=NIGHT)

        assert decision.action == "hold"
        assert decision.release_at == NIGHT.replace(hour=7, minute=0) + timedelta(days=1)

    def test_quiet_hours_let_a_failure_through(self) -> None:
        routing = Routing(quiet=QuietHours.from_mapping({"start": "22:00", "end": "07:00"}))

        assert choose(routing, note(state=State.FAILED), now=NIGHT).action == "send"

    def test_outside_the_quiet_hours_everything_is_sent(self) -> None:
        routing = Routing(quiet=QuietHours.from_mapping({"start": "22:00", "end": "07:00"}))

        assert choose(routing, note(state=State.WARNING), now=NOON).action == "send"

    def test_a_skipped_event_is_never_held(self) -> None:
        routing = Routing(
            route=Route.from_mapping({"apps": ["blog.example.com"]}),
            quiet=QuietHours.from_mapping({"start": "22:00", "end": "07:00", "allow": "none"}),
        )

        assert choose(routing, note(), now=NIGHT).action == "skip"


class TestFloodParsing:
    def test_none_is_none(self) -> None:
        assert Flood.from_mapping(None) is None
        assert Flood.from_mapping({}) is None

    def test_a_cap_round_trips(self) -> None:
        flood = Flood.from_mapping({"max": 20, "minutes": 10})

        assert flood == Flood(20, 10)
        assert flood is not None and Flood.from_mapping(flood.to_dict()) == flood

    @pytest.mark.parametrize(
        "bad",
        [
            {"max": 0, "minutes": 10},
            {"max": 20},
            {"minutes": 10},
            {"max": 20, "minutes": 0},
            {"max": 20, "minutes": 5000},
            {"max": 5000, "minutes": 10},
            {"max": True, "minutes": 10},
            {"max": "20", "minutes": 10},
            {"max": 20, "minutes": 10, "burst": 3},
            "20/10",
        ],
    )
    def test_a_cap_that_means_nothing_is_refused(self, bad: object) -> None:
        with pytest.raises(ValueError, match="flood"):
            Flood.from_mapping(bad)


class TestSummaryParsing:
    def test_none_is_none(self) -> None:
        assert Summary.from_mapping(None) is None
        assert Summary.from_mapping({}) is None

    def test_a_summary_round_trips(self) -> None:
        data = {
            "at": "08:00",
            "timezone": "Europe/Madrid",
            "below": "failed",
            "days": ["mon", "fri"],
        }

        summary = Summary.from_mapping(data)

        assert summary is not None
        assert summary.to_dict() == data

    def test_the_defaults_are_every_day_in_utc_and_what_is_not_a_problem(self) -> None:
        summary = Summary.from_mapping({"at": "8:00"})

        assert summary == Summary(time(8, 0), "UTC", "warning", ())
        assert summary is not None and "days" not in summary.to_dict()

    def test_days_may_be_written_as_a_comma_separated_string_and_by_longer_names(self) -> None:
        summary = Summary.from_mapping({"at": "08:00", "days": "Friday, mon"})

        assert summary is not None and summary.days == (0, 4)

    @pytest.mark.parametrize(
        "bad",
        [
            {"timezone": "UTC"},
            {"at": "25:00"},
            {"at": "08:00", "timezone": "Mars/Olympus"},
            {"at": "08:00", "below": "info"},
            {"at": "08:00", "below": "none"},
            {"at": "08:00", "days": ["someday"]},
            {"at": "08:00", "days": 3},
            {"at": "08:00", "every": "day"},
            "08:00",
        ],
    )
    def test_a_summary_that_cannot_be_kept_is_refused(self, bad: object) -> None:
        with pytest.raises(ValueError, match="summary"):
            Summary.from_mapping(bad)


class TestSummaryTiming:
    def test_what_is_below_the_line_waits(self) -> None:
        summary = Summary(time(8, 0))

        assert summary.holds(note(state=State.OK))
        assert summary.holds(note(state=State.INFO))
        assert not summary.holds(note(state=State.WARNING))
        assert not summary.holds(note(state=State.FAILED))
        assert Summary(time(8, 0), below="failed").holds(note(state=State.WARNING))

    def test_it_goes_out_at_the_next_hour_of_the_zone(self) -> None:
        summary = Summary(time(8, 0), "Europe/Madrid")

        # 12:00 UTC is 14:00 in Madrid in October: the next 08:00 there is tomorrow.
        assert summary.next_at(NOON) == datetime(2026, 10, 14, 6, 0, tzinfo=timezone.utc)
        # 04:00 UTC is 06:00 in Madrid: today's 08:00 is still ahead.
        early = NOON.replace(hour=4)
        assert summary.next_at(early) == datetime(2026, 10, 13, 6, 0, tzinfo=timezone.utc)

    def test_at_the_hour_itself_it_is_the_next_one(self) -> None:
        assert Summary(time(12, 0)).next_at(NOON) == NOON + timedelta(days=1)

    def test_it_skips_the_days_it_does_not_go_out_on(self) -> None:
        # 2026-10-13 is a Tuesday; only Monday (0) and Friday (4) are asked for.
        summary = Summary(time(8, 0), days=(0, 4))

        assert summary.next_at(NOON) == datetime(2026, 10, 16, 8, 0, tzinfo=timezone.utc)

    def test_across_a_clock_change_it_still_lands_on_the_local_hour(self) -> None:
        summary = Summary(time(8, 0), "Europe/Madrid")
        saturday = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)  # DST ends on Oct 25

        assert summary.next_at(saturday) == datetime(2026, 10, 25, 7, 0, tzinfo=timezone.utc)


class TestFloodAndSummaryDecisions:
    def test_a_summary_holds_what_is_below_the_line_until_its_hour(self) -> None:
        routing = Routing(summary=Summary(time(8, 0)))

        decision = choose(routing, note(state=State.OK), now=NOON)

        assert (decision.action, decision.cause) == ("hold", HOLD_SUMMARY)
        assert decision.release_at == NOON.replace(hour=8) + timedelta(days=1)

    def test_a_warning_or_a_failure_does_not_wait_for_the_summary(self) -> None:
        routing = Routing(summary=Summary(time(8, 0)))

        assert choose(routing, note(state=State.WARNING)).action == "send"
        assert choose(routing, note(state=State.FAILED)).action == "send"

    def test_the_quiet_hours_come_before_the_summary(self) -> None:
        routing = Routing(
            quiet=QuietHours.from_mapping({"start": "22:00", "end": "07:00", "allow": "none"}),
            summary=Summary(time(8, 0)),
        )

        decision = choose(routing, note(state=State.OK), now=NIGHT)

        assert decision.cause == HOLD_QUIET

    def test_a_channel_over_its_cap_holds_the_rest_for_a_window(self) -> None:
        routing = Routing(flood=Flood(3, 10))

        decision = choose(routing, note(), sent_recently=lambda minutes: 3)

        assert (decision.action, decision.cause) == ("hold", HOLD_FLOOD)
        assert decision.release_at == NOON + timedelta(minutes=10)
        assert "3 messages in 10 minutes" in decision.reason

    def test_a_channel_under_its_cap_sends_and_is_counted_over_the_window(self) -> None:
        asked: list[int] = []

        def counted(minutes: int) -> int:
            asked.append(minutes)
            return 2

        assert choose(Routing(flood=Flood(3, 10)), note(), sent_recently=counted).action == "send"
        assert asked == [10]

    def test_a_channel_without_a_cap_is_never_counted(self) -> None:
        def never(minutes: int) -> int:
            raise AssertionError("nothing to count")

        assert choose(Routing(), note(), sent_recently=never).action == "send"

    def test_a_skipped_event_is_not_counted_against_the_cap(self) -> None:
        routing = Routing(
            route=Route.from_mapping({"apps": ["blog.example.com"]}), flood=Flood(1, 10)
        )

        assert choose(routing, note(), sent_recently=lambda minutes: 99).action == "skip"
