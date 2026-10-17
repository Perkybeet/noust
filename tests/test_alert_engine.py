# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The alert engine judges rules against recorded history.

Checklist G-18: a limit that must hold for a duration, cleared with a
hysteresis and announced at most once per cooldown. History is written into a
real metrics store with a controlled clock, so what the engine reads is what a
collector would have left.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from noust.core.alerts import AlertRule, AlertRules
from noust.core.alerts.rules import FIRING, OK
from noust.core.exceptions import ValidationError
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import Notification, State
from noust.core.store import NoustStore
from noust.monitor.alert_engine import MIN_COVERAGE, AlertEngine, acknowledge_alert
from noust.monitor.timeseries import MetricsStore

START = 1_800_000_000


class Clock:
    def __init__(self) -> None:
        self.now = float(START)

    def __call__(self) -> float:
        return self.now


class Harness:
    """A metrics store, rules, an engine and the notifications it published."""

    def __init__(self, tmp_path: Path) -> None:
        NoustStore.reset_instance()
        self.clock = Clock()
        self.metrics = MetricsStore(tmp_path / "metrics.db", clock=self.clock)
        self.rules = AlertRules(NoustStore(tmp_path / "noust.db"), clock=self.clock)
        self.sent: list[Notification] = []
        self.context = NotificationContext(locale="en", server="web-1", public_url="")
        self.engine = AlertEngine(
            self.rules,
            self.metrics,
            lambda build: self.sent.append(build(self.context)),
            clock=self.clock,
        )

    def record(self, metric: str, value: float | Callable[[int], float], seconds: int) -> None:
        """Write ``seconds`` of history ending now, a sample every 5 seconds."""
        end = int(self.clock.now)
        for stamp in range(end - seconds, end + 1, 5):
            self.metrics.record(metric, value(stamp) if callable(value) else value, ts=stamp)

    def advance(self, seconds: int) -> None:
        self.clock.now += seconds

    def close(self) -> None:
        NoustStore.reset_instance()


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    harness = Harness(tmp_path)
    yield harness
    harness.close()


def cpu_rule(**overrides: object) -> AlertRule:
    fields: dict = {
        "name": "cpu-high",
        "metric": "cpu",
        "comparator": ">",
        "threshold": 90.0,
        "duration_s": 300,
        "cooldown_s": 3600,
    }
    fields.update(overrides)
    return AlertRule(**fields)


