# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db adopt NAME --engine ENGINE``: add one database to Noust (4.1, item 103).

``noust db adopt`` alone adds every database no application's engine records, as it always did;
with a name it adds that one, which is the action the Databases screen offers on a database
Noust does not manage.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.cli.commands import db as db_cli
from noust.core.logger import Logger
from noust.core.store import NoustStore
from tests.support.database_choice import noble  # noqa: F401 - the autouse fixture
from tests.support.database_servers import World


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture
def world(tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> World:
    built = World(tmp_path, store, mysql=None)
    built.classes["postgresql"].dbs["second"] = {"owner": "app", "size": None}
    monkeypatch.setattr(
        db_cli,
        "_service",
        lambda logger: type(built.service())(
            store=built.store,
            resolve=built.resolve,
            engines=lambda: list(built.classes),
            instances=built.found,
            logger=logger,
        ),
    )
    return built


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(Logger, "_write", lambda self, message, newline=True: lines.append(message))
    return lines


def run(*args: str):
    return CliRunner().invoke(root_cli, ["db", "adopt", *args])


def test_with_a_name_only_that_database_is_added(world: World, log: list[str]) -> None:
    result = run("appdb", "--engine", "postgresql")

    assert result.exit_code == 0, result.output
    assert "Now tracked: postgresql/appdb" in "\n".join(log)
    assert world.store.get_database("appdb", "postgresql") is not None
    assert world.store.get_database("second", "postgresql") is None


def test_without_one_every_database_is_added_as_before(world: World, log: list[str]) -> None:
    result = run("--engine", "postgresql")

    assert result.exit_code == 0, result.output
    assert {
        name for name in ("appdb", "second") if world.store.get_database(name, "postgresql")
    } == {
        "appdb",
        "second",
    }


def test_a_name_needs_its_engine(world: World) -> None:
    result = run("appdb")

    assert result.exit_code == 2
    assert "--engine" in result.output
    assert world.store.get_database("appdb", "postgresql") is None
