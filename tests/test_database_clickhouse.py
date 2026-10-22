# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
ClickHouse as an engine Noust runs as an instance (checklist C-06).

What is defended: the instance is made from a pinned image with the file the
server needs for backups; every statement reaches the client on its standard
input (a password is in no argument); a backup is ClickHouse's own ``BACKUP``
copied out of the container and removed from it, even when the copy fails; a
restore is aimed at the name asked, never at the original; and the accounts
and profiles do what the other engines' do. A model of the server answers the
client, so the tests assert on what Noust sent and on what it made of the answers.
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml

from noust.core.exceptions import (
    DatabaseBackupError,
    DatabaseError,
    DatabaseExistsError,
    DatabaseNotFoundError,
    DatabaseQueryError,
    DatabaseUserError,
    ValidationError,
)
from noust.core.runner import CommandResult, FakeRunner, set_runner
from noust.managers.database import instance_compose as compose
from noust.managers.database import instance_layout as layout
from noust.managers.database.backup_verify import check_dump, restore_test
from noust.managers.database.base import BackupInfo
from noust.managers.database.clickhouse import (
    DUMP_NAME,
    ClickHouseManager,
    backup_problem,
    identifier,
    literal,
    unescape,
)
from noust.managers.database.instance_catalog import SPECS, offered, resolve_pin, spec_for
from noust.managers.database.instance_records import stub_instance
from noust.managers.database.instances import (
    CLIENT_SCRIPT,
    ENGINE_PORTS,
    image_engine,
)
from noust.managers.database.registry import DatabaseRegistry
from noust.managers.database.service import DatabaseService
from tests.database_instance_support import World, build_world, close_world
from tests.test_database_instance_compose import PASSWORD, record

SECRET = "Tq9Lw2Zr7Pm4Xc1Vb8Nd5Hj3Ka6Sf0Ge"


def backup_zip(path: Path, *, with_metadata: bool = True) -> Path:
    """A zip the way ClickHouse's BACKUP writes one."""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("metadata/noust_dump.sql", "ATTACH DATABASE _ UUID 'x' ENGINE = Atomic\n")
        archive.writestr("data/noust_dump/events/all_1_1_0/data.bin", b"\x00" * 64)
        if with_metadata:
            archive.writestr(".backup", "<config><version>1</version></config>")
    return path


