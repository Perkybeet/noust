# Copyright (c) 2024-2025 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests that a store schema change is atomic.

``cursor.executescript`` commits any pending transaction before it runs, and
its own statements are never covered by a later ``rollback()`` - not even
when the transaction was opened explicitly with ``BEGIN``. A crash or a
failing statement partway through a migration used to leave the schema
half-applied while ``schema_version`` still reported the version before it,
or after it, depending on exactly where the process died. These tests build
the same frozen old-schema fixture databases as ``tests/test_store.py`` and
inject a failure mid-migration to pin the fix: a version's schema change and
its ``schema_version`` row commit or roll back together, and a version that
already committed is never undone by a later version failing in the same
opening.
"""

import sqlite3
from pathlib import Path

import pytest

from noust.core import store as store_module
from noust.core.fs import RecordingFileSystem
from noust.core.store import SCHEMA_VERSION, NoustStore
from tests.test_store import V1_SCHEMA_SQL, V2_DEPLOYMENTS_SQL, V4_JOBS_SQL


@pytest.fixture
def fresh():
    """
    Guarantee a store singleton that this test owns.

    Yields:
        Nothing; the singleton is reset before and after the test so an
        injected filesystem is actually the one used.
    """
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


def _create_v3_database(db_path: Path) -> None:
    """
    Create a real v3 database with one app, as a 1.4.x release left it.

    Args:
        db_path: Where the database file is created.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(V1_SCHEMA_SQL)
        conn.executescript(V2_DEPLOYMENTS_SQL)
        conn.execute("ALTER TABLE apps ADD COLUMN webhook_secret TEXT")
        conn.execute("INSERT INTO schema_version (version) VALUES (1)")
        conn.execute("INSERT INTO schema_version (version) VALUES (2)")
        conn.execute("INSERT INTO schema_version (version) VALUES (3)")
        conn.execute(
            "INSERT INTO apps (domain, app_type, app_path) VALUES (?, ?, ?)",
            ("v3.example.com", "nextjs", "/var/www/apps/v3-example-com"),
        )
        conn.commit()
    finally:
        conn.close()


