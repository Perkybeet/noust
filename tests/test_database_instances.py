# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Database instances (item 67): the databases Docker runs, managed by the same code.

What is defended:

- **Which image is a database** is decided by its repository, against the
  images the fleet really runs: ``zabbix/zabbix-web-nginx-mysql`` is not MySQL
  (item 72).
- **A key names an instance** and is the only thing split or built by hand,
  in one module; it fits a URL segment, and a dump's file name never lets one
  service claim another's.
- **Discovery** reads ``docker ps`` and ``docker inspect`` and keeps the
  names of the variables, never a secret value.
- **Container mode runs the same SQL inside the container**: ``docker exec``,
  ``-u`` instead of ``runuser``, ``-e NAME`` with no value, and no secret in
  any argv.
- **The service and the API** resolve an instance key wherever an engine name
  went, and list instances after the host's engines.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import DatabaseEngineError, DatabaseQueryError, ValidationError
from noust.core.runner import FakeRunner
from noust.managers.database.instances import (
    CLIENT_SCRIPT,
    DatabaseInstance,
    assign_apps,
    discover,
    engine_of,
    image_engine,
    instance_key,
    is_instance_key,
    parse_instance_key,
    storage_name,
)
from noust.managers.database.mongodb import MongoDBManager
from noust.managers.database.mysql import MySQLManager
from noust.managers.database.postgres import PostgresManager
from noust.managers.database.redis import RedisManager

PG_PASSWORD = "pg-s3cret-value"
ROOT_PASSWORD = "root-s3cret-value"
APP_PASSWORD = "app-s3cret-value"
REDIS_PASSWORD = "redis-s3cret-value"
SECRETS = (PG_PASSWORD, ROOT_PASSWORD, APP_PASSWORD, REDIS_PASSWORD)


# ==================== Images ====================


class TestWhichImagesAreDatabases:
    """The images of arennalabs and Proggest, and the ones that only look like them."""

    @pytest.mark.parametrize(
        ("image", "engine", "flavour"),
        [
            # arennalabs
            ("postgres:17-alpine", "postgresql", "postgres"),
            ("postgres:16-alpine", "postgresql", "postgres"),
            ("postgres:16", "postgresql", "postgres"),
            ("mysql:8.0", "mysql", "mysql"),
            ("mariadb:10.11", "mysql", "mariadb"),
            ("redis:7-alpine", "redis", "redis"),
            ("redis:8", "redis", "redis"),
            ("docker.io/library/postgres:17", "postgresql", "postgres"),
            ("library/mysql:8.0", "mysql", "mysql"),
            ("postgres:16@sha256:" + "a" * 64, "postgresql", "postgres"),
            # The derived images Noust knows.
            ("postgis/postgis:16-3.4", "postgresql", "postgres"),
            ("timescale/timescaledb:latest-pg16", "postgresql", "postgres"),
            ("timescale/timescaledb-ha:pg16", "postgresql", "postgres"),
            ("pgvector/pgvector:pg16", "postgresql", "postgres"),
            ("valkey/valkey:8", "redis", "valkey"),
            ("bitnami/postgresql:16", "postgresql", "postgres"),
            ("bitnami/mysql:8.0", "mysql", "mysql"),
            ("bitnami/mariadb:11", "mysql", "mariadb"),
            ("bitnami/redis:7.2", "redis", "redis"),
            ("bitnami/mongodb:7.0", "mongodb", "mongo"),
            ("mongo:7", "mongodb", "mongo"),
        ],
    )
    def test_database_images(self, image: str, engine: str, flavour: str) -> None:
        match = image_engine(image)
        assert match is not None
        assert (match.engine, match.flavour) == (engine, flavour)

    @pytest.mark.parametrize(
        "image",
        [
            "zabbix/zabbix-web-nginx-mysql:alpine-7.0-latest",
            "zabbix/zabbix-server-mysql:alpine-7.0-latest",
            "my-postgres-wrapper:1",
            "ghcr.io/acme/postgres:16",
            "localhost:5000/team/postgres",
            "mysql/mysql-server:8.0",
            "node:22-alpine",
            "3f2a9c1d7b8e",
            "",
            None,
        ],
    )
    def test_everything_else_is_not(self, image: str | None) -> None:
        assert image_engine(image) is None

    def test_only_docker_hub_library_images_are_official(self) -> None:
        assert image_engine("postgres:16").official is True  # type: ignore[union-attr]
        assert image_engine("bitnami/postgresql").official is False  # type: ignore[union-attr]


