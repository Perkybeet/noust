# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A recipe that runs on more than one SQL engine, the operator choosing which at install.

WordPress runs on MySQL and on MariaDB, which speak one protocol. A recipe says so
(``database_choices``, its own engine first) and the install takes the choice: the recipe's
requirement and the variables that read the database name the chosen engine, and the rest of the
install (the check that the server has it, provisioning, the notes) works from that. Pinned here:
what the schema accepts and refuses, that a choice the recipe does not offer is refused before
anything is created, and that the database ends up on the engine chosen.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.logger import Logger
from noust.core.store import NoustStore
from noust.deployers.helpers.databases import DatabaseCredentials
from noust.recipes import RecipeError, get_recipe, parse_recipe
from noust.recipes import deploy as deploy_module
from noust.recipes.deploy import plan_recipe, with_database
from tests.recipes.factory import native_recipe, use_demo_assets

DOMAIN = "site.example.com"


@pytest.fixture(autouse=True)
def demo_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    use_demo_assets(monkeypatch)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    NoustStore._instance = instance
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def provisioned(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def provision(engine: str, **kwargs: Any) -> DatabaseCredentials:
        calls.append({"engine": engine, **kwargs})
        return DatabaseCredentials(
            engine="mysql" if engine in ("mysql", "mariadb") else engine,
            name=kwargs["name"],
            user=kwargs["user"],
            password="p" * 24,
            host="localhost",
            port=3306,
        )

    monkeypatch.setattr(deploy_module, "provision_database", provision)
    monkeypatch.setattr(
        deploy_module, "database_identifiers", lambda app, engine: ("site_db", "site_u")
    )
    monkeypatch.setattr(deploy_module, "refuse_unmet", lambda *a, **k: None)
    return calls


def refused(data: dict[str, Any], message: str) -> None:
    with pytest.raises(RecipeError, match=re.escape(message)):
        parse_recipe("demo", data)


def recipe_with(**changes: Any) -> dict[str, Any]:
    data = native_recipe(requires=["mysql"], **changes)
    return data


# -- the schema -------------------------------------------------------------------------


def test_a_recipe_offers_the_engines_that_speak_its_protocol() -> None:
    recipe = parse_recipe("demo", recipe_with(database_choices=["mysql", "mariadb"]))

    assert recipe.database_choices == ("mysql", "mariadb")
    assert recipe.summary()["database_choices"] == ["mysql", "mariadb"]


def test_a_recipe_that_offers_nothing_says_so_in_its_summary() -> None:
    assert parse_recipe("demo", native_recipe()).summary()["database_choices"] == []


@pytest.mark.parametrize(
    ("choices", "requires", "message"),
    [
        (["mysql", "mariadb"], [], "needs a SQL database in requires"),
        (["mariadb", "mysql"], ["mysql"], "starts with the default, mysql"),
        (["mysql", "mysql"], ["mysql"], "lists the same engine twice"),
        (["mysql", "postgres"], ["mysql"], "same protocol"),
        (["mysql"], ["mysql"], "at least two engines"),
        (["postgres", "mysql"], ["postgres"], "same protocol"),
    ],
)
def test_what_is_not_a_choice_is_refused(
    choices: list[str], requires: list[str], message: str
) -> None:
    refused(native_recipe(requires=requires, database_choices=choices), message)


def test_a_compose_recipe_cannot_offer_one() -> None:
    from tests.recipes.factory import compose_recipe

    refused(compose_recipe(database_choices=["mysql", "mariadb"]), "unknown keys: database_choices")


# -- the choice ---------------------------------------------------------------------------


def test_wordpress_offers_both_and_runs_on_mysql_unless_told() -> None:
    wordpress = get_recipe("wordpress")

    assert wordpress.database_choices == ("mysql", "mariadb")
    assert with_database(wordpress, None) is wordpress
    assert with_database(wordpress, "mysql") is wordpress


def test_choosing_the_other_engine_changes_the_requirement_and_the_variables_that_read_it() -> None:
    wordpress = get_recipe("wordpress")

    chosen = with_database(wordpress, "MariaDB")

    assert chosen.requires == ("mariadb",)
    sources = {v.source for v in chosen.variables if v.source}
    assert sources and all(
        s.startswith("mariadb.") for s in sources if s.split(".")[0] in ("mysql", "mariadb")
    )
    assert not any(s.startswith("mysql.") for s in sources)
    assert wordpress.requires == ("mysql",)  # the recipe itself is untouched


def test_an_engine_the_recipe_does_not_offer_is_refused_in_words() -> None:
    with pytest.raises(RecipeError) as raised:
        with_database(get_recipe("wordpress"), "postgres")

    assert "cannot run on postgres" in raised.value.message
    assert "mysql, mariadb" in raised.value.details


def test_a_recipe_with_no_choice_refuses_one() -> None:
    with pytest.raises(RecipeError) as raised:
        with_database(get_recipe("uptime-kuma"), "mariadb")

    assert "runs on one database engine" in raised.value.message


# -- the install --------------------------------------------------------------------------------------


def test_the_database_is_made_on_the_engine_chosen(
    store: NoustStore, provisioned: list[dict[str, Any]]
) -> None:
    plan = plan_recipe(
        "wordpress", DOMAIN, port=3007, ssl=True, logger=Logger(verbose=False), database="mariadb"
    )

    assert provisioned[0]["engine"] == "mariadb"
    assert plan.env_vars["WORDPRESS_DB_NAME"] == "site_db"
    assert plan.env_vars["WORDPRESS_DB_PASSWORD"] == "p" * 24


def test_without_a_choice_it_is_the_recipes_own(
    store: NoustStore, provisioned: list[dict[str, Any]]
) -> None:
    plan_recipe("wordpress", DOMAIN, port=3007, ssl=True, logger=Logger(verbose=False))

    assert provisioned[0]["engine"] == "mysql"


def test_a_refused_choice_creates_nothing(
    store: NoustStore, provisioned: list[dict[str, Any]]
) -> None:
    with pytest.raises(RecipeError):
        plan_recipe(
            "wordpress",
            DOMAIN,
            port=3007,
            ssl=True,
            logger=Logger(verbose=False),
            database="postgres",
        )

    assert provisioned == []
