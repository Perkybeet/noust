# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The whole output of a backup that failed is kept and the notice links to it (checklist H-10).

A notice carries the end of the failure, literal but bounded to what a chat message holds, and a
policy keeps four thousand characters. A dump that fails on its ninetieth line of output, or an
rclone that says why on its first, left the operator with a sentence that stopped short of the
reason. What is pinned here: the output is kept whole (up to a generous bound, cut at the front
because a tool says why it stopped last), it is scrubbed of the credentials Noust knows before it is
stored, the notice names the record and the command that reads it, a log that cannot be written
never turns a failed backup into a different failure, and the oldest records go.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.config import Config
from noust.core.fs import DryRunFileSystem, set_fs
from noust.core.store import NoustStore
from noust.managers.backup_failures import (
    KEEP_LAST,
    MAX_OUTPUT,
    BackupFailureLog,
    record_failure,
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    opened = NoustStore(tmp_path / "noust.db")
    NoustStore._instance = opened
    yield opened
    opened.close()
    NoustStore.reset_instance()


@pytest.fixture
def log(store: NoustStore) -> BackupFailureLog:
    return BackupFailureLog(store)


class TestKeepingTheWholeOutput:
    def test_a_long_failure_is_kept_line_for_line(self, log: BackupFailureLog) -> None:
        text = "pg_dump failed\n  Details: " + "\n".join(f"line {n}" for n in range(500))

        failure_id = log.record("backup", "shop.example.com", text)

        stored = log.get(failure_id)
        assert stored is not None
        assert stored.output == text
        assert stored.truncated is False
        assert (stored.kind, stored.subject, stored.destination) == (
            "backup",
            "shop.example.com",
            None,
        )

    def test_the_destination_that_refused_is_recorded(self, log: BackupFailureLog) -> None:
        failure_id = log.record("upload", "shop.example.com", "denied", destination="nas")

        stored = log.get(failure_id)
        assert stored is not None and stored.destination == "nas" and stored.kind == "upload"

    def test_an_output_past_the_bound_is_cut_at_the_front_and_says_so(
        self, log: BackupFailureLog
    ) -> None:
        head = "THE-FIRST-LINE\n"
        tail = "\nTHE-LAST-LINE"
        text = head + ("x" * (MAX_OUTPUT + 1000)) + tail

        failure_id = log.record("backup", "shop.example.com", text)

        stored = log.get(failure_id)
        assert stored is not None and stored.truncated is True
        assert stored.output.endswith("THE-LAST-LINE")
        assert "THE-FIRST-LINE" not in stored.output
        assert stored.output.startswith("[...]")
        assert len(stored.output) <= MAX_OUTPUT + 16

    def test_an_empty_failure_is_still_a_record(self, log: BackupFailureLog) -> None:
        failure_id = log.record("backup", "shop.example.com", "")

        stored = log.get(failure_id)
        assert stored is not None and stored.output == ""

    def test_an_unknown_record_is_none(self, log: BackupFailureLog) -> None:
        assert log.get(99) is None


class TestListing:
    def test_the_newest_come_first_and_the_list_carries_no_output(
        self, log: BackupFailureLog
    ) -> None:
        first = log.record("backup", "a.example.com", "one")
        second = log.record("upload", "b.example.com", "two", destination="nas")

        listed = log.recent()

        assert [failure.id for failure in listed] == [second, first]
        assert all(failure.size == len(failure.output.encode()) for failure in listed)

    def test_a_subject_narrows_the_list(self, log: BackupFailureLog) -> None:
        log.record("backup", "a.example.com", "one")
        wanted = log.record("backup", "b.example.com", "two")

        assert [failure.id for failure in log.recent(subject="b.example.com")] == [wanted]

    def test_the_limit_is_honoured(self, log: BackupFailureLog) -> None:
        for n in range(5):
            log.record("backup", "a.example.com", f"failure {n}")

        assert len(log.recent(limit=2)) == 2

    def test_only_the_last_ones_are_kept(self, log: BackupFailureLog) -> None:
        for n in range(KEEP_LAST + 7):
            log.record("backup", "a.example.com", f"failure {n}")

        kept = log.recent(limit=KEEP_LAST + 50)
        assert len(kept) == KEEP_LAST
        assert kept[0].output == f"failure {KEEP_LAST + 6}"
        assert kept[-1].output == "failure 7"


class TestRecordingFromABackup:
    def test_the_credentials_noust_knows_are_scrubbed_before_they_are_stored(
        self, store: NoustStore
    ) -> None:
        Config().set("notifications.webhook.secret", "hunter2-hunter2-hunter2")
        text = "rclone failed\n  Details: 401 for key AKIAIOSFODNN7EXAMPLE token hunter2-hunter2-hunter2"

        failure_id = record_failure("upload", "shop.example.com", text, destination="nas")

        assert failure_id is not None
        stored = BackupFailureLog(store).get(failure_id)
        assert stored is not None
        assert "hunter2-hunter2-hunter2" not in stored.output
        assert "AKIAIOSFODNN7EXAMPLE" not in stored.output
        assert "401" in stored.output

    def test_nothing_to_record_is_none(self, store: NoustStore) -> None:
        assert record_failure("backup", "shop.example.com", None) is None

    def test_a_log_that_cannot_be_written_never_turns_the_failure_into_another(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sqlite3

        def broken(self: BackupFailureLog, *args: object, **kwargs: object) -> int:
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(BackupFailureLog, "record", broken)

        assert record_failure("backup", "shop.example.com", "boom") is None

    def test_a_dry_run_records_nothing(self, store: NoustStore) -> None:
        set_fs(DryRunFileSystem())
        try:
            record_failure("backup", "shop.example.com", "boom")
        finally:
            set_fs(None)

        assert BackupFailureLog(store).recent() == []

    def test_a_record_says_the_command_that_prints_it(self, log: BackupFailureLog) -> None:
        failure_id = log.record("backup", "shop.example.com", "boom")

        stored = log.get(failure_id)
        assert stored is not None
        assert stored.command == f"noust backup failures show {failure_id}"
        assert stored.summary()["command"] == stored.command
