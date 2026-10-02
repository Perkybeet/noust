# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the databases a Compose stack runs, copied before it is updated.

The copy exists because a migration a failed update ran cannot be undone by
going back to the previous containers: without a dump taken first there is
nothing to go back to. What is pinned here is what has to hold for that copy
to be trusted:

- the stack's own configuration, as ``docker compose config --format json``
  resolves it, decides what is dumped (Proggest's file is the fixture);
- a password never reaches a command line, on the host or in the container;
- a dump that cannot be taken says how to switch it off, and a restore puts
  the dump back into its service.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import BackupError
from noust.core.runner import FakeRunner
from noust.managers.stack_databases import (
    CLIENT_SCRIPT,
    StackBackupError,
    StackDatabase,
    client_command,
    compose_volume_names,
    detect_stack_databases,
    dump_stack_database,
    dump_stack_databases,
    image_engine,
    read_compose_config,
    restore_stack_database,
    set_backup_before_update,
    stack_host_for,
    volume_names_from_compose_file,
)

FIXTURES = Path(__file__).parent / "fixtures" / "stack"


@pytest.fixture
def proggest_config() -> dict[str, Any]:
    """What ``docker compose config --format json`` prints for Proggest's production file."""
    return json.loads((FIXTURES / "proggest-compose-config.json").read_text(encoding="utf-8"))


def service(image: str | None, **environment: str) -> dict[str, Any]:
    """Build one service the way ``docker compose config`` prints it."""
    entry: dict[str, Any] = {"environment": dict(environment)}
    if image is not None:
        entry["image"] = image
    return entry


@dataclass(frozen=True)
class Declared:
    """What ``backup.databases`` of a project file declares, structurally."""

    service: str
    engine: str
    database: str | None = None
    user: str | None = None


class TestWhichImagesAreDatabases:
    """Only the official images are recognised; the rest are declared by hand."""

    @pytest.mark.parametrize(
        ("image", "engine"),
        [
            ("postgres", "postgres"),
            ("postgres:16-alpine", "postgres"),
            ("postgres:16@sha256:" + "a" * 64, "postgres"),
            ("docker.io/library/postgres:15", "postgres"),
            ("library/postgres:15", "postgres"),
            ("mysql:8.4", "mysql"),
            ("mariadb:11", "mariadb"),
            ("mongo:7", "mongo"),
            ("registry-1.docker.io/library/mongo", "mongo"),
        ],
    )
    def test_official_images(self, image: str, engine: str) -> None:
        assert image_engine(image) == engine

    @pytest.mark.parametrize(
        "image",
        [
            "bitnami/postgresql:16",
            "bitnami/mariadb",
            "postgis/postgis:16-3.4",
            "timescale/timescaledb:latest-pg16",
            "mysql/mysql-server:8.0",
            "ghcr.io/acme/postgres:16",
            "localhost:5000/team/postgres",
            "redis:7-alpine",
            "my-postgres-wrapper:1",
            "",
            None,
        ],
    )
    def test_everything_else_is_not(self, image: str | None) -> None:
        assert image_engine(image) is None


class TestDetectionFromTheResolvedConfiguration:
    """The configuration Compose resolved says which database and as whom."""

    def test_proggest_has_one_postgres_called_proggest_as_proggest(
        self, proggest_config: dict[str, Any]
    ) -> None:
        found = detect_stack_databases(proggest_config, "auto")

        assert found == [
            StackDatabase(
                service="postgres", engine="postgres", database="proggest", user="proggest"
            )
        ]

    def test_redis_and_the_application_services_are_not_databases(
        self, proggest_config: dict[str, Any]
    ) -> None:
        names = {db.service for db in detect_stack_databases(proggest_config, "auto")}

        assert names == {"postgres"}

    def test_a_bitnami_postgres_is_not_detected(self) -> None:
        config = {
            "services": {
                "db": service(
                    "bitnami/postgresql:16",
                    POSTGRESQL_USERNAME="app",
                    POSTGRESQL_DATABASE="app",
                )
            }
        }

        assert detect_stack_databases(config, "auto") == []

    def test_postgres_defaults_are_the_images_own(self) -> None:
        config = {"services": {"db": service("postgres:16")}}

        (db,) = detect_stack_databases(config, "auto")

        # The image creates the superuser "postgres" and a database named after the user.
        assert (db.user, db.database) == ("postgres", "postgres")

    def test_postgres_database_defaults_to_the_user(self) -> None:
        config = {"services": {"db": service("postgres:16", POSTGRES_USER="shop")}}

        (db,) = detect_stack_databases(config, "auto")

        assert (db.user, db.database) == ("shop", "shop")

    def test_mysql_with_a_user_and_a_database_dumps_that_database_as_that_user(self) -> None:
        config = {
            "services": {
                "db": service(
                    "mysql:8.4", MYSQL_USER="shop", MYSQL_DATABASE="shop", MYSQL_PASSWORD="x"
                )
            }
        }

        (db,) = detect_stack_databases(config, "auto")

        assert db == StackDatabase("db", "mysql", "shop", "shop")

    def test_mysql_with_only_a_root_password_dumps_every_database_as_root(self) -> None:
        config = {"services": {"db": service("mysql:8.4", MYSQL_ROOT_PASSWORD="x")}}

        (db,) = detect_stack_databases(config, "auto")

        assert (db.user, db.database) == ("root", "")

    def test_mariadb_reads_its_own_variables_before_the_mysql_ones(self) -> None:
        config = {
            "services": {
                "db": service(
                    "mariadb:11",
                    MARIADB_USER="new",
                    MARIADB_DATABASE="new_db",
                    MYSQL_USER="old",
                    MYSQL_DATABASE="old_db",
                )
            }
        }

        (db,) = detect_stack_databases(config, "auto")

        assert (db.engine, db.user, db.database) == ("mariadb", "new", "new_db")

    def test_mongo_with_a_root_user_authenticates_as_it(self) -> None:
        config = {
            "services": {
                "db": service(
                    "mongo:7",
                    MONGO_INITDB_ROOT_USERNAME="root",
                    MONGO_INITDB_ROOT_PASSWORD="x",
                    MONGO_INITDB_DATABASE="shop",
                )
            }
        }

        (db,) = detect_stack_databases(config, "auto")

        assert db == StackDatabase("db", "mongo", "shop", "root")

    def test_mongo_without_credentials_runs_without_authentication(self) -> None:
        config = {"services": {"db": service("mongo:7")}}

        (db,) = detect_stack_databases(config, "auto")

        assert (db.user, db.database) == ("", "")

    def test_the_environment_may_be_a_list_of_assignments(self) -> None:
        config = {
            "services": {
                "db": {"image": "postgres:16", "environment": ["POSTGRES_USER=shop", "FLAG"]}
            }
        }

        (db,) = detect_stack_databases(config, "auto")

        assert db.user == "shop"

    def test_a_service_with_no_image_is_skipped(self) -> None:
        assert (
            detect_stack_databases({"services": {"web": {"build": {"context": "."}}}}, "auto") == []
        )

    def test_a_configuration_without_services_has_no_databases(self) -> None:
        assert detect_stack_databases({}, "auto") == []


class TestWhatTheProjectFileSays:
    """``backup.databases`` in ``noust.yaml``: off, auto, or a list that replaces detection."""

    def test_off_detects_nothing(self, proggest_config: dict[str, Any]) -> None:
        assert detect_stack_databases(proggest_config, "off") == []

    def test_a_declaration_adds_what_detection_cannot_see(self) -> None:
        config = {
            "services": {
                "db": service("bitnami/postgresql:16", POSTGRESQL_PASSWORD="x"),
            }
        }

        found = detect_stack_databases(config, (Declared("db", "postgres", "app", "app"),))

        assert found == [StackDatabase("db", "postgres", "app", "app")]

    def test_a_declaration_replaces_detection(self, proggest_config: dict[str, Any]) -> None:
        proggest_config["services"]["analytics"] = service("mongo:7")

        found = detect_stack_databases(proggest_config, (Declared("analytics", "mongo"),))

        assert [db.service for db in found] == ["analytics"]

    def test_what_a_declaration_leaves_out_comes_from_the_environment(self) -> None:
        config = {
            "services": {"db": service("postgres:16", POSTGRES_USER="shop", POSTGRES_DB="orders")}
        }

        (db,) = detect_stack_databases(config, (Declared("db", "postgres"),))

        assert (db.user, db.database) == ("shop", "orders")

    def test_a_declared_service_the_stack_does_not_have_is_an_error_with_a_fix(self) -> None:
        config = {"services": {"db": service("postgres:16")}}

        with pytest.raises(StackBackupError) as raised:
            detect_stack_databases(config, (Declared("database", "postgres"),))

        assert "database" in raised.value.message
        assert "noust.yaml" in (raised.value.details or "")

    def test_a_declared_engine_that_is_not_supported_is_an_error(self) -> None:
        config = {"services": {"db": service("postgres:16")}}

        with pytest.raises(StackBackupError) as raised:
            detect_stack_databases(config, (Declared("db", "oracle"),))

        assert "oracle" in raised.value.message

    @pytest.mark.parametrize("name", ["-evil", "a b", "a;b", "x" * 200])
    def test_a_database_or_user_that_could_be_taken_for_an_option_is_refused(
        self, name: str
    ) -> None:
        config = {"services": {"db": service("postgres:16")}}

        with pytest.raises(StackBackupError):
            detect_stack_databases(config, (Declared("db", "postgres", database=name),))
        with pytest.raises(StackBackupError):
            detect_stack_databases(config, (Declared("db", "postgres", user=name),))

    def test_the_error_is_a_backup_error(self) -> None:
        # The CLI, the API and the job runner already turn a BackupError into an answer.
        assert issubclass(StackBackupError, BackupError)


class TestVolumesKeepTheirRealNames:
    """A named volume is ``<project>_<key>`` unless it names itself; mounting the key mounts a new empty one."""

    def test_the_resolved_names_come_from_the_configuration(
        self, proggest_config: dict[str, Any]
    ) -> None:
        assert compose_volume_names(proggest_config) == ["proggest_pgdata", "proggest_redisdata"]

    def test_a_volume_that_names_itself_keeps_that_name(self) -> None:
        config = {
            "name": "shop",
            "volumes": {"data": {"name": "shop-shared-data"}, "cache": {"name": "shop_cache"}},
        }

        assert compose_volume_names(config) == ["shop-shared-data", "shop_cache"]

    def test_a_volume_with_no_name_is_prefixed_with_the_project(self) -> None:
        config = {"name": "shop", "volumes": {"data": None, "cache": {}}}

        assert compose_volume_names(config) == ["shop_data", "shop_cache"]

    def test_no_volumes_is_an_empty_list(self) -> None:
        assert compose_volume_names({"name": "shop"}) == []
        assert compose_volume_names({"name": "shop", "volumes": None}) == []

    def test_from_the_compose_file_the_project_prefixes_every_key(self) -> None:
        document = {"volumes": {"pgdata": None, "uploads": {"driver": "local"}}}

        assert volume_names_from_compose_file(document, "proggest") == [
            "proggest_pgdata",
            "proggest_uploads",
        ]

    def test_from_the_compose_file_name_wins_and_is_not_prefixed(self) -> None:
        document = {"volumes": {"shared": {"name": "company-shared"}}}

        assert volume_names_from_compose_file(document, "proggest") == ["company-shared"]

    def test_an_external_volume_is_not_prefixed_either(self) -> None:
        document = {
            "volumes": {
                "legacy": {"external": True},
                "named": {"external": {"name": "other-name"}},
            }
        }

        assert volume_names_from_compose_file(document, "proggest") == ["legacy", "other-name"]

    def test_the_compose_file_without_volumes_has_none(self) -> None:
        assert volume_names_from_compose_file({"services": {}}, "proggest") == []
        assert volume_names_from_compose_file({"volumes": None}, "proggest") == []


DB_PASSWORD = "dummy-db-password"


class RecordingRunner(FakeRunner):
    """A fake runner that also keeps what the real one would have done with a dump or a restore."""

    def __init__(self) -> None:
        super().__init__()
        #: ``compress`` of every ``capture_to_file``, by destination.
        self.compressed: dict[Path, bool] = {}
        #: What was on ``stdin_path`` when the command ran: it is deleted afterwards.
        self.stdin_contents: list[bytes] = []
        #: ``ps -q`` answers, by service: a service not listed is not running.
        self.running: set[str] = set()

    def capture_to_file(self, argv, destination, *, compress=False, **kwargs):  # type: ignore[no-untyped-def]
        self.compressed[destination] = compress
        return super().capture_to_file(argv, destination, compress=compress, **kwargs)

    def run(self, argv, *, stdin_path=None, **kwargs):  # type: ignore[no-untyped-def]
        if stdin_path is not None:
            self.stdin_contents.append(Path(stdin_path).read_bytes())
        if list(argv[-3:-1]) == ["ps", "-q"] or list(argv[-2:-1]) == ["-q"]:
            service_name = argv[-1]
            super().run(argv, stdin_path=stdin_path, **kwargs)
            return self.ps_result(argv, service_name in self.running)
        return super().run(argv, stdin_path=stdin_path, **kwargs)

    @staticmethod
    def ps_result(argv, running: bool):  # type: ignore[no-untyped-def]
        from noust.core.runner import CommandResult

        return CommandResult(
            argv=tuple(argv), exit_code=0, stdout="3f2a9c1d7b8e\n" if running else ""
        )


class FakeHost:
    """The part of a Compose deployer the module uses."""

    domain = "proggest.es"

    def __init__(self, runner: FakeRunner, app_path: Path) -> None:
        self._runner = runner
        self.app_path = app_path

    @property
    def runner(self) -> FakeRunner:
        return self._runner

    def _compose(self, *args: str, project: str | None = None) -> list[str]:
        return [
            "docker",
            "compose",
            "-p",
            project or "proggest",
            "-f",
            str(self.app_path / "docker-compose.prod.yml"),
            *args,
        ]


@pytest.fixture
def runner() -> RecordingRunner:
    return RecordingRunner()


@pytest.fixture
def host(runner: RecordingRunner, tmp_path: Path) -> FakeHost:
    app = tmp_path / "proggest"
    app.mkdir()
    return FakeHost(runner, app)


POSTGRES = StackDatabase("postgres", "postgres", "proggest", "proggest")
MYSQL = StackDatabase("db", "mysql", "shop", "shop")
MYSQL_ALL = StackDatabase("db", "mysql", "", "root")
MARIADB = StackDatabase("db", "mariadb", "shop", "shop")
MONGO = StackDatabase("db", "mongo", "shop", "root")
MONGO_OPEN = StackDatabase("db", "mongo", "", "")


def exec_prefix(host: FakeHost) -> list[str]:
    """What every command run inside a container of the fake stack starts with."""
    return [*host._compose("exec")]


def exec_calls(runner: FakeRunner) -> list[tuple[str, ...]]:
    """The commands that ran inside a container."""
    return [call for call in runner.calls if "exec" in call]


def client_args(call: tuple[str, ...]) -> list[str]:
    """What the client was given: everything after the fixed script's four parameters."""
    start = call.index("sh", call.index("-c") + 2)
    return list(call[start + 5 :])


class TestDumpingADatabase:
    """The dump runs the engine's own client inside the service's container."""

    def test_postgres_is_dumped_in_custom_format_to_a_compressed_file(
        self, host: FakeHost, runner: RecordingRunner, tmp_path: Path
    ) -> None:
        runner.script(["docker"], stdout="PGDMP")

        written = dump_stack_database(host, POSTGRES, tmp_path / "dumps")

        assert written == tmp_path / "dumps" / "stack-postgres-postgres-proggest.dump.gz"
        assert written.read_text() == "PGDMP"
        assert runner.compressed[written] is True
        (call,) = exec_calls(runner)
        assert call[:7] == (
            "docker",
            "compose",
            "-p",
            "proggest",
            "-f",
            str(host.app_path / "docker-compose.prod.yml"),
            "exec",
        )
        assert call[7:9] == ("-T", "postgres")
        assert client_args(call) == ["-Fc", "-Z", "0", "-U", "proggest", "-d", "proggest"]

    def test_the_password_is_in_no_command_line_and_no_environment(
        self, host: FakeHost, runner: RecordingRunner, tmp_path: Path
    ) -> None:
        for database in (POSTGRES, MYSQL, MARIADB, MONGO):
            dump_stack_database(host, database, tmp_path / database.engine)

        for call in runner.calls:
            assert not any(DB_PASSWORD in part for part in call)
        # The client reads it from the container's own environment; Noust builds none.
        assert all(env is None for env in runner.envs)

    def test_the_script_is_fixed_and_the_values_are_positional(
        self, host: FakeHost, runner: RecordingRunner, tmp_path: Path
    ) -> None:
        dump_stack_database(host, POSTGRES, tmp_path)
        dump_stack_database(host, StackDatabase("postgres", "postgres", "other", "bob"), tmp_path)

        first, second = exec_calls(runner)
        script_of = lambda call: call[call.index("-c") + 1]  # noqa: E731
        assert script_of(first) == script_of(second) == CLIENT_SCRIPT
        assert "proggest" not in CLIENT_SCRIPT and "bob" not in CLIENT_SCRIPT

    def test_mysql_is_dumped_in_one_transaction_as_the_application_user(
        self, host: FakeHost, runner: RecordingRunner, tmp_path: Path
    ) -> None:
        dump_stack_database(host, MYSQL, tmp_path)

        (call,) = exec_calls(runner)
        assert client_args(call) == [
            "--single-transaction",
            "--no-tablespaces",
            "-u",
            "shop",
            "shop",
        ]
        assert "mysqldump" in call

    def test_root_dumps_the_routines_and_every_database_when_none_is_named(
        self, host: FakeHost, runner: RecordingRunner, tmp_path: Path
    ) -> None:
        dump_stack_database(host, MYSQL_ALL, tmp_path)

        (call,) = exec_calls(runner)
        assert client_args(call) == [
            "--single-transaction",
            "--no-tablespaces",
            "--routines",
            "--events",
            "-u",
            "root",
            "--all-databases",
        ]
        assert (tmp_path / "stack-db-mysql-all.sql.gz").is_file()

    def test_mariadb_uses_whichever_client_name_the_image_ships(
        self, host: FakeHost, runner: RecordingRunner, tmp_path: Path
    ) -> None:
        dump_stack_database(host, MARIADB, tmp_path)

        (call,) = exec_calls(runner)
        assert "mariadb-dump,mysqldump" in call
        assert "--no-tablespaces" not in call

    def test_mongo_dumps_one_archive_of_the_named_database(
        self, host: FakeHost, runner: RecordingRunner, tmp_path: Path
    ) -> None:
        dump_stack_database(host, MONGO, tmp_path)

        (call,) = exec_calls(runner)
        assert client_args(call) == ["--archive", "--db", "shop"]
        assert (tmp_path / "stack-db-mongo-shop.archive.gz").is_file()

    def test_mongo_without_a_database_dumps_them_all(
        self, host: FakeHost, runner: RecordingRunner, tmp_path: Path
    ) -> None:
        dump_stack_database(host, MONGO_OPEN, tmp_path)

        (call,) = exec_calls(runner)
        assert client_args(call) == ["--archive"]

    def test_a_dump_that_fails_stops_with_the_engines_words_and_the_way_to_switch_it_off(
        self, host: FakeHost, runner: RecordingRunner, tmp_path: Path
    ) -> None:
        runner.script(
            ["docker"],
            exit_code=1,
            stderr='service "postgres" is not running',
        )

        with pytest.raises(StackBackupError) as raised:
            dump_stack_database(host, POSTGRES, tmp_path)

        error = raised.value
        assert "postgres" in error.message and "proggest" in error.message
        assert error.output == 'service "postgres" is not running'
        assert "noust app backup-before-update proggest.es off" in error.details
        # Nothing half-written is left to be taken for a backup.
        assert not list(tmp_path.glob("stack-*"))

    def test_a_missing_docker_is_that_same_error(
        self, host: FakeHost, runner: RecordingRunner, tmp_path: Path
    ) -> None:
        runner.script(["docker"], exit_code=127, stderr="docker: command not found")

        with pytest.raises(StackBackupError) as raised:
            dump_stack_database(host, POSTGRES, tmp_path)

        assert raised.value.output == "docker: command not found"


class TestPuttingADumpBack:
    """A restore needs the service up and the application stopped, and leaves things as it found them."""

    @pytest.fixture
    def dump(self, tmp_path: Path) -> Path:
        path = tmp_path / "stack-postgres-postgres-proggest.dump.gz"
        path.write_bytes(gzip.compress(b"PGDMP-contents"))
        return path

    def test_postgres_is_restored_over_what_is_there_in_one_transaction(
        self, host: FakeHost, runner: RecordingRunner, dump: Path
    ) -> None:
        runner.running.add("postgres")

        restore_stack_database(host, POSTGRES, dump, sleep=lambda _s: None)

        restores = [call for call in exec_calls(runner) if "pg_restore" in call]
        assert len(restores) == 1
        assert client_args(restores[0]) == [
            "--single-transaction",
            "--clean",
            "--if-exists",
            "-U",
            "proggest",
            "-d",
            "proggest",
        ]
        assert restores[0][7:9] == ("-T", "postgres")

    def test_the_dump_reaches_the_client_decompressed_and_through_stdin(
        self, host: FakeHost, runner: RecordingRunner, dump: Path
    ) -> None:
        runner.running.add("postgres")

        restore_stack_database(host, POSTGRES, dump, sleep=lambda _s: None)

        assert runner.stdin_contents == [b"PGDMP-contents"]
        assert not any(str(dump) in part for call in runner.calls for part in call)

    def test_a_dump_that_is_not_compressed_is_passed_as_it_is(
        self, host: FakeHost, runner: RecordingRunner, tmp_path: Path
    ) -> None:
        runner.running.add("postgres")
        plain = tmp_path / "plain.dump"
        plain.write_bytes(b"PGDMP-plain")

        restore_stack_database(host, POSTGRES, plain, sleep=lambda _s: None)

        assert runner.stdin_contents == [b"PGDMP-plain"]

    def test_a_service_that_was_not_running_is_started_alone_and_stopped_again(
        self, host: FakeHost, runner: RecordingRunner, dump: Path
    ) -> None:
        restore_stack_database(host, POSTGRES, dump, sleep=lambda _s: None)

        verbs = [
            call[call.index("-f") + 2 : call.index("-f") + 6]
            for call in runner.calls
            if "-f" in call and "exec" not in call
        ]
        started = [call for call in runner.calls if "up" in call]
        stopped = [call for call in runner.calls if "stop" in call]
        assert len(started) == 1 and started[0][-4:] == ("up", "-d", "--no-deps", "postgres")
        assert len(stopped) == 1 and stopped[0][-2:] == ("stop", "postgres")
        order = [
            "up" if "up" in call else "stop" if "stop" in call else "exec"
            for call in runner.calls
            if "up" in call or "stop" in call or "pg_restore" in call
        ]
        assert order == ["up", "exec", "stop"], verbs

    def test_a_service_that_was_running_is_left_running(
        self, host: FakeHost, runner: RecordingRunner, dump: Path
    ) -> None:
        runner.running.add("postgres")

        restore_stack_database(host, POSTGRES, dump, sleep=lambda _s: None)

        assert not any("up" in call or "stop" in call for call in runner.calls)

    def test_it_waits_for_the_engine_to_accept_connections(
        self, host: FakeHost, runner: RecordingRunner, dump: Path
    ) -> None:
        runner.running.add("postgres")
        runner.script(["docker"], exit_code=0)
        refusals = {"left": 2}
        original = runner.run

        def flaky(argv, **kwargs):  # type: ignore[no-untyped-def]
            if "pg_isready" in argv and refusals["left"]:
                refusals["left"] -= 1
                result = original(argv, **kwargs)
                from dataclasses import replace

                return replace(result, exit_code=2, stderr="no response")
            return original(argv, **kwargs)

        runner.run = flaky  # type: ignore[method-assign]
        pauses: list[float] = []

        restore_stack_database(host, POSTGRES, dump, sleep=pauses.append)

        assert pauses == [2.0, 2.0]
        assert any("pg_restore" in call for call in runner.calls)

    def test_an_engine_that_never_answers_is_an_error_and_nothing_is_restored(
        self, host: FakeHost, runner: RecordingRunner, dump: Path
    ) -> None:
        runner.running.add("postgres")
        runner.script(["docker", "compose", "-p", "proggest"], exit_code=0)
        original = runner.run

        def never(argv, **kwargs):  # type: ignore[no-untyped-def]
            result = original(argv, **kwargs)
            if "pg_isready" in argv:
                from dataclasses import replace

                return replace(result, exit_code=2, stderr="no response")
            return result

        runner.run = never  # type: ignore[method-assign]

        with pytest.raises(BackupError) as raised:
            restore_stack_database(host, POSTGRES, dump, sleep=lambda _s: None)

        assert "does not accept connections" in raised.value.message
        assert not any("pg_restore" in call for call in runner.calls)

    def test_a_restore_that_fails_says_what_the_engine_said_and_still_stops_what_it_started(
        self, host: FakeHost, runner: RecordingRunner, dump: Path
    ) -> None:
        runner.script(["docker", "compose", "-p", "proggest"], exit_code=0)
        original = runner.run

        def failing(argv, **kwargs):  # type: ignore[no-untyped-def]
            result = original(argv, **kwargs)
            if "pg_restore" in argv:
                from dataclasses import replace

                return replace(result, exit_code=1, stderr="pg_restore: error: could not execute")
            return result

        runner.run = failing  # type: ignore[method-assign]

        with pytest.raises(BackupError) as raised:
            restore_stack_database(host, POSTGRES, dump, sleep=lambda _s: None)

        assert raised.value.output == "pg_restore: error: could not execute"
        assert "proggest" in raised.value.message
        assert any("stop" in call for call in runner.calls)

    def test_mysql_is_restored_through_the_client_with_the_dump_on_stdin(
        self, host: FakeHost, runner: RecordingRunner, tmp_path: Path
    ) -> None:
        runner.running.add("db")
        dump = tmp_path / "stack-db-mysql-shop.sql.gz"
        dump.write_bytes(gzip.compress(b"CREATE TABLE t (id int);"))

        restore_stack_database(host, MYSQL, dump, sleep=lambda _s: None)

        (restore,) = [call for call in exec_calls(runner) if "mysql" in call and "-u" in call]
        assert client_args(restore) == ["-u", "shop", "shop"]
        assert runner.stdin_contents == [b"CREATE TABLE t (id int);"]

    def test_mongo_is_restored_from_its_archive_dropping_what_is_there(
        self, host: FakeHost, runner: RecordingRunner, tmp_path: Path
    ) -> None:
        runner.running.add("db")
        dump = tmp_path / "stack-db-mongo-shop.archive.gz"
        dump.write_bytes(gzip.compress(b"archive"))

        restore_stack_database(host, MONGO, dump, sleep=lambda _s: None)

        (restore,) = [call for call in exec_calls(runner) if "mongorestore" in call]
        assert client_args(restore) == ["--archive", "--drop"]


@pytest.mark.allow_subprocess
class TestTheScriptThatRunsInsideTheContainer:
    """
    The script is the part no fake can vouch for, so it runs here, for real,
    under ``sh``, against stand-ins for the clients, through the command runner.
    """

    @pytest.fixture
    def clients(self, tmp_path: Path) -> Path:
        """A directory of fake clients that report what they were given."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        # printf, not echo: dash's echo interprets backslashes, which would hide what was written.
        report = (
            "#!/bin/sh\n"
            "say() { printf '%s\\n' \"$1\"; }\n"
            'say "program=$(basename "$0")"\n'
            'say "args=$*"\n'
            'say "PGPASSWORD=${PGPASSWORD:-}"\n'
            'say "MYSQL_PWD=${MYSQL_PWD:-}"\n'
            "previous=\n"
            'for argument in "$@"; do\n'
            '  if [ "$previous" = --config ]; then\n'
            '    say "config=$(cat "$argument")"; say "config_path=$argument"\n'
            "  fi\n"
            "  previous=$argument\n"
            "done\n"
        )
        for name in ("pg_dump", "mysqldump", "mysql", "mongodump", "mongorestore", "pg_isready"):
            path = bin_dir / name
            path.write_text(report)
            path.chmod(0o755)
        return bin_dir

    def run_client(
        self, clients: Path, database: StackDatabase, action: str, environment: dict[str, str]
    ):
        import os

        from noust.core.runner import SubprocessRunner

        env = {"PATH": f"{clients}:{os.environ['PATH']}", **environment}
        return SubprocessRunner().run(client_command(database, action), env=env, timeout=30)  # type: ignore[arg-type]

    @staticmethod
    def report(stdout: str) -> dict[str, str]:
        return dict(line.split("=", 1) for line in stdout.splitlines() if "=" in line)

    def test_postgres_gets_the_password_from_the_containers_environment(
        self, clients: Path
    ) -> None:
        result = self.run_client(clients, POSTGRES, "dump", {"POSTGRES_PASSWORD": "s3cret"})

        report = self.report(result.stdout)
        assert result.success, result.stderr
        assert report["program"] == "pg_dump"
        assert report["PGPASSWORD"] == "s3cret"
        assert "s3cret" not in report["args"]

    def test_a_password_in_a_secret_file_is_read_from_the_file(
        self, clients: Path, tmp_path: Path
    ) -> None:
        secret = tmp_path / "db_password"
        secret.write_text("from-a-file\n")

        result = self.run_client(clients, POSTGRES, "dump", {"POSTGRES_PASSWORD_FILE": str(secret)})

        assert self.report(result.stdout)["PGPASSWORD"] == "from-a-file"

    def test_no_password_anywhere_runs_the_client_without_one(self, clients: Path) -> None:
        result = self.run_client(clients, POSTGRES, "dump", {})

        assert result.success
        assert self.report(result.stdout)["PGPASSWORD"] == ""

    def test_mysql_receives_its_password_as_mysql_pwd_by_user_kind(self, clients: Path) -> None:
        app_user = self.run_client(
            clients, MYSQL, "dump", {"MYSQL_PASSWORD": "app", "MYSQL_ROOT_PASSWORD": "root"}
        )
        root = self.run_client(
            clients, MYSQL_ALL, "dump", {"MYSQL_PASSWORD": "app", "MYSQL_ROOT_PASSWORD": "root"}
        )

        assert self.report(app_user.stdout)["MYSQL_PWD"] == "app"
        assert self.report(root.stdout)["MYSQL_PWD"] == "root"

    def test_the_first_client_name_the_image_has_is_used(self, clients: Path) -> None:
        # Only mysqldump exists in these stand-ins: mariadb-dump is tried first.
        result = self.run_client(clients, MARIADB, "dump", {"MARIADB_PASSWORD": "x"})

        assert self.report(result.stdout)["program"] == "mysqldump"

    def test_a_client_the_image_does_not_have_is_said_so_and_fails(self, clients: Path) -> None:
        (clients / "pg_dump").unlink()

        result = self.run_client(clients, POSTGRES, "dump", {})

        assert result.exit_code == 127
        assert "pg_dump" in result.stderr

    def test_mongo_gets_its_password_in_a_private_file_that_is_removed(self, clients: Path) -> None:
        import os

        result = self.run_client(
            clients, MONGO, "dump", {"MONGO_INITDB_ROOT_PASSWORD": 'pa"ss\\word'}
        )

        report = self.report(result.stdout)
        assert result.success, result.stderr
        assert "pa" not in report["args"], "the password must not be on the command line"
        assert "--username root" in report["args"]
        assert report["config"] == 'password: "pa\\"ss\\\\word"'
        assert not os.path.exists(report["config_path"])

    def test_mongo_without_authentication_gets_no_config_and_no_username(
        self, clients: Path
    ) -> None:
        result = self.run_client(clients, MONGO_OPEN, "dump", {})

        report = self.report(result.stdout)
        assert result.success, result.stderr
        assert "--username" not in report["args"] and "config" not in report


def config_call(runner: FakeRunner) -> tuple[str, ...]:
    """The ``docker compose config`` command that ran."""
    (call,) = [call for call in runner.calls if "config" in call]
    return call


class TestReadingTheStacksConfiguration:
    """``docker compose config --format json``, through the runner, in the application's directory."""

    def test_it_asks_compose_for_the_resolved_configuration(
        self, host: FakeHost, runner: RecordingRunner, proggest_config: dict[str, Any]
    ) -> None:
        runner.script(["docker"], stdout=json.dumps(proggest_config))

        config = read_compose_config(host)

        assert config == proggest_config
        assert config_call(runner)[-3:] == ("config", "--format", "json")

    def test_a_stack_compose_cannot_resolve_is_an_error_with_compose_own_words(
        self, host: FakeHost, runner: RecordingRunner
    ) -> None:
        runner.script(
            ["docker"],
            exit_code=1,
            stderr="required variable PERSONNEL_ENCRYPTION_KEY is missing a value",
        )

        with pytest.raises(StackBackupError) as raised:
            read_compose_config(host)

        assert (
            raised.value.output == "required variable PERSONNEL_ENCRYPTION_KEY is missing a value"
        )
        assert "noust app backup-before-update proggest.es off" in raised.value.details

    @pytest.mark.parametrize("stdout", ["", "not json", "[]", "null"])
    def test_output_that_is_not_a_configuration_is_an_error(
        self, host: FakeHost, runner: RecordingRunner, stdout: str
    ) -> None:
        runner.script(["docker"], stdout=stdout)

        with pytest.raises(StackBackupError):
            read_compose_config(host)


class TestDumpingEveryDatabaseOfAStack:
    """What goes into the backup: one file and one manifest entry per database."""

    def test_proggest_yields_one_postgres_dump_with_its_manifest_entry(
        self,
        host: FakeHost,
        runner: RecordingRunner,
        proggest_config: dict[str, Any],
        tmp_path: Path,
    ) -> None:
        runner.script(["docker"], stdout=json.dumps(proggest_config))
        runner.script(exec_prefix(host), stdout="PGDMP")

        entries = dump_stack_databases(
            host, tmp_path / "databases", archive_dir="wasm-backup/databases", declared="auto"
        )

        (entry,) = entries
        assert entry["engine"] == "postgres"
        assert entry["name"] == "proggest"
        assert (
            entry["archive_path"]
            == "wasm-backup/databases/stack-postgres-postgres-proggest.dump.gz"
        )
        assert entry["size_bytes"] == len("PGDMP")
        assert entry["stack"] == {
            "service": "postgres",
            "engine": "postgres",
            "database": "proggest",
            "user": "proggest",
        }
        assert (tmp_path / "databases" / "stack-postgres-postgres-proggest.dump.gz").is_file()
        assert StackDatabase.from_entry(entry["stack"]) == POSTGRES

    def test_a_stack_without_databases_dumps_nothing(
        self, host: FakeHost, runner: RecordingRunner, tmp_path: Path
    ) -> None:
        runner.script(["docker"], stdout=json.dumps({"services": {"web": {"image": "nginx"}}}))

        entries = dump_stack_databases(host, tmp_path, archive_dir="p", declared="auto")

        assert entries == []
        assert not exec_calls(runner)

    def test_off_does_not_even_read_the_configuration(
        self, host: FakeHost, runner: RecordingRunner, tmp_path: Path
    ) -> None:
        entries = dump_stack_databases(host, tmp_path, archive_dir="p", declared="off")

        assert entries == []
        assert runner.calls == []

    def test_the_second_dump_failing_fails_the_whole_copy(
        self, host: FakeHost, runner: RecordingRunner, tmp_path: Path
    ) -> None:
        config = {
            "services": {
                "a": service("postgres:16"),
                "b": service("mysql:8", MYSQL_ROOT_PASSWORD="x"),
            }
        }
        runner.script(["docker"], stdout=json.dumps(config))
        runner.script(exec_prefix(host), stdout="dump")
        original = runner.capture_to_file
        seen = {"n": 0}

        def second_fails(argv, destination, **kwargs):  # type: ignore[no-untyped-def]
            seen["n"] += 1
            if seen["n"] == 2:
                from noust.core.runner import CommandResult

                return CommandResult(argv=tuple(argv), exit_code=1, stderr="mysqldump: Got error")
            return original(argv, destination, **kwargs)

        runner.capture_to_file = second_fails  # type: ignore[method-assign]

        with pytest.raises(StackBackupError) as raised:
            dump_stack_databases(host, tmp_path, archive_dir="p", declared="auto")

        assert "mysql" in raised.value.message
        assert raised.value.output == "mysqldump: Got error"


class TestFindingTheStack:
    """The deployer that talks to a stack is built the way an update builds it."""

    def test_it_finds_the_compose_file_in_the_application_directory(
        self, tmp_path: Path, runner: RecordingRunner
    ) -> None:
        app = tmp_path / "apps" / "proggest-es"
        app.mkdir(parents=True)
        (app / "docker-compose.prod.yml").write_text("services: {}\n")

        host = stack_host_for("proggest.es", app, runner=runner)

        argv = host._compose("config")
        assert str(app / "docker-compose.prod.yml") in argv
        assert host.domain == "proggest.es" and host.app_path == app

    def test_the_file_a_deployment_chose_is_read_from_its_unit(
        self, tmp_path: Path, runner: RecordingRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = tmp_path / "apps" / "shop-example-com"
        (app / "deploy").mkdir(parents=True)
        (app / "deploy" / "compose.yml").write_text("services: {}\n")
        (app / "docker-compose.yml").write_text("services: {}\n")
        monkeypatch.setattr(
            "noust.managers.service_manager.ServiceManager.get_service_config",
            lambda self, name: 'Environment="COMPOSE_FILE=deploy/compose.yml"\n',
        )

        host = stack_host_for("shop.example.com", app, runner=runner)

        assert str(app / "deploy" / "compose.yml") in host._compose("config")

    def test_a_legacy_stack_is_read_from_the_unit_named_after_its_directory(
        self, tmp_path: Path, runner: RecordingRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = tmp_path / "apps" / "wasm-shop-example-com"
        (app / "deploy").mkdir(parents=True)
        (app / "deploy" / "compose.yml").write_text("services: {}\n")
        units = {"wasm-shop-example-com": 'Environment="COMPOSE_FILE=deploy/compose.yml"\n'}
        monkeypatch.setattr(
            "noust.managers.service_manager.ServiceManager.get_service_config",
            lambda self, name: units.get(name),
        )

        host = stack_host_for("shop.example.com", app, runner=runner)

        assert str(app / "deploy" / "compose.yml") in host._compose("config")

    def test_an_adopted_stack_is_read_from_the_unit_named_after_its_domain(
        self, tmp_path: Path, runner: RecordingRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Review E2: /opt/proggest runs as proggest-es.service, with its own compose file."""
        from noust.core.store import App, NoustStore

        app = tmp_path / "opt" / "proggest"
        (app / "deploy").mkdir(parents=True)
        (app / "deploy" / "compose.yml").write_text("services: {}\n")
        NoustStore.reset_instance()
        store = NoustStore(tmp_path / "noust.db")
        try:
            store.create_app(
                App(
                    domain="proggest.es",
                    app_type="docker-compose",
                    app_path=str(app),
                    compose_project="proggest",
                )
            )
            units = {"proggest-es": 'Environment="COMPOSE_FILE=deploy/compose.yml"\n'}
            monkeypatch.setattr(
                "noust.managers.service_manager.ServiceManager.get_service_config",
                lambda self, name: units.get(name),
            )

            host = stack_host_for("proggest.es", app, runner=runner)

            argv = host._compose("config")
            assert str(app / "deploy" / "compose.yml") in argv
            assert argv[argv.index("-p") + 1] == "proggest"
        finally:
            NoustStore.reset_instance()

    def test_a_directory_without_a_compose_file_is_an_error_with_a_fix(
        self, tmp_path: Path, runner: RecordingRunner
    ) -> None:
        app = tmp_path / "apps" / "empty"
        app.mkdir(parents=True)

        with pytest.raises(StackBackupError) as raised:
            stack_host_for("empty.example.com", app, runner=runner)

        assert "empty.example.com" in raised.value.message
        assert "backup-before-update empty.example.com off" in raised.value.details


class TestSwitchingTheCopyOff:
    """``noust app backup-before-update``: one setter, audited."""

    @pytest.fixture
    def store(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from noust.core.store import App, NoustStore

        NoustStore.reset_instance()
        instance = NoustStore(tmp_path / "noust.db")
        monkeypatch.setattr("noust.managers.stack_databases.get_store", lambda: instance)
        instance.create_app(
            App(domain="proggest.es", app_type="docker-compose", app_path=str(tmp_path / "p"))
        )
        instance.create_app(
            App(domain="site.example.com", app_type="nodejs", app_path=str(tmp_path / "s"))
        )
        yield instance
        NoustStore.reset_instance()

    @pytest.fixture
    def audited(self, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        monkeypatch.setattr(
            "noust.core.audit.record",
            lambda event, **kwargs: events.append({"event": event, **kwargs}),
        )
        return events

    def test_it_is_on_until_it_is_switched_off(self, store) -> None:
        assert store.get_app("proggest.es").backup_before_update is True

        previous = set_backup_before_update("proggest.es", False)

        assert previous is True
        assert store.get_app("proggest.es").backup_before_update is False

    def test_it_can_be_switched_on_again(self, store) -> None:
        set_backup_before_update("proggest.es", False)

        previous = set_backup_before_update("proggest.es", True)

        assert previous is False
        assert store.get_app("proggest.es").backup_before_update is True

    def test_the_change_is_audited_with_what_it_was(self, store, audited) -> None:
        set_backup_before_update("proggest.es", False)

        [event] = audited
        assert event["event"] == "apps.backup_before_update"
        assert event["target"] == "app:proggest.es"
        assert event["details"] == {"enabled": False, "previous": True}

    def test_an_unknown_application_is_an_error_that_says_where_to_look(self, store) -> None:
        from noust.core.exceptions import NoustError

        with pytest.raises(NoustError) as raised:
            set_backup_before_update("nobody.example.com", False)

        assert "nobody.example.com" in raised.value.message
        assert "noust list" in raised.value.details

    def test_an_application_that_is_not_a_compose_stack_has_no_copy_to_switch(
        self, store, audited
    ) -> None:
        from noust.core.exceptions import ValidationError

        with pytest.raises(ValidationError) as raised:
            set_backup_before_update("site.example.com", False)

        assert "Docker Compose" in raised.value.message
        assert audited == []


class TestConfigIsAReadOnlyProbe:
    """A dry run may read the stack's configuration to say what an update would dump."""

    @pytest.mark.parametrize(
        "argv",
        [
            [
                "docker",
                "compose",
                "-f",
                "/srv/shop/docker-compose.yml",
                "config",
                "--format",
                "json",
            ],
            [
                "docker",
                "compose",
                "-p",
                "shop",
                "-f",
                "/srv/shop/compose.yml",
                "config",
                "--format",
                "json",
            ],
            [
                "docker",
                "compose",
                "-f",
                "/srv/c.yml",
                "--profile",
                "web",
                "config",
                "--format",
                "json",
            ],
        ],
    )
    def test_config_is_read_only(self, argv):
        from noust.core.runner import forget_declared_probes, is_read_only

        forget_declared_probes()
        assert is_read_only(argv)

    def test_anything_else_of_compose_is_not(self):
        from noust.core.runner import forget_declared_probes, is_read_only

        forget_declared_probes()
        assert not is_read_only(["docker", "compose", "-f", "/srv/c.yml", "up", "-d"])
        assert not is_read_only(
            ["docker", "compose", "-f", "/srv/c.yml", "config", "--format", "json", "-o", "/etc/x"]
        )