# ==================== Keys ====================


class TestKeys:
    """Built and split in one place, and safe in a URL and a file name."""

    def test_a_compose_service(self) -> None:
        key = instance_key("postgresql", project="empleo-arennalabs-com", service="db")
        assert key == "postgresql@empleo-arennalabs-com.db"
        parts = parse_instance_key(key)
        assert (parts.engine, parts.project, parts.service) == (
            "postgresql",
            "empleo-arennalabs-com",
            "db",
        )
        assert str(parts) == key

    def test_a_container_without_project(self) -> None:
        key = instance_key("mysql", container="mariadb_contenedor")
        assert key == "mysql@mariadb_contenedor"
        assert parse_instance_key(key).container == "mariadb_contenedor"

    def test_the_host_engine_is_its_name(self) -> None:
        assert instance_key("redis") == "redis"
        assert parse_instance_key("redis").is_host
        assert not is_instance_key("redis")

    @pytest.mark.parametrize("key", ["postgresql@", "postgresql@../etc", "postgresql@a/b", "@x"])
    def test_a_malformed_key_is_refused(self, key: str) -> None:
        with pytest.raises(ValidationError):
            parse_instance_key(key)

    def test_the_engine_of_a_key(self) -> None:
        assert engine_of("postgresql@proggest.postgres") == "postgresql"
        assert engine_of("redis") == "redis"

    def test_storage_names_keep_host_engines_where_they_were(self) -> None:
        assert storage_name("postgresql") == "postgresql"
        assert storage_name("postgresql@proggest.postgres") == "postgresql.proggest.postgres"


# ==================== Discovery ====================


def container(
    name: str,
    image: str,
    *,
    env: list[str] | None = None,
    project: str | None = None,
    service: str | None = None,
    state: str = "running",
    ports: dict[str, Any] | None = None,
    exposed: tuple[str, ...] = (),
    cmd: list[str] | None = None,
) -> dict[str, Any]:
    """One object of ``docker inspect``, as Docker prints it."""
    labels = {}
    if project:
        labels["com.docker.compose.project"] = project
    if service:
        labels["com.docker.compose.service"] = service
    return {
        "Id": f"{name}-id",
        "Name": f"/{name}",
        "Config": {
            "Image": image,
            "Env": [*(env or []), "PATH=/usr/local/bin:/usr/bin"],
            "Labels": labels,
            "ExposedPorts": {port: {} for port in exposed},
            "Cmd": cmd,
            "Entrypoint": None,
        },
        "State": {"Status": state, "Running": state == "running"},
        "NetworkSettings": {"Ports": ports or {}},
        "HostConfig": {"PortBindings": {}},
    }


#: The database containers of arennalabs and Proggest, with one that only looks like one.
FLEET = [
    container(
        "empleo-arenna-db",
        "postgres:17-alpine",
        env=["POSTGRES_USER=empleo", f"POSTGRES_PASSWORD={PG_PASSWORD}", "POSTGRES_DB=empleo"],
        project="empleo-arennalabs-com",
        service="db",
        exposed=("5432/tcp",),
        ports={"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5434"}]},
    ),
    container(
        "arenna_tienda_mysql",
        "mysql:8.0",
        env=[
            f"MYSQL_ROOT_PASSWORD={ROOT_PASSWORD}",
            "MYSQL_DATABASE=tienda",
            "MYSQL_USER=tienda",
            f"MYSQL_PASSWORD={APP_PASSWORD}",
        ],
        project="tienda-arennalabs-com",
        service="mysql",
        exposed=("3306/tcp", "33060/tcp"),
    ),
    container(
        "arenna_tienda_redis",
        "redis:7-alpine",
        project="tienda-arennalabs-com",
        service="redis",
        exposed=("6379/tcp",),
        cmd=["redis-server", "--requirepass", REDIS_PASSWORD],
    ),
    container(
        "zabbix-zabbix-db-1",
        "mysql:8.0",
        env=[
            "MYSQL_RANDOM_ROOT_PASSWORD=yes",
            "MYSQL_USER=zabbix",
            f"MYSQL_PASSWORD={APP_PASSWORD}",
        ],
        project="zabbix",
        service="zabbix-db",
        state="exited",
        exposed=("3306/tcp",),
    ),
    container(
        "mariadb_contenedor",
        "mariadb:10.11",
        env=[f"MARIADB_ROOT_PASSWORD={ROOT_PASSWORD}"],
        exposed=("3306/tcp",),
        ports={"3306/tcp": [{"HostIp": "0.0.0.0", "HostPort": "3307"}]},  # noqa: S104
    ),
    container(
        "proggest-postgres-1",
        "postgres:16-alpine",
        env=["POSTGRES_USER=proggest", f"POSTGRES_PASSWORD={PG_PASSWORD}", "POSTGRES_DB=proggest"],
        project="proggest",
        service="postgres",
        exposed=("5432/tcp",),
    ),
]

