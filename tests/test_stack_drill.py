# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Compose stack's database dump is loaded into a throwaway container (checklist D-18, H-31).

The dump of a database that lives in a container is only a hope until something has loaded it.
What is pinned here is how the test is made safe and honest: the container is the stack's own
image on no network, named and labelled so nothing else is ever touched, its password reaches
Docker through the environment and never an argument, the client that loads the dump is the very
one a real restore runs, and the container is removed whatever happens.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from noust.core.runner import FakeRunner
from noust.managers.stack_databases import StackDatabase, client_command
from noust.managers.stack_drill import (
    CONTAINER_LABEL,
    StackRestoreTest,
    restore_test_stack_dump,
)

TOKEN = "ab12cd34"
NAME = f"noust-drill-{TOKEN}"

POSTGRES = StackDatabase("db", "postgres", "shop", "shopper", image="postgres:16-alpine")
MYSQL = StackDatabase("db", "mysql", "shop", "shopper", image="mysql:8.4")
MARIADB_ALL = StackDatabase("db", "mariadb", "", "root", image="mariadb:11")
MONGO = StackDatabase("db", "mongo", "shop", "root", image="mongo:7")
MONGO_OPEN = StackDatabase("db", "mongo", "", "", image="mongo:7")


@pytest.fixture
def dump(tmp_path: Path) -> Path:
    """A gzipped dump, the way a stack's dumps are stored."""
    path = tmp_path / "stack-db-postgres-shop.dump.gz"
    with gzip.open(path, "wb") as handle:
        handle.write(b"PGDMP-pretend")
    return path


def runner_for(database: StackDatabase, *, tables: str = "3\n") -> FakeRunner:
    """A runner whose container starts, finishes its first start and counts ``tables``."""
    markers = {
        "postgres": "PostgreSQL init process complete; ready for start up.",
        "mysql": "MySQL init process done. Ready for start up.",
        "mariadb": "MariaDB init process done. Ready for start up.",
        "mongo": "MongoDB init process complete; ready for start up.",
    }
    runner = FakeRunner()
    runner.script(["docker", "run"], stdout="9f8e7d6c5b4a\n")
    runner.script(["docker", "logs", NAME], stdout=markers[database.engine] + "\n")
    tables_command = client_command(database, "tables")
    runner.script(["docker", "exec", NAME, *tables_command], stdout=tables)
    return runner


def run_test(
    runner: FakeRunner, database: StackDatabase, dump: Path, **kwargs: object
) -> StackRestoreTest:
    """Run the test without sleeping, with the token the names are built from."""
    return restore_test_stack_dump(
        runner,
        database,
        dump,
        sleep=lambda seconds: None,
        token=TOKEN,
        **kwargs,  # type: ignore[arg-type]
    )


class TestAGoodDump:
    def test_it_loads_and_says_how_many_tables_it_found(self, dump: Path) -> None:
        runner = runner_for(POSTGRES, tables="4\n")

        result = run_test(runner, POSTGRES, dump)

        assert result.ok and not result.skipped
        assert "4 tables" in result.detail
        assert "postgres:16-alpine" in result.detail

    def test_the_container_is_the_stacks_own_image_on_no_network(self, dump: Path) -> None:
        runner = runner_for(POSTGRES)

        run_test(runner, POSTGRES, dump)

        started = next(call for call in runner.calls if call[:2] == ("docker", "run"))
        assert "--network" in started and started[started.index("--network") + 1] == "none"
        assert started[started.index("--name") + 1] == NAME
        assert f"{CONTAINER_LABEL}=1" in started
        assert started[-1] == "postgres:16-alpine"
        assert "-p" not in started and "--publish" not in started

    def test_the_password_is_in_the_environment_and_never_in_an_argument(self, dump: Path) -> None:
        runner = runner_for(POSTGRES)

        run_test(runner, POSTGRES, dump)

        index = next(i for i, call in enumerate(runner.calls) if call[:2] == ("docker", "run"))
        env = runner.envs[index]
        assert env is not None and len(env["POSTGRES_PASSWORD"]) >= 24
        assert env["POSTGRES_PASSWORD"] not in " ".join(" ".join(call) for call in runner.calls)
        started = runner.calls[index]
        assert "POSTGRES_PASSWORD" in started
        assert "POSTGRES_USER=shopper" in started and "POSTGRES_DB=shop" in started

    def test_the_client_that_loads_it_is_the_one_a_real_restore_runs(self, dump: Path) -> None:
        runner = runner_for(POSTGRES)

        run_test(runner, POSTGRES, dump)

        loaded = next(call for call in runner.calls if call[:4] == ("docker", "exec", "-i", NAME))
        assert list(loaded[4:]) == client_command(POSTGRES, "restore")
        # The gzip is opened on the host: what the client reads is a plain copy of the dump.
        assert runner.stdin_paths[-1].name == "dump"
        assert runner.stdin_paths[-1] != dump

    def test_the_container_and_its_anonymous_volume_are_removed_by_name(self, dump: Path) -> None:
        runner = runner_for(POSTGRES)

        run_test(runner, POSTGRES, dump)

        assert runner.calls[-1] == ("docker", "rm", "-f", "-v", NAME)

    def test_it_waits_for_the_first_start_to_finish_before_loading(self, dump: Path) -> None:
        runner = runner_for(POSTGRES)
        runner.script(["docker", "logs", NAME], stdout="")
        waits: list[float] = []

        def sleep(seconds: float) -> None:
            # The first start ends while the test waits for it.
            waits.append(seconds)
            runner.script(
                ["docker", "logs", NAME],
                stdout="PostgreSQL init process complete; ready for start up.\n",
            )

        result = restore_test_stack_dump(runner, POSTGRES, dump, sleep=sleep, token=TOKEN)

        assert result.ok and waits
        loaded = [
            i for i, call in enumerate(runner.calls) if call[:4] == ("docker", "exec", "-i", NAME)
        ]
        assert loaded, "the dump was loaded"


