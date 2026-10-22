# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A PostgreSQL dump is not loaded as the superuser (4.0, checklist H-19).

A dump is a script, and a superuser's session runs ``COPY ... FROM PROGRAM``
for it. The loader is a login of its own, made for the load: not a superuser,
borrowing the database owner's identity when that owner cannot escalate, and
dropped after. Switching roles inside a superuser session would not do, because
``RESET ROLE`` brings the superuser back from inside the script; what the real
engine does with it is shown by ``tests/integration/engines_run.py``
(``restore_unprivileged``). Here is what Noust asks of the runner:

- which account every statement runs as, and that no password is in argv or
  in a statement;
- that the login is released whether the load works or not;
- that a refusal for want of rights says how to repeat it as the superuser;
- that the superuser stays one option away, and that Noust's own dumps (the
  safety copy put back after a failed restore) do not need it asked.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from noust.core.exceptions import DatabaseBackupError
from noust.core.runner import CommandResult, FakeRunner
from noust.managers.database.pg_loader import (
    LOAD_ACCOUNT_PREFIX,
    OwnerFacts,
    asks_for_more_privilege,
    owner_query,
    parse_owner,
    provision_statements,
    release_statements,
)
from noust.managers.database.postgres import PostgresManager
from tests.test_database_import_formats import PG_CUSTOM
from tests.test_database_managers import StubConfig

PLAIN = (
    "-- PostgreSQL database dump\nSET standard_conforming_strings = on;\nCREATE TABLE t (i int);\n"
)


class Cluster(FakeRunner):
    """A PostgreSQL that answers by the statement it reads on stdin."""

    def __init__(self, *, owner: str = "app", privileged: bool = False) -> None:
        super().__init__()
        self.owner = owner
        self.privileged = privileged
        #: What the TCP login (the loader) gets back.
        self.load: CommandResult | None = None
        self.sql: list[str] = []

    def run(self, argv, *, input=None, **kwargs):  # type: ignore[no-untyped-def]
        result = super().run(argv, input=input, **kwargs)
        if input and "pg_has_role" in input:
            flag = "t" if self.privileged else "f"
            return replace(result, stdout=f"{self.owner}|{flag}\n")
        if input and "SHOW port" in input:
            return replace(result, stdout="5432\n")
        if input and "datname" in input and "pg_database_size" not in input:
            return replace(result, stdout="1\n")
        if input and "pg_database_size" in input:
            return replace(result, stdout=f"shop|UTF8|8192|{self.owner}\n")
        if input:
            self.sql.append(input)
        if self.load is not None and "-U" in argv and "-h" in argv:
            return replace(result, **{"exit_code": self.load.exit_code, "stderr": self.load.stderr})
        return result

    def logins(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if "-U" in call and "-h" in call]


@pytest.fixture
def cluster() -> Cluster:
    from noust.core.runner import set_runner

    fake = Cluster()
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture
def postgres(tmp_path: Path) -> PostgresManager:
    manager = PostgresManager()
    manager.BACKUP_DIR = tmp_path / "engine-backups"
    manager.config = StubConfig({})  # type: ignore[assignment]
    return manager


def plain(tmp_path: Path) -> Path:
    dump = tmp_path / "d.sql"
    dump.write_text(PLAIN)
    return dump


