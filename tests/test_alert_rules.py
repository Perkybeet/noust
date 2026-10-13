# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Alert rules: what they accept, how they are stored, how a limit is written.

Checklist G-18: rules on CPU, memory, swap, disk, load and traffic, for the
server or an application, with a threshold, a duration, a hysteresis and a
cooldown.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.alerts import (
    METRICS,
    AlertRule,
    AlertRules,
    AlertState,
    parse_threshold,
    validate_rule,
)
from noust.core.alerts.model import MAX_RULES, format_value
from noust.core.alerts.rules import FIRING
from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def rules(tmp_path: Path, clock: Clock) -> Iterator[AlertRules]:
    NoustStore.reset_instance()
    yield AlertRules(NoustStore(tmp_path / "noust.db"), clock=clock)
    NoustStore.reset_instance()


def rule(**overrides: object) -> AlertRule:
    fields: dict = {"name": "cpu-high", "metric": "cpu", "comparator": ">", "threshold": 90.0}
    fields.update(overrides)
    return AlertRule(**fields)


class TestThresholds:
    @pytest.mark.parametrize(
        ("text", "unit", "expected"),
        [
            ("90", "percent", 90.0),
            ("90%", "percent", 90.0),
            (" 87.5 % ", "percent", 87.5),
            (90, "percent", 90.0),
            ("512MB", "bytes", 512 * 1024**2),
            ("512 mb", "bytes", 512 * 1024**2),
            ("2GB", "bytes", 2 * 1024**3),
            ("1048576", "bytes", 1048576.0),
            ("2MB/s", "bytes_s", 2 * 1024**2),
            ("500KB", "bytes_s", 500 * 1024.0),
            ("1.5", "number", 1.5),
            ("120", "per_min", 120.0),
        ],
    )
    def test_a_limit_is_read_in_the_metrics_unit(
        self, text: object, unit: str, expected: float
    ) -> None:
        assert parse_threshold(text, unit) == expected  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("text", "unit"),
        [
            ("fast", "percent"),
            ("", "percent"),
            ("90MB", "percent"),
            ("90%", "bytes"),
            ("2MB/s", "bytes"),
            ("90kg", "bytes"),
            ("nan", "number"),
            ("1e400", "number"),
            (True, "number"),
        ],
    )
    def test_what_is_not_a_limit_is_refused_with_how_to_write_one(
        self, text: object, unit: str
    ) -> None:
        with pytest.raises(ValidationError):
            parse_threshold(text, unit)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("unit", "value", "limit", "expected"),
        [
            ("percent", 93.44, False, "93.4%"),
            ("percent", 90.0, True, "90%"),
            ("bytes", 512 * 1024**2, False, "512.0 MB"),
            ("bytes_s", 2 * 1024**2, False, "2.0 MB/s"),
            ("per_min", 119.6, False, "120/min"),
            ("number", 1.5, False, "1.50"),
            ("number", 4.0, True, "4"),
        ],
    )
    def test_a_reading_is_said_with_its_unit(
        self, unit: str, value: float, limit: bool, expected: str
    ) -> None:
        assert format_value(unit, value, limit=limit) == expected