class TestEveryEngine:
    @pytest.mark.parametrize(
        ("database", "variables"),
        [
            (MYSQL, {"MYSQL_USER=shopper", "MYSQL_DATABASE=shop"}),
            (MARIADB_ALL, set()),
            (MONGO, {"MONGO_INITDB_ROOT_USERNAME=root"}),
            (MONGO_OPEN, set()),
        ],
    )
    def test_each_one_starts_and_loads(
        self, database: StackDatabase, variables: set[str], dump: Path
    ) -> None:
        runner = runner_for(database)

        result = run_test(runner, database, dump)

        assert result.ok, result.detail
        started = next(call for call in runner.calls if call[:2] == ("docker", "run"))
        assert variables <= set(started)
        assert any(call[:4] == ("docker", "exec", "-i", NAME) for call in runner.calls)

    def test_a_mysql_application_user_gets_its_own_password_and_a_root_one(
        self, dump: Path
    ) -> None:
        runner = runner_for(MYSQL)

        run_test(runner, MYSQL, dump)

        index = next(i for i, call in enumerate(runner.calls) if call[:2] == ("docker", "run"))
        env = runner.envs[index]
        assert env is not None
        assert env["MYSQL_PASSWORD"] and env["MYSQL_ROOT_PASSWORD"]
        assert env["MYSQL_PASSWORD"] != env["MYSQL_ROOT_PASSWORD"]

    def test_mariadb_gets_the_variables_its_image_reads(self, dump: Path) -> None:
        runner = runner_for(MARIADB_ALL)

        run_test(runner, MARIADB_ALL, dump)

        index = next(i for i, call in enumerate(runner.calls) if call[:2] == ("docker", "run"))
        env = runner.envs[index]
        assert env is not None and env["MARIADB_ROOT_PASSWORD"]


class TestABadDump:
    def test_a_dump_the_client_refuses_is_a_failed_test_with_the_clients_words(
        self, dump: Path
    ) -> None:
        runner = runner_for(POSTGRES)
        restore = client_command(POSTGRES, "restore")
        runner.script(
            ["docker", "exec", "-i", NAME, *restore],
            stderr='pg_restore: error: role "ghost" does not exist',
            exit_code=1,
        )

        result = run_test(runner, POSTGRES, dump)

        assert not result.ok
        assert 'role "ghost" does not exist' in result.detail
        assert runner.calls[-1] == ("docker", "rm", "-f", "-v", NAME)

    def test_a_container_that_does_not_start_is_a_failure_that_says_why(self, dump: Path) -> None:
        runner = runner_for(POSTGRES)
        runner.script(
            ["docker", "run"],
            stderr="Unable to find image 'postgres:16-alpine' locally",
            exit_code=125,
        )

        result = run_test(runner, POSTGRES, dump)

        assert not result.ok
        assert "Unable to find image" in result.detail

    def test_an_engine_that_never_answers_is_a_failure_with_its_log(self, dump: Path) -> None:
        runner = runner_for(POSTGRES)
        runner.script(["docker", "logs", NAME], stdout="starting...\n")

        result = run_test(runner, POSTGRES, dump, attempts=3)

        assert not result.ok
        assert "never finished starting" in result.detail
        assert "starting..." in result.detail
        assert runner.calls[-1] == ("docker", "rm", "-f", "-v", NAME)


class TestWhatIsNotTried:
    def test_a_backup_that_did_not_record_the_image_is_skipped_with_the_reason(
        self, dump: Path
    ) -> None:
        runner = FakeRunner()
        database = StackDatabase("db", "postgres", "shop", "shopper")

        result = run_test(runner, database, dump)

        assert result.ok and result.skipped
        assert "does not record" in result.detail
        assert runner.calls == []

    def test_an_image_that_is_not_the_official_one_of_the_engine_is_skipped(
        self, dump: Path
    ) -> None:
        runner = FakeRunner()
        database = StackDatabase("db", "postgres", "shop", "shopper", image="bitnami/postgresql:16")

        result = run_test(runner, database, dump)

        assert result.skipped and "official" in result.detail
        assert runner.calls == []

    @pytest.mark.parametrize("image", ["-v/:/host", "postgres:16 --privileged", "a" * 400])
    def test_an_image_name_that_could_be_an_option_is_never_run(
        self, image: str, dump: Path
    ) -> None:
        runner = FakeRunner()
        database = StackDatabase("db", "postgres", "shop", "shopper", image=image)

        result = run_test(runner, database, dump)

        assert result.skipped
        assert runner.calls == []
