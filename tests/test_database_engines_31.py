# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The engine fixes of Noust 3.1 (research/3.1/databases.md, section 3.7).

Each test pins the exact statement or argv a fix builds, with the fake
runner: a connection string that survives any password (B4), a restore that
tells a custom dump from a plain one, passwords that never reach argv, access
profiles scoped to one database (never ``pg_read_all_data``), fix-owner that
changes nothing until asked (B5), Redis with ``requirepass`` (B6), the
Redis/Valkey family, MongoDB authorization (B7), and the exposure check.
"""

from __future__ import annotations

import gzip
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from noust.core.exceptions import DatabaseEngineError, DatabaseUserError, ValidationError
from noust.core.runner import FakeRunner
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.managers.database import flavours
from noust.managers.database import mongodb as mongodb_module
from noust.managers.database.base import BaseDatabaseManager, is_loopback
from noust.managers.database.eol import support_notice
from noust.managers.database.exposure import (
    find_exposed_database_ports,
    tunnel_instructions,
)
from noust.managers.database.mongodb import ADMIN_SECRET, MongoDBManager
from noust.managers.database.mysql import MySQLManager
from noust.managers.database.postgres import PostgresManager
from noust.managers.database.redis import REQUIREPASS_SECRET, RedisManager
from noust.managers.database.registry import get_db_manager
from noust.managers.database.urls import connection_url, masked
from noust.managers.server.host import HostPaths

PSQL = ("runuser", "-u", "postgres", "--", "psql")

#: A password with every character a URL splits on.
NASTY = "p@ss:w/rd#?%& x"


class StubConfig:
    """A configuration that answers with whatever the test put in it."""

    def __init__(self, data: dict[str, Any]):
        """
        Args:
            data: Values keyed by top level configuration key.
        """
        self._data = data

    def get(self, key: str, default: Any = None) -> Any:
        """
        Args:
            key: Configuration key.
            default: Value when absent.

        Returns:
            The value.
        """
        return self._data.get(key, default)


@pytest.fixture
def postgres(runner: FakeRunner, tmp_path: Path) -> Iterator[PostgresManager]:
    """
    Args:
        runner: The fake runner.
        tmp_path: Per-test temporary directory.

    Yields:
        A PostgreSQL manager with its dumps in the test's directory.
    """
    NoustStore.reset_instance()
    manager = PostgresManager()
    manager.BACKUP_DIR = tmp_path / "backups"
    manager.config = StubConfig({})
    try:
        yield manager
    finally:
        NoustStore.reset_instance()


@pytest.fixture
def mysql(runner: FakeRunner, tmp_path: Path) -> MySQLManager:
    """
    Args:
        runner: The fake runner.
        tmp_path: Per-test temporary directory.

    Returns:
        A MySQL manager.
    """
    runner.only_knows("mysql", "mysqldump")
    manager = MySQLManager()
    manager.BACKUP_DIR = tmp_path / "backups"
    manager.config = StubConfig({})
    return manager


# ============================================================ URLs (B4)


def test_a_url_survives_any_password() -> None:
    url = connection_url(
        "postgresql", database="shop", user="app", password=NASTY, host="localhost", port=5433
    )

    assert url == "postgresql://app:p%40ss%3Aw%2Frd%23%3F%25%26%20x@localhost:5433/shop"
    assert masked(url, NASTY) == "postgresql://app:********@localhost:5433/shop"


def test_redis_default_user_is_the_password_alone() -> None:
    assert (
        connection_url("redis", database="0", user="default", password="s3", port=6380)
        == "redis://:s3@localhost:6380/0"
    )
    assert (
        connection_url("redis", database="0", user=None, password=None, port=6379)
        == "redis://localhost:6379/0"
    )


def test_an_ipv6_host_is_bracketed_and_options_are_encoded() -> None:
    assert (
        connection_url(
            "mongodb",
            database="app",
            user="u",
            password="p",
            host="::1",
            port=27017,
            options={"authSource": "admin"},
        )
        == "mongodb://u:p@[::1]:27017/app?authSource=admin"
    )


def test_every_engine_builds_its_string_the_one_way(postgres: PostgresManager, runner) -> None:
    """The manager's connection string is the shared builder's, on the real port."""
    runner.script([*PSQL, "-v", "ON_ERROR_STOP=1", "-d", "postgres"], stdout="5433")

    assert postgres.get_connection_string("shop", "app", NASTY) == connection_url(
        "postgresql", database="shop", user="app", password=NASTY, port=5433
    )


def test_generated_passwords_need_no_encoding() -> None:
    password = BaseDatabaseManager.generate_password()

    assert len(password) == 32 and password.isalnum()


# ============================================================ PostgreSQL


def test_new_dumps_are_custom_format_and_not_gzipped_twice(
    postgres: PostgresManager, runner: FakeRunner
) -> None:
    runner.script(PSQL, stdout="1")
    runner.script(["runuser", "-u", "postgres", "--", "pg_dump"], stdout="PGDMP...")

    info = postgres.backup("shop")

    assert info.path.name.endswith(".dump") and not info.compressed
    assert "--format=custom" in runner.written[info.path]


def test_a_custom_dump_named_sql_is_restored_with_pg_restore(
    postgres: PostgresManager, runner: FakeRunner, tmp_path: Path
) -> None:
    """The file's own first bytes decide, not its name: 2.x named any dump .sql.gz."""
    runner.script(PSQL, stdout="1")
    dump = tmp_path / "postgresql-shop-20250101_000000.sql.gz"
    dump.write_bytes(gzip.compress(b"PGDMP\x01\x0e\x00binary archive"))

    postgres.restore("shop", dump, safety_backup=False)

    assert runner.calls[-1][4:] == (
        "pg_restore",
        "--no-password",
        "-d",
        "shop",
        str(postgres.BACKUP_DIR / ".staging" / "postgresql-restore-shop.dump"),
    )


