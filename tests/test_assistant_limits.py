# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ceilings a conversation with the built-in assistant runs under (item 171).

Every limit is always there: a value of zero or "no limit" is refused, a warning comes at 80 %
and the stop at 100 %, said in a sentence that names the limit and who changes it, and the stop
comes between two steps and never in the middle of one.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from noust.core.assistant.client import Usage
from noust.core.assistant.limits import Limits, Price, UsageMeter, cost_of
from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
MODEL = "tiny"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


class Clock:
    def __init__(self, moment: datetime = NOW) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


def meter(
    store: NoustStore,
    *,
    limits: Limits | None = None,
    prices: dict[str, Price] | None = None,
    clock: Clock | None = None,
) -> UsageMeter:
    return UsageMeter(
        limits or Limits(),
        prices if prices is not None else {},
        "EUR",
        store=store,
        clock=clock or Clock(),
    )


def codes(sentences: tuple) -> list[str]:  # type: ignore[type-arg]
    return [s.code for s in sentences]


class TestTheLimitsAreAlwaysThere:
    def test_the_defaults_are_the_ones_decided(self) -> None:
        limits = Limits()
        assert limits.steps_per_turn == 20
        assert limits.input_tokens_per_conversation == 400_000
        assert limits.spend_per_conversation == 2.0
        assert limits.spend_per_month == 20.0
        assert limits.actions_per_hour == 10

    @pytest.mark.parametrize(
        "change",
        [
            {"steps_per_turn": 0},
            {"steps_per_turn": -1},
            {"steps_per_turn": None},
            {"steps_per_turn": "unlimited"},
            {"steps_per_turn": True},
            {"input_tokens_per_conversation": 0},
            {"spend_per_conversation": 0},
            {"spend_per_month": float("inf")},
            {"spend_per_month": float("nan")},
            {"actions_per_hour": 0},
            {"actions_per_hour": 10_000_000},
        ],
    )
    def test_a_limit_that_is_no_limit_is_refused(self, change: dict[str, object]) -> None:
        with pytest.raises(ValidationError) as caught:
            Limits.from_dict(change)
        assert "limit" in caught.value.message.lower() or "must" in caught.value.message.lower()

    def test_an_unknown_name_is_refused_not_ignored(self) -> None:
        with pytest.raises(ValidationError):
            Limits.from_dict({"steps": 5})

    def test_what_is_changed_is_kept_and_the_rest_stays(self) -> None:
        limits = Limits.from_dict({"steps_per_turn": 8, "spend_per_month": "35.5"})
        assert limits.steps_per_turn == 8
        assert limits.spend_per_month == 35.5
        assert limits.actions_per_hour == 10
        assert Limits.from_dict(limits.to_dict()) == limits


class TestWhatAStepCosts:
    def test_it_is_the_tokens_times_the_price_per_million(self) -> None:
        assert cost_of(Price(3.0, 15.0), Usage(1000, 200)) == pytest.approx(0.006)

    def test_a_price_is_not_negative_or_unreadable(self) -> None:
        with pytest.raises(ValidationError):
            Price.from_dict({"input_per_million": -1, "output_per_million": 2})
        with pytest.raises(ValidationError):
            Price.from_dict({"input_per_million": "x", "output_per_million": 2})
        with pytest.raises(ValidationError):
            Price.from_dict({"input_per_million": 1})

    def test_no_price_means_no_cost_and_it_is_said(self, store: NoustStore) -> None:
        pace = meter(store)
        step = pace.record_step("c1", 1, MODEL, Usage(300_000, 1000))
        assert step.cost is None
        verdict = pace.check_step("c1", 1, step=0, model=MODEL)
        assert verdict.stop is None
        assert "assistant.limit.no_price" in codes(verdict.notes)

    def test_an_estimate_is_said_to_be_one(self, store: NoustStore) -> None:
        pace = meter(store)
        pace.record_step("c1", 1, MODEL, Usage(100, 10, estimated=True))
        verdict = pace.check_step("c1", 1, step=0, model=MODEL)
        assert "assistant.limit.estimated" in codes(verdict.notes)