class TestFiring:
    def test_a_limit_held_for_the_duration_fires_once(self, h: Harness) -> None:
        h.rules.save(cpu_rule())
        h.record("cpu.percent", 95.0, 400)

        report = h.engine.evaluate()

        assert (report.fired, report.announced) == (1, 1)
        (note,) = h.sent
        assert (note.kind, note.code, note.state) == ("alert_firing", "alert.above", State.WARNING)
        assert "CPU of the server" in note.summary and "90%" in note.summary
        assert {f.key: f.value for f in note.facts}["value"] == "95.0%"
        state = h.rules.states()[0]
        assert state.state == FIRING and state.announced

    def test_it_does_not_say_it_twice(self, h: Harness) -> None:
        h.rules.save(cpu_rule())
        h.record("cpu.percent", 95.0, 400)
        h.engine.evaluate()

        h.advance(60)
        h.record("cpu.percent", 96.0, 60)
        report = h.engine.evaluate()

        assert len(h.sent) == 1
        assert (report.fired, report.announced) == (0, 0)

    def test_a_spike_shorter_than_the_duration_is_not_an_alert(self, h: Harness) -> None:
        h.rules.save(cpu_rule())
        h.record("cpu.percent", lambda ts: 99.0 if ts > START - 120 else 40.0, 400)

        h.engine.evaluate()

        assert h.sent == [] and h.rules.states() == []

    def test_one_calm_reading_in_the_window_breaks_it(self, h: Harness) -> None:
        h.rules.save(cpu_rule())
        h.record("cpu.percent", lambda ts: 20.0 if ts == START - 100 else 95.0, 400)

        h.engine.evaluate()

        assert h.sent == []

    def test_a_gap_is_not_calm_and_not_bad(self, h: Harness) -> None:
        """A monitor that was down saw nothing: it neither fires nor clears."""
        h.rules.save(cpu_rule())
        # Only the last 20 seconds of a 300-second window were recorded.
        h.record("cpu.percent", 95.0, 20)

        report = h.engine.evaluate()

        assert h.sent == [] and report.unknown == 1 and report.checked == 0
        assert MIN_COVERAGE > 0.5

    def test_no_history_at_all_is_unknown(self, h: Harness) -> None:
        h.rules.save(cpu_rule())

        report = h.engine.evaluate()

        assert report.unknown == 1 and h.sent == []

    def test_a_disabled_rule_is_not_looked_at(self, h: Harness) -> None:
        h.rules.save(cpu_rule(enabled=False))
        h.record("cpu.percent", 99.0, 400)

        report = h.engine.evaluate()

        assert report.checked == 0 and h.sent == []

    def test_below_fires_when_it_stays_under(self, h: Harness) -> None:
        h.rules.save(
            AlertRule(
                name="memory-low",
                metric="app_memory",
                app_domain="shop.example.com",
                comparator="<",
                threshold=256 * 1024**2,
                duration_s=300,
                severity="failed",
            )
        )
        h.record("app.shop.example.com.mem.bytes", 100 * 1024**2, 400)

        h.engine.evaluate()

        (note,) = h.sent
        assert (note.code, note.state) == ("alert.below", State.FAILED)
        assert note.domain == "shop.example.com"
        assert "Memory of shop.example.com" in note.summary
        assert "256.0 MB" in note.summary

    def test_a_percentage_of_a_total_uses_the_ceiling(self, h: Harness) -> None:
        h.rules.save(cpu_rule(name="memory-high", metric="memory", threshold=80.0))
        h.record("mem.total_bytes", 8 * 1024**3, 400)
        h.record("mem.used_bytes", 7 * 1024**3, 400)  # 87.5 %

        h.engine.evaluate()

        (note,) = h.sent
        assert {f.key: f.value for f in note.facts}["value"] == "87.5%"

    def test_a_percentage_without_a_total_cannot_be_judged(self, h: Harness) -> None:
        h.rules.save(cpu_rule(name="memory-high", metric="memory", threshold=80.0))
        h.record("mem.used_bytes", 7 * 1024**3, 400)

        report = h.engine.evaluate()

        assert report.unknown == 1 and h.sent == []


class TestClearing:
    def fire(self, h: Harness, **overrides: object) -> None:
        h.rules.save(cpu_rule(**overrides))
        h.record("cpu.percent", 95.0, 400)
        h.engine.evaluate()
        h.sent.clear()

    def test_back_inside_the_limit_it_clears_and_says_how_long(self, h: Harness) -> None:
        self.fire(h)
        h.advance(900)
        h.record("cpu.percent", 40.0, 300)

        report = h.engine.evaluate()

        assert report.resolved == 1
        (note,) = h.sent
        assert (note.kind, note.code, note.state) == ("alert_resolved", "alert.resolved", State.OK)
        assert {f.key: f.value for f in note.facts}["firing_for"].endswith("min")
        assert h.rules.states()[0].state == OK

    def test_a_reading_inside_the_hysteresis_band_changes_nothing(self, h: Harness) -> None:
        self.fire(h, hysteresis=5.0)
        h.advance(900)
        h.record("cpu.percent", 88.0, 300)  # under 90, not under 85

        h.engine.evaluate()

        assert h.sent == [] and h.rules.states()[0].state == FIRING

    def test_under_the_hysteresis_it_clears(self, h: Harness) -> None:
        self.fire(h, hysteresis=5.0)
        h.advance(900)
        h.record("cpu.percent", 84.0, 300)

        h.engine.evaluate()

        assert len(h.sent) == 1 and h.rules.states()[0].state == OK

    def test_one_calm_cell_is_not_enough_to_clear(self, h: Harness) -> None:
        self.fire(h)
        h.advance(300)
        h.record("cpu.percent", lambda ts: 20.0 if ts == START + 300 else 95.0, 300)

        h.engine.evaluate()

        assert h.sent == [] and h.rules.states()[0].state == FIRING

    def test_no_data_does_not_clear(self, h: Harness) -> None:
        self.fire(h)
        h.advance(3600)

        h.engine.evaluate()

        assert h.sent == [] and h.rules.states()[0].state == FIRING


