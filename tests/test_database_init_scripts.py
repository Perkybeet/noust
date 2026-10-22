# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
SQL scripts an instance runs the first time it starts (4.0, C-29, the initialisation half).

The official images of PostgreSQL, MySQL and MariaDB run the ``.sql`` files of
``/docker-entrypoint-initdb.d`` once, when their data directory is empty. What is defended:

- **only what is safe to run is accepted**: a plain ``.sql`` file name, UTF-8 text, a size of a
  schema and not of a data load, at most ten, in name order; a shell script would run in the
  engine's own container before it is up;
- **they are files on disk and a mount in the Compose file**, written beside it, read-only in
  the container, and only for the engines whose image runs them;
- **a request that cannot succeed is refused first**, naming why, before anything exists.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.cli.commands import db as db_cli
from noust.core.config import Config
from noust.core.exceptions import DatabaseEngineError, ValidationError
from noust.managers.database import instance_layout as layout
from noust.managers.database.service import DatabaseService
from noust.validators.db_instance import (
    MAX_INIT_SCRIPT_BYTES,
    MAX_INIT_SCRIPTS,
    check_init_script_name,
    check_init_scripts,
)
from tests.database_instance_support import World, build_world, close_world

SCHEMA = b"CREATE TABLE notes (id serial PRIMARY KEY, body text);\n"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    built = build_world(tmp_path, monkeypatch)
    yield built
    close_world()


def service_of(name: str) -> dict[str, Any]:
    return yaml.safe_load(layout.compose_path(name).read_text())["services"]["db"]


class TestWhatIsAccepted:
    @pytest.mark.parametrize(
        "name", ["schema.sql", "01-schema.sql", "a_b.c-d.sql", "X" + "y" * 55 + ".sql"]
    )
    def test_a_plain_sql_file_name(self, name: str) -> None:
        assert check_init_script_name(name) == name

    @pytest.mark.parametrize(
        "name",
        [
            "setup.sh",
            "dump.sql.gz",
            "../escape.sql",
            "dir/schema.sql",
            ".hidden.sql",
            "-rf.sql",
            "schema.SQL",
            "a b.sql",
            "schema.sql;",
            "",
            "x" * 70 + ".sql",
        ],
    )
    def test_nothing_else(self, name: str) -> None:
        with pytest.raises(ValidationError) as caught:
            check_init_script_name(name)

        assert caught.value.field == "init_scripts"

    def test_the_scripts_come_back_in_the_order_they_run(self) -> None:
        scripts = check_init_scripts([("02-data.sql", b"INSERT 1;"), ("01-schema.sql", SCHEMA)])

        assert [name for name, _content in scripts] == ["01-schema.sql", "02-data.sql"]

    def test_there_are_at_most_ten(self) -> None:
        many = [(f"{index:02}.sql", SCHEMA) for index in range(MAX_INIT_SCRIPTS + 1)]

        with pytest.raises(ValidationError) as caught:
            check_init_scripts(many)

        assert "too many" in caught.value.message

    def test_a_name_twice_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            check_init_scripts([("a.sql", SCHEMA), ("a.sql", SCHEMA)])

    def test_an_empty_script_and_one_larger_than_a_schema_are_refused(self) -> None:
        with pytest.raises(ValidationError):
            check_init_scripts([("a.sql", b"")])
        with pytest.raises(ValidationError) as caught:
            check_init_scripts([("a.sql", b"x" * (MAX_INIT_SCRIPT_BYTES + 1))])

        assert "noust db import" in caught.value.details

    def test_text_that_is_not_utf8_is_refused(self) -> None:
        with pytest.raises(ValidationError) as caught:
            check_init_scripts([("a.sql", b"\xff\xfe\x00")])

        assert "UTF-8" in caught.value.message


