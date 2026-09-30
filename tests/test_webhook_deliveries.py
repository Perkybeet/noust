# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application's deploy webhook received is kept, bounded, per application.

The console's guided setup lists the last deliveries (a ping, a push that
deployed, a push to another branch, a wrong signature) so the operator can see
that the forge reaches Noust. The log must never become a way to fill the
store: the rows per application are bounded, and a burst of identical
refusals is one row with a count.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from noust.core import webhook_deliveries as wd
from noust.core.store import App, NoustStore

DOMAIN = "app.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """
    Give the module a store of its own.

    Args:
        tmp_path: Per-test temporary directory.

    Yields:
        The store.
    """
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def app_id(store: NoustStore) -> int:
    """
    Deploy one application on paper.

    Args:
        store: The store fixture.

    Returns:
        Its id.
    """
    app = store.create_app(
        App(domain=DOMAIN, app_type="nodejs", source="https://github.com/you/app", port=3000)
    )
    assert app.id is not None
    return app.id


def test_a_delivery_is_recorded_with_what_the_forge_said(store: NoustStore, app_id: int) -> None:
    row = wd.record_delivery(
        app_id,
        wd.DEPLOY_STARTED,
        provider="github",
        event="push",
        branch="main",
        detail="queued the update",
        job_id="job-1",
        delivery_id="abc-123",
    )

    assert row is not None
    assert (row.outcome, row.provider, row.event, row.branch) == (
        "deploy_started",
        "github",
        "push",
        "main",
    )
    assert (row.job_id, row.delivery_id, row.count) == ("job-1", "abc-123", 1)
    assert datetime.fromisoformat(row.received_at)


def test_the_newest_delivery_is_listed_first_and_the_list_is_bounded(
    store: NoustStore, app_id: int
) -> None:
    for number in range(5):
        wd.record_delivery(app_id, wd.PING, delivery_id=f"d{number}")

    listed = wd.list_deliveries(app_id, limit=3)

    assert [row.delivery_id for row in listed] == ["d4", "d3", "d2"]


def test_a_row_is_kept_for_the_last_deliveries_only(
    store: NoustStore, app_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wd, "KEEP_PER_APP", 4)

    for number in range(10):
        wd.record_delivery(app_id, wd.PING, delivery_id=f"d{number}")

    assert [row.delivery_id for row in wd.list_deliveries(app_id, limit=50)] == [
        "d9",
        "d8",
        "d7",
        "d6",
    ]


def test_one_application_never_pushes_out_the_deliveries_of_another(
    store: NoustStore, app_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = store.create_app(App(domain="other.example.com", app_type="nodejs", port=3001))
    assert other.id is not None
    monkeypatch.setattr(wd, "KEEP_PER_APP", 2)
    wd.record_delivery(app_id, wd.PING, delivery_id="mine")

    for number in range(6):
        wd.record_delivery(other.id, wd.PING, delivery_id=f"theirs{number}")

    assert [row.delivery_id for row in wd.list_deliveries(app_id)] == ["mine"]


def test_a_burst_of_wrong_signatures_is_one_row_with_a_count(
    store: NoustStore, app_id: int
) -> None:
    for _ in range(25):
        wd.record_delivery(app_id, wd.BAD_SIGNATURE, detail="signature verification failed")

    (row,) = wd.list_deliveries(app_id)
    assert (row.outcome, row.count) == ("bad_signature", 25)


def test_a_refusal_after_a_real_delivery_starts_a_new_row(store: NoustStore, app_id: int) -> None:
    wd.record_delivery(app_id, wd.BAD_SIGNATURE)
    wd.record_delivery(app_id, wd.PING)
    wd.record_delivery(app_id, wd.BAD_SIGNATURE)

    assert [(row.outcome, row.count) for row in wd.list_deliveries(app_id)] == [
        ("bad_signature", 1),
        ("ping", 1),
        ("bad_signature", 1),
    ]


def test_a_refusal_a_minute_later_is_a_new_row(store: NoustStore, app_id: int) -> None:
    wd.record_delivery(app_id, wd.BAD_SIGNATURE)
    old = (datetime.now() - timedelta(seconds=wd.COALESCE_SECONDS + 5)).isoformat()
    with store._transaction() as cursor:
        cursor.execute("UPDATE webhook_deliveries SET received_at = ?", (old,))

    wd.record_delivery(app_id, wd.BAD_SIGNATURE)

    assert [row.count for row in wd.list_deliveries(app_id)] == [1, 1]


def test_pings_and_pushes_are_never_folded_together(store: NoustStore, app_id: int) -> None:
    wd.record_delivery(app_id, wd.PING)
    wd.record_delivery(app_id, wd.PING)

    assert len(wd.list_deliveries(app_id)) == 2


def test_an_unknown_outcome_is_refused(store: NoustStore, app_id: int) -> None:
    with pytest.raises(ValueError, match="outcome"):
        wd.record_delivery(app_id, "made_up")


def test_the_detail_is_one_short_line(store: NoustStore, app_id: int) -> None:
    row = wd.record_delivery(app_id, wd.IGNORED_EVENT, detail="x" * 500 + "\nsecond line")

    assert row is not None
    assert row.detail is not None
    assert len(row.detail) <= wd.MAX_DETAIL
    assert "\n" not in row.detail


def test_what_the_forge_names_is_kept_at_a_bounded_length(store: NoustStore, app_id: int) -> None:
    row = wd.record_delivery(
        app_id,
        wd.IGNORED_EVENT,
        provider="p" * 500,
        event="e" * 500,
        branch="b" * 500,
        delivery_id="d" * 500,
    )

    assert row is not None
    for value in (row.provider, row.event, row.branch, row.delivery_id):
        assert value is not None and len(value) == wd.MAX_FIELD


def test_deleting_the_application_deletes_its_deliveries(store: NoustStore, app_id: int) -> None:
    wd.record_delivery(app_id, wd.PING)

    store.delete_app(DOMAIN)

    with store._transaction() as cursor:
        cursor.execute("SELECT COUNT(*) FROM webhook_deliveries")
        assert cursor.fetchone()[0] == 0


def test_a_store_that_cannot_write_does_not_fail_the_delivery(
    store: NoustStore,
    app_id: int,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import sqlite3

    def broken(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "_transaction", broken)

    with caplog.at_level("WARNING", logger="noust.core.webhook_deliveries"):
        assert wd.record_delivery(app_id, wd.PING) is None

    assert "database is locked" in caplog.text


def test_the_summary_of_an_application_that_never_heard_from_its_forge(
    store: NoustStore, app_id: int
) -> None:
    summary = wd.summarize(app_id)

    assert summary.total == 0
    assert summary.last is None
    assert summary.last_verified is None
    assert summary.last_push is None
    assert summary.refused_since_last_verified == 0


def test_the_summary_counts_the_refusals_since_the_last_verified_delivery(
    store: NoustStore, app_id: int
) -> None:
    wd.record_delivery(app_id, wd.BAD_SIGNATURE)
    wd.record_delivery(app_id, wd.PING, provider="github")
    wd.record_delivery(app_id, wd.DEPLOY_STARTED, branch="main", job_id="j1")
    wd.record_delivery(app_id, wd.IGNORED_BRANCH, branch="dev")
    for _ in range(3):
        wd.record_delivery(app_id, wd.BAD_SIGNATURE)

    summary = wd.summarize(app_id)

    assert summary.last is not None and summary.last.outcome == "bad_signature"
    assert summary.last_verified is not None and summary.last_verified.outcome == "ignored_branch"
    assert summary.last_push is not None and summary.last_push.outcome == "deploy_started"
    assert summary.refused_since_last_verified == 3