class Server(FakeRunner):
    """
    A ClickHouse the client talks to: databases, accounts, grants and a backups disk.

    ``run`` understands the statements Noust sends (on standard input) and the three
    commands it runs besides the client (``cat`` of a backup, ``sh -c 'cat > file'``
    and ``rm``). Everything it is asked is kept in ``statements``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.databases: dict[str, tuple[int, int]] = {"default": (0, 0), "shop": (4096, 3)}
        self.users: dict[str, str] = {"noust_admin": "x"}
        self.grants: list[tuple[str, str, str]] = [("noust_admin", "*", "ALL")]
        self.statements: list[str] = []
        self.files: dict[str, bytes] = {}
        self.disk_defined = True
        self.fail_on: str | None = None
        self.fail_message = "Code: 999. DB::Exception: boom"
        #: How many matching statements fail (-1: every one).
        self.fail_times = -1
        self.cat_fails = False
        self.copied_in: dict[str, bytes] = {}

    @staticmethod
    def split(argv: Sequence[str]) -> tuple[str, list[str]]:
        """The program and arguments inside a ``docker exec ... sh -c SCRIPT sh ...``."""
        position = list(argv).index("-c") + 1
        programs, args = argv[position + 5], list(argv[position + 6 :])
        return programs, args

    def run(self, argv: Sequence[str], **kwargs: Any) -> CommandResult:  # type: ignore[override]
        result = super().run(argv, **kwargs)
        if argv[:2] != ("docker", "exec") and list(argv[:2]) != ["docker", "exec"]:
            return result
        programs, args = self.split(argv)
        sql = kwargs.get("input") or ""
        if programs.startswith("clickhouse-client"):
            return self.client(result, args, sql)
        if programs == "rm":
            self.files.pop(args[-1], None)
            return result
        if programs == "sh" and args[:1] == ["-c"]:
            command = args[1]
            if command.startswith("cat > "):
                path = command.removeprefix("cat > ")
                stdin = kwargs.get("stdin_path")
                self.files[path] = Path(stdin).read_bytes() if stdin else b""
                self.copied_in[path] = self.files[path]
        return result

    def client(self, result: CommandResult, args: list[str], sql: str) -> CommandResult:
        """Answer one invocation of clickhouse-client."""
        if "--version" in args:
            return self.answer(result, "ClickHouse client version 26.8.1.1 (official build).\n")
        self.statements.append(sql)
        if self.fail_on and self.fail_on in sql and self.fail_times != 0:
            self.fail_times -= 1
            return self.answer(result, "", error=self.fail_message)
        out = ""
        for statement in [part.strip() for part in sql.split(";\n") if part.strip()]:
            out += self.statement(statement)
        return self.answer(result, out)

    def answer(self, result: CommandResult, out: str, error: str = "") -> CommandResult:
        from dataclasses import replace

        return replace(result, exit_code=1 if error else 0, stdout=out, stderr=error)

    def statement(self, sql: str) -> str:
        """The model of the server."""
        found = re.search(r"FROM system\.databases WHERE name = '([^']*)'", sql)
        if found:
            return "1\n" if found.group(1) in self.databases else "0\n"
        if "FROM system.databases AS d" in sql:
            return "".join(
                f"{name}\t{size}\t{tables}\tAtomic\n"
                for name, (size, tables) in sorted(self.databases.items())
            )
        found = re.search(r"^CREATE DATABASE `([^`]+)`$", sql)
        if found:
            self.databases[found.group(1)] = (0, 0)
            return ""
        found = re.search(r"^DROP DATABASE `([^`]+)` SYNC$", sql)
        if found:
            self.databases.pop(found.group(1), None)
            return ""
        found = re.search(r"FROM system\.users WHERE name = '([^']*)'", sql)
        if found:
            return "1\n" if found.group(1) in self.users else "0\n"
        found = re.search(r"^CREATE USER `([^`]+)` IDENTIFIED WITH sha256_password BY '(.*)'$", sql)
        if found:
            self.users[found.group(1)] = found.group(2)
            return ""
        found = re.search(r"^ALTER USER `([^`]+)` IDENTIFIED WITH sha256_password BY '(.*)'$", sql)
        if found:
            self.users[found.group(1)] = found.group(2)
            return ""
        found = re.search(r"^DROP USER `([^`]+)`$", sql)
        if found:
            self.users.pop(found.group(1), None)
            self.grants = [item for item in self.grants if item[0] != found.group(1)]
            return ""
        if sql.startswith("SELECT name FROM system.users"):
            return "".join(f"{name}\n" for name in sorted(self.users))
        if sql.startswith("SELECT user_name, database, access_type FROM system.grants"):
            return "".join(
                f"{user}\t{db}\t{kind}\n" for user, db, kind in sorted(self.grants) if db != "*"
            )
        found = re.search(r"WHERE database = '([^']*)' AND user_name IS NOT NULL", sql)
        if found:
            return "".join(
                f"{user}\t{kind}\n"
                for user, db, kind in sorted(self.grants)
                if db == found.group(1)
            )
        found = re.search(r"^GRANT (.+) ON `([^`]+)`\.\* TO `([^`]+)`$", sql)
        if found:
            for kind in [item.strip() for item in found.group(1).split(",")]:
                self.grants.append((found.group(3), found.group(2), kind))
            return ""
        found = re.search(r"^REVOKE (.+) ON `([^`]+)`\.\* FROM `([^`]+)`$", sql)
        if found:
            user, db = found.group(3), found.group(2)
            if found.group(1) == "ALL":
                self.grants = [g for g in self.grants if not (g[0] == user and g[1] == db)]
            else:
                kinds = {item.strip() for item in found.group(1).split(",")}
                self.grants = [
                    g for g in self.grants if not (g[0] == user and g[1] == db and g[2] in kinds)
                ]
            return ""
        if "FROM system.disks WHERE name = 'backups'" in sql:
            return "1\n" if self.disk_defined else "0\n"
        found = re.search(
            r"^BACKUP DATABASE `([^`]+)` AS `([^`]+)` TO Disk\('backups', '([^']+)'\)$", sql
        )
        if found:
            path = f"{compose.CLICKHOUSE_BACKUP_DIR}{found.group(3)}"
            self.files[path] = b"PK-backup-of-" + found.group(1).encode()
            return f"{found.group(3)}\tBACKUP_CREATED\n"
        found = re.search(
            r"^RESTORE DATABASE `([^`]+)` AS `([^`]+)` FROM Disk\('backups', '([^']+)'\)$", sql
        )
        if found:
            self.databases[found.group(2)] = (2048, 2)
            return f"{found.group(3)}\tRESTORED\n"
        return ""

    def capture_to_file(self, argv, destination, **kwargs):  # type: ignore[no-untyped-def]
        programs, args = self.split(argv)
        result = super()._lookup(argv, kwargs.get("user"), kwargs.get("env"))
        if programs == "cat" and not self.cat_fails and args[-1] in self.files:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(self.files[args[-1]])
            return result
        from dataclasses import replace

        return replace(result, exit_code=1, stderr="cat: no such file")


@pytest.fixture
def server() -> Iterator[Server]:
    fake = Server()
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture
def manager(server: Server, tmp_path: Path) -> ClickHouseManager:
    instance = stub_instance(record("clickhouse", name="ch"), state="running")
    bound = ClickHouseManager().bind(instance)
    bound.BACKUP_DIR = tmp_path / "backups"
    return bound


# ===================================================================== the catalog


class TestTheCatalog:
    def test_clickhouse_is_offered_in_its_long_term_releases_pinned_by_digest(self) -> None:
        spec = spec_for("clickhouse")

        assert spec.engine == "clickhouse"
        assert spec.display_name == "ClickHouse"
        assert spec.repository == "clickhouse/clickhouse-server"
        assert [pin.version for pin in spec.pins] == ["25.8", "26.3", "26.8"]
        assert resolve_pin(spec, None).version == "26.8"
        assert all(re.fullmatch(r"sha256:[0-9a-f]{64}", pin.digest) for pin in spec.pins)
        assert spec.min_memory_mb >= 1024
        assert spec.data_path("26.8") == "/var/lib/clickhouse"

    def test_the_http_port_is_the_one_an_instance_publishes(self) -> None:
        assert SPECS["clickhouse"].container_port == ENGINE_PORTS["clickhouse"] == 8123

    def test_discovery_finds_its_image_as_clickhouse(self) -> None:
        match = image_engine("clickhouse/clickhouse-server:26.8@sha256:" + "a" * 64)

        assert match is not None
        assert (match.engine, match.flavour, match.official) == ("clickhouse", "clickhouse", False)
        assert image_engine("clickhouse/clickhouse-keeper") is None

    def test_it_is_listed_for_the_api_and_the_cli(self) -> None:
        entry = next(item for item in offered() if item["flavour"] == "clickhouse")

        assert entry["engine"] == "clickhouse"
        assert [version["version"] for version in entry["versions"]] == ["25.8", "26.3", "26.8"]

    def test_it_is_an_engine_the_registry_knows_and_the_host_never_lists(self) -> None:
        assert DatabaseRegistry.canonical("ch") == "clickhouse"
        assert ClickHouseManager.CONTAINER_ONLY is True
        assert all(m.ENGINE_NAME != "clickhouse" for m in DatabaseRegistry.get_all_managers())
        service = DatabaseService()
        assert all(m.ENGINE_NAME != "clickhouse" for m in service.host_managers())


# ===================================================================== the compose project


class TestTheComposeProject:
    def document(self, **kwargs: Any) -> dict[str, Any]:
        item = record("clickhouse", **kwargs)
        return compose.build_document(item)["services"]["db"]

    def test_it_publishes_http_on_the_loopback_with_the_open_files_clickhouse_asks_for(
        self,
    ) -> None:
        service = self.document(image_tag=None)

        assert service["ports"] == ["127.0.0.1:55432:8123"]
        assert service["ulimits"] == {"nofile": {"soft": 262144, "hard": 262144}}
        assert "command" not in service

    def test_its_health_check_asks_the_liveness_url_that_needs_no_account(self) -> None:
        test = self.document()["healthcheck"]["test"]

        assert test[0] == "CMD-SHELL"
        assert "http://127.0.0.1:8123/ping" in test[1]

    def test_the_configuration_is_mounted_where_clickhouse_merges_it(self) -> None:
        volumes = self.document()["volumes"]

        assert any(
            volume.endswith(":/etc/clickhouse-server/config.d/noust.xml:ro") for volume in volumes
        )
        assert any(volume.endswith(":/var/lib/clickhouse") for volume in volumes)
        assert any(volume.endswith(":/var/log/clickhouse-server") for volume in volumes)

    def test_the_environment_creates_the_administrator_that_may_manage_accounts(self) -> None:
        text = compose.render_env("clickhouse", PASSWORD)

        assert f"CLICKHOUSE_PASSWORD={PASSWORD}" in text
        assert "CLICKHOUSE_USER=noust_admin" in text
        assert "CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1" in text

    def test_the_configuration_gives_the_backups_disk_and_holds_no_password(self) -> None:
        text = compose.render_engine_config(record("clickhouse"), PASSWORD)

        assert text is not None
        assert PASSWORD not in text
        assert text.startswith("<!-- Generated by Noust")
        assert "<allowed_disk>backups</allowed_disk>" in text
        assert "<path>/var/lib/clickhouse/noust-backups/</path>" in text
        assert "<max_server_memory_usage_to_ram_ratio>0.75" in text

    def test_it_is_well_formed_xml(self) -> None:
        import xml.etree.ElementTree as ET

        text = compose.render_engine_config(record("clickhouse"), PASSWORD)
        assert text is not None
        tree = ET.fromstring(text)  # noqa: S314 - a document Noust wrote itself
        assert tree.tag == "clickhouse"

    def test_the_compose_file_has_no_password_and_parses_back(self) -> None:
        text = compose.render_compose(record("clickhouse"))

        assert PASSWORD not in text
        assert yaml.safe_load(text) == compose.build_document(record("clickhouse"))

    def test_tls_is_refused_before_anything_changes(self) -> None:
        assert compose.tls_supported("clickhouse", "26.8", "required") is not None
        assert compose.tls_supported("clickhouse", "26.8", "off") is None


# ===================================================================== the client


class TestTheClientScript:
    """The shell script every client inside a container runs through, run for real."""

    @pytest.mark.allow_subprocess
    def test_the_user_and_password_reach_the_client_in_a_file_and_never_in_an_argument(
        self, tmp_path: Path
    ) -> None:
        import subprocess

        fake = tmp_path / "clickhouse-client"
        fake.write_text('#!/bin/sh\nprintf "ARGS:%s\\n" "$*"\ncat "$2"\n')
        fake.chmod(0o755)
        env = {
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "CLICKHOUSE_PASSWORD": "pa&ss<w>ord",
            "TMPDIR": str(tmp_path),
        }

        outcome = subprocess.run(
            [
                "sh",
                "-c",
                CLIENT_SCRIPT,
                "sh",
                "clickhouse",
                "noust_admin",
                "CLICKHOUSE_PASSWORD",
                "clickhouse-client",
                "--query",
                "SELECT 1",
            ],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

        assert outcome.returncode == 0, outcome.stderr
        arguments = outcome.stdout.splitlines()[0]
        assert arguments.startswith("ARGS:--config-file ")
        assert "pa&ss" not in arguments and "noust_admin" not in arguments
        assert arguments.endswith("--query SELECT 1")
        assert (
            "<config><user>noust_admin</user><password>pa&amp;ss&lt;w&gt;ord</password></config>"
            in outcome.stdout
        )
        assert list(tmp_path.glob("tmp.*")) == []

    @pytest.mark.allow_subprocess
    def test_without_a_password_the_client_runs_as_it_is(self, tmp_path: Path) -> None:
        import subprocess

        fake = tmp_path / "clickhouse-client"
        fake.write_text('#!/bin/sh\nprintf "ARGS:%s" "$*"\n')
        fake.chmod(0o755)

        outcome = subprocess.run(
            [
                "sh",
                "-c",
                CLIENT_SCRIPT,
                "sh",
                "clickhouse",
                "default",
                "CLICKHOUSE_PASSWORD",
                "clickhouse-client",
                "--version",
            ],
            capture_output=True,
            text=True,
            env={"PATH": f"{tmp_path}:/usr/bin:/bin"},
            check=False,
        )

        assert outcome.stdout == "ARGS:--version"

    def test_only_the_client_is_given_the_configuration(self) -> None:
        instance = stub_instance(record("clickhouse"), state="running")

        client = instance.exec_argv(["clickhouse-client", "--query", "SELECT 1"])
        other = instance.exec_argv(["cat", "/var/lib/clickhouse/noust-backups/x.zip"])

        assert client[client.index("-c") + 3] == "clickhouse"
        assert client[client.index("-c") + 4] == "noust_admin"
        assert other[other.index("-c") + 3] == "env"


# ===================================================================== databases


class TestDatabases:
    def test_they_are_listed_with_their_size_and_tables(
        self, manager: ClickHouseManager, server: Server
    ) -> None:
        databases = manager.list_databases()

        assert [(item.name, item.tables) for item in databases] == [("default", 0), ("shop", 3)]
        assert databases[1].size == "4.0 KB"
        assert databases[1].engine == "clickhouse@ch"

    def test_one_is_created_dropped_and_asked_about(
        self, manager: ClickHouseManager, server: Server
    ) -> None:
        info = manager.create_database("analytics")
        assert info.name == "analytics"
        assert manager.database_exists("analytics")

        manager.drop_database("analytics")
        assert not manager.database_exists("analytics")
        assert any("DROP DATABASE `analytics` SYNC" in sql for sql in server.statements)

    def test_creating_one_that_exists_is_refused(self, manager: ClickHouseManager) -> None:
        with pytest.raises(DatabaseExistsError):
            manager.create_database("shop")

    def test_a_clickhouse_database_is_never_dropped_or_made(
        self, manager: ClickHouseManager
    ) -> None:
        with pytest.raises(DatabaseError, match="own databases"):
            manager.drop_database("system")
        with pytest.raises(DatabaseExistsError):
            manager.create_database("system")

    def test_dropping_a_missing_one_is_an_error_unless_forced(
        self, manager: ClickHouseManager
    ) -> None:
        with pytest.raises(DatabaseNotFoundError):
            manager.drop_database("ghost")
        manager.drop_database("ghost", force=True)

    @pytest.mark.parametrize("name", ["", "a;DROP DATABASE shop", "x" * 64, "a b", "a\n"])
    def test_a_name_that_is_not_one_is_refused_before_the_client_hears_of_it(
        self, manager: ClickHouseManager, server: Server, name: str
    ) -> None:
        with pytest.raises(DatabaseError):
            manager.create_database(name)
        assert server.statements == []

    def test_a_listing_that_fails_is_never_an_empty_one(
        self, manager: ClickHouseManager, server: Server
    ) -> None:
        server.fail_on = "system.databases"
        server.fail_message = "Code: 516. DB::Exception: noust_admin: Authentication failed"

        with pytest.raises(DatabaseError, match="does not let Noust sign in"):
            manager.list_databases()

    def test_the_owner_gets_every_privilege_on_a_new_database(
        self, manager: ClickHouseManager, server: Server
    ) -> None:
        manager.create_user("shop_app", "pw")
        manager.create_database("analytics", owner="shop_app")

        assert ("shop_app", "analytics", "ALL") in server.grants


# ===================================================================== accounts


class TestAccounts:
    def test_creating_one_sends_the_password_on_stdin_only(
        self, manager: ClickHouseManager, server: Server
    ) -> None:
        user, password = manager.create_user("shop_app", SECRET)

        assert (user.username, password) == ("shop_app", SECRET)
        assert any(
            f"CREATE USER `shop_app` IDENTIFIED WITH sha256_password BY '{SECRET}'" in sql
            for sql in server.statements
        )
        for call in server.calls:
            assert SECRET not in " ".join(call)

    def test_a_generated_password_is_returned_and_works_in_a_url(
        self, manager: ClickHouseManager
    ) -> None:
        _, password = manager.create_user("shop_app")

        assert len(password) >= 24 and password.isalnum()

    def test_a_password_with_a_quote_cannot_end_the_literal(
        self, manager: ClickHouseManager, server: Server
    ) -> None:
        manager.create_user("shop_app", "a'b\\c")

        assert server.users["shop_app"] == "a\\'b\\\\c"

    def test_an_existing_one_is_refused_and_a_missing_one_is_said(
        self, manager: ClickHouseManager
    ) -> None:
        with pytest.raises(DatabaseUserError):
            manager.create_user("noust_admin")
        with pytest.raises(DatabaseUserError, match="does not exist"):
            manager.drop_user("ghost")

    def test_the_password_is_changed_on_stdin(
        self, manager: ClickHouseManager, server: Server
    ) -> None:
        manager.create_user("shop_app", "old")
        manager.set_user_password("shop_app", SECRET)

        assert server.users["shop_app"] == SECRET
        assert any(sql.startswith("ALTER USER `shop_app`") for sql in server.statements)
        for call in server.calls:
            assert SECRET not in " ".join(call)

    def test_privileges_are_a_whitelist(self, manager: ClickHouseManager, server: Server) -> None:
        manager.create_user("shop_app", "pw")
        manager.grant_privileges("shop_app", "shop", ["select", "insert"])
        assert ("shop_app", "shop", "SELECT") in server.grants

        with pytest.raises(DatabaseUserError):
            manager.grant_privileges("shop_app", "shop", ["SELECT; DROP USER x"])
        with pytest.raises(DatabaseUserError):
            manager.grant_privileges("shop_app", "shop", ["ACCESS MANAGEMENT"])

        manager.revoke_privileges("shop_app", "shop", ["SELECT"])
        assert ("shop_app", "shop", "SELECT") not in server.grants

    def test_users_are_listed_with_the_databases_they_hold(
        self, manager: ClickHouseManager
    ) -> None:
        manager.create_user("shop_app", "pw")
        manager.grant_privileges("shop_app", "shop", ["SELECT"])

        listed = {user.username: user for user in manager.list_users()}

        assert listed["shop_app"].databases == ["shop"]
        assert listed["shop_app"].privileges == ["SELECT"]
        assert manager.is_internal_user("noust_admin") and manager.is_internal_user("default")
        assert not manager.is_internal_user("shop_app")


class TestProfiles:
    @pytest.mark.parametrize(
        ("profile", "granted"),
        [
            ("owner", {"ALL"}),
            (
                "read_write",
                {"SELECT", "INSERT", "ALTER UPDATE", "ALTER DELETE", "TRUNCATE", "SHOW"},
            ),
            ("read_only", {"SELECT", "SHOW"}),
        ],
    )
    def test_a_profile_replaces_what_the_account_held(
        self, manager: ClickHouseManager, server: Server, profile: str, granted: set[str]
    ) -> None:
        manager.create_user("shop_app", "pw")
        manager.grant_privileges("shop_app", "shop", ["DROP", "CREATE"])

        manager.apply_profile("shop_app", "shop", profile)

        held = {kind for user, db, kind in server.grants if user == "shop_app" and db == "shop"}
        assert held == granted

    def test_the_two_statements_run_together_so_a_failure_leaves_no_half(
        self, manager: ClickHouseManager, server: Server
    ) -> None:
        manager.create_user("shop_app", "pw")

        manager.apply_profile("shop_app", "shop", "read_only")

        sql = server.statements[-1]
        assert sql.index("REVOKE ALL") < sql.index("GRANT SELECT, SHOW")

    def test_access_is_read_back_as_the_profile_it_amounts_to(
        self, manager: ClickHouseManager
    ) -> None:
        for name, profile in (("o", "owner"), ("w", "read_write"), ("r", "read_only")):
            manager.create_user(name, "pw")
            manager.apply_profile(name, "shop", profile)
        manager.create_user("c", "pw")
        manager.grant_privileges("c", "shop", ["DROP"])

        access = {entry.username: entry.profile for entry in manager.list_access("shop")}

        assert access == {"o": "owner", "w": "read_write", "r": "read_only", "c": "custom"}

    def test_an_unknown_profile_or_account_is_refused(self, manager: ClickHouseManager) -> None:
        manager.create_user("shop_app", "pw")
        with pytest.raises(DatabaseUserError, match="Unknown access profile"):
            manager.apply_profile("shop_app", "shop", "root")
        with pytest.raises(DatabaseUserError, match="does not exist"):
            manager.apply_profile("ghost", "shop", "read_only")


# ===================================================================== backups


class TestBackup:
    def test_it_is_clickhouses_own_backup_copied_out_and_removed_from_the_container(
        self, manager: ClickHouseManager, server: Server
    ) -> None:
        info = manager.backup("shop")

        assert isinstance(info, BackupInfo)
        assert info.path.name.startswith("clickhouse.ch.shop-") and info.path.suffix == ".zip"
        assert info.compressed is False
        assert info.path.read_bytes().startswith(b"PK-backup-of-shop")
        sql = next(item for item in server.statements if item.startswith("BACKUP"))
        assert sql.startswith("BACKUP DATABASE `shop` AS `noust_dump` TO Disk('backups', 'noust-")
        assert server.files == {}

    def test_the_directory_is_prepared_for_the_account_the_server_runs_as(
        self, manager: ClickHouseManager, server: Server
    ) -> None:
        manager.backup("shop")

        prepared = [
            call
            for call in server.calls
            if call[-2:]
            == (
                "-c",
                f"mkdir -p {compose.CLICKHOUSE_BACKUP_DIR} && chown "
                f"clickhouse:clickhouse {compose.CLICKHOUSE_BACKUP_DIR}",
            )
        ]
        assert len(prepared) == 1

    def test_a_failed_backup_says_what_clickhouse_said_and_leaves_nothing_behind(
        self, manager: ClickHouseManager, server: Server
    ) -> None:
        server.fail_on = "BACKUP DATABASE"

        with pytest.raises(DatabaseBackupError) as caught:
            manager.backup("shop")

        assert "boom" in (caught.value.details or "")
        assert server.files == {}

    def test_a_copy_that_fails_still_removes_the_file_from_the_container(
        self, manager: ClickHouseManager, server: Server
    ) -> None:
        server.cat_fails = True

        with pytest.raises(DatabaseBackupError):
            manager.backup("shop")

        assert server.files == {}

    def test_it_is_refused_when_the_container_has_no_backups_disk(
        self, manager: ClickHouseManager, server: Server
    ) -> None:
        server.disk_defined = False

        with pytest.raises(DatabaseBackupError, match="no disk called backups") as caught:
            manager.backup("shop")

        assert "noust db instance create" in (caught.value.details or "")
        assert not any(item.startswith("BACKUP") for item in server.statements)

    def test_a_missing_database_and_tables_to_leave_out_are_refused_first(
        self, manager: ClickHouseManager, server: Server
    ) -> None:
        with pytest.raises(DatabaseNotFoundError):
            manager.backup("ghost")
        with pytest.raises(DatabaseBackupError, match="cannot leave tables out"):
            manager.backup("shop", exclude_tables=["events"])
        assert not any(item.startswith("BACKUP") for item in server.statements)

    def test_it_is_listed_with_the_dumps_of_this_instance_only(
        self, manager: ClickHouseManager
    ) -> None:
        manager.backup("shop")

        assert [item.database for item in manager.list_backups()] == ["shop"]
        assert manager.list_backups("other") == []


class TestRestore:
    @pytest.fixture
    def dump(self, tmp_path: Path) -> Path:
        return backup_zip(tmp_path / "clickhouse.ch.shop-20261016_100000.zip")

    def test_it_copies_the_file_in_restores_it_under_the_name_asked_and_removes_it(
        self, manager: ClickHouseManager, server: Server, dump: Path
    ) -> None:
        outcome = manager.restore("shop_copy", dump, safety_backup=False)

        assert outcome.database == "shop_copy" and outcome.replaced is False
        sql = next(item for item in server.statements if item.startswith("RESTORE"))
        assert sql.startswith("RESTORE DATABASE `noust_dump` AS `shop_copy` FROM Disk('backups', ")
        assert len(server.copied_in) == 1
        assert next(iter(server.copied_in.values())) == dump.read_bytes()
        assert server.files == {}
        assert manager.database_exists("shop_copy")

    def test_replacing_a_database_takes_a_safety_copy_and_drops_it_first(
        self, manager: ClickHouseManager, server: Server, dump: Path
    ) -> None:
        outcome = manager.restore("shop", dump, drop_existing=True)

        assert outcome.replaced is True and outcome.safety_copy is not None
        order = [
            item for item in server.statements if item.startswith(("BACKUP", "DROP", "RESTORE"))
        ]
        assert [item.split()[0] for item in order] == ["BACKUP", "DROP", "RESTORE"]

    def test_over_an_existing_database_without_drop_it_says_how(
        self, manager: ClickHouseManager, dump: Path
    ) -> None:
        with pytest.raises(DatabaseBackupError, match="already exists") as caught:
            manager.restore("shop", dump, safety_backup=False)
        assert "--drop" in (caught.value.details or "")

    def test_a_file_that_is_not_a_backup_is_refused_before_anything_changes(
        self, manager: ClickHouseManager, server: Server, tmp_path: Path
    ) -> None:
        bad = tmp_path / "clickhouse.ch.shop-20261016_100000.zip"
        bad.write_bytes(b"not a zip")

        with pytest.raises(DatabaseBackupError, match="not a ClickHouse backup"):
            manager.restore("shop_copy", bad, safety_backup=False)
        assert server.statements == []

    def test_a_failed_restore_with_a_safety_copy_puts_the_database_back(
        self, manager: ClickHouseManager, server: Server, dump: Path
    ) -> None:
        server.fail_on = "AS `shop` FROM Disk"
        server.fail_times = 1

        with pytest.raises(DatabaseBackupError, match="previous contents were put back") as caught:
            manager.restore("shop", dump, drop_existing=True)

        assert "boom" in (caught.value.details or "")
        assert "shop" in server.databases
        restores = [item for item in server.statements if item.startswith("RESTORE")]
        assert len(restores) == 2

    def test_the_restore_test_loads_into_a_temporary_database_and_drops_it(
        self, manager: ClickHouseManager, server: Server, dump: Path
    ) -> None:
        outcome = restore_test(manager, dump)

        assert outcome.ok, outcome.detail
        assert outcome.database.startswith("noust_verify_")
        assert outcome.database not in server.databases
        assert "shop" in server.databases and server.databases["shop"] == (4096, 3)


class TestVerification:
    def test_a_whole_backup_passes(self, manager: ClickHouseManager, tmp_path: Path) -> None:
        check = check_dump(manager, backup_zip(tmp_path / "b.zip"))

        assert check.ok and check.method == "zip"

    def test_a_cut_short_or_foreign_archive_does_not(
        self, manager: ClickHouseManager, tmp_path: Path
    ) -> None:
        assert not check_dump(manager, backup_zip(tmp_path / "a.zip", with_metadata=False)).ok
        truncated = tmp_path / "t.zip"
        truncated.write_bytes(backup_zip(tmp_path / "w.zip").read_bytes()[:40])
        assert not check_dump(manager, truncated).ok
        assert backup_problem(tmp_path / "w.zip") is None

    def test_a_flipped_byte_in_the_data_is_found(self, tmp_path: Path) -> None:
        path = backup_zip(tmp_path / "f.zip")
        content = bytearray(path.read_bytes())
        content[content.index(b"\x00" * 64) + 10] ^= 0xFF
        path.write_bytes(bytes(content))

        assert "checksum" in (backup_problem(path) or "")


# ===================================================================== the rest


class TestTheRest:
    def test_a_statement_runs_as_the_administrator_and_a_read_only_one_is_refused(
        self, manager: ClickHouseManager, server: Server
    ) -> None:
        ok, output = manager.execute_query("shop", "SELECT 1")
        assert ok

        with pytest.raises(DatabaseQueryError, match="no read-only mode"):
            manager.execute_query("shop", "SELECT 1", read_only=True)
        with pytest.raises(DatabaseNotFoundError):
            manager.execute_query("ghost", "SELECT 1")

    def test_a_failing_statement_carries_clickhouses_words(
        self, manager: ClickHouseManager, server: Server
    ) -> None:
        server.fail_on = "SELECT nope"
        with pytest.raises(DatabaseQueryError) as caught:
            manager.execute_query("shop", "SELECT nope")
        assert "boom" in (caught.value.details or "")

    def test_the_connection_string_is_the_http_one_with_credentials_encoded(
        self, manager: ClickHouseManager
    ) -> None:
        url = manager.get_connection_string("shop", "app", "p@ss/word")

        assert url == f"http://app:p%40ss%2Fword@localhost:{manager.server_port()}/shop"

    def test_the_shell_opens_on_the_database(self, manager: ClickHouseManager) -> None:
        argv = manager.get_interactive_command("shop")

        assert argv[:3] == ["docker", "exec", "-it"]
        assert argv[-2:] == ["--database", "shop"]

    def test_capabilities_are_only_what_it_does(self, manager: ClickHouseManager) -> None:
        assert manager.CAPABILITIES == frozenset({"users", "profiles", "dump"})
        status = manager.get_status()
        assert status["capabilities"] == ["dump", "profiles", "users"]
        assert status["version"] == "26.8.1"
        assert status["kind"] == "container"

    def test_on_the_host_there_is_nothing_to_install_or_ask(self, server: Server) -> None:
        host = ClickHouseManager()

        assert host.is_installed() is False
        with pytest.raises(DatabaseError, match="runs ClickHouse as an instance"):
            host.install()
        with pytest.raises(DatabaseError, match="instance Noust makes"):
            host.list_databases()


class TestQuoting:
    def test_identifiers_and_literals_escape_what_ends_them(self) -> None:
        assert identifier("a`b") == "`a\\`b`"
        assert identifier("a\\b") == "`a\\\\b`"
        assert literal("it's") == "'it\\'s'"
        assert literal("a\\") == "'a\\\\'"

    def test_tabseparated_escaping_is_undone(self) -> None:
        assert unescape("a\\tb\\nc\\\\d") == "a\tb\nc\\d"
        assert unescape("plain") == "plain"

    def test_the_dump_name_is_the_one_the_statements_use(self) -> None:
        assert DUMP_NAME == "noust_dump"


class TestMakingOne:
    @pytest.fixture
    def world(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
        built = build_world(tmp_path, monkeypatch)
        try:
            yield built
        finally:
            close_world()

    def test_the_instance_comes_up_with_its_files_and_its_password_in_the_environment_file_only(
        self, world: World
    ) -> None:
        world.host.exec_stdout = ""

        described = world.managed.create("ch", "clickhouse", version="26.8", memory="2g")

        assert described["key"] == "clickhouse@ch"
        env = layout.env_path("ch").read_text()
        password = world.password("ch")
        assert f"CLICKHOUSE_PASSWORD={password}" in env
        assert "CLICKHOUSE_USER=noust_admin" in env
        config = layout.config_path("ch").read_text()
        assert "<allowed_disk>backups</allowed_disk>" in config and password not in config
        compose_text = layout.compose_path("ch").read_text()
        assert "127.0.0.1:" in compose_text and ":8123" in compose_text
        assert password not in compose_text
        for call in world.host.calls:
            assert all(password not in part for part in call)

    def test_less_memory_than_it_starts_in_is_refused_before_anything_is_made(
        self, world: World
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            world.managed.plan("ch", "clickhouse", version="26.8", memory="512m")

        assert (
            "1024" in str(caught.value)
            or "1 GB" in str(caught.value)
            or "1g" in str(caught.value.details)
        )
        assert not layout.directory("ch").exists()

    def test_it_takes_no_initialization_script(self, world: World) -> None:
        with pytest.raises(ValidationError):
            world.managed.plan(
                "ch",
                "clickhouse",
                version="26.8",
                memory="2g",
                init_scripts=[("01.sql", b"SELECT 1")],
            )