class TestMakingOneWithScripts:
    def test_the_scripts_are_written_beside_the_compose_file_and_mounted_read_only(
        self, world: World
    ) -> None:
        world.managed.create(
            "pg17",
            "postgresql",
            version="17",
            memory="1g",
            init_scripts=[
                ("02-seed.sql", b"INSERT INTO notes VALUES (1, 'x');"),
                ("01-schema.sql", SCHEMA),
            ],
        )

        folder = layout.init_directory("pg17")
        assert sorted(item.name for item in folder.iterdir()) == ["01-schema.sql", "02-seed.sql"]
        assert (folder / "01-schema.sql").read_bytes() == SCHEMA
        assert (folder / "01-schema.sql").stat().st_mode & 0o777 == 0o644
        assert "./initdb:/docker-entrypoint-initdb.d:ro" in service_of("pg17")["volumes"]
        assert world.managed.get("pg17").init_scripts == ("01-schema.sql", "02-seed.sql")

    def test_without_scripts_there_is_no_mount_and_no_directory(self, world: World) -> None:
        world.managed.create("plain", "postgresql", version="17", memory="1g")

        assert not layout.init_directory("plain").exists()
        assert not any(
            "docker-entrypoint-initdb.d" in item for item in service_of("plain")["volumes"]
        )
        assert world.managed.get("plain").init_scripts == ()

    def test_mysql_runs_them_too(self, world: World) -> None:
        world.host.exec_stdout = ""

        world.managed.create(
            "my",
            "mysql",
            version="8.4",
            memory="1g",
            init_scripts=[("schema.sql", b"CREATE TABLE t (id int);")],
        )

        assert "./initdb:/docker-entrypoint-initdb.d:ro" in service_of("my")["volumes"]

    def test_an_engine_whose_image_runs_no_sql_refuses_them_before_anything_exists(
        self, world: World
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            world.managed.create(
                "cache", "redis", version="8.0", memory="256m", init_scripts=[("a.sql", SCHEMA)]
            )

        assert caught.value.field == "init_scripts" and "noust db import" in caught.value.details
        assert world.managed.records.all() == []

    def test_a_script_that_is_not_acceptable_stops_the_creation_first(self, world: World) -> None:
        with pytest.raises(ValidationError):
            world.managed.create(
                "pg17",
                "postgresql",
                memory="1g",
                init_scripts=[("setup.sh", b"rm -rf /")],
            )

        assert world.managed.records.all() == [] and not layout.directory("pg17").exists()

    def test_the_plan_names_them_and_changes_nothing(self, world: World) -> None:
        plan = world.managed.plan(
            "pg17", "postgresql", memory="1g", init_scripts=[("b.sql", SCHEMA), ("a.sql", SCHEMA)]
        )

        assert plan.init_scripts == ("a.sql", "b.sql")
        assert plan.to_dict()["init_scripts"] == ["a.sql", "b.sql"]
        assert not layout.directory("pg17").exists()

    def test_a_failed_creation_leaves_no_scripts_behind(self, world: World) -> None:
        world.host.health["noust-db-pg17"] = ["unhealthy"]

        with pytest.raises(DatabaseEngineError):
            world.managed.create(
                "pg17", "postgresql", version="17", memory="1g", init_scripts=[("a.sql", SCHEMA)]
            )

        assert not layout.directory("pg17").exists()

    def test_the_description_lists_them(self, world: World) -> None:
        described = world.managed.create(
            "pg17", "postgresql", version="17", memory="1g", init_scripts=[("a.sql", SCHEMA)]
        )

        assert described["init_scripts"] == ["a.sql"]


@pytest.fixture
def cli_world(world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
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
        "noust.managers.database.managed.server_resources", world.managed._resources
    )
    try:
        yield world
    finally:
        Config.reset_instance()


def run(*args: str, input: str | None = None):
    return CliRunner().invoke(root_cli, ["db", "instance", *args], input=input)


class TestTheCommand:
    def test_a_script_is_given_as_a_file_and_runs_in_name_order(
        self, cli_world: World, tmp_path: Path
    ) -> None:
        later = tmp_path / "02-seed.sql"
        later.write_text("INSERT INTO notes VALUES (1, 'x');")
        first = tmp_path / "01-schema.sql"
        first.write_bytes(SCHEMA)

        result = run(
            "create",
            "pg17",
            "-e",
            "postgresql",
            "--version",
            "17",
            "--memory",
            "1g",
            "--init-script",
            str(later),
            "--init-script",
            str(first),
            "--yes",
        )

        assert result.exit_code == 0, result.output
        assert cli_world.managed.get("pg17").init_scripts == ("01-schema.sql", "02-seed.sql")

    def test_the_plan_says_what_will_run(self, cli_world: World, tmp_path: Path) -> None:
        script = tmp_path / "schema.sql"
        script.write_bytes(SCHEMA)

        result = run(
            "create",
            "pg17",
            "-e",
            "postgresql",
            "--memory",
            "1g",
            "--init-script",
            str(script),
            input="n\n",
        )

        assert result.exit_code != 0
        assert "Runs on first start" in result.output

    def test_a_file_that_is_too_large_is_refused_with_the_way_to_load_a_dump(
        self, cli_world: World, tmp_path: Path
    ) -> None:
        big = tmp_path / "big.sql"
        big.write_bytes(b"x" * (MAX_INIT_SCRIPT_BYTES + 1))

        result = run(
            "create",
            "pg17",
            "-e",
            "postgresql",
            "--memory",
            "1g",
            "--init-script",
            str(big),
            "--yes",
        )

        assert result.exit_code != 0
        assert "noust db import" in result.output
        assert cli_world.managed.records.all() == []

    def test_a_file_that_is_not_there_is_a_usage_error(self, cli_world: World) -> None:
        result = run(
            "create", "pg17", "-e", "postgresql", "--memory", "1g", "--init-script", "/no/such.sql"
        )

        assert result.exit_code == 2