def _create_v7_database(db_path: Path) -> None:
    """
    Create a real v7 database with one deployment, as 2.0 pre-releases left it.

    Identical fixture to ``TestSchemaV8Migration._create_v7_database`` in
    ``tests/test_store.py``: a database at v7 is the exact input the v7-to-v8
    migration this file crashes mid-way through is meant to run against.

    Args:
        db_path: Where the database file is created.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(V1_SCHEMA_SQL)
        conn.executescript(V2_DEPLOYMENTS_SQL)
        conn.execute("ALTER TABLE apps ADD COLUMN webhook_secret TEXT")
        conn.executescript(V4_JOBS_SQL)
        conn.execute("ALTER TABLE jobs ADD COLUMN actor TEXT")
        for name, definition in store_module.APPS_V5_COLUMNS:
            conn.execute(f"ALTER TABLE apps ADD COLUMN {name} {definition}")
        conn.executescript(store_module.RELEASES_SCHEMA_SQL)
        conn.executescript(store_module.DOMAINS_SCHEMA_SQL)
        for version in (1, 2, 3, 4, 5, 6, 7):
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        conn.execute(
            "INSERT INTO deployments (domain, status, triggered_by, started_at)"
            " VALUES (?, ?, ?, ?)",
            ("old.example.com", "success", "cli", "2026-01-02T03:04:05"),
        )
        conn.commit()
    finally:
        conn.close()


def _raw_tables(db_path: Path) -> set[str]:
    """
    Args:
        db_path: Database file to inspect, opened directly with a plain
            ``sqlite3`` connection - not through a store, so inspecting the
            outcome of a failed migration cannot itself trigger another one.

    Returns:
        Names of every table in the database.
    """
    conn = sqlite3.connect(db_path)
    try:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def _raw_columns(db_path: Path, table: str) -> set[str]:
    """
    Args:
        db_path: Database file to inspect, opened directly.
        table: Table name.

    Returns:
        Column names of the given table.
    """
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        conn.close()


def _raw_max_version(db_path: Path) -> int:
    """
    Args:
        db_path: Database file to inspect, opened directly.

    Returns:
        The highest committed ``schema_version`` row, or 0 if none.
    """
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
    finally:
        conn.close()


class TestAFailingMigrationStepIsAtomic:
    """
    A statement failing partway through one version's migration must not
    leave that version half-applied, whether the failure is the first
    statement or, as here, comes after some of the version's own DDL already
    ran.
    """

    def test_a_failing_v7_to_v8_step_leaves_v7_intact_and_a_retry_completes_it(
        self, fresh, tmp_path
    ):
        """
        The real v7-to-v8 migration adds three columns to ``deployments``.
        Crashing right after the first must roll that column back too, not
        just leave ``schema_version`` at 7 while ``job_id`` quietly exists -
        a fix that only wraps the version row insert, and not the migration's
        own statements, would pass on the version number and fail on this.
        A later, real run must still take the untouched v7 database to v8.
        """
        db_path = tmp_path / "wasm.db"
        _create_v7_database(db_path)

        def _crash_after_first_statement(self: NoustStore, cursor: sqlite3.Cursor) -> None:
            cursor.execute("ALTER TABLE deployments ADD COLUMN job_id TEXT")
            raise sqlite3.OperationalError("simulated crash mid-migration")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(NoustStore, "_migrate_v7_to_v8", _crash_after_first_statement)
            with pytest.raises(sqlite3.OperationalError):
                NoustStore(db_path, fs=RecordingFileSystem())

        # The failed attempt must not have left a trace: not the version
        # bump, and not the one column it managed to add before crashing.
        assert _raw_max_version(db_path) == 7
        assert "job_id" not in _raw_columns(db_path, "deployments")

        NoustStore.reset_instance()
        store = NoustStore(db_path, fs=RecordingFileSystem())

        with store._transaction() as cursor:
            cursor.execute("SELECT MAX(version) FROM schema_version")
            assert cursor.fetchone()[0] == SCHEMA_VERSION

        record = store.list_deployments("old.example.com")[0]
        assert record.job_id is None
        assert record.release_id is None
        assert record.commit_message is None


class TestAFailingStepDoesNotUndoEarlierCommittedSteps:
    """
    Per-version atomicity: a version's schema change and its
    ``schema_version`` row are committed as soon as that version's migration
    finishes, so a later version failing in the same climb rolls back only
    itself. A v3 database upgrading in one opening walks v4, v5 and v6 before
    it ever reaches the v6-to-v7 step this fails; those three must stay
    committed, and nothing from v7 or v8 must exist.
    """

    def test_a_failure_at_v6_to_v7_keeps_v4_v5_v6_committed(self, fresh, tmp_path):
        db_path = tmp_path / "wasm.db"
        _create_v3_database(db_path)

        def _crash(self: NoustStore, cursor: sqlite3.Cursor) -> None:
            raise sqlite3.OperationalError("simulated crash at v6-to-v7")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(NoustStore, "_migrate_v6_to_v7", _crash)
            with pytest.raises(sqlite3.OperationalError):
                NoustStore(db_path, fs=RecordingFileSystem())

        assert _raw_max_version(db_path) == 6

        tables = _raw_tables(db_path)
        assert "jobs" in tables  # v3 -> v4
        assert "releases" in tables  # v4 -> v5
        assert "domains" in tables  # v5 -> v6
        assert "layout" in _raw_columns(db_path, "apps")  # v4 -> v5

        # Nothing from the step after the failing one leaked through. (The
        # jobs table is created directly from today's JOBS_SCHEMA_SQL at
        # v3->v4, which already carries the v7 "actor" column - see
        # _migrate_v6_to_v7's docstring - so "actor" existing here says
        # nothing about whether v6->v7 ran; job_id on deployments, added
        # only by v7->v8, is the step that could not possibly have run.)
        assert "job_id" not in _raw_columns(db_path, "deployments")  # v7 -> v8

        NoustStore.reset_instance()
        store = NoustStore(db_path, fs=RecordingFileSystem())

        with store._transaction() as cursor:
            cursor.execute("SELECT MAX(version) FROM schema_version")
            assert cursor.fetchone()[0] == SCHEMA_VERSION
        assert store.get_app("v3.example.com") is not None


class TestAFailingFreshInstallIsAtomic:
    """
    The fresh-install path runs ``SCHEMA_SQL`` and inserts the version row in
    one call, the same shape as a migration step, and must be exactly as
    atomic: a database that never got that far must not end up with some
    tables and no ``schema_version`` to say what happened.
    """

    def test_a_failing_fresh_install_leaves_no_tables_and_a_retry_completes_it(
        self, fresh, tmp_path
    ):
        db_path = tmp_path / "wasm.db"

        def _crash_after_one_table(self: NoustStore) -> None:
            with self._ddl_transaction() as cursor:
                cursor.execute(
                    "CREATE TABLE schema_version ("
                    "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                raise sqlite3.OperationalError("simulated crash mid-install")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(NoustStore, "_create_fresh_schema", _crash_after_one_table)
            with pytest.raises(sqlite3.OperationalError):
                NoustStore(db_path, fs=RecordingFileSystem())

        assert _raw_tables(db_path) == set()

        NoustStore.reset_instance()
        store = NoustStore(db_path, fs=RecordingFileSystem())

        with store._transaction() as cursor:
            cursor.execute("SELECT MAX(version) FROM schema_version")
            assert cursor.fetchone()[0] == SCHEMA_VERSION
        assert "apps" in _raw_tables(db_path)


def _create_v8_database(db_path: Path) -> None:
    """
    Create a real v8 database as 2.0.x leaves it, with data in every table v9 touches.

    One application with a non-default retention and limits, and one
    deployment with the v8 links filled in: the rows the v8-to-v9 migration
    must carry over unchanged while it adds its columns.

    Args:
        db_path: Where the database file is created.
    """
    _create_v7_database(db_path)
    conn = sqlite3.connect(db_path)
    try:
        for column in ("job_id", "release_id", "commit_message"):
            conn.execute(f"ALTER TABLE deployments ADD COLUMN {column} TEXT")
        conn.execute("INSERT INTO schema_version (version) VALUES (8)")
        conn.execute(
            "INSERT INTO apps (domain, app_type, app_path, layout, keep_releases, memory_max_mb)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("v8.example.com", "nodejs", "/var/www/apps/v8-example-com", "releases", 7, 512),
        )
        conn.execute(
            "UPDATE deployments SET job_id = 'ab12cd34', release_id = '20260101-000000-aaaaaaa'"
        )
        conn.commit()
    finally:
        conn.close()


class TestSchemaV9Migration:
    """
    Schema v9: the health gate's settings on every application, and the
    backup that holds exactly what a deployment produced.
    """

    def test_a_v8_database_keeps_its_rows_and_gains_empty_settings(self, fresh, tmp_path):
        """Every new column is NULL - "what WASM did before" - and nothing else moves."""
        db_path = tmp_path / "wasm.db"
        _create_v8_database(db_path)

        store = NoustStore(db_path, fs=RecordingFileSystem())

        assert _raw_max_version(db_path) == SCHEMA_VERSION
        app = store.get_app("v8.example.com")
        assert app is not None
        assert (app.layout, app.keep_releases, app.memory_max_mb) == ("releases", 7, 512)
        assert (app.health_path, app.health_expect, app.health_timeout) == (None, None, None)
        record = store.list_deployments("old.example.com")[0]
        assert (record.job_id, record.release_id) == ("ab12cd34", "20260101-000000-aaaaaaa")
        assert record.snapshot_backup is None

    def test_the_migration_is_idempotent(self, fresh, tmp_path):
        """Run twice on the same cursor, the second run finds its columns and adds nothing."""
        db_path = tmp_path / "wasm.db"
        _create_v8_database(db_path)
        store = NoustStore(db_path, fs=RecordingFileSystem())
        before = (_raw_columns(db_path, "apps"), _raw_columns(db_path, "deployments"))

        with store._ddl_transaction() as cursor:
            store._migrate_v8_to_v9(cursor)

        assert (_raw_columns(db_path, "apps"), _raw_columns(db_path, "deployments")) == before
        NoustStore.reset_instance()
        reopened = NoustStore(db_path, fs=RecordingFileSystem())
        assert reopened.get_app("v8.example.com") is not None

    def test_a_failing_v8_to_v9_step_leaves_v8_intact(self, fresh, tmp_path):
        """A crash after the first column rolls that column back with the version."""
        db_path = tmp_path / "wasm.db"
        _create_v8_database(db_path)

        def _crash(self: NoustStore, cursor: sqlite3.Cursor) -> None:
            cursor.execute("ALTER TABLE apps ADD COLUMN health_path TEXT")
            raise sqlite3.OperationalError("simulated crash mid-migration")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(NoustStore, "_migrate_v8_to_v9", _crash)
            with pytest.raises(sqlite3.OperationalError):
                NoustStore(db_path, fs=RecordingFileSystem())

        assert _raw_max_version(db_path) == 8
        assert "health_path" not in _raw_columns(db_path, "apps")

    def test_the_fresh_schema_and_the_migration_agree(self, fresh, tmp_path):
        """Both paths to v9 give apps and deployments the same columns."""
        db_path = tmp_path / "migrated.db"
        _create_v8_database(db_path)
        NoustStore(db_path, fs=RecordingFileSystem())
        NoustStore.reset_instance()
        NoustStore(tmp_path / "fresh.db", fs=RecordingFileSystem())

        for table in ("apps", "deployments"):
            assert _raw_columns(db_path, table) == _raw_columns(tmp_path / "fresh.db", table)
        assert {"health_path", "health_expect", "health_timeout"} <= _raw_columns(db_path, "apps")
        assert "snapshot_backup" in _raw_columns(db_path, "deployments")

    def test_a_migrated_database_records_the_new_facts(self, fresh, tmp_path):
        """The columns are usable at once: settings round-trip, a snapshot is linked."""
        db_path = tmp_path / "wasm.db"
        _create_v8_database(db_path)
        store = NoustStore(db_path, fs=RecordingFileSystem())

        assert store.set_app_health("v8.example.com", path="/healthz", expect="200-399", timeout=90)
        store.set_deployment_snapshot(1, "v8-example-com_20260102_030405")

        app = store.get_app("v8.example.com")
        assert (app.health_path, app.health_expect, app.health_timeout) == (
            "/healthz",
            "200-399",
            90,
        )
        assert store.get_deployment(1).snapshot_backup == "v8-example-com_20260102_030405"


class TestSchemaV10Migration:
    """
    Schema v10: blue/green, secret marks, the GitHub link and the preview link
    on every application, and the tables previews, backup destinations and
    schedules, and the GitHub App keep.
    """

    V10_TABLES = {
        "preview_settings",
        "previews",
        "backup_destinations",
        "backup_schedules",
        "github_app",
        "github_installations",
    }

    def test_a_v8_database_climbs_to_v10_with_everything_off(self, fresh, tmp_path):
        """Every new column is off or NULL - exactly what 2.1 did - and the rows survive."""
        db_path = tmp_path / "wasm.db"
        _create_v8_database(db_path)

        store = NoustStore(db_path, fs=RecordingFileSystem())

        assert _raw_max_version(db_path) == SCHEMA_VERSION
        app = store.get_app("v8.example.com")
        assert app is not None
        assert (app.zero_downtime, app.active_color, app.drain_seconds) == (False, None, None)
        assert app.env_secret_marks == {}
        assert (app.github_installation_id, app.preview_parent) == (None, None)
        with sqlite3.connect(db_path) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        assert self.V10_TABLES <= tables
        assert {"allow_bots", "exclude_env"} <= set(_raw_columns(db_path, "preview_settings"))

    def test_the_fresh_schema_and_the_migration_agree(self, fresh, tmp_path):
        """Both paths to v10 give every table the same columns."""
        db_path = tmp_path / "migrated.db"
        _create_v8_database(db_path)
        NoustStore(db_path, fs=RecordingFileSystem())
        NoustStore.reset_instance()
        NoustStore(tmp_path / "fresh.db", fs=RecordingFileSystem())

        for table in ("apps", *sorted(self.V10_TABLES)):
            assert _raw_columns(db_path, table) == _raw_columns(tmp_path / "fresh.db", table)

    def test_the_migration_is_idempotent(self, fresh, tmp_path):
        """Run again on a migrated database, it adds nothing and breaks nothing."""
        db_path = tmp_path / "wasm.db"
        _create_v8_database(db_path)
        store = NoustStore(db_path, fs=RecordingFileSystem())
        before = _raw_columns(db_path, "apps")

        with store._ddl_transaction() as cursor:
            store._migrate_v9_to_v10(cursor)

        assert _raw_columns(db_path, "apps") == before

    def test_a_failing_v9_to_v10_step_leaves_v9_intact(self, fresh, tmp_path):
        """A crash partway rolls the step back with its version row."""
        db_path = tmp_path / "wasm.db"
        _create_v8_database(db_path)

        def _crash(self: NoustStore, cursor: sqlite3.Cursor) -> None:
            cursor.execute("ALTER TABLE apps ADD COLUMN zero_downtime INTEGER NOT NULL DEFAULT 0")
            raise sqlite3.OperationalError("simulated crash mid-migration")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(NoustStore, "_migrate_v9_to_v10", _crash)
            with pytest.raises(sqlite3.OperationalError):
                NoustStore(db_path, fs=RecordingFileSystem())

        assert _raw_max_version(db_path) == 9
        assert "zero_downtime" not in _raw_columns(db_path, "apps")


def _create_v10_database(db_path: Path) -> None:
    """
    Create a real v10 database as 2.2/2.3 leave it, with one application.

    The v8 fixture climbed to v10 by the store itself, with the version it
    may reach capped at 10: exactly the file a 2.3 server hands to 3.0.

    Args:
        db_path: Where the database file is created.
    """
    _create_v8_database(db_path)
    NoustStore.reset_instance()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(store_module, "SCHEMA_VERSION", 10)
        NoustStore(db_path, fs=RecordingFileSystem())
    NoustStore.reset_instance()
    assert _raw_max_version(db_path) == 10
    assert "nodes" not in _raw_tables(db_path)


class TestSchemaV11Migration:
    """Schema v11: the fleet's node registry."""

    NODE_COLUMNS = {
        "name",
        "ssh_host",
        "ssh_port",
        "ssh_user",
        "host_key",
        "console_port",
        "version",
        "status",
        "last_seen",
        "allow_shell",
        "created_at",
        "updated_at",
    }

    def test_a_v10_database_gains_an_empty_node_table_and_keeps_its_rows(self, fresh, tmp_path):
        db_path = tmp_path / "noust.db"
        _create_v10_database(db_path)

        store = NoustStore(db_path, fs=RecordingFileSystem())

        assert _raw_max_version(db_path) == SCHEMA_VERSION
        assert store.get_app("v8.example.com") is not None
        assert store.list_nodes() == []
        assert _raw_columns(db_path, "nodes") == self.NODE_COLUMNS

    def test_the_fresh_schema_and_the_migration_agree(self, fresh, tmp_path):
        db_path = tmp_path / "migrated.db"
        _create_v10_database(db_path)
        NoustStore(db_path, fs=RecordingFileSystem())
        NoustStore.reset_instance()
        NoustStore(tmp_path / "fresh.db", fs=RecordingFileSystem())

        assert _raw_columns(db_path, "nodes") == _raw_columns(tmp_path / "fresh.db", "nodes")

    def test_the_migration_is_idempotent(self, fresh, tmp_path):
        db_path = tmp_path / "noust.db"
        _create_v10_database(db_path)
        store = NoustStore(db_path, fs=RecordingFileSystem())
        store.save_node(_node("web-2"))

        with store._ddl_transaction() as cursor:
            store._migrate_v10_to_v11(cursor)

        assert [node.name for node in store.list_nodes()] == ["web-2"]

    def test_a_failing_v10_to_v11_step_leaves_v10_intact(self, fresh, tmp_path):
        db_path = tmp_path / "noust.db"
        _create_v10_database(db_path)

        def _crash(self: NoustStore, cursor: sqlite3.Cursor) -> None:
            cursor.execute("CREATE TABLE nodes (name TEXT PRIMARY KEY)")
            raise sqlite3.OperationalError("simulated crash mid-migration")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(NoustStore, "_migrate_v10_to_v11", _crash)
            with pytest.raises(sqlite3.OperationalError):
                NoustStore(db_path, fs=RecordingFileSystem())

        assert _raw_max_version(db_path) == 10
        assert "nodes" not in _raw_tables(db_path)



