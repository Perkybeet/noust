# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A server that existed before teams is given one, and keeps working exactly as it did.

What an upgrade promises (I-01, I-02): a team ``default`` with a project
``default`` and an environment ``production``; every application, site, database
and backup schedule in it; and every account that may be, a member of it with the
role that matches its instance role - so nobody gains or loses a power. The step
that does it is a data step run by the store's migration (the schema module
only declares columns and rewrites no row), and it is idempotent.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import schema_v12, schema_v13, schema_v14
from noust.core import store as store_module
from noust.core.fs import RecordingFileSystem
from noust.core.store import SCHEMA_VERSION, NoustStore
from noust.core.teams import Scope, TeamStore, backfill
from tests.test_store_migrations import _create_v3_database


def create_v13_store(path: Path) -> None:
    """
    Build a database as 3.3 left it: every table of v13, and rows.

    Args:
        path: Where the database file is made.
    """
    conn = sqlite3.connect(path)
    try:
        cursor = conn.cursor()
        store_module._run_script(cursor, store_module.SCHEMA_SQL)
        schema_v12.apply_v12(cursor, store_module._run_script)
        schema_v13.apply_v13(cursor, store_module._run_script)
        cursor.execute("INSERT INTO schema_version (version) VALUES (13)")
        for domain in ("shop.example.com", "blog.example.com"):
            cursor.execute(
                "INSERT INTO apps (domain, app_type, app_path, status) VALUES (?, 'nodejs', ?, 'running')",
                (domain, f"/srv/{domain}"),
            )
        for app_id, domain in ((1, "shop.example.com"), (2, "blog.example.com")):
            cursor.execute(
                "INSERT INTO sites (app_id, domain, config_path) VALUES (?, ?, ?)",
                (app_id, domain, f"/etc/nginx/{domain}"),
            )
        cursor.execute(
            "INSERT INTO sites (app_id, domain, config_path) VALUES (NULL, 'hand.example.com', '/x')"
        )
        cursor.execute(
            "INSERT INTO databases (app_id, name, engine) VALUES (1, 'shop_db', 'postgresql')"
        )
        cursor.execute(
            "INSERT INTO databases (app_id, name, engine) VALUES (NULL, 'loose', 'mysql')"
        )
        cursor.execute(
            "INSERT INTO backup_schedules (app_domain, schedule, created_at, updated_at) "
            "VALUES ('shop.example.com', 'daily', 'now', 'now')"
        )
        for username, role in (
            ("vera", "viewer"),
            ("olga", "operator"),
            ("adam", "admin"),
            ("sofia", "security"),
            ("aurora", "auditor"),
        ):
            cursor.execute(
                "INSERT INTO accounts (username, role, created_at, updated_at) VALUES (?, ?, 1, 1)",
                (username, role),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def fresh() -> Iterator[None]:
    """A store singleton this test owns, so an injected filesystem is the one used."""
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def migrated(fresh: None, tmp_path: Path) -> NoustStore:
    """A 3.3 server, opened by this version."""
    db = tmp_path / "noust.db"
    create_v13_store(db)
    return NoustStore(db, fs=RecordingFileSystem())


def owner_of(store: NoustStore, table: str, **where: object) -> tuple[int | None, int | None]:
    """
    Args:
        store: The store.
        table: A table that carries ownership.
        **where: Equality conditions that pick one row.

    Returns:
        Its project and environment ids.
    """
    clause = " AND ".join(f"{column} = ?" for column in where)
    row = (
        store._get_connection()
        .execute(
            f"SELECT project_id, environment_id FROM {table} WHERE {clause}",  # noqa: S608
            tuple(where.values()),
        )
        .fetchone()
    )
    return row[0], row[1]


class TestAnExistingServerIsGivenItsDefault:
    def test_it_has_the_default_team_project_and_environment(self, migrated: NoustStore) -> None:
        teams = TeamStore(migrated)

        assert [t.slug for t in teams.list_teams()] == ["default"]
        assert [(p.team, p.slug) for p in teams.list_projects()] == [("default", "default")]
        assert [(e.project, e.slug) for e in teams.list_environments()] == [
            ("default", "production")
        ]

    def test_every_resource_is_in_it(self, migrated: NoustStore) -> None:
        default = TeamStore(migrated).default_target()

        for table, where in (
            ("apps", {"domain": "shop.example.com"}),
            ("apps", {"domain": "blog.example.com"}),
            ("sites", {"domain": "shop.example.com"}),
            ("sites", {"domain": "hand.example.com"}),
            ("databases", {"name": "shop_db"}),
            ("databases", {"name": "loose"}),
            ("backup_schedules", {"app_domain": "shop.example.com"}),
        ):
            assert owner_of(migrated, table, **where) == (
                default.project_id,
                default.environment_id,
            ), (table, where)

    def test_nothing_is_left_without_an_owner(self, migrated: NoustStore) -> None:
        for table in ("apps", "sites", "databases", "backup_schedules"):
            unowned = (
                migrated._get_connection()
                .execute(
                    f"SELECT COUNT(*) FROM {table} WHERE project_id IS NULL OR environment_id IS NULL"  # noqa: S608
                )
                .fetchone()[0]
            )
            assert unowned == 0, table

    @pytest.mark.parametrize(
        ("username", "role"),
        [("vera", "viewer"), ("olga", "operator"), ("adam", "admin")],
    )
    def test_an_account_becomes_a_member_with_the_matching_role(
        self, migrated: NoustStore, username: str, role: str
    ) -> None:
        member = TeamStore(migrated).member("default", username)

        assert member.role == role
        assert member.created_by == "migration"

    @pytest.mark.parametrize("username", ["sofia", "aurora"])
    def test_security_and_auditor_accounts_join_nothing(
        self, migrated: NoustStore, username: str
    ) -> None:
        teams = TeamStore(migrated)
        account_id = (
            migrated._get_connection()
            .execute("SELECT id FROM accounts WHERE username = ?", (username,))
            .fetchone()[0]
        )

        assert teams.memberships_of(account_id) == {}

    def test_nobody_gains_or_loses_sight_of_anything(self, migrated: NoustStore) -> None:
        # One team: a viewer and an operator are members of every team there is.
        teams = TeamStore(migrated)
        for username, role in (("vera", "viewer"), ("olga", "operator"), ("adam", "admin")):
            account_id = (
                migrated._get_connection()
                .execute("SELECT id FROM accounts WHERE username = ?", (username,))
                .fetchone()[0]
            )
            assert teams.scope_for(account_id, role).sees_all

    def test_the_rest_of_every_row_is_as_it_was(self, migrated: NoustStore) -> None:
        app = migrated.get_app("shop.example.com")

        assert app is not None
        assert (app.app_type, app.app_path, app.status) == (
            "nodejs",
            "/srv/shop.example.com",
            "running",
        )

    def test_the_version_is_the_current_schema(self, migrated: NoustStore) -> None:
        version = (
            migrated._get_connection()
            .execute("SELECT MAX(version) FROM schema_version")
            .fetchone()[0]
        )

        assert version == SCHEMA_VERSION

    def test_the_climb_from_an_old_release_ends_in_the_same_place(
        self,
        fresh,
        tmp_path: Path,
    ) -> None:
        db = tmp_path / "noust.db"
        _create_v3_database(db)

        store = NoustStore(db, fs=RecordingFileSystem())

        default = TeamStore(store).default_target()
        assert owner_of(store, "apps", domain="v3.example.com") == (
            default.project_id,
            default.environment_id,
        )


class TestTheStepRunsAgainWithoutHarm:
    def test_running_it_twice_changes_nothing(self, migrated: NoustStore) -> None:
        teams = TeamStore(migrated)
        before = (
            [t.to_dict() for t in teams.list_teams()],
            [m.to_dict() for m in teams.list_members("default")],
            owner_of(migrated, "apps", domain="shop.example.com"),
        )

        with migrated._ddl_transaction() as cursor:
            backfill.run(cursor)
            backfill.run(cursor)

        assert before == (
            [t.to_dict() for t in teams.list_teams()],
            [m.to_dict() for m in teams.list_members("default")],
            owner_of(migrated, "apps", domain="shop.example.com"),
        )

    def test_an_account_taken_out_of_the_default_team_is_not_put_back(
        self, migrated: NoustStore
    ) -> None:
        teams = TeamStore(migrated)
        teams.remove_member("default", "olga")

        with migrated._ddl_transaction() as cursor:
            backfill.run(cursor)

        assert "olga" not in {m.username for m in teams.list_members("default")}

    def test_a_row_that_arrives_without_an_owner_is_given_its_applications(
        self, migrated: NoustStore
    ) -> None:
        teams = TeamStore(migrated)
        acme = teams.create_team("Acme", slug="acme")
        target = teams.resolve_target(Scope.everything(), team="acme")
        with migrated._transaction() as cursor:
            cursor.execute(
                "UPDATE apps SET project_id = ?, environment_id = ? WHERE domain = 'shop.example.com'",
                (target.project_id, target.environment_id),
            )
            cursor.execute("UPDATE sites SET project_id = NULL, environment_id = NULL")
            cursor.execute("UPDATE databases SET project_id = NULL, environment_id = NULL")
            cursor.execute("UPDATE backup_schedules SET project_id = NULL, environment_id = NULL")

        with migrated._ddl_transaction() as cursor:
            backfill.run(cursor)

        for table, where in (
            ("sites", {"domain": "shop.example.com"}),
            ("databases", {"name": "shop_db"}),
            ("backup_schedules", {"app_domain": "shop.example.com"}),
        ):
            project, _environment = owner_of(migrated, table, **where)
            assert teams.project_by_id(project or 0).team_id == acme.id, (table, where)  # type: ignore[union-attr]
        # What belongs to no application goes to the default.
        project, _ = owner_of(migrated, "databases", name="loose")
        assert teams.project_by_id(project or 0).team == "default"  # type: ignore[union-attr]


class TestAStoreStampedV14WithoutItsTeams:
    def test_the_tables_and_the_default_are_added_on_open(self, fresh, tmp_path: Path) -> None:
        db = tmp_path / "noust.db"
        create_v13_store(db)
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO schema_version (version) VALUES (14)")
        conn.commit()
        conn.close()

        store = NoustStore(db, fs=RecordingFileSystem())

        teams = TeamStore(store)
        assert [t.slug for t in teams.list_teams()] == ["default"]
        assert {m.username for m in teams.list_members("default")} == {"vera", "olga", "adam"}
        assert owner_of(store, "apps", domain="shop.example.com")[0] is not None

    def test_a_complete_store_is_not_touched_on_open(
        self, migrated: NoustStore, tmp_path: Path
    ) -> None:
        teams = TeamStore(migrated)
        teams.remove_member("default", "vera")
        NoustStore.reset_instance()

        reopened = NoustStore(tmp_path / "noust.db", fs=RecordingFileSystem())

        assert "vera" not in {m.username for m in TeamStore(reopened).list_members("default")}


class TestTheSchemaItself:
    def test_the_fragment_is_only_declarations(self) -> None:
        # The data step is not in the schema module (schema_v13's rule): no row is written there.
        text = schema_v14.TEAMS_SQL.upper()

        assert not re.search(r"INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM", text)

    def test_a_fresh_store_has_the_same_shape_as_a_migrated_one(
        self,
        fresh,
        tmp_path: Path,
    ) -> None:
        a = tmp_path / "a.db"
        b = tmp_path / "b.db"
        create_v13_store(a)
        migrated_store = NoustStore(a, fs=RecordingFileSystem())
        NoustStore.reset_instance()
        fresh_store = NoustStore(b, fs=RecordingFileSystem())

        def shape(store: NoustStore) -> dict[str, list[str]]:
            conn = store._get_connection()
            tables = [
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            ]
            return {
                t: sorted(col[1] for col in conn.execute(f"PRAGMA table_info({t})")) for t in tables
            }

        assert shape(migrated_store) == shape(fresh_store)

    def test_the_guards_are_installed_in_a_migrated_store_as_in_a_fresh_one(
        self, fresh: None, tmp_path: Path
    ) -> None:
        a = tmp_path / "a.db"
        b = tmp_path / "b.db"
        create_v13_store(a)
        migrated_store = NoustStore(a, fs=RecordingFileSystem())
        NoustStore.reset_instance()
        fresh_store = NoustStore(b, fs=RecordingFileSystem())

        def triggers(store: NoustStore) -> set[str]:
            rows = store._get_connection().execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
            return {row[0] for row in rows}

        assert triggers(migrated_store) == triggers(fresh_store)
        assert len(triggers(fresh_store)) == 8

    def test_a_migrated_store_refuses_an_environment_of_another_project(
        self, migrated: NoustStore
    ) -> None:
        teams = TeamStore(migrated)
        teams.create_team("Acme", slug="acme")
        elsewhere = teams.require_environment("acme", "default", "production")
        default = teams.default_target()

        with pytest.raises(sqlite3.IntegrityError, match="does not belong"):
            with migrated._transaction() as cursor:
                cursor.execute(
                    "UPDATE apps SET project_id = ?, environment_id = ? WHERE id = 1",
                    (default.project_id, elsewhere.id),
                )

    def test_foreign_keys_hold_what_a_manager_could_forget(self, migrated: NoustStore) -> None:
        conn = migrated._get_connection()
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        project = TeamStore(migrated).default_target().project_id

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM projects WHERE id = ?", (project,))
        conn.rollback()
