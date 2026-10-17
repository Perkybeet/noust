# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which occurrences of a scheduled task ran, and which did not (checklist B-66).

A timer with ``Persistent=true`` makes up one run at boot for the time the machine was off and says
nothing about the others. Pinned here:

- the verdict for each moment a job was due: ran on time, late (a later run answered for it), or
  missed, with ``server_off`` as the reason when the journal's boots show the machine was down;
- that the ledger judges only from when it can (the journal's reach, the timer's age, the oldest
  run it read), says from when, and never judges what may still run;
- the manager over a scripted journal and ``systemd-analyze``, the command line and the API, and
  the timeline, where a missed occurrence is an event of the ``jobs`` source.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.config import Config
from noust.core.exceptions import ServiceError, ValidationError
from noust.core.runner import FakeRunner
from noust.managers import cron_ledger
from noust.managers.cron_ledger import GRACE_SECONDS, LATE, MISSED, RAN, Occurrence, judge
from noust.managers.cron_manager import READ_ONLY_PROBES, CronManager
from noust.managers.timeline import TimelineBuilder, TimelineReaders
from noust.web.api import cron as cron_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers


def at(day: int, hour: int = 0, minute: int = 0, second: int = 0) -> float:
    """A moment of October 2026, UTC, as epoch seconds."""
    return dt.datetime(2026, 10, day, hour, minute, second, tzinfo=dt.timezone.utc).timestamp()


# ---------------------------------------------------------------------------
# The verdicts
# ---------------------------------------------------------------------------


def test_a_run_inside_the_grace_period_is_on_time() -> None:
    found = judge([at(7, 2)], [at(7, 2, 0, 5)], now=at(8))

    assert found == [Occurrence(at(7, 2), RAN, at(7, 2, 0, 5))]


def test_a_run_at_the_moment_it_was_due_is_on_time() -> None:
    assert judge([at(7, 2)], [at(7, 2)], now=at(8))[0].status == RAN


def test_a_run_after_the_grace_period_but_before_the_next_occurrence_is_late() -> None:
    found = judge([at(7, 2), at(8, 2)], [at(7, 9, 0, 10), at(8, 2, 0, 1)], now=at(9))

    assert [(item.status, item.ran_at) for item in found] == [
        (LATE, at(7, 9, 0, 10)),
        (RAN, at(8, 2, 0, 1)),
    ]


def test_an_occurrence_nothing_answered_for_is_missed() -> None:
    found = judge([at(7, 2), at(8, 2)], [at(8, 2, 0, 1)], now=at(9))

    assert found[0] == Occurrence(at(7, 2), MISSED)


def test_a_catch_up_run_answers_for_the_last_occurrence_it_follows_and_the_others_are_missed() -> (
    None
):
    """Three hourly occurrences were due while the machine was off; boot made up one run."""
    due = [at(7, 3), at(7, 4), at(7, 5), at(7, 6)]
    down = [(at(7, 2, 30), at(7, 5, 30))]

    found = judge(due, [at(7, 5, 31)], down, now=at(7, 7))

    assert [(item.status, item.reason) for item in found] == [
        (MISSED, "server_off"),
        (MISSED, "server_off"),
        (LATE, ""),
        (MISSED, ""),
    ]


def test_missed_with_the_machine_up_has_no_reason_because_it_does_not_guess() -> None:
    assert judge([at(7, 2)], [], [(at(6, 1), at(6, 2))], now=at(8))[0].reason == ""


def test_what_may_still_run_is_not_judged() -> None:
    found = judge([at(7, 2), at(7, 12)], [], now=at(7, 12, 0, 30))

    assert [item.at for item in found] == [at(7, 2)]


def test_no_occurrence_is_no_verdict() -> None:
    assert judge([], [at(7)], now=at(8)) == []


def test_the_occurrences_may_be_given_in_any_order() -> None:
    found = judge([at(8, 2), at(7, 2)], [at(7, 2, 0, 1), at(8, 2, 0, 1)], now=at(9))

    assert [item.at for item in found] == [at(7, 2), at(8, 2)]