class TestStepsInOneTurn:
    def test_a_warning_at_eighty_percent_and_the_stop_at_the_limit(self, store: NoustStore) -> None:
        pace = meter(store, limits=Limits(steps_per_turn=10))
        assert pace.check_step("c1", 1, step=7, model=MODEL).warnings == ()
        near = pace.check_step("c1", 1, step=8, model=MODEL)
        assert near.stop is None
        assert codes(near.warnings) == ["assistant.limit.steps_near"]
        stopped = pace.check_step("c1", 1, step=10, model=MODEL)
        assert stopped.stop is not None
        assert stopped.stop.code == "assistant.limit.steps"
        assert stopped.stop.params["limit"] == 10

    def test_the_sentence_says_who_changes_it(self, store: NoustStore) -> None:
        pace = meter(store, limits=Limits(steps_per_turn=1))
        stop = pace.check_step("c1", 1, step=1, model=MODEL).stop
        assert stop is not None
        assert stop.hint is not None
        assert "settings.manage" in stop.hint


class TestTokensOfAConversation:
    def test_the_input_of_every_step_adds_up_to_the_limit(self, store: NoustStore) -> None:
        pace = meter(store, limits=Limits(input_tokens_per_conversation=100_000))
        for _ in range(4):
            pace.record_step("c1", 1, MODEL, Usage(19_000, 100))
        assert pace.check_step("c1", 1, step=0, model=MODEL).warnings == ()
        pace.record_step("c1", 1, MODEL, Usage(10_000, 100))
        assert codes(pace.check_step("c1", 1, step=0, model=MODEL).warnings) == [
            "assistant.limit.input_tokens_near"
        ]
        pace.record_step("c1", 1, MODEL, Usage(20_000, 100))
        stop = pace.check_step("c1", 1, step=0, model=MODEL).stop
        assert stop is not None and stop.code == "assistant.limit.input_tokens"

    def test_what_is_about_to_be_sent_counts_before_it_is_sent(self, store: NoustStore) -> None:
        pace = meter(store, limits=Limits(input_tokens_per_conversation=100_000))
        pace.record_step("c1", 1, MODEL, Usage(90_000, 100))
        assert (
            pace.check_step("c1", 1, step=0, model=MODEL, pending_input_tokens=5_000).stop is None
        )
        stop = pace.check_step("c1", 1, step=0, model=MODEL, pending_input_tokens=20_000).stop
        assert stop is not None and stop.code == "assistant.limit.input_tokens"

    def test_another_conversation_has_its_own_account(self, store: NoustStore) -> None:
        pace = meter(store, limits=Limits(input_tokens_per_conversation=100_000))
        pace.record_step("c1", 1, MODEL, Usage(120_000, 100))
        assert pace.check_step("c2", 1, step=0, model=MODEL).stop is None


