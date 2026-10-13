# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Going back to an earlier Noust, as a plan (checklist N-27).

The store's schema only moves forward and a release refuses a store written by a newer one. These
tests hold the table that says which release understands which schema, the plan for reaching an
earlier release (install it, or restore the copy the upgrade kept), and that the way back works
for real: a store the current code migrated and kept a copy of can be put back and opened again.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core import store_compat
from noust.core.store import SCHEMA_VERSION, NoustStore, StoreError
from noust.core.store_compat import SCHEMA_RELEASES, KeptCopy, plan


def copy(tmp_path: Path, schema: int, stamp: str = "20261003T101500Z") -> KeptCopy:
    path = tmp_path / f"noust.db.v{schema}-{stamp}-4242-ab12cd.bak"
    path.write_bytes(b"x" * 10)
    return KeptCopy(
        path, schema, datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc), 10
    )


# -- the table


def test_every_schema_this_release_can_write_names_the_release_that_first_wrote_it() -> None:
    """A release cannot ship without saying which schema it brings: the way back depends on it."""
    assert SCHEMA_VERSION in SCHEMA_RELEASES
    assert set(range(min(SCHEMA_RELEASES), SCHEMA_VERSION + 1)) <= set(SCHEMA_RELEASES)


def test_the_table_only_moves_forward() -> None:
    releases = [store_compat.release_key(SCHEMA_RELEASES[s]) for s in sorted(SCHEMA_RELEASES)]

    assert releases == sorted(releases)
    assert len(set(releases)) == len(releases)


@pytest.mark.parametrize(
    ("release", "schema"),
    [
        ("2.0.0", 8),
        ("2.9.0", 10),
        ("3.0.0", 11),
        ("3.1.17", 12),
        ("3.2.0", 13),
        ("3.3.1", 13),
        ("v3.4.0", 14),
    ],
)
def test_a_release_understands_up_to_the_schema_it_or_an_earlier_one_wrote(
    release: str, schema: int
) -> None:
    assert store_compat.schema_of_release(release) == schema


def test_a_release_older_than_the_table_understands_nothing_it_lists() -> None:
    assert store_compat.schema_of_release("1.9.0") is None


@pytest.mark.parametrize(
    ("schema", "release"),
    [(8, "2.0.0"), (11, "3.0.0"), (13, "3.2.0"), (14, "3.4.0")],
)
def test_the_oldest_release_that_opens_a_store_is_the_one_that_wrote_its_schema(
    schema: int, release: str
) -> None:
    assert store_compat.oldest_release_for(schema) == release


def test_a_schema_newer_than_any_release_the_table_knows_has_no_release() -> None:
    assert store_compat.oldest_release_for(SCHEMA_VERSION + 50) is None


@pytest.mark.parametrize("text", ["", "latest", "3", "three.four"])
def test_a_release_that_is_not_one_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match="is not a release"):
        store_compat.release_key(text)


# -- the copies the upgrades keep


def test_kept_copies_are_found_by_name_newest_first(tmp_path: Path) -> None:
    db = tmp_path / "noust.db"
    older = copy(tmp_path, 12, "20260930T101500Z")
    newer = copy(tmp_path, 13, "20261003T101500Z")
    (tmp_path / "noust.db.v13-notastamp.bak").write_bytes(b"?")
    (tmp_path / "other.db.v13-20261003T101500Z-1-a.bak").write_bytes(b"?")

    found = store_compat.kept_copies(db)

    assert [c.path for c in found] == [newer.path, older.path]
    assert [c.schema for c in found] == [13, 12]


def test_a_link_is_not_a_copy(tmp_path: Path) -> None:
    real = copy(tmp_path, 13)
    (tmp_path / "noust.db.v13-20261004T101500Z-1-a.bak").symlink_to(real.path)

    assert [c.path for c in store_compat.kept_copies(tmp_path / "noust.db")] == [real.path]


def test_no_directory_means_no_copies(tmp_path: Path) -> None:
    assert store_compat.kept_copies(tmp_path / "nowhere" / "noust.db") == []


# -- the plan


def test_a_release_that_understands_the_store_is_just_installed(tmp_path: Path) -> None:
    result = plan("3.3.1", 13, [], db_path=tmp_path / "noust.db")

    assert result.compatible
    assert result.copy is None
    assert "understands this store (schema 13)" in result.steps[0]
    assert len(result.steps) == 1


def test_a_release_that_cannot_open_the_store_gets_the_newest_copy_it_can_open(
    tmp_path: Path,
) -> None:
    db = tmp_path / "noust.db"
    too_new = copy(tmp_path, 14, "20261010T101500Z")
    right = copy(tmp_path, 13, "20261003T101500Z")
    older = copy(tmp_path, 12, "20260930T101500Z")

    result = plan("3.3.1", 14, [too_new, right, older], db_path=db)

    assert not result.compatible
    assert result.copy == right
    joined = "\n".join(result.steps)
    assert "systemctl stop noust-web noust-monitor" in joined
    assert f"cp {right.path} {db}" in joined
    assert "before-downgrade" in joined
    assert "is not in it" in joined


