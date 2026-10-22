# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The roles of a PostgreSQL server are dumped with ``pg_dumpall --globals-only`` and put back (checklist H-28, H-30).

The argv is what is pinned: ``pg_dumpall`` runs as the cluster's superuser through the runner, the
file is a secret (it holds password hashes) and is written private, and putting it back asks the
server which roles it has first, so that only the missing ones are created.
"""

# The fixtures are imported from the tests of the thing they set up.
# ruff: noqa: F811

from __future__ import annotations

import gzip
import stat
from pathlib import Path

import pytest

from noust.core.exceptions import DatabaseBackupError
from noust.core.runner import FakeRunner
from noust.managers.database.postgres import PostgresManager
from tests.test_database_managers import PSQL_PREFIX, postgres  # noqa: F401

GLOBALS = (
    "SET client_encoding = 'UTF8';\n"
    "CREATE ROLE postgres;\n"
    "ALTER ROLE postgres WITH SUPERUSER INHERIT LOGIN PASSWORD 'SCRAM-SHA-256$4096:a$b:c';\n"
    "CREATE ROLE shopper;\n"
    "ALTER ROLE shopper WITH NOSUPERUSER INHERIT LOGIN PASSWORD 'SCRAM-SHA-256$4096:x$y:z';\n"
)


class TestDumpingTheRoles:
    def test_pg_dumpall_runs_as_the_superuser_for_the_globals_only(
        self, postgres: PostgresManager, runner: FakeRunner
    ) -> None:
        runner.script(["runuser", "-u", "postgres", "--", "pg_dumpall"], stdout=GLOBALS)

        info = postgres.backup_globals()

        call = next(call for call in runner.calls if "pg_dumpall" in call)
        assert call[:4] == ("runuser", "-u", "postgres", "--")
        assert call[4:] == ("pg_dumpall", "--no-password", "--globals-only")
        assert info.path.parent == postgres.BACKUP_DIR / "globals"
        assert info.path.name.startswith("postgresql-globals-")
        assert info.path.suffixes == [".sql", ".gz"]
        # The real runner gzips what it captures; the fake writes it as it is.
        assert info.path.read_text() == GLOBALS

    def test_the_directory_is_private_because_the_file_holds_password_hashes(
        self, postgres: PostgresManager, runner: FakeRunner
    ) -> None:
        runner.script(["runuser", "-u", "postgres", "--", "pg_dumpall"], stdout=GLOBALS)

        info = postgres.backup_globals()

        assert stat.S_IMODE(info.path.parent.stat().st_mode) & 0o027 == 0

    def test_a_failing_pg_dumpall_says_what_it_said(
        self, postgres: PostgresManager, runner: FakeRunner
    ) -> None:
        runner.script(
            ["runuser", "-u", "postgres", "--", "pg_dumpall"],
            stderr="pg_dumpall: error: connection to server failed",
            exit_code=1,
        )

        with pytest.raises(DatabaseBackupError) as caught:
            postgres.backup_globals()

        assert "connection to server failed" in str(caught.value)

    def test_the_globals_listing_is_newest_first_and_apart_from_the_dumps(
        self, postgres: PostgresManager, runner: FakeRunner
    ) -> None:
        runner.script(["runuser", "-u", "postgres", "--", "pg_dumpall"], stdout=GLOBALS)
        first = postgres.backup_globals()
        older = first.path.with_name("postgresql-globals-20200101_000000.sql.gz")
        older.write_bytes(first.path.read_bytes())

        listed = postgres.list_globals()

        assert [path.name for path in listed][-1] == older.name
        assert postgres.list_backups() == []


class TestPuttingTheRolesBack:
    def test_only_the_roles_the_server_lacks_are_created(
        self, postgres: PostgresManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        runner.script(PSQL_PREFIX, stdout="postgres\n")
        source = tmp_path / "globals.sql"
        source.write_text(GLOBALS)

        result = postgres.restore_globals(source)

        assert result.created == ("shopper",)
        assert result.present == ("postgres",)
        applied = runner.inputs[-1]
        assert applied is not None
        assert "CREATE ROLE shopper;" in applied
        assert "CREATE ROLE postgres" not in applied
        assert "SCRAM-SHA-256$4096:a" not in applied

    def test_the_script_runs_as_the_superuser_without_stopping_at_the_first_refusal(
        self, postgres: PostgresManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        runner.script(PSQL_PREFIX, stdout="postgres\n")
        source = tmp_path / "globals.sql"
        source.write_text(GLOBALS)

        postgres.restore_globals(source)

        call = runner.calls[-1]
        assert call[:5] == ("runuser", "-u", "postgres", "--", "psql")
        assert "ON_ERROR_STOP=0" in call
        assert call[-2:] == ("-f", "-")

    def test_a_gzipped_file_is_read_through_gzip(
        self, postgres: PostgresManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        runner.script(PSQL_PREFIX, stdout="postgres\n")
        source = tmp_path / "globals.sql.gz"
        with gzip.open(source, "wt") as handle:
            handle.write(GLOBALS)

        result = postgres.restore_globals(source)

        assert result.created == ("shopper",)

    def test_nothing_is_run_when_the_server_has_every_role(
        self, postgres: PostgresManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        runner.script(PSQL_PREFIX, stdout="postgres\nshopper\n")
        source = tmp_path / "globals.sql"
        source.write_text(GLOBALS)
        before = len(runner.calls)

        result = postgres.restore_globals(source)

        assert result.created == ()
        assert len(runner.calls) == before + 1  # only the question about the roles

    def test_what_the_server_said_about_a_statement_is_in_the_result(
        self, postgres: PostgresManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        runner.script(PSQL_PREFIX, stdout="postgres\n")
        source = tmp_path / "globals.sql"
        source.write_text(GLOBALS)
        # psql exits 0 with ON_ERROR_STOP=0 and prints each refusal as an ERROR line.
        runner.script(
            [*PSQL_PREFIX, "-v", "ON_ERROR_STOP=0"],
            stderr='psql:<stdin>:4: ERROR:  role "pg_read_all_data" does not exist',
        )

        result = postgres.restore_globals(source)

        assert result.errors == ('role "pg_read_all_data" does not exist',)

    def test_a_missing_file_is_a_clear_error(
        self, postgres: PostgresManager, tmp_path: Path
    ) -> None:
        with pytest.raises(DatabaseBackupError) as caught:
            postgres.restore_globals(tmp_path / "nope.sql")

        assert "nope.sql" in str(caught.value)