class TestSpending:
    PRICES = {MODEL: Price(1.0, 2.0)}

    def test_a_conversation_stops_at_its_spending_limit(self, store: NoustStore) -> None:
        pace = meter(
            store,
            limits=Limits(spend_per_conversation=2.0, input_tokens_per_conversation=100_000_000),
            prices=self.PRICES,
        )
        pace.record_step("c1", 1, MODEL, Usage(1_700_000, 0))
        verdict = pace.check_step("c1", 1, step=0, model=MODEL)
        assert codes(verdict.warnings) == ["assistant.limit.conversation_spend_near"]
        pace.record_step("c1", 1, MODEL, Usage(600_000, 0))
        stop = pace.check_step("c1", 1, step=0, model=MODEL).stop
        assert stop is not None and stop.code == "assistant.limit.conversation_spend"
        assert stop.params["currency"] == "EUR"

    def test_the_month_adds_up_every_conversation_of_every_person(self, store: NoustStore) -> None:
        pace = meter(
            store,
            limits=Limits(spend_per_conversation=100.0, spend_per_month=20.0),
            prices=self.PRICES,
        )
        pace.record_step("c1", 1, MODEL, Usage(9_000_000, 0))
        pace.record_step("c2", 2, MODEL, Usage(9_000_000, 0))
        assert codes(pace.check_step("c3", 3, step=0, model=MODEL).warnings) == [
            "assistant.limit.month_spend_near"
        ]
        pace.record_step("c3", 3, MODEL, Usage(2_000_000, 0))
        stop = pace.check_step("c4", 4, step=0, model=MODEL).stop
        assert stop is not None and stop.code == "assistant.limit.month_spend"

    def test_a_new_month_starts_from_nothing(self, store: NoustStore) -> None:
        clock = Clock(datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc))
        pace = meter(
            store,
            limits=Limits(spend_per_conversation=100.0, spend_per_month=5.0),
            prices=self.PRICES,
            clock=clock,
        )
        pace.record_step("c1", 1, MODEL, Usage(6_000_000, 0))
        assert pace.check_step("c2", 1, step=0, model=MODEL).stop is not None
        clock.moment = datetime(2026, 11, 1, 0, 5, tzinfo=timezone.utc)
        assert pace.check_step("c2", 1, step=0, model=MODEL).stop is None

    def test_a_step_that_crosses_the_limit_is_recorded_and_the_next_one_is_refused(
        self, store: NoustStore
    ) -> None:
        pace = meter(
            store,
            limits=Limits(spend_per_conversation=1.0, input_tokens_per_conversation=100_000_000),
            prices=self.PRICES,
        )
        assert pace.check_step("c1", 1, step=0, model=MODEL).stop is None
        step = pace.record_step("c1", 1, MODEL, Usage(5_000_000, 0))  # never raises
        assert step.cost == pytest.approx(5.0)
        assert pace.check_step("c1", 1, step=1, model=MODEL).stop is not None


class TestChangesPerHour:
    def test_the_tenth_is_allowed_and_the_eleventh_is_not(self, store: NoustStore) -> None:
        pace = meter(store, limits=Limits(actions_per_hour=10))
        for _ in range(9):
            pace.record_action("c1", 1, "restart_application")
        assert pace.check_action(1).stop is None
        pace.record_action("c1", 1, "restart_application")
        stop = pace.check_action(1).stop
        assert stop is not None and stop.code == "assistant.limit.actions"

    def test_an_hour_later_they_are_forgotten(self, store: NoustStore) -> None:
        clock = Clock()
        pace = meter(store, limits=Limits(actions_per_hour=2), clock=clock)
        pace.record_action("c1", 1, "stop_application")
        pace.record_action("c1", 1, "stop_application")
        assert pace.check_action(1).stop is not None
        clock.moment = NOW + timedelta(minutes=61)
        assert pace.check_action(1).stop is None

    def test_each_person_has_their_own_count(self, store: NoustStore) -> None:
        pace = meter(store, limits=Limits(actions_per_hour=1))
        pace.record_action("c1", 1, "stop_application")
        assert pace.check_action(1).stop is not None
        assert pace.check_action(2).stop is None


class TestTheReport:
    def test_a_conversation_and_the_month(self, store: NoustStore) -> None:
        pace = meter(store, prices={MODEL: Price(1.0, 2.0)})
        pace.record_step("c1", 1, MODEL, Usage(1_000_000, 500_000))
        pace.record_step("c1", 1, MODEL, Usage(1_000_000, 0))
        pace.record_step("c2", 2, MODEL, Usage(100, 100, estimated=True))
        report = pace.report(conversation_id="c1")
        assert report.conversation is not None
        assert report.conversation.steps == 2
        assert report.conversation.prompt_tokens == 2_000_000
        assert report.conversation.spend == pytest.approx(3.0)
        assert report.month.steps == 3
        assert report.month.estimated_steps == 1
        assert report.currency == "EUR"
        assert report.limits == Limits()

    def test_a_step_without_a_price_is_counted_and_not_priced(self, store: NoustStore) -> None:
        pace = meter(store, prices={MODEL: Price(1.0, 2.0)})
        pace.record_step("c1", 1, MODEL, Usage(1_000_000, 0))
        pace.record_step("c1", 1, "another-model", Usage(1_000_000, 0))
        month = pace.report().month
        assert month.steps == 2
        assert month.unpriced_steps == 1
        assert month.spend == pytest.approx(1.0)