def test_the_grace_period_is_two_minutes() -> None:
    assert GRACE_SECONDS == 120


# ---------------------------------------------------------------------------
# What the journal says of the machine
# ---------------------------------------------------------------------------


def boot(first: float, last: float) -> dict[str, Any]:
    return {"first_entry": int(first * 1e6), "last_entry": int(last * 1e6)}


def test_the_stretches_between_boots_are_when_the_machine_was_down() -> None:
    boots = [boot(at(1), at(8, 1)), boot(at(8, 9), at(10, 11)), boot(at(10, 12), at(10, 13))]

    assert cron_ledger.boot_gaps(boots) == [(at(8, 1), at(8, 9)), (at(10, 11), at(10, 12))]


def test_boots_out_of_order_or_with_a_bad_entry_are_read_all_the_same() -> None:
    boots = [boot(at(8, 9), at(10)), {"first_entry": "x"}, boot(at(1), at(8, 1)), {}]

    assert cron_ledger.boot_gaps(boots) == [(at(8, 1), at(8, 9))]


def test_the_journal_reaches_back_to_its_oldest_boot() -> None:
    assert cron_ledger.coverage_start([boot(at(3), at(4)), boot(at(1), at(2))]) == at(1)
    assert cron_ledger.coverage_start([]) is None
    assert cron_ledger.coverage_start([{"first_entry": None}]) is None


# ---------------------------------------------------------------------------
# The manager
# ---------------------------------------------------------------------------

#: The timer of a job due every day at 02:00, as ``systemctl show`` prints it.
SHOW_TIMER = "TimersCalendar={ OnCalendar=*-*-* 02:00:00 ; next_elapse=n/a }\nLastTriggerUSec=n/a\n"

NOW = at(10, 12)


def analyze(days: list[int]) -> str:
    """What ``systemd-analyze calendar`` prints for a 02:00 job on these days."""
    lines = ["  Original form: daily", "Normalized form: *-*-* 02:00:00"]
    for index, day in enumerate(days):
        stamp = dt.datetime(2026, 10, day, 2, tzinfo=dt.timezone.utc).strftime(
            "%a %Y-%m-%d %H:%M:%S UTC"
        )
        lines.append(
            f"    Next elapse: {stamp}" if index == 0 else f"   Iter. #{index + 1}: {stamp}"
        )
    return "\n".join(lines) + "\n"


def run_entry(started: float, invocation: str) -> str:
    return json.dumps(
        {
            "__REALTIME_TIMESTAMP": str(int(started * 1e6)),
            "UNIT": "noust-cron-cleanup.service",
            "INVOCATION_ID": invocation,
            "JOB_RESULT": "done",
        }
    )


@pytest.fixture
def systemd_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "systemd"
    path.mkdir()
    monkeypatch.setattr(CronManager, "SYSTEMD_DIR", path)
    timer = path / "noust-cron-cleanup.timer"
    timer.write_text("# Generated by Noust\n[Timer]\nOnCalendar=*-*-* 02:00:00\n")
    (path / "noust-cron-cleanup.service").write_text(
        "# Generated by Noust\n[Service]\nUser=www-data\nExecStart=/usr/bin/true\n"
    )
    os.utime(timer, (at(1), at(1)))
    return path


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "etc" / "noust" / "config.yaml"
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", path)
    Config.reset_instance()
    try:
        yield path
    finally:
        Config.reset_instance()


@pytest.fixture
def journal(runner: FakeRunner, systemd_dir: Path, config_file: Path) -> FakeRunner:
    """A server that was off on the 8th until nine, and a job that ran when it was up."""
    runner.script(("systemctl", "is-enabled"), stdout="enabled\n")
    runner.script(("systemctl", "show", "noust-cron-cleanup.timer"), stdout=SHOW_TIMER)
    runner.script(("systemd-analyze", "calendar"), stdout=analyze([7, 8, 9, 10, 11, 12]))
    runner.script(
        ("journalctl", "--list-boots"),
        stdout=json.dumps([boot(at(1), at(8, 1)), boot(at(8, 9), at(10, 11, 59))]),
    )
    runner.script(
        ("journalctl", "-u"),
        stdout="\n".join(
            [
                run_entry(at(7, 2, 0, 5), "aaa"),
                run_entry(at(8, 9, 0, 10), "bbb"),
                run_entry(at(9, 2, 0, 3), "ccc"),
            ]
        ),
    )
    return runner