PS_LINES = "\n".join(
    [
        *(f"{entry['Id']}\t{entry['Config']['Image']}" for entry in FLEET),
        "zabbix-web-id\tzabbix/zabbix-web-nginx-mysql:alpine-7.0-latest",
        "zabbix-server-id\tzabbix/zabbix-server-mysql:alpine-7.0-latest",
    ]
)


@pytest.fixture
def fleet(runner: FakeRunner) -> FakeRunner:
    """A Docker that runs :data:`FLEET`."""
    runner.script(["docker", "ps"], stdout=PS_LINES + "\n")
    runner.script(["docker", "inspect"], stdout=json.dumps(FLEET))
    return runner


def by_key(found: list[DatabaseInstance]) -> dict[str, DatabaseInstance]:
    return {item.key: item for item in found}


class TestDiscovery:
    """``docker ps`` and ``docker inspect``, through the runner."""

    def test_every_database_container_is_found_by_its_service(self, fleet: FakeRunner) -> None:
        found = by_key(discover(fleet))

        assert sorted(found) == [
            "mysql@mariadb_contenedor",
            "mysql@tienda-arennalabs-com.mysql",
            "mysql@zabbix.zabbix-db",
            "postgresql@empleo-arennalabs-com.db",
            "postgresql@proggest.postgres",
            "redis@tienda-arennalabs-com.redis",
        ]

    def test_the_applications_that_only_look_like_databases_are_not_even_inspected(
        self, fleet: FakeRunner
    ) -> None:
        discover(fleet)

        (inspect,) = [call for call in fleet.calls if call[:2] == ("docker", "inspect")]
        assert "zabbix-web-id" not in inspect and "zabbix-server-id" not in inspect

    def test_ports_state_and_who_signs_in(self, fleet: FakeRunner) -> None:
        found = by_key(discover(fleet))

        empleo = found["postgresql@empleo-arennalabs-com.db"]
        assert (empleo.port, empleo.default_port, empleo.running) == (5434, 5432, True)
        assert empleo.admin_user == "empleo" and empleo.access == "full"
        mariadb = found["mysql@mariadb_contenedor"]
        assert (mariadb.flavour, mariadb.port, mariadb.admin_user) == ("mariadb", 3307, "root")
        assert found["mysql@tienda-arennalabs-com.mysql"].port == 3306

    def test_a_random_root_password_leaves_the_application_account_and_limited_access(
        self, fleet: FakeRunner
    ) -> None:
        zabbix = by_key(discover(fleet))["mysql@zabbix.zabbix-db"]

        assert zabbix.admin_user == "zabbix"
        assert zabbix.access == "limited"
        assert not zabbix.running

    def test_no_secret_value_is_kept_or_rendered(self, fleet: FakeRunner) -> None:
        for instance in discover(fleet):
            rendered = json.dumps(instance.to_dict()) + repr(instance)
            assert not any(secret in rendered for secret in SECRETS)
            assert not any(
                secret in value for value in instance.settings.values() for secret in SECRETS
            )

    def test_without_docker_there_are_none(self, runner: FakeRunner) -> None:
        runner.only_knows("psql")

        assert discover(runner) == []
        assert runner.calls == []

    def test_a_docker_that_does_not_answer_says_so(self, runner: FakeRunner) -> None:
        runner.script(["docker", "ps"], exit_code=1, stderr="Cannot connect to the Docker daemon")

        with pytest.raises(DatabaseQueryError) as raised:
            discover(runner)

        assert raised.value.output == "Cannot connect to the Docker daemon"

    def test_a_stack_belongs_to_the_application_whose_project_it_is(
        self, fleet: FakeRunner
    ) -> None:
        class App:
            def __init__(self, domain: str, compose_project: str | None = None) -> None:
                self.domain, self.compose_project = domain, compose_project

        owned = by_key(
            assign_apps(
                discover(fleet),
                [App("empleo.arennalabs.com"), App("proggest.es", compose_project="proggest")],
            )
        )

        assert owned["postgresql@empleo-arennalabs-com.db"].app == "empleo.arennalabs.com"
        assert owned["postgresql@proggest.postgres"].app == "proggest.es"
        assert owned["mysql@mariadb_contenedor"].app is None


