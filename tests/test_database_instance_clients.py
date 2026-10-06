# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an instance Noust runs asks of the clients inside its container.

Found by running the managers against the real images: ``mongosh`` 2 prints its
prompt around every result when its stdin is a pipe, so a script given on stdin
came back as ``admin> {"ok":1}`` and the listing could not be parsed. An instance
Noust runs has that shell, so it is started with an empty prompt (``--shell --eval``;
evaluating the script with ``eval`` instead breaks the shell's waiting for its
commands' results); every other container is run as it was.
And what a key says about where a database is must not decide which clients a
tunnel suggests, or which scheme a URL has.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from noust.core.db_instance_store import READY, InstanceRecord
from noust.core.runner import FakeRunner
from noust.managers.database.exposure import tunnel_instructions
from noust.managers.database.instance_catalog import SPECS, resolve_pin
from noust.managers.database.instance_records import stub_instance
from noust.managers.database.instances import discover
from noust.managers.database.mongodb import MongoDBManager
from noust.managers.database.mysql import MySQLManager
from tests.test_database_instances import container

SILENT = 'void (prompt = () => "")'


def mongo_instance() -> InstanceRecord:
    spec = SPECS["mongodb"]
    pin = resolve_pin(spec, "8.0")
    return InstanceRecord(
        id=1,
        name="docs",
        flavour="mongodb",
        version=pin.version,
        image=spec.repository,
        digest=pin.digest,
        previous_digest=None,
        host_port=27018,
        memory_limit_mb=1024,
        state=READY,
        created_at="t",
        created_by=None,
        updated_at="t",
    )


def shell_calls(runner: FakeRunner) -> list[tuple[str, ...]]:
    return [call for call in runner.calls if "mongosh,mongo" in call]


class TestMongoShell:
    def test_an_instances_shell_is_told_to_print_no_prompt_around_a_result(
        self, runner: FakeRunner
    ) -> None:
        runner.script(["docker", "exec"], stdout='{"databases":[]}')
        manager = MongoDBManager().bind(stub_instance(mongo_instance(), state="running"))

        manager._execute_mongo("db.adminCommand({ping: 1})")

        (call,) = shell_calls(runner)
        assert call[-4:] == ("--quiet", "--shell", "--eval", SILENT)
        assert runner.inputs[-1] is not None and "db.adminCommand({ping: 1})" in runner.inputs[-1]

    def test_the_script_is_on_stdin_and_not_in_any_argument(self, runner: FakeRunner) -> None:
        runner.script(["docker", "exec"], stdout="")
        manager = MongoDBManager().bind(stub_instance(mongo_instance(), state="running"))

        manager._execute_mongo("db.getSiblingDB('x').createUser({pwd: 'a-secret-value'})")

        (call,) = shell_calls(runner)
        assert not any("a-secret-value" in part for part in call)
        assert "a-secret-value" in (runner.inputs[-1] or "")

    def test_a_container_noust_did_not_make_is_run_as_it_was(self, runner: FakeRunner) -> None:
        entry = container(
            "legacy-mongo",
            "mongo:4.4",
            env=["MONGO_INITDB_ROOT_USERNAME=root", "MONGO_INITDB_ROOT_PASSWORD=x"],
            project="legacy",
            service="mongo",
            exposed=("27017/tcp",),
        )
        runner.script(["docker", "ps"], stdout=f"{entry['Id']}\t{entry['Config']['Image']}\n")
        runner.script(["docker", "inspect"], stdout=json.dumps([entry]))
        (found,) = discover(runner)
        runner.calls.clear()
        runner.script(["docker", "exec"], stdout="")
        manager = MongoDBManager().bind(found)

        manager._execute_mongo("db.adminCommand({ping: 1})")

        (call,) = shell_calls(runner)
        assert "--eval" not in call


class TestKeysSayWhereNotWhat:
    def test_a_tunnel_to_an_instance_suggests_the_engines_clients(self) -> None:
        tunnel = tunnel_instructions(
            "postgresql@pg16",
            database="shop",
            username="shop_user",
            remote_port=5433,
            server="203.0.113.9",
            masked_url="",
        )

        assert tunnel.local_port == 15433
        assert tunnel.clients["psql"] == "psql -h 127.0.0.1 -p 15433 -U shop_user -d shop"
        assert tunnel.clients["jdbc"] == "jdbc:postgresql://127.0.0.1:15433/shop"

    def test_the_other_engines_too(self) -> None:
        for key, client in (
            ("mysql@maria", "mysql"),
            ("redis@cache", "redis-cli"),
            ("mongodb@docs", "mongosh"),
        ):
            tunnel = tunnel_instructions(
                key, database="d", username="u", remote_port=7000, server="s", masked_url=""
            )
            assert client in tunnel.clients, key


class TestMysqlDumps:
    """Found on MySQL 9.7: its image has GTIDs on, and a dump of it could not be restored."""

    def dump_argv(self, runner: FakeRunner, flavour: str, tmp_path: Path) -> tuple[str, ...]:
        runner.script(["docker", "exec"], stdout="shop\n")
        record = mongo_instance()
        spec = SPECS[flavour]
        pin = resolve_pin(spec, None)
        record = replace(
            record, name="m", flavour=flavour, version=pin.version, image=spec.repository,
            digest=pin.digest, host_port=3307,
        )  # fmt: skip
        manager = MySQLManager().bind(stub_instance(record, state="running"))
        manager.BACKUP_DIR = tmp_path
        info = manager.backup("shop", compress=False)
        return runner.written[info.path]

    def test_a_mysql_dump_does_not_set_gtid_purged_so_it_can_be_loaded_back(
        self, runner: FakeRunner, tmp_path: Path
    ) -> None:
        argv = self.dump_argv(runner, "mysql", tmp_path)

        assert "--set-gtid-purged=OFF" in argv
        assert "--single-transaction" in argv

    def test_a_mariadb_dump_is_not_given_an_option_its_dump_tool_does_not_have(
        self, runner: FakeRunner, tmp_path: Path
    ) -> None:
        argv = self.dump_argv(runner, "mariadb", tmp_path)

        assert not any("gtid" in part for part in argv)
        assert "--single-transaction" in argv
