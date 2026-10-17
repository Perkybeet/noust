# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The roles of a PostgreSQL server travel with its copies (checklist H-28, H-30 and the audit's `pg_dumpall` gap).

A database dump names its owners and the roles it grants to, and none of them is in it: they
belong to the cluster. Restored onto a new server a dump loses its owners, or fails on the first
``ALTER ... OWNER TO``. ``pg_dumpall --globals-only`` writes those roles, with their password
hashes. What is pinned here is how it is put back without making anything worse: only the roles
the server does not have are created, nothing that exists is altered (the superuser's password
included), and what cannot be put back yet (a tablespace's directory, a setting for a database
that is not there) is said rather than failing the rest.
"""

from __future__ import annotations

import pytest

from noust.managers.database.pg_globals import filter_missing, role_names

GLOBALS = '''\
--
-- PostgreSQL database cluster dump
--

SET default_transaction_read_only = off;

SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;

--
-- Roles
--

CREATE ROLE postgres;
ALTER ROLE postgres WITH SUPERUSER INHERIT CREATEROLE CREATEDB LOGIN REPLICATION BYPASSRLS PASSWORD 'SCRAM-SHA-256$4096:abc$def:ghi';
CREATE ROLE shopper;
ALTER ROLE shopper WITH NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB LOGIN NOREPLICATION NOBYPASSRLS PASSWORD 'SCRAM-SHA-256$4096:xyz$aaa:bbb';
CREATE ROLE "Weird ""Role""";
ALTER ROLE "Weird ""Role""" WITH NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB NOLOGIN NOREPLICATION NOBYPASSRLS;
ALTER ROLE shopper SET search_path TO 'shop', 'public';
ALTER ROLE shopper IN DATABASE shop SET work_mem TO '8MB';
GRANT pg_read_all_data TO shopper GRANTED BY postgres;
GRANT "Weird ""Role""" TO shopper GRANTED BY postgres;
GRANT shopper TO postgres GRANTED BY postgres;
COMMENT ON ROLE shopper IS 'the shop; it''s ours';

--
-- Tablespaces
--

CREATE TABLESPACE fast OWNER postgres LOCATION '/mnt/fast';
ALTER TABLESPACE fast OWNER TO shopper;

--
-- PostgreSQL database cluster dump complete
--
'''


class TestWhichRolesAreMissing:
    def test_only_the_roles_the_server_lacks_are_created(self) -> None:
        plan = filter_missing(GLOBALS, existing={"postgres"})

        assert plan.created == ("shopper", 'Weird "Role"')
        assert plan.present == ("postgres",)
        assert "CREATE ROLE shopper;" in plan.sql
        assert 'CREATE ROLE "Weird ""Role""";' in plan.sql

    def test_a_role_that_exists_is_never_altered_not_even_the_superusers_password(self) -> None:
        plan = filter_missing(GLOBALS, existing={"postgres"})

        assert "CREATE ROLE postgres" not in plan.sql
        assert "ALTER ROLE postgres" not in plan.sql
        assert "SCRAM-SHA-256$4096:abc" not in plan.sql

    def test_the_new_roles_keep_their_password_hashes_and_settings(self) -> None:
        plan = filter_missing(GLOBALS, existing={"postgres"})

        assert "PASSWORD 'SCRAM-SHA-256$4096:xyz$aaa:bbb'" in plan.sql
        assert "ALTER ROLE shopper SET search_path TO 'shop', 'public';" in plan.sql
        assert "COMMENT ON ROLE shopper IS 'the shop; it''s ours';" in plan.sql

    def test_memberships_are_kept_only_for_the_roles_that_are_new(self) -> None:
        plan = filter_missing(GLOBALS, existing={"postgres"})

        assert "GRANT pg_read_all_data TO shopper GRANTED BY postgres;" in plan.sql
        assert 'GRANT "Weird ""Role""" TO shopper GRANTED BY postgres;' in plan.sql
        # postgres is not new: what it is a member of is not this restore's to change.
        assert "GRANT shopper TO postgres" not in plan.sql

    def test_nothing_is_created_when_every_role_is_already_there(self) -> None:
        plan = filter_missing(GLOBALS, existing={"postgres", "shopper", 'Weird "Role"'})

        assert plan.created == ()
        assert "CREATE ROLE" not in plan.sql and "ALTER ROLE" not in plan.sql


class TestWhatIsLeftOutAndSaid:
    def test_a_tablespace_needs_its_directory_so_it_is_left_out(self) -> None:
        plan = filter_missing(GLOBALS, existing={"postgres"})

        assert "TABLESPACE" not in plan.sql
        assert any("tablespace fast" in item for item in plan.left_out)

    def test_a_setting_for_a_database_that_is_not_there_yet_is_left_out(self) -> None:
        plan = filter_missing(GLOBALS, existing={"postgres"})

        assert "IN DATABASE" not in plan.sql
        assert any("shopper" in item and "database shop" in item for item in plan.left_out)

    def test_the_header_that_sets_the_session_up_is_kept(self) -> None:
        plan = filter_missing(GLOBALS, existing=set())

        assert "SET client_encoding = 'UTF8';" in plan.sql
        assert "SET standard_conforming_strings = on;" in plan.sql


class TestAHostileFile:
    @pytest.mark.parametrize(
        "line",
        [
            "\\! id",
            "\\i /etc/passwd",
            "CREATE ROLE ok; \\! id",
        ],
    )
    def test_a_psql_meta_command_is_never_carried_into_what_is_applied(self, line: str) -> None:
        plan = filter_missing(f"CREATE ROLE a;\n{line}\nCREATE ROLE b;\n", existing=set())

        assert "\\!" not in plan.sql and "\\i" not in plan.sql

    def test_a_statement_that_is_not_about_roles_is_dropped(self) -> None:
        plan = filter_missing(
            "CREATE ROLE a;\nDROP DATABASE shop;\nCREATE EXTENSION dblink;\nCOPY x TO PROGRAM 'id';\n",
            existing=set(),
        )

        assert "DROP DATABASE" not in plan.sql
        assert "CREATE EXTENSION" not in plan.sql
        assert "PROGRAM" not in plan.sql
        assert plan.created == ("a",)

    def test_a_quote_that_never_closes_swallows_nothing_that_follows_into_the_plan(self) -> None:
        plan = filter_missing(
            "CREATE ROLE a;\nALTER ROLE a SET x TO 'oops;\nDROP ROLE b;\n", existing=set()
        )

        assert "DROP ROLE" not in plan.sql


class TestRoleNames:
    def test_the_roles_a_globals_file_creates_are_listed_unquoted(self) -> None:
        assert role_names(GLOBALS) == ("postgres", "shopper", 'Weird "Role"')