class TestWhatARuleAccepts:
    def test_a_sensible_rule_passes(self) -> None:
        assert validate_rule(rule()) == rule()

    def test_every_metric_has_a_scope_and_a_unit(self) -> None:
        for spec in METRICS.values():
            assert spec.scope in {"server", "app"}
            assert ("{app}" in spec.series) == (spec.scope == "app")

    @pytest.mark.parametrize(
        ("overrides", "field"),
        [
            ({"name": "Bad Name"}, "name"),
            ({"name": ""}, "name"),
            ({"metric": "joy"}, "metric"),
            ({"comparator": "="}, "comparator"),
            ({"threshold": float("inf")}, "threshold"),
            ({"threshold": 5000.0}, "threshold"),
            ({"duration_s": 10}, "duration"),
            ({"duration_s": 10**6}, "duration"),
            ({"cooldown_s": -1}, "cooldown"),
            ({"hysteresis": -3.0}, "hysteresis"),
            ({"severity": "meh"}, "severity"),
            ({"app_domain": "shop.example.com"}, "app_domain"),
            ({"metric": "app_cpu", "app_domain": "not a domain!"}, "app_domain"),
            ({"note": "x" * 600}, "note"),
        ],
    )
    def test_what_cannot_be_kept_is_refused_naming_the_field(
        self, overrides: dict, field: str
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            validate_rule(rule(**overrides))

        assert caught.value.field == field

    def test_an_application_metric_may_name_an_application_or_all(self) -> None:
        validate_rule(rule(metric="app_cpu", app_domain="shop.example.com"))
        validate_rule(rule(metric="app_5xx", app_domain=None))


class TestTheLimitsMeaning:
    def test_above_breaches_over_the_limit(self) -> None:
        r = rule(comparator=">", threshold=90.0, hysteresis=5.0)

        assert r.breached(90.1) and not r.breached(90.0)
        assert r.cleared(85.0) and not r.cleared(85.1)

    def test_at_or_above_includes_the_limit(self) -> None:
        assert rule(comparator=">=").breached(90.0)

    def test_below_breaches_under_the_limit_and_clears_above_it(self) -> None:
        r = rule(metric="app_memory", comparator="<", threshold=100.0, hysteresis=20.0)

        assert r.breached(99.0) and not r.breached(100.0)
        assert r.cleared(120.0) and not r.cleared(119.0)

    def test_without_a_hysteresis_it_clears_at_the_limit(self) -> None:
        r = rule(comparator=">", threshold=90.0)

        assert r.cleared(90.0) and not r.cleared(90.5)


class TestStoring:
    def test_a_rule_round_trips(self, rules: AlertRules) -> None:
        saved = rules.save(
            rule(
                metric="app_5xx",
                app_domain="shop.example.com",
                comparator=">=",
                threshold=25.0,
                duration_s=600,
                hysteresis=5.0,
                cooldown_s=7200,
                severity="failed",
                remind=True,
                note="the checkout",
            ),
            actor="alice",
        )

        assert saved.id is not None and saved.created_at is not None
        assert rules.get("cpu-high") == saved
        assert saved.to_dict()["scope"] == "app"
        assert saved.to_dict()["unit"] == "per_min"

    def test_rules_come_back_by_name(self, rules: AlertRules) -> None:
        rules.save(rule(name="b-rule"))
        rules.save(rule(name="a-rule"))

        assert [r.name for r in rules.rules()] == ["a-rule", "b-rule"]

    def test_saving_again_replaces_and_keeps_the_row(self, rules: AlertRules, clock: Clock) -> None:
        first = rules.save(rule())
        clock.now += 60

        second = rules.save(rule(threshold=95.0))

        assert second.id == first.id
        assert second.threshold == 95.0
        assert second.created_at == first.created_at
        assert second.updated_at == clock.now
        assert len(rules.rules()) == 1

    def test_an_edit_forgets_what_the_old_limit_decided(
        self, rules: AlertRules, clock: Clock
    ) -> None:
        saved = rules.save(rule())
        assert saved.id is not None
        rules.put_state(AlertState(saved.id, "", FIRING, clock.now, 92.0, announced=True))

        rules.save(rule(threshold=95.0))

        assert rules.states() == []

    def test_turning_a_rule_off_clears_its_states(self, rules: AlertRules, clock: Clock) -> None:
        saved = rules.save(rule())
        assert saved.id is not None
        rules.put_state(AlertState(saved.id, "", FIRING, clock.now, 92.0))

        off = rules.set_enabled("cpu-high", False, actor="alice")

        assert off is not None and not off.enabled
        assert rules.states() == []
        again = rules.set_enabled("cpu-high", True)
        assert again is not None and again.enabled

    def test_turning_an_unknown_rule_on_is_nothing(self, rules: AlertRules) -> None:
        assert rules.set_enabled("nope", True) is None

    def test_removing_a_rule_removes_its_states(self, rules: AlertRules, clock: Clock) -> None:
        saved = rules.save(rule())
        assert saved.id is not None
        rules.put_state(AlertState(saved.id, "", FIRING, clock.now, 92.0))

        assert rules.remove("cpu-high") is True

        assert rules.get("cpu-high") is None and rules.states() == []
        assert rules.remove("cpu-high") is False

    def test_there_is_a_ceiling(self, rules: AlertRules) -> None:
        for index in range(MAX_RULES):
            rules.save(rule(name=f"rule-{index}"))

        with pytest.raises(ValidationError, match=str(MAX_RULES)):
            rules.save(rule(name="one-too-many"))
        rules.save(rule(name="rule-0", threshold=50.0))  # editing one is not adding

    def test_a_state_is_kept_and_replaced(self, rules: AlertRules, clock: Clock) -> None:
        saved = rules.save(rule(metric="app_cpu"))
        assert saved.id is not None
        rules.put_state(
            AlertState(saved.id, "shop.example.com", FIRING, clock.now, 92.0, True, 5.0)
        )
        rules.put_state(AlertState(saved.id, "blog.example.com", "ok", clock.now, 10.0))

        shown = rules.state(saved.id, "shop.example.com")
        assert shown is not None
        assert (shown.state, shown.announced, shown.announced_at) == (FIRING, True, 5.0)
        assert [s.subject for s in rules.states(saved.id)] == [
            "shop.example.com",
            "blog.example.com",
        ]

        rules.put_state(AlertState(saved.id, "shop.example.com", "ok", clock.now + 1, 10.0))
        assert rules.state(saved.id, "shop.example.com").state == "ok"  # type: ignore[union-attr]

        rules.forget(saved.id, "shop.example.com")
        assert rules.state(saved.id, "shop.example.com") is None
