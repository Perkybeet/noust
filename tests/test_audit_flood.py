# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Flood protection (finding H3): anonymous floods are counted, never lost silently.

3.0 rotated its log by size, so anyone able to reach the console could erase
the history by failing to sign in often enough. 3.1 keeps events by age and
bounds a flood instead: a burst is written in full, the rest is counted, and
the count is written when the window closes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from noust.core.audit import Actor
from noust.core.audit.flood import AGGREGATE_FACTOR, FloodGuard
from noust.core.audit.log import AuditLog
from noust.core.audit.settings import AuditSettings


@pytest.fixture
def log(tmp_path: Path) -> AuditLog:
    return AuditLog(
        tmp_path / "web-audit.log",
        settings=AuditSettings(journald="off", flood_window_seconds=60, flood_burst=3),
    )


def written(log: AuditLog, action: str) -> list[dict]:
    return [
        json.loads(line)
        for line in log.path.read_text().splitlines()
        if json.loads(line)["action"] == action
    ]


def test_one_source_is_written_in_full_up_to_the_burst(log: AuditLog) -> None:
    for _ in range(10):
        log.append("auth.login", actor=Actor.anonymous("203.0.113.9"), outcome="failure")

    assert len(written(log, "auth.login")) == 3
    assert written(log, "audit.coalesced") == []


def test_the_rest_is_counted_and_written_when_the_window_closes(log: AuditLog) -> None:
    for _ in range(10):
        log.append("auth.login", actor=Actor.anonymous("203.0.113.9"), outcome="failure")

    log.flush_flood(force=True)

    (summary,) = written(log, "audit.coalesced")
    assert summary["details"]["event"] == "auth.login"
    assert summary["details"]["count"] == 7
    assert summary["details"]["sources"] == ["203.0.113.9"]
    assert summary["sev"] == 4
    assert log.verify().ok


def test_a_flood_from_many_sources_is_bounded_too(log: AuditLog) -> None:
    """Rotating addresses must not defeat the guard."""
    total = 500
    for index in range(total):
        log.append(
            "ws.connect",
            actor=Actor.anonymous(f"10.0.{index // 256}.{index % 256}"),
            target="/ws/events",
            outcome="denied",
        )
    log.flush_flood(force=True)

    individual = written(log, "ws.connect")
    (summary,) = written(log, "audit.coalesced")
    assert len(individual) == 3 * AGGREGATE_FACTOR
    assert len(individual) + summary["details"]["count"] == total
    assert summary["details"]["distinct_sources"] == total - len(individual)
    assert len(summary["details"]["sources"]) == 16


def test_identified_actors_are_never_held_back(log: AuditLog) -> None:
    for _ in range(20):
        log.append("apps.update", actor=Actor(kind="user", name="maria"))
    assert len(written(log, "apps.update")) == 20


def test_critical_events_are_never_held_back(log: AuditLog) -> None:
    for _ in range(20):
        log.append("auth.lockout", actor=Actor.anonymous("203.0.113.9"))
    assert len(written(log, "auth.lockout")) == 20


def test_over_the_size_limit_only_one_per_window_is_written(log: AuditLog) -> None:
    log.over_limit = True
    for _ in range(10):
        log.append("auth.credential", actor=Actor.anonymous("203.0.113.9"), outcome="denied")
    # A refusal is shipped at warning severity, but the event is informational
    # by nature: over the limit it is the first to be counted.
    assert len(written(log, "auth.credential")) == 1


def test_a_new_window_starts_after_the_old_one_ends() -> None:
    guard = FloodGuard(window=60, burst=2)
    decisions = [guard.admit("auth.login", "a", None, now)[0] for now in (0, 1, 2, 3)]
    assert decisions == [True, True, False, False]

    admitted, summaries = guard.admit("auth.login", "a", None, 61)

    assert admitted
    assert [(summary.event, summary.count) for summary in summaries] == [("auth.login", 2)]


def test_reloading_the_settings_keeps_what_was_counted(log: AuditLog) -> None:
    for _ in range(5):
        log.append("auth.login", actor=Actor.anonymous("203.0.113.9"), outcome="failure")
    log.reload_settings(AuditSettings(journald="off", flood_window_seconds=60, flood_burst=3))
    log.flush_flood(force=True)
    assert written(log, "audit.coalesced")[0]["details"]["count"] == 2

    for _ in range(5):
        log.append("auth.login", actor=Actor.anonymous("203.0.113.9"), outcome="failure")
    log.reload_settings(AuditSettings(journald="off", flood_window_seconds=30, flood_burst=3))
    assert written(log, "audit.coalesced")[-1]["details"]["count"] == 2