def test_the_ledger_sets_what_was_due_against_what_ran(journal: FakeRunner) -> None:
    report = CronManager().ledger("cleanup", days=4, now=NOW)

    assert (report["ran"], report["late"], report["missed"]) == (2, 1, 1)
    verdicts = {item["at"]: (item["status"], item["reason"]) for item in report["occurrences"]}
    assert verdicts == {
        at(7, 2): (RAN, ""),
        at(8, 2): (LATE, ""),
        at(9, 2): (RAN, ""),
        at(10, 2): (MISSED, ""),
    }
    assert report["schedule"] == "*-*-* 02:00:00" and report["enabled"] is True
    assert report["until"] == NOW


def test_the_occurrence_the_machine_was_off_for_says_so(journal: FakeRunner) -> None:
    """The 8th at 02:00 fell inside the gap between the two boots."""
    report = CronManager().ledger("cleanup", days=4, now=NOW)

    late = next(item for item in report["occurrences"] if item["at"] == at(8, 2))
    assert late["status"] == LATE and late["ran_at"] == at(8, 9, 0, 10)


def test_a_catch_up_that_a_later_occurrence_hides_is_still_a_missed_one(
    journal: FakeRunner,
) -> None:
    """Off for two nights: the boot made up one run, so the earlier night is missed."""
    journal.script(("systemd-analyze", "calendar"), stdout=analyze([6, 7, 8, 9, 10, 11]))
    journal.script(
        ("journalctl", "--list-boots"),
        stdout=json.dumps([boot(at(1), at(6, 1)), boot(at(8, 9), at(10, 11, 59))]),
    )
    journal.script(
        ("journalctl", "-u"),
        stdout="\n".join([run_entry(at(8, 9, 0, 10), "bbb"), run_entry(at(9, 2, 0, 3), "ccc")]),
    )

    report = CronManager().ledger("cleanup", days=5, now=NOW)

    by_day = {
        dt.datetime.fromtimestamp(item["at"], dt.timezone.utc).day: item
        for item in report["occurrences"]
    }
    assert (by_day[6]["status"], by_day[6]["reason"]) == (MISSED, "server_off")
    assert (by_day[7]["status"], by_day[7]["reason"]) == (MISSED, "server_off")
    assert by_day[8]["status"] == LATE


def test_it_judges_only_from_when_the_journal_begins(journal: FakeRunner) -> None:
    journal.script(
        ("journalctl", "--list-boots"), stdout=json.dumps([boot(at(8, 9), at(10, 11, 59))])
    )

    report = CronManager().ledger("cleanup", days=10, now=NOW)

    assert report["since"] == at(8, 9)
    assert {item["at"] for item in report["occurrences"]} == {at(9, 2), at(10, 2)}


def test_it_judges_only_from_when_the_timer_was_written(
    journal: FakeRunner, systemd_dir: Path
) -> None:
    os.utime(systemd_dir / "noust-cron-cleanup.timer", (at(9, 1), at(9, 1)))

    report = CronManager().ledger("cleanup", days=10, now=NOW)

    assert report["since"] == at(9, 1)
    assert {item["at"] for item in report["occurrences"]} == {at(9, 2), at(10, 2)}


