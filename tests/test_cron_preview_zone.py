# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The preview of a repeating scheduled task is the moment its timer will fire, not one computed in UTC (item 151).

A calendar expression such as ``*-*-* 02:00:00`` has no zone of its own, and the systemd timer that runs the
task reads it in the zone of the server. The preview asked ``systemd-analyze`` with ``TZ=UTC`` instead, so a
server in Madrid was told "02:00" by the preview and fired at 02:00 local, an hour (or two) before what the
preview said in UTC. The preview now asks in the zone of the server, as the timer reads it, and reports the
instants systemd prints on its ``(in UTC)`` line: the moments are right and the console shows them in
whichever zone it shows times in.
"""

from __future__ import annotations

from noust.core.runner import FakeRunner
from noust.managers.cron_manager import CronManager

#: What ``systemd-analyze calendar --iterations=2`` prints on a server whose zone is CET (UTC+1 in winter).
CET_OUTPUT = (
    "  Original form: *-*-* 02:00:00\n"
    "Normalized form: *-*-* 02:00:00\n"
    "    Next elapse: Fri 2026-11-13 02:00:00 CET\n"
    "       (in UTC): Fri 2026-11-13 01:00:00 UTC\n"
    "       From now: 10h left\n"
    "\n"
    "      Iter. #2: Sat 2026-11-14 02:00:00 CET\n"
    "       (in UTC): Sat 2026-11-14 01:00:00 UTC\n"
    "       From now: 1 day 10h left\n"
)


def _manager(runner: FakeRunner) -> CronManager:
    return CronManager(runner=runner, verbose=False)


def test_the_preview_reports_the_instants_the_timer_will_fire_in_the_servers_zone() -> None:
    runner = FakeRunner()
    runner.script(
        ["systemd-analyze", "calendar", "--iterations=2", "*-*-* 02:00:00"], stdout=CET_OUTPUT
    )

    calendar, runs = _manager(runner).preview_calendar("*-*-* 02:00:00", iterations=2)

    # 02:00 in CET is 01:00 UTC: the moment the timer fires, not 02:00 UTC.
    assert calendar == "*-*-* 02:00:00"
    assert runs == ["2026-11-13T01:00:00+00:00", "2026-11-14T01:00:00+00:00"]


def test_the_preview_does_not_pin_the_zone_to_utc_because_the_timer_does_not_read_it_there() -> (
    None
):
    runner = FakeRunner()
    runner.script(
        ["systemd-analyze", "calendar", "--iterations=2", "*-*-* 02:00:00"], stdout=CET_OUTPUT
    )

    _manager(runner).preview_calendar("*-*-* 02:00:00", iterations=2)

    environment = runner.envs[0]
    assert environment is None or "TZ" not in environment


def test_a_server_already_in_utc_is_read_from_the_elapse_line_itself() -> None:
    runner = FakeRunner()
    runner.script(
        ["systemd-analyze", "calendar", "--iterations=1", "*-*-* 02:00:00"],
        stdout="Normalized form: *-*-* 02:00:00\n    Next elapse: Fri 2026-11-13 02:00:00 UTC\n",
    )

    _, runs = _manager(runner).preview_calendar("*-*-* 02:00:00", iterations=1)

    assert runs == ["2026-11-13T02:00:00+00:00"]


def test_the_base_moment_the_ledger_asks_from_is_given_to_systemd_as_utc() -> None:
    runner = FakeRunner()
    runner.script(["systemd-analyze", "calendar"], stdout=CET_OUTPUT)

    _manager(runner).preview_calendar("*-*-* 02:00:00", iterations=2, base_time=0.0)

    # Without a zone, systemd would read it in the server's own, and the moment would move with the zone.
    assert "--base-time=1970-01-01 00:00:00 UTC" in runner.calls[0]