class TestSchemaV12Migration:
    """Schema v12: everything Noust 3.1 stores, one fragment per area."""

    def test_a_v10_database_climbs_to_the_current_version_and_keeps_its_rows(self, fresh, tmp_path):
        db_path = tmp_path / "noust.db"
        _create_v10_database(db_path)

        store = NoustStore(db_path, fs=RecordingFileSystem())

        assert _raw_max_version(db_path) == SCHEMA_VERSION == 12
        assert store.get_app("v8.example.com") is not None

    def test_the_fresh_schema_and_the_migration_agree_on_every_table(self, fresh, tmp_path):
        migrated = tmp_path / "migrated.db"
        _create_v10_database(migrated)
        NoustStore(migrated, fs=RecordingFileSystem())
        NoustStore.reset_instance()
        NoustStore(tmp_path / "fresh.db", fs=RecordingFileSystem())
        fresh_db = tmp_path / "fresh.db"

        assert _raw_tables(migrated) == _raw_tables(fresh_db)
        for table in _raw_tables(fresh_db):
            assert _raw_columns(migrated, table) == _raw_columns(fresh_db, table), table

    def test_the_v12_step_is_idempotent(self, fresh, tmp_path):
        db_path = tmp_path / "noust.db"
        store = NoustStore(db_path, fs=RecordingFileSystem())
        before = {table: _raw_columns(db_path, table) for table in _raw_tables(db_path)}

        with store._ddl_transaction() as cursor:
            store._migrate_v11_to_v12(cursor)

        assert {table: _raw_columns(db_path, table) for table in _raw_tables(db_path)} == before

