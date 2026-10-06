# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the store keeps of the database instances Noust runs in containers.

A row is the authority on what is Noust's, written before anything is created
and saying how far it got, so what is pinned is: a fresh store and one stamped
v14 before the fragment existed both have the table, every value is checked
before it is written, an interrupted action is readable from the state, and the
digest an upgrade replaces is the one it keeps.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import schema_v14
from noust.core.db_instance_store import (
    CREATING,
    READY,
    REMOVING,
    STATES,
    UPGRADING,
    DbInstanceStore,
    InstanceRecord,
)
from noust.core.exceptions import DatabaseExistsError, ValidationError
from noust.core.store import NoustStore

DIGEST = "sha256:" + "a" * 64
NEWER = "sha256:" + "b" * 64


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def instances(store: NoustStore) -> DbInstanceStore:
    return DbInstanceStore(store)


def make(
    instances: DbInstanceStore, name: str = "pg16", port: int = 5433, **kwargs
) -> InstanceRecord:
    values = {
        "name": name,
        "flavour": "postgresql",
        "version": "16",
        "image": "postgres",
        "digest": DIGEST,
        "host_port": port,
        "memory_limit_mb": 512,
        "created_by": "admin",
    }
    values.update(kwargs)
    return instances.create(**values)


def run_script(cursor: sqlite3.Cursor, script: str) -> None:
    for statement in script.split(";"):
        if statement.strip():
            cursor.execute(statement)


class TestTheFragment:
    def test_a_fresh_store_has_the_table(self, tmp_path: Path, store: NoustStore) -> None:
        connection = sqlite3.connect(tmp_path / "noust.db")
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        finally:
            connection.close()
        assert "db_instances" in tables

    def test_the_fragment_is_idempotent_and_adds_the_table_to_a_store_without_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in list(vars(schema_v14)):
            if name.endswith("_SQL") and name != "DBINSTANCES_SQL":
                monkeypatch.setattr(schema_v14, name, "")
            elif name.endswith("_COLUMNS") and name != "DBINSTANCES_COLUMNS":
                monkeypatch.setattr(schema_v14, name, [])
        connection = sqlite3.connect(":memory:")
        cursor = connection.cursor()
        assert schema_v14.incomplete(cursor) is True

        schema_v14.apply_v14(cursor, run_script)
        schema_v14.apply_v14(cursor, run_script)

        assert schema_v14.incomplete(cursor) is False
        cursor.execute("PRAGMA table_info(db_instances)")
        columns = {row[1] for row in cursor.fetchall()}
        assert {"name", "flavour", "version", "image", "digest", "host_port"} <= columns
        assert "password" not in " ".join(columns), "no secret is a column"

    def test_the_database_refuses_a_state_the_code_does_not_know(self, store: NoustStore) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            with store._transaction() as cursor:
                cursor.execute(
                    "INSERT INTO db_instances (name, flavour, version, image, digest, host_port, "
                    "memory_limit_mb, state, created_at, updated_at) "
                    "VALUES ('x', 'postgresql', '16', 'postgres', ?, 5433, 512, 'bogus', 't', 't')",
                    (DIGEST,),
                )


class TestRecording:
    def test_an_instance_is_recorded_creating_before_anything_exists(
        self, instances: DbInstanceStore
    ) -> None:
        record = make(instances)

        assert record.state == CREATING
        assert not record.ready
        assert record.reference == f"postgres:16@{DIGEST}"
        assert instances.get("pg16") == record
        assert instances.on_port(5433) == record

    def test_the_list_is_by_name(self, instances: DbInstanceStore) -> None:
        make(instances, "zeta", 5434)
        make(instances, "alpha", 5435)

        assert [record.name for record in instances.all()] == ["alpha", "zeta"]

    def test_a_name_or_a_port_is_taken_once(self, instances: DbInstanceStore) -> None:
        make(instances)

        with pytest.raises(DatabaseExistsError):
            make(instances, "pg16", 5440)
        with pytest.raises(DatabaseExistsError):
            make(instances, "pg17", 5433)

    def test_a_missing_instance_is_none(self, instances: DbInstanceStore) -> None:
        assert instances.get("nope") is None
        assert instances.on_port(1234) is None

    def test_a_row_survives_a_new_connection(
        self, tmp_path: Path, instances: DbInstanceStore
    ) -> None:
        make(instances)

        again = DbInstanceStore(NoustStore(tmp_path / "noust.db"))

        assert [record.name for record in again.all()] == ["pg16"]


class TestEveryValueIsCheckedBeforeItIsWritten:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("name", "Pg16"),
            ("name", "-pg"),
            ("name", "pg_16"),
            ("name", "../etc"),
            ("name", "a" * 25),
            ("flavour", "Postgres; rm"),
            ("version", "16; rm -rf"),
            ("version", "latest"),
            ("image", "--privileged"),
            ("image", "postgres:16"),
            ("digest", "sha256:short"),
            ("digest", "latest"),
            ("host_port", 80),
            ("host_port", 70000),
            ("host_port", True),
            ("memory_limit_mb", 0),
            ("memory_limit_mb", -5),
        ],
    )
    def test_a_bad_value_writes_nothing(
        self, instances: DbInstanceStore, field: str, value: object
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            make(instances, **{field: value})

        assert caught.value.field is not None
        assert instances.all() == []


class TestHowFarItGot:
    def test_the_state_moves_and_only_to_a_known_one(self, instances: DbInstanceStore) -> None:
        make(instances)

        for state in (READY, UPGRADING, READY, REMOVING):
            assert instances.set_state("pg16", state) is True
            record = instances.get("pg16")
            assert record is not None
            assert record.state == state

        with pytest.raises(ValidationError):
            instances.set_state("pg16", "half-way")
        assert set(STATES) == {CREATING, READY, UPGRADING, REMOVING}

    def test_a_state_of_a_missing_instance_is_not_written(self, instances: DbInstanceStore) -> None:
        assert instances.set_state("nope", READY) is False

    def test_the_memory_limit_is_a_validated_setter(self, instances: DbInstanceStore) -> None:
        make(instances)

        assert instances.set_memory_limit("pg16", 1024) is True
        assert instances.get("pg16").memory_limit_mb == 1024  # type: ignore[union-attr]
        with pytest.raises(ValidationError):
            instances.set_memory_limit("pg16", 0)

    def test_an_upgrade_keeps_what_ran_before_it(self, instances: DbInstanceStore) -> None:
        make(instances)
        assert instances.get("pg16").previous_digest is None  # type: ignore[union-attr]

        assert instances.set_digest("pg16", NEWER) is True

        record = instances.get("pg16")
        assert record is not None
        assert (record.digest, record.previous_digest) == (NEWER, DIGEST)
        assert record.upgraded_at is not None

    def test_a_digest_that_is_not_one_is_refused(self, instances: DbInstanceStore) -> None:
        make(instances)

        with pytest.raises(ValidationError):
            instances.set_digest("pg16", "latest")
        assert instances.get("pg16").digest == DIGEST  # type: ignore[union-attr]

    def test_forgetting_frees_the_name_and_the_port(self, instances: DbInstanceStore) -> None:
        make(instances)

        instances.delete("pg16")

        assert instances.all() == []
        make(instances)

    def test_no_secret_is_in_what_a_row_describes(self, instances: DbInstanceStore) -> None:
        record = make(instances)

        assert not any("pass" in key for key in record.to_dict())
