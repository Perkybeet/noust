# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Schema v18: the scaffold 4.3 fills one fragment per area.

The rules are those of v17 (``tests/test_schema_v17.py``): a store climbs from v17 to v18 in the
opening that finds it, a fresh store is born at v18, and a store a development build of 4.3
stamped v18 before an area wrote its fragment gets that fragment the next time it opens. What is
new here is that the scaffold lands first and every lane of the release fills its own pair later,
so these tests hold what must stay true while the pairs fill: each area keeps both of its names,
and each pair is wired into the climb, the fresh store and the completion, whatever it holds.
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

import noust.core.store as store_module
from noust.core import schema_v18, store_compat
from noust.core.store import SCHEMA_VERSION, App, NoustStore, StoreError

#: The areas of the release, in the order of the module, and the lane that owns each.
AREAS = [
    "DEPLOYS",
    "DOMAINS",
    "TASKS",
    "FLEET",
    "RELEASES",
    "SLEEP",
    "BACKUPS",
    "MAINTENANCE",
    "REPORTS",
    "AGENTS",
    "ASSISTANT",
    "DOCKER",
]


@pytest.fixture
def fresh() -> Iterator[None]:
    NoustStore.reset_instance()
    yield None
    NoustStore.reset_instance()


def version_of(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0])
    finally:
        conn.close()


