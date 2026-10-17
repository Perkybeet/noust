# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Reading a pg_dumpall file (4.0, checklist H-18).

The fixtures are what a real ``pg_dumpall`` of PostgreSQL 16 and 17 wrote for a cluster with
four accounts (one a superuser), a role membership, a per-role setting and three user databases,
one of them holding rows that look like the boundaries the reader cuts at. What is pinned:

- the accounts come out as data, with the attributes that are not carried named and the password
  kept only when it is a hash of the shape the server stores;
- each database is cut out into a file of its own that is an ordinary pg_dump plain dump, cut where
  psql would read the boundary and nowhere a string, a COPY row or a function body only looks like
  one;
- the statements Noust writes to create an account are built from that data, never from the
  file's text.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from noust.core.exceptions import DatabaseBackupError
from noust.managers.database.cluster_dump import (
    SYSTEM_DATABASES,
    MembershipSpec,
    RoleSpec,
    is_cluster_dump,
    read_cluster,
    render_membership,
    render_role,
    usable_name,
)
from noust.managers.database.psql_script import check_plain_dump

FIXTURES = Path(__file__).parent / "fixtures" / "pg_dumpall"


def scratch(tmp_path: Path) -> Path:
    """A private directory for what the reader writes."""
    directory = tmp_path / "scratch"
    directory.mkdir(exist_ok=True)
    return directory


@pytest.fixture(params=["16", "17"])
def cluster(request: pytest.FixtureRequest, tmp_path: Path):  # type: ignore[no-untyped-def]
    return read_cluster(FIXTURES / f"all{request.param}.sql", scratch(tmp_path))


class TestTheAccounts:
    def test_every_account_of_the_file_is_read(self, cluster) -> None:  # type: ignore[no-untyped-def]
        assert [role.name for role in cluster.roles] == [
            "bossman",
            "legacy",
            "postgres",
            "reporting",
            "shopapp",
        ]

    def test_the_attributes_that_could_escalate_are_named_and_not_carried(self, cluster) -> None:  # type: ignore[no-untyped-def]
        by_name = {role.name: role for role in cluster.roles}

        assert by_name["bossman"].dropped == ("SUPERUSER",)
        assert by_name["postgres"].dropped == (
            "SUPERUSER",
            "CREATEROLE",
            "REPLICATION",
            "BYPASSRLS",
        )
        assert by_name["shopapp"].dropped == ()

    def test_what_is_safe_is_carried(self, cluster) -> None:  # type: ignore[no-untyped-def]
        by_name = {role.name: role for role in cluster.roles}

        assert by_name["shopapp"].login and by_name["shopapp"].createdb
        assert not by_name["reporting"].login
        legacy = by_name["legacy"]
        assert legacy.connection_limit == 5
        assert legacy.valid_until == "2030-01-01 00:00:00+00"

    def test_the_password_is_kept_as_the_hash_the_server_stores(self, cluster) -> None:  # type: ignore[no-untyped-def]
        by_name = {role.name: role for role in cluster.roles}

        assert (by_name["shopapp"].password_hash or "").startswith("SCRAM-SHA-256$4096:")
        assert by_name["reporting"].password_hash is None

    def test_a_membership_between_accounts_is_read(self, cluster) -> None:  # type: ignore[no-untyped-def]
        assert cluster.memberships == [MembershipSpec("reporting", "shopapp", False)]

    def test_what_is_not_carried_is_listed_for_the_operator(self, cluster) -> None:  # type: ignore[no-untyped-def]
        assert any("per-role setting" in note for note in cluster.ignored)

    def test_a_globals_file_has_accounts_and_no_databases(self, tmp_path: Path) -> None:
        cluster = read_cluster(FIXTURES / "globals17.sql", scratch(tmp_path))

        assert len(cluster.roles) == 5
        assert cluster.databases == []

    def test_a_plaintext_password_is_not_a_hash_and_is_not_carried(self, tmp_path: Path) -> None:
        dump = tmp_path / "g.sql"
        dump.write_text(
            "CREATE ROLE ana;\n"
            "ALTER ROLE ana WITH NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB LOGIN "
            "NOREPLICATION NOBYPASSRLS PASSWORD 'hunter2';\n"
        )

        (ana,) = read_cluster(dump, scratch(tmp_path)).roles

        assert ana.password_hash is None
        assert "PASSWORD" in ana.dropped
        assert "hunter2" not in render_role(ana)

    def test_a_gzipped_file_is_read_as_it_is(self, tmp_path: Path) -> None:
        packed = tmp_path / "all.sql.gz"
        packed.write_bytes(gzip.compress((FIXTURES / "all17.sql").read_bytes()))

        cluster = read_cluster(packed, scratch(tmp_path))

        assert [d.name for d in cluster.databases] == ["template1", "blog", "postgres", "shop"]