# ==================== Container mode ====================


@pytest.fixture
def instances(fleet: FakeRunner) -> dict[str, DatabaseInstance]:
    found = by_key(discover(fleet))
    fleet.calls.clear()
    fleet.envs.clear()
    return found


def no_secret_in_any_argv(runner: FakeRunner) -> None:
    for call in runner.calls:
        for part in call:
            assert not any(secret in part for secret in SECRETS), call


class TestPostgresInAContainer:
    """psql runs inside the container, as postgres, signed in as the image's user."""

    def test_a_listing_runs_in_the_container_with_u_instead_of_runuser(
        self, fleet: FakeRunner, instances: dict[str, DatabaseInstance]
    ) -> None:
        fleet.script(["docker", "exec"], stdout="empleo|UTF8|8192|empleo\npostgres|UTF8|1|x\n")
        manager = PostgresManager().bind(instances["postgresql@empleo-arennalabs-com.db"])

        databases = manager.list_databases()

        assert [db.name for db in databases] == ["empleo"]
        assert databases[0].engine == "postgresql@empleo-arennalabs-com.db"
        (call,) = fleet.calls
        assert call[:8] == (
            "docker",
            "exec",
            "-i",
            "-u",
            "postgres",
            "-e",
            "PGUSER=empleo",
            "empleo-arenna-db",
        )
        assert call[8:11] == ("sh", "-c", CLIENT_SCRIPT)
        assert call[11:16] == (
            "sh",
            "env",
            "PGPASSWORD",
            "POSTGRES_PASSWORD,POSTGRESQL_PASSWORD,POSTGRESQL_POSTGRES_PASSWORD",
            "psql",
        )
        assert "runuser" not in call
        no_secret_in_any_argv(fleet)

    def test_the_read_only_login_forwards_its_password_by_name_only(
        self, fleet: FakeRunner, instances: dict[str, DatabaseInstance], tmp_path: Path
    ) -> None:
        fleet.script(["docker", "exec"], stdout="1\n")
        manager = PostgresManager().bind(instances["postgresql@empleo-arennalabs-com.db"])

        manager.execute_query("empleo", "SELECT 1", read_only=True)

        login = next(call for call in fleet.calls if "wasm_ro_empleo" in call)
        env = fleet.envs[fleet.calls.index(login)]
        assert env is not None and env["PGPASSWORD"]
        position = login.index("PGPASSWORD")
        assert login[position - 1] == "-e"
        # Its own password wins: the container's is not looked for.
        script_at = login.index(CLIENT_SCRIPT)
        assert login[script_at + 4] == ""
        # Inside the container the engine is on its own port, not the published one.
        assert login[login.index("-p") + 1] == "5432"
        assert not any(env["PGPASSWORD"] in part for part in login)
        no_secret_in_any_argv(fleet)

    def test_a_dump_is_captured_from_the_container_under_the_instance_name(
        self, fleet: FakeRunner, instances: dict[str, DatabaseInstance], tmp_path: Path
    ) -> None:
        fleet.script(["docker", "exec"], stdout="1\n")
        manager = PostgresManager().bind(instances["postgresql@empleo-arennalabs-com.db"])
        manager.BACKUP_DIR = tmp_path

        backup = manager.backup("empleo")

        assert backup.path.name.startswith("postgresql.empleo-arennalabs-com.db.empleo-")
        assert backup.engine == "postgresql@empleo-arennalabs-com.db"
        (dump,) = [call for call in fleet.calls if "pg_dump" in call]
        assert dump[:3] == ("docker", "exec", "-i")
        assert [b.database for b in manager.list_backups()] == ["empleo"]

    def test_a_restore_reaches_the_client_on_stdin(
        self, fleet: FakeRunner, instances: dict[str, DatabaseInstance], tmp_path: Path
    ) -> None:
        fleet.script(["docker", "exec"], stdout="")
        manager = PostgresManager().bind(instances["postgresql@empleo-arennalabs-com.db"])
        manager.BACKUP_DIR = tmp_path
        dump = tmp_path / "plain.sql"
        dump.write_text("CREATE TABLE t (id int);\n")

        manager._load_backup("empleo", dump, format="plain")

        (load,) = [call for call in fleet.calls if call[-2:] == ("-f", "-")]
        assert str(dump) not in " ".join(load)

    def test_install_is_refused_with_the_compose_file_as_the_way(
        self, instances: dict[str, DatabaseInstance]
    ) -> None:
        manager = PostgresManager().bind(instances["postgresql@empleo-arennalabs-com.db"])

        with pytest.raises(DatabaseEngineError) as raised:
            manager.install()

        assert "compose file" in raised.value.details

    def test_start_stop_and_restart_are_dockers(
        self, fleet: FakeRunner, instances: dict[str, DatabaseInstance]
    ) -> None:
        manager = PostgresManager().bind(instances["postgresql@empleo-arennalabs-com.db"])

        manager.restart()
        manager.stop()

        assert fleet.calls == [
            ("docker", "restart", "empleo-arenna-db"),
            ("docker", "stop", "empleo-arenna-db"),
        ]
        with pytest.raises(DatabaseEngineError):
            manager.enable()

    def test_its_status_says_where_it_runs(self, fleet: FakeRunner, instances) -> None:
        fleet.script(["docker", "exec"], stdout="psql (PostgreSQL) 17.2\n")
        manager = PostgresManager().bind(instances["postgresql@empleo-arennalabs-com.db"])

        status = manager.get_status()

        assert status["engine"] == "postgresql@empleo-arennalabs-com.db"
        assert (status["kind"], status["container"], status["project"]) == (
            "container",
            "empleo-arenna-db",
            "empleo-arennalabs-com",
        )
        assert (status["version"], status["port"], status["access"]) == ("17.2", 5434, "full")
        assert manager.is_internal_user("empleo")


