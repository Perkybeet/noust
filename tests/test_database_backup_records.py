# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The two tables behind database backups: policies, and what is known of each dump.

The files on disk are the truth for which dumps exist; these rows say what
Noust knows about them, so what matters is that a row survives its own
rewriting: a run's outcome does not erase the last success, saving a policy
again does not erase its last run, and a damaged JSON column reads as empty
rather than taking a listing down.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.store import NoustStore
from noust.managers.database.backup_records import (
    MAX_EVIDENCE,
    BackupPolicy,
    BackupRecords,
    DumpRecord,
    bound_evidence,
)


@pytest.fixture
def records(tmp_path: Path) -> Iterator[BackupRecords]:
    """
    Args:
        tmp_path: Per-test temporary directory.

    Yields:
        The records over an isolated store.
    """
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    try:
        yield BackupRecords(store)
    finally:
        store.close()
        NoustStore.reset_instance()


def policy(**overrides: object) -> BackupPolicy:
    values: dict[str, object] = {
        "engine": "postgresql",
        "db_name": "shop",
        "schedule": "*-*-* 02:00:00",
        "retention_count": 7,
        "retention_days": 30,
        "destinations": [{"name": "nas", "retention_count": 3, "retention_days": None}],
        "dump_format": "custom",
        "verify_restore": True,
    }
    values.update(overrides)
    return BackupPolicy(**values)  # type: ignore[arg-type]


class TestPolicies:
    def test_a_policy_round_trips_every_field(self, records: BackupRecords) -> None:
        records.save_policy(policy())

        stored = records.policy("postgresql", "shop")

        assert stored is not None
        assert stored.schedule == "*-*-* 02:00:00"
        assert (stored.retention_count, stored.retention_days) == (7, 30)
        assert stored.destinations == [
            {"name": "nas", "retention_count": 3, "retention_days": None}
        ]
        assert stored.dump_format == "custom"
        assert stored.verify_restore is True
        assert stored.enabled is True
        assert stored.created_at and stored.updated_at
        assert stored.last_status is None

    def test_saving_again_replaces_the_settings_and_keeps_what_the_last_run_said(
        self, records: BackupRecords
    ) -> None:
        records.save_policy(policy())
        records.record_run("postgresql", "shop", ok=True, error=None, dump="d1.dump")

        records.save_policy(policy(schedule="weekly", retention_count=None))

        stored = records.policy("postgresql", "shop")
        assert stored is not None
        assert stored.schedule == "weekly"
        assert stored.retention_count is None
        assert stored.last_status == "ok"
        assert stored.last_dump == "d1.dump"

    def test_a_failed_run_keeps_the_last_success(self, records: BackupRecords) -> None:
        records.save_policy(policy())
        records.record_run("postgresql", "shop", ok=True, error=None, dump="d1.dump")
        first = records.policy("postgresql", "shop")
        assert first is not None

        records.record_run("postgresql", "shop", ok=False, error="pg_dump: boom", dump=None)

        stored = records.policy("postgresql", "shop")
        assert stored is not None
        assert stored.last_status == "failed"
        assert stored.last_error == "pg_dump: boom"
        assert stored.last_success_at == first.last_success_at
        assert stored.last_dump is None

    def test_a_success_clears_the_last_error(self, records: BackupRecords) -> None:
        records.save_policy(policy())
        records.record_run("postgresql", "shop", ok=False, error="boom", dump=None)

        records.record_run("postgresql", "shop", ok=True, error=None, dump="d2.dump")

        stored = records.policy("postgresql", "shop")
        assert stored is not None
        assert stored.last_error is None
        assert stored.last_success_at

    def test_policies_are_listed_by_engine_and_database_and_deleted(
        self, records: BackupRecords
    ) -> None:
        records.save_policy(policy(db_name="shop"))
        records.save_policy(policy(db_name="blog"))
        records.save_policy(policy(engine="mysql", db_name="wp"))

        assert [(p.engine, p.db_name) for p in records.policies()] == [
            ("mysql", "wp"),
            ("postgresql", "blog"),
            ("postgresql", "shop"),
        ]
        assert [p.db_name for p in records.policies("postgresql")] == ["blog", "shop"]
        assert records.delete_policy("postgresql", "blog") is True
        assert records.delete_policy("postgresql", "blog") is False

    def test_running_a_policy_that_is_not_stored_is_not_an_error(
        self, records: BackupRecords
    ) -> None:
        records.record_run("postgresql", "ghost", ok=True, error=None, dump="d.dump")

        assert records.policy("postgresql", "ghost") is None

    def test_a_damaged_destinations_column_reads_as_none(self, records: BackupRecords) -> None:
        records.save_policy(policy())
        with records.store._transaction() as cursor:
            cursor.execute("UPDATE database_backup_policies SET destinations = 'not json'")

        stored = records.policy("postgresql", "shop")

        assert stored is not None
        assert stored.destinations == []