class TestCooldown:
    def test_an_episode_inside_the_cooldown_is_silent_and_so_is_its_end(self, h: Harness) -> None:
        h.rules.save(cpu_rule(cooldown_s=3600))
        h.record("cpu.percent", 95.0, 400)
        h.engine.evaluate()  # announced
        h.advance(600)
        h.record("cpu.percent", 30.0, 300)
        h.engine.evaluate()  # cleared: announced, because the firing was
        h.sent.clear()

        h.advance(600)  # 20 minutes after the first message: inside the hour
        h.record("cpu.percent", 95.0, 400)
        again = h.engine.evaluate()
        assert again.fired == 1 and again.announced == 0 and h.sent == []
        assert h.rules.states()[0].announced is False

        h.advance(300)
        h.record("cpu.percent", 30.0, 300)
        h.engine.evaluate()
        assert h.sent == []  # it was never told, so its end is not either

    def test_after_the_cooldown_it_speaks_again(self, h: Harness) -> None:
        h.rules.save(cpu_rule(cooldown_s=3600))
        h.record("cpu.percent", 95.0, 400)
        h.engine.evaluate()
        h.advance(600)
        h.record("cpu.percent", 30.0, 300)
        h.engine.evaluate()
        h.sent.clear()

        h.advance(4000)
        h.record("cpu.percent", 95.0, 400)
        h.engine.evaluate()

        assert [n.kind for n in h.sent] == ["alert_firing"]

    def test_a_rule_that_keeps_firing_does_not_remind_by_default(self, h: Harness) -> None:
        h.rules.save(cpu_rule(cooldown_s=3600))
        h.record("cpu.percent", 95.0, 400)
        h.engine.evaluate()

        h.advance(7200)
        h.record("cpu.percent", 95.0, 400)
        h.engine.evaluate()

        assert len(h.sent) == 1

    def test_a_reminder_comes_once_per_cooldown_when_asked_for(self, h: Harness) -> None:
        h.rules.save(cpu_rule(cooldown_s=3600, remind=True))
        h.record("cpu.percent", 95.0, 400)
        h.engine.evaluate()

        h.advance(1800)
        h.record("cpu.percent", 95.0, 400)
        h.engine.evaluate()
        assert len(h.sent) == 1

        h.advance(1900)
        h.record("cpu.percent", 95.0, 400)
        h.engine.evaluate()
        assert len(h.sent) == 2
        assert "alert_firing" == h.sent[1].kind


class TestApplications:
    def rule_for_all(self) -> AlertRule:
        return AlertRule(
            name="errors",
            metric="app_5xx",
            comparator=">=",
            threshold=10.0,
            duration_s=300,
            hysteresis=2.0,
        )

    def test_a_rule_about_every_application_judges_each_one_that_has_the_series(
        self, h: Harness
    ) -> None:
        h.rules.save(self.rule_for_all())
        h.record("app.shop.example.com.http.errors_5xx_per_min", 30.0, 400)
        h.record("app.blog.example.com.http.errors_5xx_per_min", 1.0, 400)
        h.record("app.shop.example.com.cpu.percent", 99.0, 400)  # another metric

        report = h.engine.evaluate()

        assert report.checked == 2 and report.fired == 1
        (note,) = h.sent
        assert note.domain == "shop.example.com"
        assert note.subject == "shop.example.com"
        assert {s.subject: s.state for s in h.rules.states()} == {"shop.example.com": FIRING}

    def test_one_application_is_judged_alone(self, h: Harness) -> None:
        h.rules.save(
            AlertRule(
                name="errors",
                metric="app_5xx",
                app_domain="blog.example.com",
                comparator=">=",
                threshold=10.0,
            )
        )
        h.record("app.shop.example.com.http.errors_5xx_per_min", 30.0, 400)
        h.record("app.blog.example.com.http.errors_5xx_per_min", 30.0, 400)

        h.engine.evaluate()

        assert [n.domain for n in h.sent] == ["blog.example.com"]

    def test_what_an_application_that_disappeared_remembers_is_forgotten(self, h: Harness) -> None:
        saved = h.rules.save(self.rule_for_all())
        h.record("app.shop.example.com.http.errors_5xx_per_min", 30.0, 400)
        h.engine.evaluate()
        assert saved.id is not None
        assert h.rules.state(saved.id, "shop.example.com") is not None

        # The application is deleted: its series stop being in the store.
        h.metrics.close()
        fresh = MetricsStore(Path(h.metrics.db_path).with_name("fresh.db"), clock=h.clock)
        h.engine._metrics = fresh
        h.engine.evaluate()

        assert h.rules.state(saved.id, "shop.example.com") is None

    def test_a_server_rule_never_looks_at_applications(self, h: Harness) -> None:
        h.rules.save(cpu_rule())

        assert h.engine.subjects(cpu_rule()) == [""]