class TestMySQLInAContainer:
    """No option file on the host: the password is found inside the container."""

    def test_root_signs_in_with_the_root_password_of_the_container(
        self, fleet: FakeRunner, instances: dict[str, DatabaseInstance]
    ) -> None:
        manager = MySQLManager().bind(instances["mysql@tienda-arennalabs-com.mysql"])

        manager.database_exists("tienda")

        (call,) = fleet.calls
        script_at = call.index(CLIENT_SCRIPT)
        assert call[script_at + 2 : script_at + 6] == (
            "env",
            "MYSQL_PWD",
            "MYSQL_ROOT_PASSWORD",
            "mariadb,mysql",
        )
        assert ("-u", "root") == call[script_at + 6 : script_at + 8]
        assert not any("defaults-extra-file" in part for part in call)
        assert manager.DISPLAY_NAME == "MySQL"
        no_secret_in_any_argv(fleet)

    def test_limited_access_signs_in_as_the_application(
        self, fleet: FakeRunner, instances: dict[str, DatabaseInstance]
    ) -> None:
        manager = MySQLManager().bind(instances["mysql@zabbix.zabbix-db"])

        manager.database_exists("zabbix")

        (call,) = fleet.calls
        script_at = call.index(CLIENT_SCRIPT)
        assert call[script_at + 4] == "MYSQL_PASSWORD"
        assert ("-u", "zabbix") == call[script_at + 6 : script_at + 8]

    def test_the_read_only_account_password_is_forwarded_as_mysql_pwd(
        self, fleet: FakeRunner, instances: dict[str, DatabaseInstance]
    ) -> None:
        fleet.script(["docker", "exec"], stdout="tienda\n")
        manager = MySQLManager().bind(instances["mysql@tienda-arennalabs-com.mysql"])

        manager.execute_query("tienda", "SELECT 1", read_only=True)

        login = next(call for call in fleet.calls if any("--user=wasm_ro_" in p for p in call))
        env = fleet.envs[fleet.calls.index(login)]
        assert env is not None and set(env) == {"MYSQL_PWD"}
        assert login[login.index("MYSQL_PWD") - 1] == "-e"
        assert not any(env["MYSQL_PWD"] in part for part in login)

    def test_mariadb_is_named_after_its_image(self, instances) -> None:
        manager = MySQLManager().bind(instances["mysql@mariadb_contenedor"])

        assert manager.DISPLAY_NAME == "MariaDB" and manager.is_mariadb


