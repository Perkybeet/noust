"""
When a server counts as down, how long to wait before asking again, and when to say nothing (F-09).

The rules are :mod:`noust.fleet.liveness`'s; the aggregator feeds them and the probe asks them
which servers are worth a look. What is pinned: the threshold and the grace before an outage is
declared, one announcement per outage however many looks follow, a count that goes back to
nothing when the server answers (Coolify's never did, #11416), a wait that grows while it stays
down, a pause that silences a server for maintenance, and state that survives a restart of the
console without announcing the same outage again.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.store import NoustStore
from noust.fleet import aggregate
from noust.fleet.aggregate import gather, set_aggregator
from noust.fleet.liveness import (
    BACKOFF_CYCLES,
    COUNT_EVERY_SECONDS,
    STALE_AFTER_SECONDS,
    UNREACHABLE_GRACE_SECONDS,
    UNREACHABLE_THRESHOLD,
    Liveness,
    cycles_after,
)
from tests.fleet_views_support import Central, build_central

T0 = 1_000_000.0


@pytest.fixture
def store(tmp_path: Path):
    NoustStore.reset_instance()
    built = NoustStore(tmp_path / "noust.db")
    yield built
    NoustStore.reset_instance()


@pytest.fixture
def liveness(store: NoustStore) -> Liveness:
    return Liveness(store)


GRACE = UNREACHABLE_GRACE_SECONDS


class TestTheThresholdAndTheGrace:
    def test_one_silence_is_a_blip(self, liveness):
        verdict = liveness.failed("web-2", T0)

        assert verdict.announce_down is False
        assert verdict.state.failures == 1

    def test_enough_silences_over_too_short_a_time_is_still_a_blip(self, liveness):
        for step in range(6):
            verdict = liveness.failed("web-2", T0 + step * COUNT_EVERY_SECONDS)

        assert verdict.state.failures == 6
        assert verdict.announce_down is False

    def test_the_outage_is_declared_once_the_threshold_and_the_grace_are_both_met(self, liveness):
        liveness.failed("web-2", T0)

        verdict = liveness.failed("web-2", T0 + GRACE)

        assert verdict.announce_down is True
        assert verdict.state.announced is True
        assert verdict.state.failures >= UNREACHABLE_THRESHOLD

    def test_it_is_announced_once_however_many_looks_follow(self, liveness):
        liveness.failed("web-2", T0)
        announced = [liveness.failed("web-2", T0 + GRACE + n * 60).announce_down for n in range(10)]

        assert announced.count(True) == 1

    def test_a_person_reloading_the_page_counts_as_one_look(self, liveness):
        for second in range(10):
            verdict = liveness.failed("web-2", T0 + second)

        assert verdict.state.failures == 1

    def test_a_lone_silence_long_ago_does_not_pair_with_a_later_one(self, liveness):
        liveness.failed("web-2", T0)

        verdict = liveness.failed("web-2", T0 + STALE_AFTER_SECONDS + 1)

        assert verdict.state.failures == 1
        assert verdict.state.since == T0 + STALE_AFTER_SECONDS + 1
        assert verdict.announce_down is False

    def test_an_outage_that_was_announced_is_not_forgotten_however_long_it_lasts(self, liveness):
        liveness.failed("web-2", T0)
        liveness.failed("web-2", T0 + GRACE)

        verdict = liveness.failed("web-2", T0 + 5 * 86400)

        assert verdict.state.announced is True
        assert verdict.state.since == T0
        assert verdict.announce_down is False


class TestTheCountGoesBackToNothing:
    def test_an_answer_resets_everything_and_a_new_outage_is_announced_again(self, liveness):
        liveness.failed("web-2", T0)
        liveness.failed("web-2", T0 + GRACE)

        back = liveness.answered("web-2", T0 + GRACE + 500)

        assert back.announce_recovered is True
        assert back.down_for == pytest.approx(GRACE + 500)
        assert liveness.get("web-2").failures == 0
        liveness.failed("web-2", T0 + 10_000)
        again = liveness.failed("web-2", T0 + 10_000 + GRACE)
        assert again.announce_down is True

    def test_a_blip_that_ended_before_it_was_announced_announces_nothing(self, liveness):
        liveness.failed("web-2", T0)

        back = liveness.answered("web-2", T0 + 30)

        assert back.announce_recovered is False
        assert liveness.get("web-2").failures == 0
        assert liveness.all() == {}

    def test_a_server_that_always_answers_leaves_no_row(self, liveness):
        liveness.answered("web-2", T0)

        assert liveness.all() == {}


class TestTheWaitThatGrows:
    def test_the_cycles_to_wait_grow_while_it_stays_down_and_stop_at_the_last(self):
        waits = [cycles_after(n) for n in range(0, 9)]

        assert waits[0] == 1
        assert waits[1:4] == [1, 1, 1]
        assert waits[4:] == [3, 6, 12, 12, 12]
        assert BACKOFF_CYCLES[-1] == 12

    def test_a_silent_server_is_not_asked_again_until_its_wait_is_over(self, liveness):
        for step in range(5):
            verdict = liveness.failed("web-2", T0 + step * 60, interval=30.0)

        wait = verdict.state.next_probe_at
        assert wait == T0 + 4 * 60 + 30.0 * 6
        assert liveness.due(["web-2", "web-3"], wait - 1) == ["web-3"]
        assert liveness.due(["web-2", "web-3"], wait) == ["web-2", "web-3"]

    def test_a_server_that_answers_is_asked_at_every_cycle(self, liveness):
        liveness.failed("web-2", T0)
        liveness.answered("web-2", T0 + 10)

        assert liveness.due(["web-2"], T0 + 11) == ["web-2"]


class TestThePause:
    def test_a_paused_server_is_not_counted_announced_or_asked(self, liveness):
        liveness.pause("web-2", reason="moving to a new rack", actor="ana")

        verdicts = [liveness.failed("web-2", T0 + n * 100) for n in range(5)]

        assert not any(v.announce_down for v in verdicts)
        assert liveness.get("web-2").failures == 0
        assert liveness.due(["web-2", "web-3"], T0 + 500) == ["web-3"]

    def test_the_pause_remembers_who_and_why(self, liveness):
        state = liveness.pause("web-2", reason="  moving to a new rack  ", actor="ana")

        assert (state.paused_reason, state.paused_by) == ("moving to a new rack", "ana")
        assert state.paused_at is not None
        assert liveness.get("web-2").paused is True

    def test_pausing_drops_the_outage_in_progress(self, liveness):
        liveness.failed("web-2", T0)
        liveness.failed("web-2", T0 + GRACE)

        liveness.pause("web-2")

        assert liveness.get("web-2").announced is False
        assert liveness.get("web-2").failures == 0

    def test_an_answer_does_not_end_a_pause_only_the_operator_does(self, liveness):
        liveness.pause("web-2")

        liveness.answered("web-2", T0)

        assert liveness.get("web-2").paused is True
        assert liveness.resume("web-2").paused is False
        assert liveness.all() == {}

    def test_an_overlong_reason_is_cut(self, liveness):
        assert len(liveness.pause("web-2", reason="x" * 500).paused_reason or "") == 200


class TestWhatSurvivesARestart:
    def test_the_state_is_in_the_store_not_in_the_process(self, store):
        Liveness(store).failed("web-2", T0, error="Connection refused")
        Liveness(store).failed("web-2", T0 + GRACE)

        state = Liveness(store).get("web-2")

        assert (state.failures, state.announced, state.since) == (2, True, T0)
        assert state.last_error == "Connection refused"

    def test_forgetting_a_node_drops_its_row(self, liveness):
        liveness.pause("web-2")

        liveness.forget("web-2")

        assert liveness.all() == {}


@pytest.fixture
def central(tmp_path: Path):
    built = build_central(tmp_path)
    yield built
    set_aggregator(None)
    NoustStore.reset_instance()


@pytest.fixture
def told(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "noust.core.notifications.fleet.notify_node_unreachable",
        lambda node, **kw: sent.append(("down", node)),
    )
    monkeypatch.setattr(
        "noust.core.notifications.fleet.notify_node_recovered",
        lambda node, **kw: sent.append(("up", node)),
    )
    return sent


def _look(central: Central) -> None:
    gather("servers", manager=central.manager, refresh=True)


class TestThroughTheAggregator:
    def test_an_outage_is_announced_once_and_its_end_once(self, central, told):
        central.nodes["web-3"].down = True
        _look(central)
        central.fleet.clock.advance(GRACE + 1)
        _look(central)
        central.fleet.clock.advance(40)
        _look(central)
        assert told == [("down", "web-3")]

        central.nodes["web-3"].down = False
        central.fleet.clock.advance(40)
        _look(central)

        assert told == [("down", "web-3"), ("up", "web-3")]

    def test_a_console_restarted_in_the_middle_of_an_outage_does_not_announce_it_again(
        self, central, told
    ):
        central.nodes["web-3"].down = True
        _look(central)
        central.fleet.clock.advance(GRACE + 1)
        _look(central)
        assert told == [("down", "web-3")]

        # The process dies and comes back: a new aggregator knows nothing in memory.
        set_aggregator(aggregate.Aggregator(clock=central.fleet.clock))
        central.fleet.clock.advance(60)
        _look(central)
        central.fleet.clock.advance(60)
        _look(central)

        assert told == [("down", "web-3")]
        central.nodes["web-3"].down = False
        central.fleet.clock.advance(60)
        _look(central)
        assert told[-1] == ("up", "web-3")

    def test_a_paused_server_that_is_down_tells_nobody_and_is_still_shown_down(self, central, told):
        Liveness(central.store).pause("web-3", reason="maintenance", actor="ana")
        central.nodes["web-3"].down = True

        for _ in range(4):
            _look(central)
            central.fleet.clock.advance(GRACE)

        assert told == []
        assert central.store.get_node("web-3").status == "unreachable"

    def test_the_fleet_rows_say_a_server_is_paused_and_why(self, central):
        Liveness(central.store).pause("web-3", reason="maintenance", actor="ana")

        result = gather("servers", manager=central.manager, refresh=True)

        rows = {row["name"]: row for row in result.items}
        assert rows["web-3"]["monitoring"]["paused"] is True
        assert rows["web-3"]["monitoring"]["paused_reason"] == "maintenance"
        assert rows["web-2"]["monitoring"] is None

    def test_removing_a_node_forgets_its_silence(self, central, told):
        central.nodes["web-3"].down = True
        _look(central)
        assert "web-3" in Liveness(central.store).all()

        central.manager.remove("web-3", revoke=False)

        assert "web-3" not in Liveness(central.store).all()


def test_the_probes_cycle_is_the_one_the_waits_are_counted_in() -> None:
    from noust.fleet import probe

    assert probe.PROBE_INTERVAL_SECONDS == 30.0
    assert aggregate.UNREACHABLE_GRACE_SECONDS == UNREACHABLE_GRACE_SECONDS