def test_a_plain_dump_with_a_meta_command_is_refused_before_anything(
    postgres: PostgresManager, runner: FakeRunner, tmp_path: Path
) -> None:
    dump = tmp_path / "evil.sql"
    dump.write_text("SELECT 1;\n\\! id\n")

    with pytest.raises(Exception, match="meta-command"):
        postgres.restore("shop", dump, drop_existing=True)

    assert runner.calls == [], "nothing is dumped, dropped or loaded"


def test_a_password_change_sends_a_verifier_never_the_password(
    postgres: PostgresManager, runner: FakeRunner
) -> None:
    runner.script(PSQL, stdout="1")

    postgres.set_user_password("app", "correcthorsebatterystaple42")

    statement = runner.inputs[-1] or ""
    assert statement.startswith('ALTER ROLE "app" PASSWORD \'SCRAM-SHA-256$4096:')
    assert "correcthorsebatterystaple42" not in statement
    assert all("correcthorsebatterystaple42" not in " ".join(call) for call in runner.calls)


def test_a_read_only_profile_is_scoped_to_one_database(
    postgres: PostgresManager, runner: FakeRunner
) -> None:
    """SELECT on this database's schemas, never pg_read_all_data (cluster-wide)."""
    runner.script(PSQL, stdout="1")
    runner.script(
        [*PSQL, "-v", "ON_ERROR_STOP=1", "-d", "shop", "-t", "-A", "-X", "-f", "-"], stdout="public"
    )
    postgres.get_database_info = lambda name: type("I", (), {"owner": "shop_owner"})()  # type: ignore[method-assign]

    postgres.apply_profile("reporter", "shop", "read_only")

    scripts = "\n".join(text for text in runner.inputs if text)
    assert 'REVOKE ALL ON DATABASE "shop" FROM "reporter";' in scripts
    assert 'GRANT CONNECT ON DATABASE "shop" TO "reporter";' in scripts
    assert 'GRANT SELECT ON ALL TABLES IN SCHEMA "public" TO "reporter";' in scripts
    assert (
        'ALTER DEFAULT PRIVILEGES FOR ROLE "shop_owner" IN SCHEMA "public" GRANT SELECT ON '
        'TABLES TO "reporter";'
    ) in scripts
    assert "INSERT" not in scripts.split("REVOKE ALL ON ALL SEQUENCES")[-1]
    assert "pg_read_all_data" not in scripts