def test_it_judges_only_from_the_oldest_run_it_read_when_the_journal_had_more(
    journal: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("noust.managers.cron_manager._LEDGER_JOURNAL_ENTRIES", 3)

    report = CronManager().ledger("cleanup", days=4, now=NOW)

    assert report["since"] == at(7, 2, 0, 5)
    assert at(7, 2) not in {item["at"] for item in report["occurrences"]}


def test_a_disabled_timer_has_nothing_due(journal: FakeRunner) -> None:
    journal.script(("systemctl", "is-enabled"), stdout="disabled\n", exit_code=1)

    report = CronManager().ledger("cleanup", days=4, now=NOW)

    assert report["enabled"] is False and report["occurrences"] == [] and report["since"] is None


def test_a_foreign_unit_is_refused(runner: FakeRunner, systemd_dir: Path) -> None:
    (systemd_dir / "noust-cron-cleanup.timer").write_text("[Timer]\n")

    with pytest.raises(ServiceError, match="does not manage"):
        CronManager().ledger("cleanup", now=NOW)


@pytest.mark.parametrize("days", [0, 32, -1])
def test_a_stretch_the_ledger_does_not_cover_is_refused(journal: FakeRunner, days: int) -> None:
    with pytest.raises(ValidationError, match="Ask for 1 to 31 days"):
        CronManager().ledger("cleanup", days=days, now=NOW)


def test_the_schedule_is_asked_of_systemd_from_the_moment_the_ledger_starts(
    journal: FakeRunner,
) -> None:
    CronManager().ledger("cleanup", days=4, now=NOW)

    [call] = [c for c in journal.calls if c[:2] == ("systemd-analyze", "calendar")]
    assert call[2] == "--iterations=100" and call[3].startswith("--base-time=2026-10-06 12:00:00")
    assert call[-1] == "*-*-* 02:00:00"


def test_the_questions_it_asks_are_declared_read_only() -> None:
    from noust.core.runner import is_read_only as is_read_only_probe

    assert is_read_only_probe(
        (
            "systemd-analyze",
            "calendar",
            "--iterations=100",
            "--base-time=2026-10-06 12:00:00",
            "*-*-* 02:00:00",
        )
    )
    assert is_read_only_probe(("systemd-analyze", "calendar", "--iterations=5", "daily"))
    assert not is_read_only_probe(("systemd-analyze", "unit-paths"))
    assert READ_ONLY_PROBES


# ---------------------------------------------------------------------------
# The command line and the API
# ---------------------------------------------------------------------------


def test_the_command_lists_what_did_not_run_on_time(
    journal: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("noust.managers.cron_manager.time.time", lambda: NOW)

    result = CliRunner().invoke(root_cli, ["cron", "ledger", "cleanup", "--days", "4"])

    assert result.exit_code == 0, result.output
    assert "2 on time, 1 late, 1 missed" in result.output
    assert "late: ran at 2026-10-08 09:00 UTC" in result.output
    assert "missed: did not run" in result.output


def test_the_command_says_when_everything_ran(
    journal: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("noust.managers.cron_manager.time.time", lambda: at(9, 12))

    result = CliRunner().invoke(root_cli, ["cron", "ledger", "cleanup", "--days", "3"])

    assert result.exit_code == 0, result.output
    assert "1 late" in result.output  # the 8th: made up at boot


def test_the_command_prints_json(journal: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("noust.managers.cron_manager.time.time", lambda: NOW)

    result = CliRunner().invoke(root_cli, ["--json", "cron", "ledger", "cleanup", "--days", "4"])

    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert (body["ran"], body["late"], body["missed"]) == (2, 1, 1)


def test_the_command_refuses_a_stretch_out_of_range(journal: FakeRunner) -> None:
    result = CliRunner().invoke(root_cli, ["cron", "ledger", "cleanup", "--days", "99"])

    assert result.exit_code != 0


def test_the_command_says_an_unknown_job(
    runner: FakeRunner, systemd_dir: Path, config_file: Path
) -> None:
    result = CliRunner().invoke(root_cli, ["cron", "ledger", "nothing"])

    assert result.exit_code != 0


def test_the_api_answers_the_same_ledger(
    journal: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("noust.managers.cron_manager.time.time", lambda: NOW)
    server = FastAPI()
    install_error_handlers(server)
    server.include_router(cron_api.router, prefix="/api/cron")
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    server.dependency_overrides[get_current_session] = lambda: session

    body = TestClient(server).get("/api/cron/cleanup/ledger", params={"days": 4}).json()

    assert (body["ran"], body["late"], body["missed"]) == (2, 1, 1)
    assert body["occurrences"][1]["status"] == "late" and body["occurrences"][1]["ran_at"] == at(
        8, 9, 0, 10
    )
    assert (
        TestClient(server).get("/api/cron/cleanup/ledger", params={"days": 99}).status_code == 422
    )


def test_the_route_is_one_to_read_applications() -> None:
    from noust.web.permissions.routes_cron import ROUTES

    assert ROUTES[("GET", "/api/cron/{name}/ledger")] == "apps.read"


# ---------------------------------------------------------------------------
# The timeline
# ---------------------------------------------------------------------------


class _Store:
    def get_app(self, domain: str) -> Any:
        return None

    def list_jobs(self, limit: int = 0, domain: str | None = None) -> list[Any]:
        return []


def timeline_of(missed: list[dict[str, Any]] | None, **kwargs: Any) -> Any:
    readers = TimelineReaders(
        journal=None,
        audit=None,
        store=_Store(),
        metrics=None,
        observations=None,
        units_of=lambda app: [],
        clock=lambda: float(NOW),
        cron_missed=None if missed is None else (lambda start, end: missed),
    )
    return TimelineBuilder(readers).build(int(at(7)), int(NOW), sources=["jobs"], **kwargs)


MISSED_ONE = {
    "at": at(10, 2),
    "name": "cleanup",
    "unit": "noust-cron-cleanup.service",
    "app": "shop.example.com",
    "status": MISSED,
    "reason": "server_off",
}
LATE_ONE = {**MISSED_ONE, "at": at(8, 2), "status": LATE, "reason": "", "app": ""}


def test_a_missed_occurrence_is_an_event_of_the_jobs_source() -> None:
    timeline = timeline_of([MISSED_ONE, LATE_ONE])

    events = [e for e in timeline.events if e.kind == "cron_missed"]
    assert [(e.source, e.level, e.unit) for e in events] == [
        ("jobs", "notice", "noust-cron-cleanup.service"),
        ("jobs", "warning", "noust-cron-cleanup.service"),
    ]
    assert events[1].text == "cleanup: missed (the server was off)"
    assert events[0].text == "cleanup: ran late (did not run)"
    assert events[1].app == "shop.example.com" and events[1].status == MISSED


def test_the_events_of_an_application_are_its_tasks_only() -> None:
    class Store(_Store):
        def get_app(self, domain: str) -> Any:
            return object()

    readers = TimelineReaders(
        journal=None,
        audit=None,
        store=Store(),
        metrics=None,
        observations=None,
        units_of=lambda app: [],
        clock=lambda: float(NOW),
        cron_missed=lambda start, end: [MISSED_ONE, LATE_ONE],
    )

    timeline = TimelineBuilder(readers).build(
        int(at(7)), int(NOW), sources=["jobs"], app="shop.example.com"
    )

    assert [e.text for e in timeline.events if e.kind == "cron_missed"] == [
        "cleanup: missed (the server was off)"
    ]


def test_no_reader_is_no_event() -> None:
    assert timeline_of(None).events == []


def test_a_reader_that_fails_does_not_take_the_timeline_with_it() -> None:
    def broken(start: int, end: int) -> list[dict[str, Any]]:
        raise ServiceError("systemd could not be asked")

    readers = TimelineReaders(
        journal=None,
        audit=None,
        store=_Store(),
        metrics=None,
        observations=None,
        units_of=lambda app: [],
        clock=lambda: float(NOW),
        cron_missed=broken,
    )

    timeline = TimelineBuilder(readers).build(int(at(7)), int(NOW), sources=["jobs"])

    assert timeline.events == []
    assert [s.state for s in timeline.sources if s.source == "jobs"] == ["shown"]
