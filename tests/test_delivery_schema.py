# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The delivery lane's part of schema v14: what a store gains for cancelling.

A cancelled deployment cannot be a new status (the column's CHECK cannot be
altered in SQLite), so it is a failed row that carries when and by whom it was
cancelled, and a request to cancel is a row of its own. Both are declared in
the delivery fragment of :mod:`noust.core.schema_v14`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import delivery_store, schema_v14
from noust.core.store import DeploymentRecord, NoustStore


@pytest.fixture
def db_path(tmp_path: Path) -> Iterator[Path]:
    """
    Where the test's store lives.

    Yields:
        The database file.
    """
    NoustStore.reset_instance()
    yield tmp_path / "noust.db"
    NoustStore.reset_instance()


def columns(path: Path, table: str) -> set[str]:
    """
    Args:
        path: The database file, read with a plain connection.
        table: The table.

    Returns:
        Its column names.
    """
    connection = sqlite3.connect(path)
    try:
        return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    finally:
        connection.close()


def tables(path: Path) -> set[str]:
    """
    Args:
        path: The database file, read with a plain connection.

    Returns:
        Its table names.
    """
    connection = sqlite3.connect(path)
    try:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    finally:
        connection.close()


def test_the_fragment_declares_the_requests_table_and_the_two_columns() -> None:
    assert "delivery_cancel_requests" in schema_v14.DELIVERY_SQL
    assert ("deployments", "cancelled_at", "TEXT") in schema_v14.DELIVERY_COLUMNS
    assert ("deployments", "cancelled_by", "TEXT") in schema_v14.DELIVERY_COLUMNS


def test_a_fresh_store_has_them(db_path: Path) -> None:
    NoustStore(db_path)

    assert "delivery_cancel_requests" in tables(db_path)
    assert {"cancelled_at", "cancelled_by"} <= columns(db_path, "deployments")


def test_a_store_from_before_4_0_gains_them_and_keeps_its_history(db_path: Path) -> None:
    store = NoustStore(db_path)
    deployment = store.record_deployment_start("shop.example.com", "cli")
    store.finish_deployment(deployment, "success")
    NoustStore.reset_instance()
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("DROP TABLE delivery_cancel_requests")
        connection.execute("ALTER TABLE deployments DROP COLUMN cancelled_at")
        connection.execute("ALTER TABLE deployments DROP COLUMN cancelled_by")
        connection.execute("UPDATE schema_version SET version = 13 WHERE version > 13")
        connection.commit()
    finally:
        connection.close()

    store = NoustStore(db_path)

    assert "delivery_cancel_requests" in tables(db_path)
    assert {"cancelled_at", "cancelled_by"} <= columns(db_path, "deployments")
    kept = store.get_deployment(deployment)
    assert kept is not None and kept.status == "success" and kept.cancelled is False


def test_a_cancelled_deployment_is_a_failed_row_that_says_so(db_path: Path) -> None:
    """The status column keeps the three values older stores can hold."""
    store = NoustStore(db_path)
    deployment = store.record_deployment_start("shop.example.com", "panel")
    store.finish_deployment(
        deployment, "failed", error="The update of shop.example.com was cancelled"
    )

    assert delivery_store.mark_deployment_cancelled(deployment, "token:ci", store=store) is True

    row = store.get_deployment(deployment)
    assert row is not None
    assert row.status == "failed"
    assert row.cancelled is True
    assert row.cancelled_by == "token:ci"
    assert row.cancelled_at is not None
    assert DeploymentRecord().cancelled is False


def test_marking_a_deployment_that_is_gone_says_so(db_path: Path) -> None:
    store = NoustStore(db_path)

    assert delivery_store.mark_deployment_cancelled(404, "op", store=store) is False


def test_who_cancelled_is_kept_to_a_sane_length(db_path: Path) -> None:
    store = NoustStore(db_path)
    deployment = store.record_deployment_start("shop.example.com", "panel")

    delivery_store.mark_deployment_cancelled(deployment, "x" * 500, store=store)

    row = store.get_deployment(deployment)
    assert row is not None and row.cancelled_by is not None
    assert len(row.cancelled_by) == delivery_store.MAX_REQUESTER


def test_a_request_replaces_one_addressed_to_an_operation_that_is_gone(db_path: Path) -> None:
    store = NoustStore(db_path)
    delivery_store.put_request(
        "a.example.com", holder_pid=1, holder_started_at="t1", requested_by="x", store=store
    )

    fresh = delivery_store.put_request(
        "a.example.com", holder_pid=2, holder_started_at="t2", requested_by="y", store=store
    )

    assert delivery_store.get_request("a.example.com", store) == fresh
    assert delivery_store.request_for("a.example.com", 1, "t1", store) is None
    assert delivery_store.request_for("a.example.com", 2, "t2", store) == fresh


def test_an_answered_request_is_not_answered_twice(db_path: Path) -> None:
    store = NoustStore(db_path)
    delivery_store.put_request(
        "a.example.com", holder_pid=1, holder_started_at="t", requested_by="x", store=store
    )

    assert delivery_store.decide("a.example.com", 1, "t", delivery_store.CANCELLED, store) is True
    assert delivery_store.decide("a.example.com", 1, "t", delivery_store.FINISHED, store) is False

    answered = delivery_store.get_request("a.example.com", store)
    assert answered is not None and answered.outcome == delivery_store.CANCELLED


def test_the_requests_of_other_operations_are_dropped_when_one_starts(db_path: Path) -> None:
    store = NoustStore(db_path)
    delivery_store.put_request(
        "a.example.com", holder_pid=1, holder_started_at="old", requested_by="x", store=store
    )

    assert delivery_store.drop_stale("a.example.com", 2, "new", store) == 1
    assert delivery_store.get_request("a.example.com", store) is None
    assert delivery_store.drop_stale("a.example.com", 2, "new", store) == 0
