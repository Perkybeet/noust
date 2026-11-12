# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a one-time task leaves when it retires (item 164).

The units are gone, so the record is the only place its result and its output are. It is kept for
thirty days, shows the recent first, holds no secret and one row per task however many times the
hook that writes it runs.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from noust.core.cron_once_history import KEEP_DAYS, MAX_OUTPUT_LINES, CronOnceHistory, OnceRun
from noust.core.store import NoustStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def history(store: NoustStore) -> CronOnceHistory:
    return CronOnceHistory(store)


def ran(**overrides: object) -> OnceRun:
    fields: dict[str, object] = {
        "name": "reindex",
        "command": "/usr/bin/php artisan search:reindex",
        "app_domain": "shop.example.com",
        "run_as": "noust-app-shop",
        "working_directory": "/var/www/apps/shop/current",
        "timeout": 600,
        "service": "",
        "run_at": "2026-12-01T14:30:00+01:00",
        "started_at": "2026-12-01T13:30:02+00:00",
        "finished_at": "2026-12-01T13:31:10+00:00",
        "result": "success",
        "exit_code": 0,
        "late": False,
        "output": "reindexed 120 rows\n",
    }
    fields.update(overrides)
    return OnceRun(**fields)  # type: ignore[arg-type]


def test_a_run_comes_back_as_it_was_recorded(history: CronOnceHistory) -> None:
    history.record(ran())

    [found] = history.recent()

    assert found.name == "reindex" and found.result == "success" and found.exit_code == 0
    assert found.app_domain == "shop.example.com" and found.run_as == "noust-app-shop"
    assert found.run_at == "2026-12-01T14:30:00+01:00" and found.late is False
    assert found.output == "reindexed 120 rows\n" and found.id > 0
    assert found.retired_at


def test_the_newest_is_first(history: CronOnceHistory) -> None:
    history.record(ran(name="first", run_at="2026-12-01T14:30:00+01:00"))
    history.record(ran(name="second", run_at="2026-12-02T14:30:00+01:00"))

    assert [run.name for run in history.recent()] == ["second", "first"]


def test_the_same_task_is_one_row_however_often_it_is_settled(history: CronOnceHistory) -> None:
    first = history.record(ran())
    again = history.record(ran(result="exit-code", exit_code=1))

    assert again == first
    [found] = history.recent()
    assert found.result == "success"


def test_the_same_name_can_run_again_another_day(history: CronOnceHistory) -> None:
    history.record(ran(run_at="2026-12-01T14:30:00+01:00"))
    history.record(ran(run_at="2026-12-08T14:30:00+01:00"))

    assert len(history.recent()) == 2


def test_a_credential_in_the_output_or_the_command_is_not_kept(history: CronOnceHistory) -> None:
    token = "ghp_abcdefghijklmnopqrstuvwxyz0123456789ab"
    history.record(
        ran(
            output=f"pulling with {token}\n",
            command=f"/usr/bin/curl -H 'Authorization: Bearer {token}'",
        )
    )

    [found] = history.recent()

    assert token not in found.output and token not in found.command


def test_only_the_last_lines_of_a_long_output_are_kept(history: CronOnceHistory) -> None:
    lines = [f"line {number}" for number in range(MAX_OUTPUT_LINES + 50)]

    history.record(ran(output="\n".join(lines)))

    kept = history.recent()[0].output.splitlines()
    assert len(kept) == MAX_OUTPUT_LINES
    assert kept[-1] == f"line {MAX_OUTPUT_LINES + 49}"


def test_a_run_older_than_thirty_days_is_gone(history: CronOnceHistory, store: NoustStore) -> None:
    history.record(ran(name="old", run_at="2026-10-01T14:30:00+01:00"))
    history.record(ran(name="new", run_at="2026-12-01T14:30:00+01:00"))
    old = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS + 1)).isoformat()
    with store._transaction() as cursor:
        cursor.execute("UPDATE cron_once_history SET retired_at = ? WHERE name = 'old'", (old,))

    assert [run.name for run in history.recent()] == ["new"]
    with store._transaction() as cursor:
        cursor.execute("SELECT COUNT(*) AS n FROM cron_once_history")
        assert cursor.fetchone()["n"] == 1


def test_a_run_that_never_started_is_kept_with_what_is_known(history: CronOnceHistory) -> None:
    history.record(ran(started_at=None, finished_at=None, result="unknown", exit_code=None))

    [found] = history.recent()

    assert found.started_at is None and found.exit_code is None and found.result == "unknown"


def test_a_task_of_no_application_has_no_domain(history: CronOnceHistory) -> None:
    history.record(ran(app_domain="", run_as="root"))

    assert history.recent()[0].app_domain == ""


def test_the_row_is_the_dataclass_back(history: CronOnceHistory) -> None:
    run = ran(late=True, service="web")
    history.record(run)

    found = history.recent()[0]

    assert replace(found, id=0, retired_at="") == replace(run, id=0, retired_at="")
