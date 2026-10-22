# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Which recipe an application came from, and at which version: the v14 table."""

from __future__ import annotations

import dataclasses
import sqlite3
from typing import Any

import pytest

from noust.core import schema_v14
from noust.core.exceptions import ValidationError
from noust.core.store import App, NoustStore
from noust.recipes import get_recipe
from noust.recipes.installed import (
    UNVERSIONED,
    get_installed,
    list_installed,
    record_install,
    record_update,
)

DOMAIN = "site.example.com"


def deploy(store: NoustStore, domain: str = DOMAIN) -> App:
    """An application row, as a deployment leaves it."""
    return store.create_app(App(domain=domain, app_type="docker-compose"))


def test_the_table_is_part_of_schema_v14_and_a_fresh_store_has_it(recipe_store: NoustStore) -> None:
    """The fragment the recipes lane owns, applied by the store itself."""
    assert "app_recipes" in schema_v14.RECIPES_SQL
    assert schema_v14.RECIPES_COLUMNS == []
    with recipe_store._transaction() as cursor:
        cursor.execute("PRAGMA table_info(app_recipes)")
        columns = {row[1] for row in cursor.fetchall()}
    assert columns == {
        "app_id",
        "recipe",
        "kind",
        "version",
        "revision",
        "installed_at",
        "updated_at",
    }


def test_an_install_is_recorded_and_read_back(recipe_store: NoustStore) -> None:
    """Recipe, kind, version, revision and when."""
    deploy(recipe_store)
    recipe = get_recipe("vaultwarden")

    assert record_install(DOMAIN, recipe, store=recipe_store) is True

    installed = get_installed(DOMAIN, store=recipe_store)
    assert installed is not None
    assert (installed.domain, installed.recipe, installed.kind) == (
        DOMAIN,
        "vaultwarden",
        "compose",
    )
    assert (installed.version, installed.revision) == (recipe.version, recipe.revision)
    assert installed.installed_at == installed.updated_at


def test_there_is_nothing_to_record_for_an_application_that_does_not_exist(
    recipe_store: NoustStore,
) -> None:
    """False, and no row."""
    assert record_install("ghost.example.com", get_recipe("memos"), store=recipe_store) is False
    assert list_installed(store=recipe_store) == []
    assert record_update("ghost.example.com", get_recipe("memos"), store=recipe_store) is False


def test_an_update_changes_the_version_and_keeps_when_it_was_installed(
    recipe_store: NoustStore,
) -> None:
    """The same row, a newer version."""
    deploy(recipe_store)
    recipe = get_recipe("vaultwarden")
    record_install(DOMAIN, recipe, store=recipe_store)
    before = get_installed(DOMAIN, store=recipe_store)
    assert before is not None

    newer = dataclasses.replace(recipe, version="9.9.9", revision=2)
    assert record_update(DOMAIN, newer, store=recipe_store) is True

    after = get_installed(DOMAIN, store=recipe_store)
    assert after is not None
    assert (after.version, after.revision) == ("9.9.9", 2)
    assert after.installed_at == before.installed_at
    assert after.updated_at >= before.updated_at


def test_an_application_never_installed_from_a_recipe_has_no_record(
    recipe_store: NoustStore,
) -> None:
    """record_update does not invent one."""
    deploy(recipe_store)

    assert record_update(DOMAIN, get_recipe("memos"), store=recipe_store) is False
    assert get_installed(DOMAIN, store=recipe_store) is None


def test_installing_again_replaces_the_record(recipe_store: NoustStore) -> None:
    """A redeploy of a failed first attempt."""
    deploy(recipe_store)
    record_install(DOMAIN, get_recipe("vaultwarden"), store=recipe_store)

    record_install(DOMAIN, get_recipe("memos"), store=recipe_store)

    assert [installed.recipe for installed in list_installed(store=recipe_store)] == ["memos"]


def test_the_record_goes_with_the_application(recipe_store: NoustStore) -> None:
    """Deleting the application deletes it: it is not a column of apps, it cascades."""
    deploy(recipe_store)
    record_install(DOMAIN, get_recipe("memos"), store=recipe_store)

    recipe_store.delete_app(DOMAIN)

    assert get_installed(DOMAIN, store=recipe_store) is None
    with recipe_store._transaction() as cursor:
        cursor.execute("SELECT COUNT(*) FROM app_recipes")
        assert cursor.fetchone()[0] == 0


def test_a_redeploy_that_writes_the_whole_row_does_not_touch_the_record(
    recipe_store: NoustStore,
) -> None:
    """The row survives update_app's full write of the application."""
    app = deploy(recipe_store)
    record_install(DOMAIN, get_recipe("memos"), store=recipe_store)

    app.status = "running"
    app.port = 8123
    recipe_store.update_app(app)

    installed = get_installed(DOMAIN, store=recipe_store)
    assert installed is not None and installed.recipe == "memos"


def test_the_listing_is_by_domain(recipe_store: NoustStore) -> None:
    """Every application installed from a recipe."""
    for domain, name in (("b.example.com", "memos"), ("a.example.com", "ntfy")):
        deploy(recipe_store, domain)
        record_install(domain, get_recipe(name), store=recipe_store)

    assert [
        (installed.domain, installed.recipe) for installed in list_installed(store=recipe_store)
    ] == [
        ("a.example.com", "ntfy"),
        ("b.example.com", "memos"),
    ]


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"name": "Not A Name"}, "recipe"),
        ({"kind": "docker"}, "kind"),
        ({"version": "x" * 41}, "version"),
        ({"revision": 0}, "revision"),
        ({"revision": True}, "revision"),
    ],
)
def test_what_is_recorded_is_validated_and_nothing_is_written_when_it_is_not(
    recipe_store: NoustStore, changes: dict[str, Any], field: str
) -> None:
    """Each column has a setter that refuses what the table may not hold."""
    deploy(recipe_store)
    recipe = dataclasses.replace(get_recipe("memos"), **changes)

    with pytest.raises(ValidationError) as failure:
        record_install(DOMAIN, recipe, store=recipe_store)

    assert failure.value.field == field
    assert get_installed(DOMAIN, store=recipe_store) is None


def test_a_recipe_without_a_version_is_recorded_as_unversioned(recipe_store: NoustStore) -> None:
    """The 2.3 format carries none."""
    deploy(recipe_store)
    legacy = dataclasses.replace(get_recipe("memos"), version="", schema=1)

    record_install(DOMAIN, legacy, store=recipe_store)

    installed = get_installed(DOMAIN, store=recipe_store)
    assert installed is not None and installed.version == UNVERSIONED


def test_a_store_made_before_the_table_gets_it_on_migration(tmp_path: Any) -> None:
    """v13 to v14 adds app_recipes and leaves the applications alone."""
    path = tmp_path / "old.db"
    NoustStore.reset_instance()
    store = NoustStore(path)
    with store._transaction() as cursor:
        cursor.execute("DROP TABLE app_recipes")
    store.close()
    connection = sqlite3.connect(path)
    cursor = connection.cursor()
    assert schema_v14.incomplete(cursor) is True
    schema_v14.apply_v14(
        cursor, lambda cur, script: [cur.execute(s) for s in script.split(";") if s.strip()]
    )
    connection.commit()
    assert schema_v14.incomplete(cursor) is False
    connection.close()
    NoustStore.reset_instance()