def _node(name: str, **overrides) -> "store_module.NodeRecord":
    """
    Build a node record for the store tests.

    Args:
        name: The node's name.
        **overrides: Fields to change.

    Returns:
        The record.
    """
    values = {
        "name": name,
        "ssh_host": "web2.example.com",
        "ssh_port": 22,
        "ssh_user": "root",
        "host_key": "noust-node-" + name + " ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB",
        "console_port": 8080,
    }
    values.update(overrides)
    return store_module.NodeRecord(**values)


class TestNodeRows:
    """The v11 node methods: save, read, list, status, delete."""

    def test_save_then_read_round_trips_every_field(self, fresh, tmp_path):
        store = NoustStore(tmp_path / "noust.db", fs=RecordingFileSystem())

        saved = store.save_node(_node("web-2", ssh_port=2222, version="3.0.0"))

        assert saved.created_at and saved.updated_at
        assert store.get_node("web-2") == saved
        assert (saved.ssh_port, saved.version, saved.status, saved.allow_shell) == (
            2222,
            "3.0.0",
            "unknown",
            False,
        )

    def test_saving_again_updates_and_keeps_created_at(self, fresh, tmp_path):
        store = NoustStore(tmp_path / "noust.db", fs=RecordingFileSystem())
        first = store.save_node(_node("web-2"))

        second = store.save_node(_node("web-2", ssh_host="10.0.0.2", created_at=first.created_at))

        assert second.ssh_host == "10.0.0.2"
        assert second.created_at == first.created_at
        assert len(store.list_nodes()) == 1

    def test_list_is_by_name(self, fresh, tmp_path):
        store = NoustStore(tmp_path / "noust.db", fs=RecordingFileSystem())
        for name in ("web-3", "db-1", "web-2"):
            store.save_node(_node(name))

        assert [node.name for node in store.list_nodes()] == ["db-1", "web-2", "web-3"]

    def test_reachable_stamps_last_seen_and_version(self, fresh, tmp_path):
        store = NoustStore(tmp_path / "noust.db", fs=RecordingFileSystem())
        store.save_node(_node("web-2", version="3.0.0"))

        assert store.set_node_status("web-2", "reachable", version="3.0.1")
        seen = store.get_node("web-2")
        assert (seen.status, seen.version) == ("reachable", "3.0.1")
        assert seen.last_seen is not None

        store.set_node_status("web-2", "unreachable")
        down = store.get_node("web-2")
        assert (down.status, down.version, down.last_seen) == (
            "unreachable",
            "3.0.1",
            seen.last_seen,
        )

    def test_an_unknown_status_is_refused(self, fresh, tmp_path):
        store = NoustStore(tmp_path / "noust.db", fs=RecordingFileSystem())
        store.save_node(_node("web-2"))

        with pytest.raises(store_module.ValidationError):
            store.set_node_status("web-2", "sleepy")
        with pytest.raises(store_module.ValidationError):
            store.save_node(_node("web-3", status="sleepy"))

    def test_status_of_an_unknown_node_reports_false(self, fresh, tmp_path):
        store = NoustStore(tmp_path / "noust.db", fs=RecordingFileSystem())

        assert store.set_node_status("ghost", "reachable") is False

    def test_delete(self, fresh, tmp_path):
        store = NoustStore(tmp_path / "noust.db", fs=RecordingFileSystem())
        store.save_node(_node("web-2"))

        assert store.delete_node("web-2") is True
        assert store.delete_node("web-2") is False
        assert store.get_node("web-2") is None