def test_without_a_usable_copy_the_plan_says_so_and_suggests_the_self_backup(
    tmp_path: Path,
) -> None:
    result = plan("3.3.1", 14, [copy(tmp_path, 14)], db_path=tmp_path / "noust.db")

    assert not result.compatible
    assert result.copy is None
    assert "self-backup list" in result.steps[0]


def test_a_release_older_than_the_table_cannot_be_planned_for(tmp_path: Path) -> None:
    result = plan("1.9.0", 13, [copy(tmp_path, 13)], db_path=tmp_path / "noust.db")

    assert not result.compatible
    assert result.target_schema is None
    assert result.copy is None


def test_the_plan_is_json_ready(tmp_path: Path) -> None:
    data = plan("3.3.1", 14, [copy(tmp_path, 13)], db_path=tmp_path / "noust.db").to_dict()

    assert data["compatible"] is False
    assert data["copy"]["schema"] == 13
    assert json.dumps(data)


# -- the way back, for real


def migrated_store(tmp_path: Path) -> tuple[NoustStore, Path]:
    """A store stamped one schema behind, as an upgrade finds it, opened by the current code."""
    db = tmp_path / "noust.db"
    NoustStore.reset_instance()
    NoustStore(db)
    NoustStore.reset_instance()
    connection = sqlite3.connect(db)
    connection.execute("DELETE FROM schema_version")
    connection.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION - 1,))
    connection.commit()
    connection.close()
    NoustStore.reset_instance()
    return NoustStore(db), db


def test_opening_an_older_store_keeps_a_copy_that_a_later_open_can_use(tmp_path: Path) -> None:
    store, db = migrated_store(tmp_path)

    copies = store_compat.kept_copies(db)
    assert copies and copies[0].schema == SCHEMA_VERSION - 1
    assert store_compat.read_schema(db) == SCHEMA_VERSION
    plan_back = plan("3.3.1", SCHEMA_VERSION, copies, db_path=db)
    assert not plan_back.compatible
    assert plan_back.copy == copies[0]
    NoustStore.reset_instance()

    # Put the copy where the plan says: the store opens again, on its own schema, and migrates.
    restored = db.with_name("restored.db")
    restored.write_bytes(plan_back.copy.path.read_bytes())
    assert store_compat.read_schema(restored) == SCHEMA_VERSION - 1
    NoustStore.reset_instance()
    NoustStore(restored)
    assert store_compat.read_schema(restored) == SCHEMA_VERSION
    NoustStore.reset_instance()


def test_a_store_written_by_a_newer_release_is_refused_with_the_way_back(tmp_path: Path) -> None:
    db = tmp_path / "noust.db"
    NoustStore.reset_instance()
    NoustStore(db)
    NoustStore.reset_instance()
    connection = sqlite3.connect(db)
    connection.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION + 1,))
    connection.commit()
    connection.close()

    with pytest.raises(StoreError) as caught:
        NoustStore(db)
    NoustStore.reset_instance()

    assert "written by a newer Noust" in str(caught.value)
    assert ".bak" in str(caught.value)


def test_the_schema_of_a_missing_or_foreign_file(tmp_path: Path) -> None:
    assert store_compat.read_schema(tmp_path / "none.db") is None

    foreign = tmp_path / "foreign.db"
    sqlite3.connect(foreign).close()
    assert store_compat.read_schema(foreign) is None

    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"not sqlite" * 50)
    with pytest.raises(sqlite3.Error):
        store_compat.read_schema(garbage)


# -- the command


def test_compat_prints_the_plan_for_a_release_as_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "noust.db"
    NoustStore.reset_instance()
    NoustStore(db)

    class FakeStore:
        db_path = db

    monkeypatch.setattr("noust.core.store.get_store", lambda: FakeStore())

    result = CliRunner().invoke(root_cli, ["store", "compat", "--to", "3.3.1", "--json"])
    NoustStore.reset_instance()

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["store_schema"] == SCHEMA_VERSION
    assert data["oldest_release"] == store_compat.oldest_release_for(SCHEMA_VERSION)
    assert data["plan"]["target"] == "3.3.1"
    assert data["plan"]["compatible"] is (SCHEMA_VERSION <= 13)


def test_compat_refuses_a_target_that_is_not_a_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "noust.db"
    NoustStore.reset_instance()
    NoustStore(db)

    class FakeStore:
        db_path = db

    monkeypatch.setattr("noust.core.store.get_store", lambda: FakeStore())

    result = CliRunner().invoke(root_cli, ["store", "compat", "--to", "latest"])
    NoustStore.reset_instance()

    assert result.exit_code == 2
    assert "is not a release" in result.output
