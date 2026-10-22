# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A dump that fails quietly leaves nothing behind (checklist H-06), tried with real processes."""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from noust.core.exceptions import DatabaseBackupError
from noust.core.runner import SubprocessRunner, set_runner
from noust.managers.database.postgres import PostgresManager

pytestmark = pytest.mark.allow_subprocess


@pytest.fixture
def manager(tmp_path: Path) -> PostgresManager:
    """A manager that dumps with whatever program the test names, under a temporary folder."""
    set_runner(SubprocessRunner())
    instance = PostgresManager()
    instance.BACKUP_DIR = tmp_path / "backups"
    yield instance
    set_runner(None)


def dump(manager: PostgresManager, script: str, *, compress: bool) -> Path:
    """Run a shell snippet as the dump program and return where its file would be."""
    destination = manager.BACKUP_DIR / "postgresql-shop.sql"
    manager._dump_to_file(["sh", "-c", script], destination, database="shop", compress=compress)
    return destination


class TestAnEngineThatDiesMidDump:
    @pytest.mark.parametrize("compress", [False, True])
    def test_the_partial_file_is_removed_and_the_error_carries_what_it_said(
        self, manager: PostgresManager, compress: bool
    ) -> None:
        script = "printf 'COPY half of a table'; echo 'pg_dump: connection lost' >&2; exit 3"

        with pytest.raises(DatabaseBackupError) as failure:
            dump(manager, script, compress=compress)

        assert "connection lost" in failure.value.details
        assert list(manager.BACKUP_DIR.glob("*")) == []

    def test_a_dump_that_outlives_its_deadline_leaves_no_prefix(
        self, manager: PostgresManager
    ) -> None:
        destination = manager.BACKUP_DIR / "postgresql-shop.sql"

        with pytest.raises(DatabaseBackupError):
            manager._dump_to_file(
                ["sh", "-c", "printf 'a prefix'; sleep 30"],
                destination,
                database="shop",
                compress=False,
                timeout=1,
            )

        assert not destination.exists()

    def test_the_status_of_the_producer_is_not_lost_behind_gzip(
        self, manager: PostgresManager
    ) -> None:
        """``dump | gzip`` reports gzip's 0 unless the producer's status is kept too."""
        with pytest.raises(DatabaseBackupError):
            dump(manager, "printf 'some rows'; exit 1", compress=True)

        assert list(manager.BACKUP_DIR.glob("*")) == []


class TestAnEngineThatSaysItWorkedAndWroteNothing:
    def test_an_empty_file_is_refused_and_removed(self, manager: PostgresManager) -> None:
        with pytest.raises(DatabaseBackupError, match="is empty") as failure:
            dump(manager, "exit 0", compress=False)

        assert "wrote nothing" in failure.value.details
        assert list(manager.BACKUP_DIR.glob("*")) == []

    def test_a_gzip_of_nothing_is_refused_and_removed(self, manager: PostgresManager) -> None:
        with pytest.raises(DatabaseBackupError, match="is empty"):
            dump(manager, "exit 0", compress=True)

        assert list(manager.BACKUP_DIR.glob("*")) == []

    def test_a_small_real_dump_is_kept(self, manager: PostgresManager) -> None:
        destination = dump(manager, "printf 'x'", compress=True)

        assert gzip.decompress(destination.read_bytes()) == b"x"

    def test_a_tiny_dump_is_kept_uncompressed_too(self, manager: PostgresManager) -> None:
        destination = dump(manager, "printf ';'", compress=False)

        assert destination.read_bytes() == b";"