def test_the_owner_keeps_its_profile_until_another_owns(postgres, runner) -> None:
    runner.script(PSQL, stdout="1")
    postgres.get_database_info = lambda name: type("I", (), {"owner": "app"})()  # type: ignore[method-assign]

    with pytest.raises(DatabaseUserError, match="owns"):
        postgres.apply_profile("app", "shop", "read_write")


def test_access_is_read_from_one_catalog_query(postgres: PostgresManager, runner) -> None:
    runner.script(PSQL, stdout="1")
    runner.script(
        [*PSQL, "-v", "ON_ERROR_STOP=1", "-d", "shop", "-t", "-A", "-X", "-f", "-"],
        stdout=(
            "app|t|f|3|3|3|f|f\n"
            "reporter|f|f|3|3|0|t|f\n"
            "writer|f|f|3|3|3|t|t\n"
            "postgres|f|t|3|3|3|f|f\n"
            "wasm_ro_shop|f|f|3|3|0|f|f\n"
        ),
    )

    entries = {entry.username: entry for entry in postgres.list_access("shop")}

    assert entries["app"].profile == "owner"
    assert entries["reporter"].profile == "read_only"
    assert entries["writer"].profile == "read_write"
    assert entries["postgres"].internal and entries["wasm_ro_shop"].internal


def test_fix_owner_shows_and_changes_nothing_until_asked(postgres, runner) -> None:
    runner.script(PSQL, stdout="1")
    runner.script(
        [*PSQL, "-v", "ON_ERROR_STOP=1", "-d", "shop", "-t", "-A", "-X", "-f", "-"],
        stdout="S|public|orders_seq\nn|billing|\nr|public|orders\n",
    )
    postgres.get_database_info = lambda name: type("I", (), {"owner": "postgres"})()  # type: ignore[method-assign]
    before = len(runner.inputs)

    plan = postgres.fix_owner("shop", "shop_user")

    assert plan.applied is False
    assert plan.statements[0] == 'ALTER DATABASE "shop" OWNER TO "shop_user";'
    assert 'ALTER TABLE "public"."orders" OWNER TO "shop_user";' in plan.statements
    assert 'ALTER SCHEMA "billing" OWNER TO "shop_user";' in plan.statements
    assert not any("ALTER" in (text or "") for text in runner.inputs[before:]), "only reads"

    applied = postgres.fix_owner("shop", "shop_user", apply=True)

    assert applied.applied is True
    assert any((text or "").startswith("BEGIN;\nALTER") for text in runner.inputs)


def test_a_name_holding_the_dollar_tag_cannot_close_the_block(postgres, runner) -> None:
    """Names may contain '$': the read-only role's DO blocks pick a tag they do not contain."""
    runner.script(PSQL, stdout="1")
    name = "a$wasm_ro_role$b"

    postgres._ensure_read_only_role(name)

    script = next(text for text in runner.inputs if text and "DO $" in text)
    tag = script.split("DO ", 1)[1].split("\n", 1)[0]
    assert tag.startswith("$wasm_ro_role_") and tag not in name
    assert script.count(tag) == 2


def test_dropping_a_database_drops_its_read_only_role(postgres, runner, tmp_path) -> None:
    runner.script(PSQL, stdout="")

    postgres.drop_read_only_account("shop")

    assert runner.inputs[-1] == 'DROP ROLE IF EXISTS "wasm_ro_shop";'


def test_listen_addresses_come_from_the_server(postgres, runner) -> None:
    runner.script(PSQL, stdout="*")

    listen = postgres.listen_addresses()

    assert listen is not None and listen.addresses == ("*",) and not listen.loopback_only


# ============================================================ MySQL / MariaDB