class TestResilience:
    def test_one_rule_that_cannot_be_read_does_not_stop_the_others(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        h.rules.save(cpu_rule(name="a-broken"))
        h.rules.save(cpu_rule(name="b-fine", metric="load", threshold=4.0))
        h.record("load.1m", 9.0, 400)
        original = h.engine._evaluate_rule

        def flaky(rule: AlertRule, report: object) -> None:
            if rule.name == "a-broken":
                raise OSError("disk I/O error")
            original(rule, report)  # type: ignore[arg-type]

        monkeypatch.setattr(h.engine, "_evaluate_rule", flaky)

        h.engine.evaluate()

        assert len(h.sent) == 1 and "a-broken" in caplog.text


class TestAcknowledged:
    def fire(self, h: Harness, **overrides: object) -> None:
        h.rules.save(cpu_rule(cooldown_s=3600, remind=True, **overrides))
        h.record("cpu.percent", 95.0, 400)
        h.engine.evaluate()
        h.sent.clear()

    def publish(self, h: Harness) -> Callable[..., None]:
        return lambda build: h.sent.append(build(h.context))

    def test_somebody_taking_it_tells_the_channels_who(self, h: Harness) -> None:
        self.fire(h)
        h.advance(120)

        state = acknowledge_alert(
            h.rules, "cpu-high", by="alice", publish=self.publish(h), clock=h.clock
        )

        assert state.acknowledged_by == "alice"
        (note,) = h.sent
        assert (note.kind, note.code, note.state) == (
            "alert_acknowledged",
            "alert.acknowledged",
            State.INFO,
        )
        facts = {f.key: f.value for f in note.facts}
        assert facts["acknowledged_by"] == "alice" and facts["rule"] == "cpu-high"
        assert "alice is looking into it" in note.summary and "90%" in note.summary
        assert facts["firing_for"].startswith("7 min")

    def test_taking_it_twice_tells_nobody_twice(self, h: Harness) -> None:
        self.fire(h)

        for who in ("alice", "bob"):
            acknowledge_alert(h.rules, "cpu-high", by=who, publish=self.publish(h), clock=h.clock)

        assert len(h.sent) == 1

    def test_what_is_not_firing_tells_nobody(self, h: Harness) -> None:
        h.rules.save(cpu_rule())

        with pytest.raises(ValidationError):
            acknowledge_alert(h.rules, "cpu-high", by="alice", publish=self.publish(h))

        assert h.sent == []

    def test_a_taken_alert_is_not_reminded_of(self, h: Harness) -> None:
        self.fire(h)
        acknowledge_alert(h.rules, "cpu-high", by="alice", publish=self.publish(h), clock=h.clock)
        h.sent.clear()

        h.advance(7200)
        h.record("cpu.percent", 95.0, 400)
        h.engine.evaluate()

        assert h.sent == []

    def test_handed_back_it_is_reminded_of_again(self, h: Harness) -> None:
        self.fire(h)
        acknowledge_alert(h.rules, "cpu-high", by="alice", publish=self.publish(h), clock=h.clock)
        h.rules.unacknowledge("cpu-high")
        h.sent.clear()

        h.advance(7200)
        h.record("cpu.percent", 95.0, 400)
        h.engine.evaluate()

        assert [n.kind for n in h.sent] == ["alert_firing"]

    def test_a_taken_alert_still_says_when_it_clears(self, h: Harness) -> None:
        self.fire(h)
        acknowledge_alert(h.rules, "cpu-high", by="alice", publish=self.publish(h), clock=h.clock)
        h.sent.clear()

        h.advance(900)
        h.record("cpu.percent", 40.0, 300)
        h.engine.evaluate()

        assert [n.kind for n in h.sent] == ["alert_resolved"]
        state = h.rules.states()[0]
        assert state.state == OK and state.acknowledged_by is None