class TestTheDatabases:
    def test_each_database_is_found_with_its_owner_and_encoding(self, cluster) -> None:  # type: ignore[no-untyped-def]
        by_name = {db.name: db for db in cluster.databases}

        assert list(by_name) == ["template1", "blog", "postgres", "shop"]
        assert (by_name["blog"].owner, by_name["blog"].encoding) == ("legacy", "UTF8")
        assert by_name["shop"].owner == "shopapp"

    def test_the_cluster_own_databases_are_flagged(self, cluster) -> None:  # type: ignore[no-untyped-def]
        assert {db.name for db in cluster.databases if db.system} == {"template1", "postgres"}
        assert {"template0", "template1", "postgres"} == SYSTEM_DATABASES

    def test_a_databases_file_is_its_own_dump_and_nothing_else(self, cluster) -> None:  # type: ignore[no-untyped-def]
        by_name = {db.name: db for db in cluster.databases}
        shop = by_name["shop"].path.read_text()  # type: ignore[union-attr]

        assert "CREATE TABLE public.orders" in shop
        assert "CREATE DATABASE" not in shop and "posts" not in shop
        assert "GRANT CONNECT ON DATABASE shop TO reporting;" in shop
        assert by_name["shop"].size == len(shop.encode())

    def test_rows_that_look_like_boundaries_are_data(self, cluster) -> None:  # type: ignore[no-untyped-def]
        blog = {db.name: db for db in cluster.databases}["blog"].path.read_text()  # type: ignore[union-attr]

        assert '1\t-- Database "shop" dump' in blog
        assert "\\\\connect shop" in blog
        assert "CREATE TABLE public.orders" not in blog

    def test_every_file_passes_the_scan_for_meta_commands(self, cluster) -> None:  # type: ignore[no-untyped-def]
        for database in cluster.databases:
            check_plain_dump(database.path)  # type: ignore[arg-type]

    def test_the_files_are_private(self, cluster) -> None:  # type: ignore[no-untyped-def]
        for database in cluster.databases:
            assert (database.path.stat().st_mode & 0o777) == 0o600  # type: ignore[union-attr]

    def test_a_connect_inside_a_function_body_is_not_a_boundary(self, tmp_path: Path) -> None:
        dump = tmp_path / "all.sql"
        dump.write_text(
            "--\n-- PostgreSQL database cluster dump\n--\n\n"
            '--\n-- Database "app" dump\n--\n\n'
            "CREATE DATABASE app WITH TEMPLATE = template0 ENCODING = 'UTF8';\n\n"
            "\\connect app\n\n"
            "CREATE FUNCTION f() RETURNS text LANGUAGE sql AS $$\n"
            "\\connect other\n"
            '-- Database "other" dump\n'
            "$$;\n\n"
        )

        cluster = read_cluster(dump, scratch(tmp_path))

        assert [db.name for db in cluster.databases] == ["app"]
        assert "\\connect other" in cluster.databases[0].path.read_text()  # type: ignore[union-attr]

    def test_a_meta_command_in_the_globals_is_refused(self, tmp_path: Path) -> None:
        dump = tmp_path / "all.sql"
        dump.write_text("CREATE ROLE a;\n\\! id\n")

        with pytest.raises(DatabaseBackupError, match="meta-command"):
            read_cluster(dump, scratch(tmp_path))

    def test_a_database_connected_twice_is_refused(self, tmp_path: Path) -> None:
        dump = tmp_path / "all.sql"
        dump.write_text(
            '-- Database "a" dump\n\\connect a\nSELECT 1;\n'
            '-- Database "b" dump\n\\connect a\nSELECT 2;\n'
        )

        with pytest.raises(DatabaseBackupError, match="twice"):
            read_cluster(dump, scratch(tmp_path))


class TestWhatIsWritten:
    def test_an_account_is_written_from_its_data(self) -> None:
        role = RoleSpec(
            name="shopapp",
            login=True,
            createdb=True,
            connection_limit=5,
            password_hash="md5" + "a" * 32,
            valid_until="2030-01-01 00:00:00+00",
        )

        sql = render_role(role)

        assert sql == (
            'CREATE ROLE "shopapp" WITH LOGIN INHERIT CREATEDB NOSUPERUSER NOCREATEROLE '
            "NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 5 PASSWORD 'md5" + "a" * 32 + "' "
            "VALID UNTIL '2030-01-01 00:00:00+00';\n"
        )

    def test_an_account_that_cannot_sign_in_has_no_password_clause(self) -> None:
        assert "PASSWORD" not in render_role(RoleSpec(name="reporting"))

    def test_a_membership_is_written_from_its_data(self) -> None:
        assert (
            render_membership(MembershipSpec("reporting", "shopapp", True))
            == 'GRANT "reporting" TO "shopapp" WITH ADMIN OPTION;\n'
        )

    @pytest.mark.parametrize(
        ("name", "usable"),
        [
            ("shopapp", True),
            ("shop-app$1", True),
            ("postgres", False),
            ("pg_execute_server_program", False),
            ("wasm_ro_shop", False),
            ("noust_load_ab12", False),
            ("with space", False),
            ('a"; drop role x; --', False),
            ("", False),
            ("a" * 64, False),
        ],
    )
    def test_a_name_is_used_only_when_noust_would_write_it(self, name: str, usable: bool) -> None:
        assert usable_name(name) is usable

    def test_the_cluster_header_is_what_tells_the_file(self) -> None:
        assert is_cluster_dump((FIXTURES / "all17.sql").read_bytes()[:8192])
        assert not is_cluster_dump(b"-- PostgreSQL database dump\nSELECT 1;\n")