def test_mysql_profiles_revoke_then_grant(mysql: MySQLManager, runner) -> None:
    mysql.apply_profile("reporter", "shop", "read_only", host="localhost")

    assert runner.inputs[-1] == (
        "GRANT SELECT ON `shop`.* TO 'reporter'@'localhost';\n"
        "REVOKE ALL PRIVILEGES ON `shop`.* FROM 'reporter'@'localhost';\n"
        "GRANT SELECT ON `shop`.* TO 'reporter'@'localhost';\n"
        "FLUSH PRIVILEGES;\n"
    )


def test_mysql_access_reads_the_grant_table(mysql: MySQLManager, runner) -> None:
    runner.script(
        ["mysql", "-N", "-B"],
        stdout=(
            "app\tlocalhost\tY\tY\tY\tY\tY\tY\tY\tY\n"
            "rw\tlocalhost\tY\tY\tY\tY\tN\tN\tN\tN\n"
            "ro\t%\tY\tN\tN\tN\tN\tN\tN\tN\n"
            "wasm_ro_shop\tlocalhost\tY\tN\tN\tN\tN\tN\tN\tN\n"
        ),
    )

    entries = {entry.username: entry for entry in mysql.list_access("shop")}

    assert entries["app"].profile == "owner"
    assert entries["rw"].profile == "read_write"
    assert (entries["ro"].profile, entries["ro"].host) == ("read_only", "%")
    assert entries["wasm_ro_shop"].internal


def test_mysql_password_change_goes_on_stdin(mysql: MySQLManager, runner) -> None:
    runner.script(["mysql", "-N", "-B"], stdout="app")

    mysql.set_user_password("app", "correcthorsebatterystaple42")

    assert "IDENTIFIED BY 'correcthorsebatterystaple42'" in (runner.inputs[-1] or "")
    assert all("correcthorsebatterystaple42" not in " ".join(call) for call in runner.calls)


def test_mariadb_is_judged_by_mariadb_s_lifecycle(runner: FakeRunner) -> None:
    runner.only_knows("mysql", "mariadb")

    assert MySQLManager().EOL_FAMILY == "mariadb"


def test_install_is_refused_without_apt(mysql: MySQLManager, runner) -> None:
    with pytest.raises(DatabaseEngineError) as excinfo:
        mysql.install()

    assert "dnf or zypper" in excinfo.value.details
    assert runner.calls == []


# ============================================================ Redis / Valkey