class TestRedisInAContainer:
    """A --requirepass on the command line reaches redis-cli as REDISCLI_AUTH, never argv."""

    def test_the_password_goes_by_environment(
        self, fleet: FakeRunner, instances: dict[str, DatabaseInstance]
    ) -> None:
        fleet.script(["docker", "exec"], stdout="PONG\n")
        manager = RedisManager().bind(instances["redis@tienda-arennalabs-com.redis"])

        manager.execute_query("0", "PING")

        (call,) = fleet.calls
        assert call[3:5] == ("-e", "REDISCLI_AUTH")
        assert fleet.envs[0] == {"REDISCLI_AUTH": REDIS_PASSWORD}
        assert "redis-cli,valkey-cli" in call
        no_secret_in_any_argv(fleet)

    def test_a_snapshot_restore_says_how_instead(self, instances, tmp_path: Path) -> None:
        manager = RedisManager().bind(instances["redis@tienda-arennalabs-com.redis"])
        snapshot = tmp_path / "dump.rdb"
        snapshot.write_bytes(b"REDIS")

        with pytest.raises(Exception) as raised:
            manager.restore("0", snapshot)

        assert "docker cp" in raised.value.details  # type: ignore[attr-defined]


class TestMongoInAContainer:
    """The root password is read by the shell inside the container."""

    def test_the_shell_preamble_carries_no_password(self, runner: FakeRunner) -> None:
        instance = DatabaseInstance(
            key="mongodb@shop.mongo",
            engine="mongodb",
            flavour="mongo",
            container="shop-mongo-1",
            container_id="m",
            image="mongo:7",
            project="shop",
            service="mongo",
            state="running",
            env_names=frozenset({"MONGO_INITDB_ROOT_USERNAME", "MONGO_INITDB_ROOT_PASSWORD"}),
            settings={"MONGO_INITDB_ROOT_USERNAME": "root"},
        )
        manager = MongoDBManager().bind(instance)

        manager._execute_mongo("db.version()")

        (call,) = runner.calls
        assert "mongosh,mongo" in call
        assert "process.env" in (runner.inputs[0] or "")
        assert runner.envs[0] is None


# ==================== Backups ====================


def test_a_service_named_like_another_never_claims_its_dumps(
    runner: FakeRunner, tmp_path: Path
) -> None:
    def bound(service: str) -> PostgresManager:
        manager = PostgresManager().bind(
            DatabaseInstance(
                key=instance_key("postgresql", project="shop", service=service),
                engine="postgresql",
                flavour="postgres",
                container=f"shop-{service}-1",
                container_id=service,
                image="postgres:16",
                project="shop",
                service=service,
                state="running",
            )
        )
        manager.BACKUP_DIR = tmp_path
        return manager

    (tmp_path / "postgresql.shop.db-2.orders-20261003_101010.dump").write_text("x")
    (tmp_path / "postgresql.shop.db.orders-20261003_101011.dump").write_text("x")
    (tmp_path / "postgresql-orders-20261003_101012.dump").write_text("x")

    assert [b.path.name for b in bound("db").list_backups()] == [
        "postgresql.shop.db.orders-20261003_101011.dump"
    ]
    assert [b.database for b in bound("db-2").list_backups()] == ["orders"]
    host = PostgresManager()
    host.BACKUP_DIR = tmp_path
    assert [b.path.name for b in host.list_backups()] == ["postgresql-orders-20261003_101012.dump"]


# ==================== Service ====================