class TestDumps:
    def test_a_dump_round_trips_with_its_evidence_and_copies(self, records: BackupRecords) -> None:
        records.save_dump(
            DumpRecord(
                engine="postgresql",
                file_name="postgresql-shop-1.dump",
                db_name="shop",
                origin="scheduled",
                size=42,
                sha256="ab" * 32,
                verified_at="2026-09-29T10:00:00+00:00",
                verify_status="ok",
                verify_method="pg_restore --list",
                verify_detail="12 objects listed.",
                remote_copies=[{"destination": "nas", "verified_by": "md5"}],
            )
        )

        stored = records.dump("postgresql", "postgresql-shop-1.dump")

        assert stored is not None
        assert (stored.origin, stored.size, stored.verify_status) == ("scheduled", 42, "ok")
        assert stored.remote_copies == [{"destination": "nas", "verified_by": "md5"}]
        assert stored.created_at

    def test_saving_again_replaces_it(self, records: BackupRecords) -> None:
        record = DumpRecord(engine="postgresql", file_name="d.dump", db_name="shop")
        records.save_dump(record)
        record.verify_status = "failed"
        record.verify_detail = "pg_restore: error: bad"

        records.save_dump(record)

        (only,) = records.dumps("postgresql", "shop")
        assert only.verify_status == "failed"

    def test_dumps_are_listed_for_a_database_and_deleted(self, records: BackupRecords) -> None:
        records.save_dump(DumpRecord(engine="postgresql", file_name="a.dump", db_name="shop"))
        records.save_dump(DumpRecord(engine="postgresql", file_name="b.dump", db_name="blog"))
        records.save_dump(DumpRecord(engine="mysql", file_name="c.sql", db_name="shop"))

        assert [d.file_name for d in records.dumps("postgresql", "shop")] == ["a.dump"]
        assert len(records.dumps("postgresql")) == 2
        assert len(records.dumps()) == 3
        assert records.delete_dump("postgresql", "a.dump") is True
        assert records.delete_dump("postgresql", "a.dump") is False

    def test_the_same_file_name_on_two_engines_is_two_dumps(self, records: BackupRecords) -> None:
        records.save_dump(DumpRecord(engine="postgresql", file_name="x", db_name="shop"))
        records.save_dump(DumpRecord(engine="mysql", file_name="x", db_name="shop"))

        assert len(records.dumps()) == 2


class TestEvidence:
    def test_short_evidence_is_kept_whole(self) -> None:
        assert bound_evidence("  pg_restore: error: bad  \n") == "pg_restore: error: bad"

    def test_long_evidence_keeps_its_end_where_a_tool_says_why_it_stopped(self) -> None:
        text = "start\n" + "x" * (MAX_EVIDENCE * 2) + "\nthe reason it stopped"

        bounded = bound_evidence(text)

        assert bounded.startswith("[...]")
        assert bounded.endswith("the reason it stopped")
        assert len(bounded) <= MAX_EVIDENCE + len("[...]\n")