class TestTheLoginIsNotTheSuperuser:
    def test_a_plain_dump_is_read_by_a_login_that_is_not_postgres(
        self, postgres: PostgresManager, cluster: Cluster, tmp_path: Path
    ) -> None:
        dump = plain(tmp_path)

        postgres.restore("shop", dump, safety_backup=False)

        (login,) = cluster.logins()
        assert login[0] == "psql"
        who = login[login.index("-U") + 1]
        assert who.startswith(LOAD_ACCOUNT_PREFIX)
        assert login[login.index("-h") + 1] == "127.0.0.1"
        assert login[login.index("-p") + 1] == "5432"
        # The superuser's own way in (runuser postgres) is not how the dump is read.
        assert "runuser" not in login
        assert cluster.stdin_paths[-1] == dump

    def test_its_password_travels_in_the_environment_only(
        self, postgres: PostgresManager, cluster: Cluster, tmp_path: Path
    ) -> None:
        postgres.restore("shop", plain(tmp_path), safety_backup=False)

        index = cluster.calls.index(cluster.logins()[0])
        password = (cluster.envs[index] or {}).get("PGPASSWORD")
        assert password and len(password) >= 32
        assert not any(password in part for call in cluster.calls for part in call)
        assert not any(password in text for text in cluster.sql)
        assert not any(password in (text or "") for text in cluster.inputs)

    def test_it_is_a_member_of_the_owner_and_starts_as_that_role(
        self, postgres: PostgresManager, cluster: Cluster, tmp_path: Path
    ) -> None:
        postgres.restore("shop", plain(tmp_path), safety_backup=False)

        index = cluster.calls.index(cluster.logins()[0])
        options = (cluster.envs[index] or {}).get("PGOPTIONS", "")
        assert "-c role=app" in options
        provision = next(text for text in cluster.sql if "CREATE ROLE" in text)
        assert 'GRANT "app" TO "noust_load_' in provision
        for clause in (
            "NOSUPERUSER",
            "NOCREATEROLE",
            "NOREPLICATION",
            "NOBYPASSRLS",
            "VALID UNTIL",
        ):
            assert clause in provision
        # Only a verifier is sent: the server's log never sees the password.
        assert "PASSWORD 'SCRAM-SHA-256$" in provision

    def test_a_custom_archive_is_read_by_the_same_kind_of_login(
        self, postgres: PostgresManager, cluster: Cluster, tmp_path: Path
    ) -> None:
        dump = tmp_path / "d.dump"
        dump.write_bytes(PG_CUSTOM)

        postgres.restore("shop", dump, safety_backup=False)

        (login,) = cluster.logins()
        assert login[0] == "pg_restore"
        assert login[login.index("-U") + 1].startswith(LOAD_ACCOUNT_PREFIX)
        assert login[-2:] == ("-d", "shop") or "shop" in login

    def test_the_login_is_dropped_when_the_load_works(
        self, postgres: PostgresManager, cluster: Cluster, tmp_path: Path
    ) -> None:
        postgres.restore("shop", plain(tmp_path), safety_backup=False)

        assert any("DROP OWNED BY" in text for text in cluster.sql)
        assert any("DROP ROLE IF EXISTS" in text for text in cluster.sql)

    def test_the_login_is_dropped_when_the_load_fails(
        self, postgres: PostgresManager, cluster: Cluster, tmp_path: Path
    ) -> None:
        cluster.load = CommandResult(argv=(), exit_code=3, stderr="ERROR: syntax error")

        with pytest.raises(DatabaseBackupError):
            postgres.restore("shop", plain(tmp_path), safety_backup=False)

        assert any("DROP ROLE IF EXISTS" in text for text in cluster.sql)


class TestAnOwnerThatCouldGiveTheSuperuserBack:
    def test_a_superuser_owned_database_is_loaded_by_a_login_that_owns_what_it_makes(
        self, postgres: PostgresManager, cluster: Cluster, tmp_path: Path
    ) -> None:
        cluster.owner, cluster.privileged = "postgres", True

        postgres.restore("shop", plain(tmp_path), safety_backup=False)

        index = cluster.calls.index(cluster.logins()[0])
        # The owner's identity would be the superuser's: it is not borrowed.
        assert "role=" not in (cluster.envs[index] or {}).get("PGOPTIONS", "")
        provision = next(text for text in cluster.sql if "CREATE ROLE" in text)
        assert 'GRANT CREATE, TEMPORARY ON DATABASE "shop" TO "noust_load_' in provision
        assert 'GRANT "postgres"' not in provision
        # What it made is handed to the real owner before it goes.
        assert any(
            text.startswith("REASSIGN OWNED BY") and 'TO "postgres"' in text for text in cluster.sql
        )