class TestTheService:
    """An instance key goes wherever an engine name went."""

    @pytest.fixture
    def service(self, fleet: FakeRunner, tmp_path: Path):
        from noust.core.secrets import SecretStore
        from noust.core.store import NoustStore
        from noust.managers.database.service import DatabaseService

        return DatabaseService(
            store=NoustStore(tmp_path / "noust.db"),
            secrets=SecretStore(root=tmp_path / "secrets"),
            resolve=lambda name: {"postgresql": PostgresManager, "mysql": MySQLManager}.get(
                name, lambda: None
            )(),
        )

    def test_an_instance_key_resolves_to_a_bound_manager(self, service) -> None:
        manager = service.manager("postgresql@proggest.postgres")

        assert manager.instance is not None
        assert manager.instance.container == "proggest-postgres-1"
        assert manager.engine_type == "postgresql"

    def test_an_alias_works_in_a_key(self, service) -> None:
        assert service.manager("pg@proggest.postgres").ENGINE_NAME == "postgresql@proggest.postgres"

    def test_an_unknown_instance_names_the_ones_found(self, service) -> None:
        with pytest.raises(DatabaseEngineError) as raised:
            service.manager("postgresql@nope.db")

        assert "postgresql@proggest.postgres" in raised.value.details

    def test_engines_lists_instances_after_the_host(self, service, fleet: FakeRunner) -> None:
        fleet.script(["docker", "exec"], stdout="")

        described = service.engines()

        names = [entry["engine"] for entry in described]
        assert set(names[:2]) == {"postgresql", "mysql"}
        assert "postgresql@empleo-arennalabs-com.db" in names
        empleo = next(e for e in described if e["engine"] == "postgresql@empleo-arennalabs-com.db")
        assert empleo["kind"] == "container" and empleo["stored_account"] is False
        assert next(e for e in described if e["engine"] == "postgresql")["kind"] == "host"

    def test_the_listing_includes_a_containers_databases(self, service, fleet) -> None:
        fleet.script(["docker", "exec"], stdout="proggest|UTF8|8192|proggest\n")
        fleet.script(["systemctl"], stdout="inactive", exit_code=3)

        listing = service.listing()

        keys = {(view.engine, view.name) for view in listing.databases}
        assert ("postgresql@proggest.postgres", "proggest") in keys
        # A stopped container is not read, and is not a problem either.
        assert not any(view.engine == "mysql@zabbix.zabbix-db" for view in listing.databases)

    def test_docker_failing_is_a_problem_of_the_listing(self, runner: FakeRunner, tmp_path) -> None:
        from noust.core.secrets import SecretStore
        from noust.core.store import NoustStore
        from noust.managers.database.service import DatabaseService

        runner.script(["docker", "ps"], exit_code=1, stderr="permission denied")
        runner.script(["systemctl"], stdout="inactive", exit_code=3)
        service = DatabaseService(
            store=NoustStore(tmp_path / "noust.db"),
            secrets=SecretStore(root=tmp_path / "secrets"),
        )

        listing = service.listing()

        (problem,) = listing.problems
        assert (problem.kind, problem.output) == ("docker", "permission denied")

    def test_a_curated_engine_list_discovers_nothing(self, runner: FakeRunner, tmp_path) -> None:
        from noust.core.secrets import SecretStore
        from noust.core.store import NoustStore
        from noust.managers.database.service import DatabaseService

        service = DatabaseService(
            store=NoustStore(tmp_path / "noust.db"),
            secrets=SecretStore(root=tmp_path / "secrets"),
            resolve=lambda name: None,
            engines=lambda: [],
        )

        assert service.all_managers() == []
        assert not any(call[:1] == ("docker",) for call in runner.calls)


# ==================== CLI and API ====================


def test_the_cli_accepts_an_instance_key_and_refuses_a_bad_one() -> None:
    import click

    from noust.cli.commands.db import ENGINE

    assert (
        ENGINE.convert("postgresql@proggest.postgres", None, None) == "postgresql@proggest.postgres"
    )
    with pytest.raises(click.BadParameter):
        ENGINE.convert("nosuch@proggest.postgres", None, None)
    with pytest.raises(click.BadParameter):
        ENGINE.convert("postgresql@../x", None, None)