def tables_of(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    finally:
        conn.close()
    return {str(row[0]) for row in rows}


def columns_of(path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        conn.close()


def stamp(path: Path, version: int) -> None:
    """Make the store at ``path`` say it is at ``version``, as one an older Noust left."""
    NoustStore.reset_instance()
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
    conn.commit()
    conn.close()


def drop(path: Path, *tables: str) -> None:
    conn = sqlite3.connect(path)
    for table in tables:
        conn.execute(f"DROP TABLE {table}")
    conn.commit()
    conn.close()


def probe_table(area: str) -> str:
    return f"v18_probe_{area.lower()}"


def probe_sql(area: str) -> str:
    return f"CREATE TABLE IF NOT EXISTS {probe_table(area)} (id INTEGER PRIMARY KEY);"


def pair_names(module: ModuleType) -> tuple[list[str], list[str]]:
    """The areas that have a ``_SQL`` name and the ones that have a ``_COLUMNS`` name."""
    names = list(vars(module))
    sql = [name.removesuffix("_SQL") for name in names if name.endswith("_SQL")]
    columns = [name.removesuffix("_COLUMNS") for name in names if name.endswith("_COLUMNS")]
    return sql, columns


# -- the scaffold


def test_the_version_follows_v17() -> None:
    assert schema_v18.VERSION == 18
    assert SCHEMA_VERSION == schema_v18.VERSION


def test_each_area_of_4_3_has_both_names_of_its_pair() -> None:
    for area in AREAS:
        fragment = getattr(schema_v18, f"{area}_SQL", None)
        columns = getattr(schema_v18, f"{area}_COLUMNS", None)
        assert isinstance(fragment, str), f"{area}_SQL is missing or is not a string"
        assert isinstance(columns, list), f"{area}_COLUMNS is missing or is not a list"


def test_no_name_of_a_pair_is_left_without_the_other() -> None:
    sql, columns = pair_names(schema_v18)

    # A lane that adds a name of its own and forgets the other half would be silently skipped by
    # the one that reads them, so the pair is held here, not left to review.
    assert sorted(sql) == sorted(columns)


def test_the_areas_are_the_ones_of_the_plan_in_its_order() -> None:
    sql, _columns = pair_names(schema_v18)

    assert sql == AREAS


def test_an_empty_fragment_runs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[str] = []
    for area in AREAS:
        monkeypatch.setattr(schema_v18, f"{area}_SQL", "")
        monkeypatch.setattr(schema_v18, f"{area}_COLUMNS", [])
    monkeypatch.setattr(schema_v18, "ASSISTANT_SQL", "  \n\t")
    conn = sqlite3.connect(":memory:")

    schema_v18.apply_v18(conn.cursor(), lambda _cursor, script: ran.append(script))

    assert ran == []
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0
    assert not schema_v18.incomplete(conn.cursor())
    conn.close()


# -- each pair is wired in


@pytest.mark.parametrize("area", AREAS)
def test_a_fragment_written_in_an_area_is_created_by_the_climb_from_v17(
    area: str, tmp_path: Path, fresh: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "noust.db"
    NoustStore(path)
    stamp(path, 17)
    monkeypatch.setattr(schema_v18, f"{area}_SQL", probe_sql(area))

    NoustStore(path)

    assert probe_table(area) in tables_of(path)
    assert version_of(path) == 18


@pytest.mark.parametrize("area", AREAS)
def test_a_fragment_written_in_an_area_is_in_a_fresh_store(
    area: str, tmp_path: Path, fresh: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "noust.db"
    monkeypatch.setattr(schema_v18, f"{area}_SQL", probe_sql(area))

    NoustStore(path)

    assert probe_table(area) in tables_of(path)
    assert version_of(path) == 18


@pytest.mark.parametrize("area", AREAS)
def test_a_fragment_written_after_a_store_was_stamped_v18_is_added_when_it_opens(
    area: str, tmp_path: Path, fresh: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "noust.db"
    NoustStore(path)
    NoustStore.reset_instance()
    assert probe_table(area) not in tables_of(path)

    monkeypatch.setattr(schema_v18, f"{area}_SQL", probe_sql(area))
    NoustStore(path)

    assert probe_table(area) in tables_of(path)
    assert version_of(path) == 18


@pytest.mark.parametrize("area", AREAS)
def test_a_column_written_in_an_area_is_added_by_the_climb_and_by_the_completion(
    area: str, tmp_path: Path, fresh: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    column = f"v18_probe_{area.lower()}"
    climbing = tmp_path / "climbing.db"
    NoustStore(climbing)
    stamp(climbing, 17)
    stamped = tmp_path / "stamped.db"
    NoustStore(stamped)
    NoustStore.reset_instance()
    monkeypatch.setattr(schema_v18, f"{area}_COLUMNS", [("apps", column, "TEXT")])

    NoustStore(climbing)
    NoustStore.reset_instance()
    NoustStore(stamped)
    NoustStore.reset_instance()

    assert column in columns_of(climbing, "apps")
    assert column in columns_of(stamped, "apps")
    assert version_of(climbing) == version_of(stamped) == 18


def test_a_complete_store_is_left_alone(tmp_path: Path, fresh: None) -> None:
    path = tmp_path / "noust.db"
    NoustStore(path)
    NoustStore.reset_instance()

    conn = sqlite3.connect(path)
    try:
        assert not schema_v18.incomplete(conn.cursor())
    finally:
        conn.close()


# -- the climb


def test_a_fresh_store_is_born_at_v18(tmp_path: Path, fresh: None) -> None:
    path = tmp_path / "noust.db"

    NoustStore(path)

    assert version_of(path) == 18
    # What 4.2 added is in a store born now: the climb is not the only way to v18.
    assert {"account_dashboards", "installed_software"} <= tables_of(path)


def test_a_store_at_v17_climbs_to_v18_and_keeps_its_rows(tmp_path: Path, fresh: None) -> None:
    path = tmp_path / "noust.db"
    store = NoustStore(path)
    store.create_app(App(domain="shop.example.com", app_type="nextjs", app_path="/opt/shop"))
    stamp(path, 17)

    reopened = NoustStore(path)

    assert version_of(path) == 18
    shop = reopened.get_app("shop.example.com")
    assert shop is not None and shop.app_path == "/opt/shop"


def test_a_store_a_development_build_stamped_v17_early_is_completed_before_it_climbs(
    tmp_path: Path, fresh: None
) -> None:
    # The climb to v18 leaves the store "not at v17" for the check that completes v17, so v17 is
    # completed first or the tables it lacked would be missing for good.
    path = tmp_path / "noust.db"
    NoustStore(path)
    stamp(path, 17)
    drop(path, "installed_software", "account_dashboards")

    NoustStore(path)

    assert {"installed_software", "account_dashboards"} <= tables_of(path)
    assert version_of(path) == 18


def test_a_store_that_climbs_from_a_much_older_release_ends_at_v18(
    tmp_path: Path, fresh: None
) -> None:
    path = tmp_path / "noust.db"
    NoustStore(path)
    stamp(path, 16)
    drop(path, "installed_software", "account_dashboards")

    NoustStore(path)

    assert {"installed_software", "account_dashboards"} <= tables_of(path)
    assert version_of(path) == 18


# -- the way back to 4.2


def test_the_climb_keeps_a_v17_copy_that_4_2_opens(
    tmp_path: Path, fresh: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "noust.db"
    store = NoustStore(path)
    store.create_app(App(domain="shop.example.com", app_type="nextjs", app_path="/opt/shop"))
    stamp(path, 17)
    NoustStore(path)
    NoustStore.reset_instance()

    copies = store_compat.kept_copies(path)

    assert copies and copies[0].schema == 17
    restored = tmp_path / "restored.db"
    shutil.copyfile(copies[0].path, restored)
    # 4.2 writes schema 17: what it understands is what SCHEMA_VERSION said there.
    monkeypatch.setattr(store_module, "SCHEMA_VERSION", 17)
    old_release = NoustStore(restored)
    assert old_release.get_app("shop.example.com") is not None
    assert version_of(restored) == 17


def test_4_2_refuses_a_store_at_v18_in_its_own_words_and_writes_nothing(
    tmp_path: Path, fresh: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "noust.db"
    NoustStore(path)
    NoustStore.reset_instance()
    monkeypatch.setattr(store_module, "SCHEMA_VERSION", 17)

    with pytest.raises(StoreError) as caught:
        NoustStore(path)

    text = str(caught.value)
    assert "written by a newer Noust" in text
    assert "schema v18" in text and "understands up to v17" in text
    assert ".bak" in text
    assert version_of(path) == 18
    assert not list(tmp_path.glob("*.bak")), "the refusal must not keep a copy: it changed nothing"


# -- what `noust store compat` knows


def test_the_compat_table_says_4_3_0_is_the_first_release_that_writes_18() -> None:
    assert store_compat.SCHEMA_RELEASES[18] == "4.3.0"
    assert store_compat.oldest_release_for(18) == "4.3.0"
    assert store_compat.schema_of_release("4.2.1") == 17
    assert store_compat.schema_of_release("4.3.0") == 18