def test_redis_authenticates_with_the_password_noust_stored(
    runner: FakeRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B6: the password used to live only in the process that set it."""
    store = SecretStore(root=tmp_path / "secrets")
    store.write(REQUIREPASS_SECRET, "hunter2")
    monkeypatch.setattr(RedisManager, "_secrets", lambda self: store)
    manager = RedisManager()
    manager.config = StubConfig({})

    manager.list_databases()

    assert runner.envs[0] == {"REDISCLI_AUTH": "hunter2"}
    assert "hunter2" not in " ".join(runner.calls[0])


def test_redis_configured_password_wins(runner: FakeRunner) -> None:
    manager = RedisManager()
    manager.config = StubConfig({"databases": {"credentials": {"redis": {"password": "cfg"}}}})

    manager.execute_query("0", "PING")

    assert runner.envs[-1] == {"REDISCLI_AUTH": "cfg"}


def test_redis_commands_split_like_the_client(runner: FakeRunner) -> None:
    manager = RedisManager()
    manager.config = StubConfig({})

    manager.execute_query("2", 'SET greeting "hello world"')

    assert runner.calls[-1] == ("redis-cli", "-n", "2", "SET", "greeting", "hello world")


def test_setting_requirepass_is_remembered(
    runner: FakeRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SecretStore(root=tmp_path / "secrets")
    monkeypatch.setattr(RedisManager, "_secrets", lambda self: store)
    manager = RedisManager()
    manager.config = StubConfig({})

    manager.set_user_password("default", "s3cret")

    assert store.read(REQUIREPASS_SECRET) == "s3cret"
    assert "s3cret" not in " ".join(" ".join(call) for call in runner.calls)


def test_every_snapshot_shows_on_every_slot(runner: FakeRunner, tmp_path: Path) -> None:
    manager = RedisManager()
    manager.BACKUP_DIR = tmp_path
    (tmp_path / "redis-dump-20260101_120000.rdb.gz").write_text("x")

    assert [b.database for b in manager.list_backups(database="3")] == ["dump"]


def test_valkey_is_the_same_family(runner: FakeRunner) -> None:
    runner.only_knows("valkey-server", "valkey-cli")
    runner.script(["valkey-server", "--version"], stdout="Valkey server v=8.0.2 sha=0")
    runner.script(["systemctl", "show"], stdout="not-found")
    runner.script(
        ["systemctl", "show", "--property=LoadState", "--value", "valkey-server"],
        stdout="loaded",
    )
    manager = get_db_manager("valkey")
    assert isinstance(manager, RedisManager)
    manager.config = StubConfig({})

    assert manager.is_installed()
    assert manager.get_version() == "8.0.2" and manager.DISPLAY_NAME == "Valkey"
    assert manager.service_unit() == "valkey-server"
    manager.execute_query("0", "PING")
    assert runner.calls[-1][0] == "valkey-cli"


# ============================================================ MongoDB


def test_mongodb_scripts_authenticate_on_stdin(
    runner: FakeRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SecretStore(root=tmp_path / "secrets")
    store.write(ADMIN_SECRET, "adminpass")
    monkeypatch.setattr(MongoDBManager, "_secrets", lambda self: store)
    runner.only_knows("mongosh", "mongod")
    manager = MongoDBManager()

    manager.drop_database("shop", force=True)

    assert (runner.inputs[0] or "").startswith(
        'void db.getSiblingDB(\'admin\').auth("noust_admin", "adminpass");\n'
    )
    assert "adminpass" not in " ".join(" ".join(call) for call in runner.calls)


@pytest.mark.parametrize(
    ("release", "expected"),
    [
        (
            "ID=ubuntu\nVERSION_CODENAME=noble\n",
            "https://repo.mongodb.org/apt/ubuntu noble/mongodb-org/8.0 multiverse",
        ),
        (
            "ID=debian\nVERSION_CODENAME=bookworm\n",
            "https://repo.mongodb.org/apt/debian bookworm/mongodb-org/8.0 main",
        ),
    ],
)
def test_mongodb_repository_follows_the_distribution(
    runner: FakeRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, release, expected
) -> None:
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "os-release").write_text(release)
    monkeypatch.setattr(flavours, "HOST", HostPaths(tmp_path))

    plan = MongoDBManager().default_install_plan()

    assert plan.repository is not None
    assert expected in plan.repository.line()


def test_mongodb_refuses_a_distribution_it_has_no_packages_for(
    runner: FakeRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "os-release").write_text(
        'ID=fedora\nVERSION_CODENAME=""\nPRETTY_NAME="Fedora Linux 41"\n'
    )
    monkeypatch.setattr(flavours, "HOST", HostPaths(tmp_path))
    runner.only_knows("apt-get")

    with pytest.raises(ValidationError, match="Fedora Linux 41"):
        MongoDBManager().install()

    assert runner.calls == [], "no key is downloaded for a distribution that is refused"


def test_a_new_mongodb_install_turns_authorization_on(
    runner: FakeRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SecretStore(root=tmp_path / "secrets")
    monkeypatch.setattr(MongoDBManager, "_secrets", lambda self: store)
    conf = tmp_path / "mongod.conf"
    conf.write_text("net:\n  port: 27017\n  bindIp: 127.0.0.1\n")
    monkeypatch.setattr(mongodb_module, "MONGOD_CONF", conf)
    runner.only_knows("mongosh", "mongod")
    manager = MongoDBManager()

    manager._post_install()

    password = store.read(ADMIN_SECRET)
    assert password and "createUser" in (runner.inputs[0] or "")
    assert password not in " ".join(" ".join(call) for call in runner.calls)
    written = yaml.safe_load(conf.read_text())
    assert written["security"]["authorization"] == "enabled"
    assert written["net"]["bindIp"] == "127.0.0.1"
    assert runner.calls[-1] == ("systemctl", "restart", "mongod")
    assert manager.warnings() == []


def test_an_existing_mongodb_without_authorization_is_warned_about(
    runner: FakeRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conf = tmp_path / "mongod.conf"
    conf.write_text("net:\n  port: 27017\n")
    monkeypatch.setattr(mongodb_module, "MONGOD_CONF", conf)

    [warning] = MongoDBManager().warnings()

    assert "protect nothing" in warning and "security.authorization: enabled" in warning


# ============================================================ support and exposure


@pytest.mark.parametrize(
    ("family", "version", "status"),
    [
        ("postgresql", "13.18", "ended"),
        ("postgresql", "14.19", "ending_soon"),
        ("postgresql", "16.4", "supported"),
        ("mariadb", "10.6.18", "ended"),
        ("mariadb", "10.11.8", "supported"),
        ("mysql", "8.0.39", "ended"),
        ("redis", "7.0.15", "unknown"),
    ],
)
def test_upstream_support_is_dated(family: str, version: str, status: str) -> None:
    notice = support_notice(family, version, today=date(2026, 9, 29))

    assert notice.status == status


def test_exposure_reads_the_kernel_and_docker(runner: FakeRunner) -> None:
    runner.script(
        ["ss", "-ltnpH"],
        stdout=(
            'LISTEN 0 244 0.0.0.0:5433 0.0.0.0:* users:(("postgres",pid=1,fd=6))\n'
            'LISTEN 0 244 127.0.0.1:3306 0.0.0.0:* users:(("mariadbd",pid=2,fd=6))\n'
            'LISTEN 0 4096 0.0.0.0:5435 0.0.0.0:* users:(("docker-proxy",pid=3,fd=4))\n'
            'LISTEN 0 4096 [::]:6379 [::]:* users:(("redis-server",pid=4,fd=7))\n'
            'LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=5,fd=3))\n'
        ),
    )
    # A server that has an IPv6 route: its [::] publication is a real one. Without
    # a route nothing outside reaches it, and exposure leaves it out.
    runner.script(["ip", "-6", "route", "show", "default"], stdout="default via fe80::1 dev eth0\n")
    runner.script(
        ["docker", "ps"],
        stdout=(
            "shop-db\tpostgres:16\t0.0.0.0:5435->5432/tcp, [::]:5435->5432/tcp\n"
            "cache\tredis:7\t127.0.0.1:6380->6379/tcp\n"
            "web\tnginx:1\t0.0.0.0:80->80/tcp\n"
        ),
    )

    found = [(e.engine, e.port, e.address, e.source) for e in find_exposed_database_ports()]

    assert found == [
        ("postgresql", 5433, "0.0.0.0", "engine"),  # noqa: S104 - an address in ss output
        ("postgresql", 5435, "0.0.0.0", "docker"),  # noqa: S104 - an address in ss output
        ("postgresql", 5435, "::", "docker"),
        ("redis", 6379, "::", "engine"),
    ]


def test_the_tunnel_ends_on_the_loopback() -> None:
    tunnel = tunnel_instructions(
        "postgresql",
        database="shop",
        username="app",
        remote_port=5432,
        server="203.0.113.5",
        ssh_port=2222,
        masked_url="",
    )

    assert tunnel.command == "ssh -N -L 15432:127.0.0.1:5432 -p 2222 root@203.0.113.5"
    assert tunnel.clients["psql"] == "psql -h 127.0.0.1 -p 15432 -U app -d shop"


@pytest.mark.parametrize(
    ("address", "loopback"),
    [("127.0.0.1", True), ("127.0.1.1", True), ("::1", True), ("[::1]", True), ("*", False)],
)
def test_loopback_addresses(address: str, loopback: bool) -> None:
    assert is_loopback(address) is loopback