def test_the_api_routes_a_key_with_at_and_dots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, runner: FakeRunner
) -> None:
    from fastapi.testclient import TestClient

    import noust.managers.database.service as db_service
    from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
    from noust.web.server import create_app, get_token_manager
    from tests.test_web_databases_api import make_engine

    fake = make_engine(tmp_path / "dumps")
    instance = DatabaseInstance(
        key="postgresql@proggest.postgres",
        engine="postgresql",
        flavour="postgres",
        container="proggest-postgres-1",
        container_id="p",
        image="postgres:16-alpine",
        project="proggest",
        service="postgres",
        state="running",
    )
    monkeypatch.setattr(
        db_service,
        "get_db_manager",
        lambda engine, verbose=False: fake() if engine == "postgresql" else None,
    )
    monkeypatch.setattr(db_service, "discover", lambda: [instance])
    app = create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))
    client = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    login = client.post(
        "/api/auth/login", json={"token": get_token_manager().generate_master_token()}
    )
    client.headers[CSRF_HEADER_NAME] = login.json()["csrf_token"]

    response = client.get("/api/databases/databases/postgresql@proggest.postgres/appdb")

    assert response.status_code == 200, response.text
    assert response.json()["engine"] == "postgresql@proggest.postgres"
    engines = client.get("/api/databases/engines").json()["engines"]
    entry = next(e for e in engines if e["name"] == "postgresql@proggest.postgres")
    assert (entry["kind"], entry["container"], entry["compose_service"]) == (
        "container",
        "proggest-postgres-1",
        "postgres",
    )


@pytest.mark.allow_subprocess
class TestWhatRunsInsideTheContainer:
    """
    What follows ``docker exec ... <container>`` runs here, under ``sh``, against
    stand-ins for the clients: the part no fake runner can vouch for.
    """

    @pytest.fixture
    def clients(self, tmp_path: Path) -> Path:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        report = (
            "#!/bin/sh\n"
            "printf 'program=%s\\n' \"${0##*/}\"\n"
            "printf 'args=%s\\n' \"$*\"\n"
            "printf 'PGPASSWORD=%s\\n' \"${PGPASSWORD:-}\"\n"
            "printf 'MYSQL_PWD=%s\\n' \"${MYSQL_PWD:-}\"\n"
        )
        for name in ("psql", "mysql"):
            path = bin_dir / name
            path.write_text(report)
            path.chmod(0o755)
        return bin_dir

    def run_inside(self, clients: Path, argv: list[str], environment: dict[str, str]):
        import shutil

        from noust.core.runner import SubprocessRunner

        inside = argv[argv.index("sh") :]
        # Only the stand-ins on PATH: a host with a real mariadb or psql would
        # otherwise run it.
        inside[0] = shutil.which("sh") or "/bin/sh"
        env = {"PATH": str(clients), **environment}
        result = SubprocessRunner().run(inside, env=env, timeout=30)
        assert result.success, result.stderr
        return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    @pytest.fixture
    def found(self) -> dict[str, DatabaseInstance]:
        runner = FakeRunner()
        runner.script(["docker", "ps"], stdout=PS_LINES + "\n")
        runner.script(["docker", "inspect"], stdout=json.dumps(FLEET))
        return by_key(discover(runner))

    def test_the_container_password_reaches_psql_as_pgpassword(self, clients, found) -> None:
        argv = found["postgresql@empleo-arennalabs-com.db"].exec_argv(
            ["psql", "-d", "postgres"], user="postgres"
        )

        report = self.run_inside(clients, argv, {"POSTGRES_PASSWORD": PG_PASSWORD})

        assert report["program"] == "psql"
        assert report["PGPASSWORD"] == PG_PASSWORD
        assert PG_PASSWORD not in report["args"]

    def test_a_password_noust_forwards_wins_over_the_containers(self, clients, found) -> None:
        argv = found["postgresql@empleo-arennalabs-com.db"].exec_argv(
            ["psql"], env={"PGPASSWORD": "read-only"}
        )

        report = self.run_inside(
            clients, argv, {"POSTGRES_PASSWORD": PG_PASSWORD, "PGPASSWORD": "read-only"}
        )

        assert report["PGPASSWORD"] == "read-only"

    def test_mysql_falls_back_to_the_name_the_image_ships(self, clients, found) -> None:
        argv = found["mysql@tienda-arennalabs-com.mysql"].exec_argv(["mysql", "-u", "root"])

        report = self.run_inside(clients, argv, {"MYSQL_ROOT_PASSWORD": ROOT_PASSWORD})

        # Only "mysql" is among the stand-ins; "mariadb" is tried first.
        assert report["program"] == "mysql"
        assert report["MYSQL_PWD"] == ROOT_PASSWORD