class TestWhenTheDumpAsksForMore:
    def test_a_refusal_for_rights_says_how_to_repeat_it_as_the_superuser(
        self, postgres: PostgresManager, cluster: Cluster, tmp_path: Path
    ) -> None:
        cluster.load = CommandResult(
            argv=(),
            exit_code=3,
            stderr="ERROR:  permission denied to execute program\nDETAIL: Only roles with the "
            "privileges of the pg_execute_server_program role may COPY to or from an external program.",
        )

        with pytest.raises(DatabaseBackupError) as raised:
            postgres.restore("shop", plain(tmp_path), safety_backup=False)

        assert "--as-superuser" in (raised.value.details or "")
        # The server's own words are kept verbatim next to the hint.
        assert "pg_execute_server_program" in (raised.value.details or "")

    def test_another_failure_does_not_suggest_the_superuser(
        self, postgres: PostgresManager, cluster: Cluster, tmp_path: Path
    ) -> None:
        cluster.load = CommandResult(
            argv=(), exit_code=3, stderr="ERROR:  syntax error at end of input"
        )

        with pytest.raises(DatabaseBackupError) as raised:
            postgres.restore("shop", plain(tmp_path), safety_backup=False)

        assert "--as-superuser" not in (raised.value.details or "")

    def test_the_superuser_is_one_explicit_option_away(
        self, postgres: PostgresManager, cluster: Cluster, tmp_path: Path
    ) -> None:
        postgres.restore("shop", plain(tmp_path), safety_backup=False, as_superuser=True)

        assert not cluster.logins()
        loads = [call for call in cluster.calls if call[-2:] == ("-f", "-")]
        assert loads and loads[-1][:5] == ("runuser", "-u", "postgres", "--", "psql")
        assert not any("CREATE ROLE" in text for text in cluster.sql)

    def test_the_options_that_leave_owners_out_still_apply_with_the_superuser(
        self, postgres: PostgresManager, cluster: Cluster, tmp_path: Path
    ) -> None:
        dump = tmp_path / "d.sql"
        dump.write_text(PLAIN + "ALTER TABLE t OWNER TO other;\n")

        postgres.restore("shop", dump, safety_backup=False, as_superuser=True, keep_owners=False)

        stripped = cluster.stdin_paths[-1]
        assert stripped.name == "owners-stripped.sql"


class TestTheHelpers:
    def test_the_owner_query_reads_the_roles_that_run_programs(self) -> None:
        sql = owner_query("shop")
        for word in (
            "rolsuper",
            "rolcreaterole",
            "pg_execute_server_program",
            "pg_read_server_files",
        ):
            assert word in sql
        assert "'shop'" in sql

    def test_a_quote_in_a_database_name_cannot_end_the_literal(self) -> None:
        assert "'it''s'" in owner_query("it's")

    @pytest.mark.parametrize(
        ("output", "expected"),
        [
            ("app|f\n", OwnerFacts("app", False)),
            ("postgres|t", OwnerFacts("postgres", True)),
            ("we|ird|f", OwnerFacts("we|ird", False)),
            ("", None),
            ("app", None),
            ("app|x", None),
        ],
    )
    def test_the_answer_is_read_or_refused(self, output: str, expected: OwnerFacts | None) -> None:
        assert parse_owner(output) == expected

    def test_a_loader_borrowing_its_owner_needs_nothing_inside_the_database(self) -> None:
        cluster, inside = provision_statements(
            "noust_load_ab", "verifier", "shop", OwnerFacts("app", False)
        )
        assert inside is None
        assert 'GRANT "app" TO "noust_load_ab"' in cluster

    def test_a_loader_that_owns_what_it_makes_is_given_the_schema(self) -> None:
        _cluster, inside = provision_statements(
            "noust_load_ab", "verifier", "shop", OwnerFacts("postgres", True)
        )
        assert inside is not None and 'ON SCHEMA public TO "noust_load_ab"' in inside

    def test_releasing_hands_over_only_when_the_loader_owned_things(self) -> None:
        borrowed, _ = release_statements("noust_load_ab", OwnerFacts("app", False))
        owned, drop = release_statements("noust_load_ab", OwnerFacts("postgres", True))
        assert "REASSIGN" not in borrowed
        assert owned.startswith('REASSIGN OWNED BY "noust_load_ab" TO "postgres"')
        assert drop.endswith('DROP ROLE IF EXISTS "noust_load_ab";\n')
        assert "pg_terminate_backend" in drop

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ('ERROR:  permission denied to create extension "postgis"', True),
            ("ERROR:  must be superuser to create this extension", True),
            ("ERROR:  must be owner of extension plpgsql", True),
            ("ERROR:  permission denied for table t", True),
            ('ERROR:  extension "postgis" is not a trusted extension', True),
            # PostgreSQL 16 words the refusal to hand an object to a role the loader is not in so.
            ('ERROR:  must be able to SET ROLE "postgres"', True),
            ("ERROR:  syntax error at end of input", False),
            ('ERROR:  relation "t" already exists', False),
            ("", False),
        ],
    )
    def test_a_failure_for_want_of_rights_is_told_from_the_rest(
        self, text: str, expected: bool
    ) -> None:
        assert asks_for_more_privilege(text) is expected
