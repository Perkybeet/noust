# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db instance list|catalog|create|show|start|stop|upgrade|remove`` (checklist C-03, C-04).

The commands are clients of :class:`~noust.managers.database.managed.ManagedInstances`,
like the console's API. What matters at this level: nothing is made before the
operator has seen every consequence and said yes, a rehearsal changes nothing, the
memory limit is mandatory, no password is printed, and a removal says what it deletes
before it deletes it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.cli.commands import db as db_cli
from noust.core.config import Config
from noust.core.db_instance_store import DbInstanceStore
from noust.core.logger import Logger
from noust.core.runner import SubprocessRunner
from noust.managers.database.managed import ManagedInstances
from noust.managers.database.service import DatabaseService
from tests.database_instance_support import World, build_world, close_world


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    yield build_world(tmp_path, monkeypatch)
    close_world()


@pytest.fixture
def cli_world(world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    """The server of the managed-instances tests, with the CLI pointed at it."""
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    Config.reset_instance()
    monkeypatch.setattr(
        db_cli,
        "_service",
        lambda logger: DatabaseService(
            store=world.store, secrets=world.service.secrets, logger=logger
        ),
    )
    monkeypatch.setattr(
        "noust.managers.database.managed.server_resources",
        world.managed._resources,
    )
    try:
        yield world
    finally:
        Config.reset_instance()


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(Logger, "_write", lambda self, message, newline=True: lines.append(message))
    return lines


def run(*args: str, input: str | None = None):
    return CliRunner().invoke(root_cli, ["db", "instance", *args], input=input)


CREATE = ["create", "pg16", "--engine", "postgresql", "--version", "16", "--memory", "512m"]


class TestMaking:
    def test_it_shows_what_it_will_do_and_asks_first(
        self, cli_world: World, log: list[str]
    ) -> None:
        result = run(*CREATE, input="n\n")

        assert result.exit_code != 0
        text = "\n".join(log) + result.output
        assert "127.0.0.1:5433" in text and "512 MB" in text and "postgresql@pg16" in text
        assert "SSH tunnel" in text
        assert DbInstanceStore(cli_world.store).all() == []
        assert not any(cli_world.units.iterdir())

    def test_yes_makes_it_and_says_how_to_use_it(self, cli_world: World, log: list[str]) -> None:
        result = run(*CREATE, "--yes")

        assert result.exit_code == 0, result.output
        text = "\n".join(log)
        assert "pg16 runs on 127.0.0.1:5433" in text
        assert "--engine postgresql@pg16" in text
        (row,) = DbInstanceStore(cli_world.store).all()
        assert row.state == "ready"
        assert cli_world.password("pg16") not in text + result.output

    def test_the_memory_limit_is_mandatory(self, cli_world: World) -> None:
        result = run("create", "pg16", "--engine", "postgresql")

        assert result.exit_code == 2
        assert "--memory" in result.output

    def test_an_engine_the_catalog_lacks_is_a_usage_error(self, cli_world: World) -> None:
        result = run("create", "x", "--engine", "oracle", "--memory", "512m")

        assert result.exit_code == 2
        assert "oracle" in result.output

    def test_a_refusal_says_why(self, cli_world: World) -> None:
        result = run(
            "create", "pg16", "-e", "postgresql", "--version", "9.6", "--memory", "512m", "-y"
        )

        assert result.exit_code != 0
        assert "15, 16, 17, 18" in str(result.exception)

    def test_an_alias_names_the_engine(self, cli_world: World) -> None:
        result = run("create", "pg17", "-e", "pg", "--version", "17", "--memory", "512m", "-y")

        assert result.exit_code == 0, result.output
        assert DbInstanceStore(cli_world.store).get("pg17") is not None

    def test_json_carries_the_instance_and_no_password(self, cli_world: World) -> None:
        result = run(*CREATE, "--yes", "--json")

        assert result.exit_code == 0, result.output
        body = json.loads(result.stdout)
        assert body["key"] == "postgresql@pg16" and body["memory_limit_mb"] == 512
        assert cli_world.password("pg16") not in result.output

    def test_a_dry_run_changes_nothing(
        self, cli_world: World, log: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A rehearsal still looks (docker ps, ss) through the real runner; the server it
        # looks at is the fake one.
        monkeypatch.setattr(
            SubprocessRunner, "run", lambda self, argv, **kwargs: cli_world.host.run(argv, **kwargs)
        )

        result = run(*CREATE, "--dry-run")

        assert result.exit_code == 0, result.output
        assert "Would make pg16" in "\n".join(log)
        assert DbInstanceStore(cli_world.store).all() == []
        assert not any(cli_world.units.iterdir())
        assert not layout_exists(cli_world)
        assert not any(call[:2] == ("docker", "pull") for call in cli_world.host.calls)


def layout_exists(world: World) -> bool:
    from noust.managers.database import instance_layout as layout

    return layout.directory("pg16").exists()


class TestSeeing:
    def test_an_empty_server_says_how_to_make_one(self, cli_world: World, log: list[str]) -> None:
        result = run("list")

        assert result.exit_code == 0
        assert "noust db instance create" in "\n".join(log)

    def test_the_listing_has_each_instance_and_how_much_memory_is_left(
        self, cli_world: World, log: list[str]
    ) -> None:
        cli_world.managed.create("pg16", "postgresql", version="16", memory="512m")

        result = run("list")

        assert result.exit_code == 0, result.output
        text = "\n".join(log)
        assert "pg16" in text and "127.0.0.1:5433" in text and "postgresql@pg16" in text
        assert "512 MB of the 7372 MB" in text

    def test_json_lists_the_instances_and_the_budget(self, cli_world: World) -> None:
        cli_world.managed.create("pg16", "postgresql", version="16", memory="512m")

        body = json.loads(run("list", "--json").stdout)

        assert [item["name"] for item in body["instances"]] == ["pg16"]
        assert body["budget"]["allocated_mb"] == 512

    def test_the_catalog_lists_every_engine_and_marks_the_default(
        self, cli_world: World, log: list[str]
    ) -> None:
        result = run("catalog")

        assert result.exit_code == 0
        text = "\n".join(log)
        for flavour in ("postgresql", "mysql", "mariadb", "redis", "valkey", "mongodb"):
            assert flavour in text
        assert "18 (default)" in text

    def test_show_names_where_everything_is(self, cli_world: World, log: list[str]) -> None:
        cli_world.managed.create("pg16", "postgresql", version="16", memory="512m")

        result = run("show", "pg16")

        assert result.exit_code == 0, result.output
        text = "\n".join(log)
        assert (
            "noust-db-pg16-data" in text and "noust-db-pg16" in text and "postgresql@pg16" in text
        )

    def test_an_unknown_instance_is_named_as_such(self, cli_world: World) -> None:
        result = run("show", "ghost")

        assert result.exit_code != 0
        assert "no database instance called ghost" in str(result.exception)


class TestLivingWithIt:
    def test_stop_then_start(self, cli_world: World, log: list[str]) -> None:
        cli_world.managed.create("pg16", "postgresql", version="16", memory="512m")

        stopped = run("stop", "pg16")
        assert stopped.exit_code == 0, stopped.output
        assert "noust db instance start pg16" in "\n".join(log)
        assert "noust-db-pg16" not in cli_world.host.containers

        started = run("start", "pg16")
        assert started.exit_code == 0, started.output
        assert "noust-db-pg16" in cli_world.host.containers

    def test_an_upgrade_that_finds_nothing_newer_says_so(
        self, cli_world: World, log: list[str]
    ) -> None:
        described = cli_world.managed.create("pg16", "postgresql", version="16", memory="512m")
        cli_world.host.served["postgres:16"] = described["digest"]

        result = run("upgrade", "pg16")

        assert result.exit_code == 0, result.output
        assert "already runs the newest" in "\n".join(log)

    def test_an_upgrade_reports_what_it_moved_to_and_what_it_ran(
        self, cli_world: World, log: list[str]
    ) -> None:
        described = cli_world.managed.create("pg16", "postgresql", version="16", memory="512m")
        cli_world.host.served["postgres:16"] = "sha256:" + "d" * 64

        result = run("upgrade", "pg16", "--no-backup")

        assert result.exit_code == 0, result.output
        text = "\n".join(log)
        assert "sha256:" + "d" * 64 in text and described["digest"] in text

    def test_a_removal_lists_what_goes_and_asks(self, cli_world: World, log: list[str]) -> None:
        cli_world.managed.create("pg16", "postgresql", version="16", memory="512m")

        refused = run("remove", "pg16", input="n\n")

        assert refused.exit_code != 0
        text = "\n".join(log)
        assert "noust-db-pg16-data" in text and "appdb" in text and "no way back" in text
        assert DbInstanceStore(cli_world.store).get("pg16") is not None

    def test_yes_removes_it(self, cli_world: World, log: list[str]) -> None:
        cli_world.managed.create("pg16", "postgresql", version="16", memory="512m")

        result = run("remove", "pg16", "--yes")

        assert result.exit_code == 0, result.output
        assert DbInstanceStore(cli_world.store).all() == []
        assert cli_world.host.volumes == set()
        assert "the dumps already taken" in "\n".join(log)

    def test_an_application_that_uses_it_stops_the_removal(self, cli_world: World) -> None:
        from noust.core.store import App
        from noust.managers.database.records import DatabaseLink

        cli_world.managed.create("pg16", "postgresql", version="16", memory="512m")
        app = cli_world.store.create_app(App(domain="shop.example.com", app_path="/srv/shop"))
        cli_world.service.records.save_link(
            DatabaseLink(
                app_id=app.id or 0,
                engine="postgresql@pg16",
                db_name="appdb",
                username="app",
                env_var="DATABASE_URL",
            )
        )

        result = run("remove", "pg16", "--yes")

        assert result.exit_code != 0
        assert "shop.example.com" in str(result.exception)
        assert DbInstanceStore(cli_world.store).get("pg16") is not None


def test_the_class_the_commands_use_is_the_one_the_api_uses() -> None:
    from noust.web.api.databases import instances as api

    assert api.ManagedInstances is ManagedInstances
