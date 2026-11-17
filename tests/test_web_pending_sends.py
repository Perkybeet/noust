# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The API says which copies were made and have not reached a destination (item 201).

A listing of each kind of copy (an application's backups, the data of an application, a database's
dumps, Noust's own bundles) carries ``pending_sends``: for each destination the copy is still owed to,
what it said, how often it was tried and when it is tried next. The console paints it another day; the
field is the contract. An empty list is a copy that is everywhere it was meant to be, and a store that
cannot be read costs a listing that field and nothing more.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core.pending_sends import PendingSends, owed_by_copy
from noust.core.store import NoustStore
from noust.managers.backup_manager import BackupMetadata
from noust.web.api.backups import _to_backup_info
from noust.web.api.self_backup import BundleResponse, _entry

NOW = datetime(2026, 11, 17, 2, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    NoustStore._instance = instance
    yield instance
    NoustStore.reset_instance()


def owe(item: str, kind: str = "app", destination: str = "nas") -> None:
    PendingSends().defer(
        kind,
        item,
        destination,
        owner="shop.example.com",
        copy_created_at=NOW,
        reason="i/o timeout",
        permanent=False,
        failure_id=None,
        now=NOW,
        next_attempt_at=NOW + timedelta(minutes=15),
    )


def backup(backup_id: str) -> BackupMetadata:
    return BackupMetadata(
        id=backup_id,
        domain="shop.example.com",
        app_name="shop-example-com",
        created_at="2026-11-17T01:00:00",
        size_bytes=1024,
        app_type="static",
        version="4.3.0",
        description="",
        includes_env=True,
        includes_node_modules=False,
    )


class TestAnApplicationsBackup:
    def test_a_backup_that_was_not_sent_says_where_why_and_when_it_is_tried_next(
        self, store: NoustStore
    ) -> None:
        owe("shop-example-com_a")

        info = _to_backup_info(backup("shop-example-com_a"), owed_by_copy("app"))

        (entry,) = info.pending_sends
        assert entry["destination"] == "nas"
        assert entry["reason"] == "i/o timeout"
        assert entry["attempts"] == 0
        assert entry["next_attempt_at"] == (NOW + timedelta(minutes=15)).isoformat(
            timespec="seconds"
        )
        assert entry["permanent"] is False

    def test_a_backup_that_is_everywhere_says_nothing(self, store: NoustStore) -> None:
        owe("shop-example-com_a")

        assert (
            _to_backup_info(backup("shop-example-com_b"), owed_by_copy("app")).pending_sends == []
        )

    def test_without_what_is_owed_the_field_is_empty(self) -> None:
        assert _to_backup_info(backup("shop-example-com_a")).pending_sends == []


class TestTheOtherKinds:
    def test_a_bundle_of_noust_s_own_state_says_it_too(self, store: NoustStore) -> None:
        owe("noust-20261117.tar.gz.enc", kind="noust")

        data = _entry(
            {"name": "noust-20261117.tar.gz.enc", "path": "/x", "size": 10},
            owed_by_copy("noust"),
        )

        assert BundleResponse(**data).pending_sends[0]["destination"] == "nas"
        assert "path" not in data

    def test_what_is_owed_is_read_by_the_kind_of_copy(self, store: NoustStore) -> None:
        owe("same-name", kind="app")
        owe("same-name", kind="volume", destination="cloud")

        assert [row.destination for row in owed_by_copy("app")["same-name"]] == ["nas"]
        assert [row.destination for row in owed_by_copy("volume")["same-name"]] == ["cloud"]
        assert owed_by_copy("database") == {}


def test_a_store_that_cannot_be_read_costs_a_listing_the_field_and_nothing_more(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def locked(self: PendingSends) -> list[Any]:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(PendingSends, "all", locked)

    assert owed_by_copy("app") == {}
