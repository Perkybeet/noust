# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Data safety of the databases Docker runs (3.3 review).

What is defended, each with a runner that answers like the real thing
instead of "every docker exec succeeds":

- **A host file is handled on the host.** Staging, decompressing and copying
  a dump never runs inside the container, where the host's paths do not
  exist; a plain dump is the loader's stdin as it is.
- **Nothing is dropped before the dump is ready.** A gzip that does not
  decompress costs the database nothing, and the safety copy is made
  loadable before the put-back drops anything either.
- **A restore that runs out of time is a failed restore** and is put back.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import DatabaseBackupError
from noust.core.runner import CommandResult, FakeRunner, set_runner
from noust.managers.database.base import (
    RESTORE_TIMEOUT_CAP,
    TRANSFER_TIMEOUT,
    restore_timeout,
)
from noust.managers.database.instances import CLIENT_SCRIPT, DatabaseInstance, discover
from noust.managers.database.mongodb import MongoDBManager
from noust.managers.database.mysql import MySQLManager
from noust.managers.database.postgres import PostgresManager
from tests.test_database_instances import FLEET, PS_LINES, container

PG_KEY = "postgresql@empleo-arennalabs-com.db"
MYSQL_KEY = "mysql@tienda-arennalabs-com.mysql"

#: Answers a call, or None to let the scripted default answer.
Answer = Callable[[tuple[str, ...], "str | None", "Path | None"], "CommandResult | None"]


class Docker(FakeRunner):
    """
    A FakeRunner whose answers can depend on what a call carries.

    ``docker exec`` hides the client behind ``sh -c CLIENT_SCRIPT``, out of
    reach of a prefix; the answer function sees the whole argv, the stdin
    text and the stdin file.
    """

    def __init__(self) -> None:
        super().__init__()
        self.answer: Answer = lambda argv, text, path: None
        #: The index in :attr:`calls` of every run, with the text on its stdin.
        self.sent: list[tuple[int, str | None]] = []

    def run(self, argv: Any, **kwargs: Any) -> CommandResult:  # type: ignore[override]
        result = super().run(argv, **kwargs)
        self.sent.append((len(self.calls) - 1, kwargs.get("input")))
        custom = self.answer(
            tuple(str(a) for a in argv), kwargs.get("input"), kwargs.get("stdin_path")
        )
        return custom if custom is not None else result


def inner(call: tuple[str, ...]) -> tuple[str, ...]:
    """The client a ``docker exec`` runs, program first, or () for a host call."""
    if call[:2] != ("docker", "exec") or CLIENT_SCRIPT not in call:
        return ()
    return call[call.index(CLIENT_SCRIPT) + 5 :]


def ok(stdout: str = "") -> CommandResult:
    return CommandResult(argv=(), exit_code=0, stdout=stdout)


def failed(stderr: str, *, timed_out: bool = False) -> CommandResult:
    return CommandResult(
        argv=(), exit_code=-9 if timed_out else 1, stderr=stderr, timed_out=timed_out
    )


@pytest.fixture
def docker() -> Iterator[Docker]:
    fake = Docker()
    fake.script(["docker", "ps"], stdout=PS_LINES + "\n")
    fake.script(["docker", "inspect"], stdout=json.dumps(FLEET))
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture
def instances(docker: Docker) -> dict[str, DatabaseInstance]:
    found = {item.key: item for item in discover(docker)}
    docker.calls.clear()
    return found


def no_host_path_inside_a_container(runner: FakeRunner, *paths: Path) -> None:
    """No ``docker exec`` names a file of the host, nor copies or decompresses one."""
    for call in runner.calls:
        client = inner(call)
        if not client:
            continue
        assert client[0] not in ("cp", "gzip", "test", "stat"), call
        for path in paths:
            assert not any(str(path) in part for part in call), call


class Cluster:
    """
    A server with one database, answering its clients the way the real one would.

    It exists until dropped and again once created, so a restore's own
    checks see what they would see on a server. A load is a client given a
    file on its stdin.
    """

    def __init__(self, docker: Docker, name: str) -> None:
        self.name = name
        self.exists = True
        self.loads: list[Path] = []
        #: What the loads answer, in order; success once exhausted.
        self.load_results: list[CommandResult] = []
        docker.answer = self.answer

    def answer(self, call: tuple[str, ...], text: str | None, path: Path | None):
        if not inner(call):
            return None
        sql = (text or "").upper()
        if "FROM PG_DATABASE WHERE DATNAME" in sql:
            return ok("1\n" if self.exists else "")
        if "INFORMATION_SCHEMA.SCHEMATA" in sql and "SCHEMA_NAME =" in sql:
            return ok(f"{self.name}\n" if self.exists else "")
        if "DROP DATABASE" in sql:
            self.exists = False
            return ok()
        if "CREATE DATABASE" in sql:
            self.exists = True
            return ok()
        if path is not None:
            self.loads.append(path)
            if self.load_results:
                return self.load_results.pop(0)
        return None


def sent_with(runner: Docker, needle: str) -> list[int]:
    """Where in the calls a statement holding ``needle`` was sent on stdin."""
    return [index for index, text in runner.sent if text is not None and needle in text]


def drops(runner: Docker) -> list[int]:
    """Where in the calls a DROP DATABASE was sent."""
    return sent_with(runner, "DROP DATABASE")


# ==================== Finding 1: staging on the host, before the drop ====================


class TestRestoringIntoAPostgresContainer:
    """psql and pg_restore read the dump on their stdin; the host prepares it."""

    @pytest.fixture
    def manager(self, instances, tmp_path: Path) -> PostgresManager:
        manager = PostgresManager().bind(instances[PG_KEY])
        manager.BACKUP_DIR = tmp_path / "backups"
        return manager

    @pytest.fixture
    def cluster(self, docker: Docker) -> Cluster:
        return Cluster(docker, "empleo")

    def test_a_plain_dump_is_the_clients_stdin_and_never_copied_inside(
        self, docker: Docker, manager: PostgresManager, cluster: Cluster, tmp_path: Path
    ) -> None:
        dump = tmp_path / "empleo.sql"
        dump.write_text("CREATE TABLE t (id int);\n")

        manager.restore("empleo", dump, drop_existing=True)

        no_host_path_inside_a_container(docker, dump, tmp_path)
        assert cluster.loads == [dump]
        load = docker.calls[-1]
        assert load[:3] == ("docker", "exec", "-i") and inner(load)[-2:] == ("-f", "-")

    def test_a_custom_dump_reaches_pg_restore_on_stdin(
        self, docker: Docker, manager: PostgresManager, cluster: Cluster, tmp_path: Path
    ) -> None:
        dump = tmp_path / "empleo.dump"
        dump.write_bytes(b"PGDMP\x01\x0e\x00archive")

        manager.restore("empleo", dump, drop_existing=True)

        no_host_path_inside_a_container(docker, dump, tmp_path)
        (load,) = [call for call in docker.calls if inner(call)[:1] == ("pg_restore",)]
        assert cluster.loads == [dump]
        assert str(dump) not in " ".join(load)

    def test_a_gzipped_dump_is_decompressed_on_the_host_before_the_drop(
        self, docker: Docker, manager: PostgresManager, cluster: Cluster, tmp_path: Path
    ) -> None:
        dump = tmp_path / "empleo.sql.gz"
        dump.write_bytes(gzip.compress(b"CREATE TABLE t (id int);\n"))

        manager.restore("empleo", dump, drop_existing=True)

        decompress = [i for i, c in enumerate(docker.calls) if c[:2] == ("gzip", "-dc")]
        assert docker.calls[decompress[0]] == ("gzip", "-dc", str(dump))
        assert decompress[0] < drops(docker)[0]
        no_host_path_inside_a_container(docker, tmp_path)

    def test_a_dump_that_cannot_be_staged_drops_nothing(
        self, docker: Docker, manager: PostgresManager, cluster: Cluster, tmp_path: Path
    ) -> None:
        docker.script(
            ["gzip", "-dc"], stderr="gzip: write error: No space left on device", exit_code=1
        )
        dump = tmp_path / "empleo.sql.gz"
        dump.write_bytes(gzip.compress(b"CREATE TABLE t (id int);\n"))

        with pytest.raises(DatabaseBackupError) as raised:
            manager.restore("empleo", dump, drop_existing=True)

        assert "No space left on device" in (raised.value.details or "")
        assert drops(docker) == []
        assert cluster.exists

    def test_a_failed_load_is_put_back_from_the_safety_copy_on_stdin(
        self, docker: Docker, manager: PostgresManager, cluster: Cluster, tmp_path: Path
    ) -> None:
        cluster.load_results = [failed('psql: ERROR:  syntax error at or near "CREAT"')]
        dump = tmp_path / "empleo.sql"
        dump.write_text("CREAT TABLE t (id int);\n")

        with pytest.raises(DatabaseBackupError) as raised:
            manager.restore("empleo", dump, drop_existing=True)

        assert "were put back" in raised.value.message
        first, safety = cluster.loads
        assert first == dump
        assert safety is not None and safety.parent == manager.BACKUP_DIR
        assert cluster.exists
        no_host_path_inside_a_container(docker, dump, tmp_path)

    def test_a_restore_that_runs_out_of_time_is_put_back(
        self, docker: Docker, manager: PostgresManager, cluster: Cluster, tmp_path: Path
    ) -> None:
        cluster.load_results = [failed("", timed_out=True)]
        dump = tmp_path / "empleo.sql"
        dump.write_text("CREATE TABLE t (id int);\n")

        with pytest.raises(DatabaseBackupError) as raised:
            manager.restore("empleo", dump, drop_existing=True)

        assert "were put back" in raised.value.message
        assert "deadline" in (raised.value.details or "")
        assert len(cluster.loads) == 2


class TestRestoringIntoAMySQLContainer:
    """The client reads the dump on its stdin, in a container as on the host."""

    @pytest.fixture
    def manager(self, instances, tmp_path: Path) -> MySQLManager:
        manager = MySQLManager().bind(instances[MYSQL_KEY])
        manager.BACKUP_DIR = tmp_path / "backups"
        return manager

    @pytest.fixture
    def tienda(self, docker: Docker) -> Cluster:
        return Cluster(docker, "tienda")

    def test_an_uncompressed_dump_is_not_copied_inside(
        self, docker: Docker, manager: MySQLManager, tienda: Cluster, tmp_path: Path
    ) -> None:
        dump = tmp_path / "tienda.sql"
        dump.write_text("CREATE TABLE t (id int);\n-- Dump completed\n")

        manager.restore("tienda", dump, drop_existing=True)

        no_host_path_inside_a_container(docker, dump, tmp_path)
        assert docker.stdin_paths[-1] == dump

    def test_a_gzipped_dump_is_decompressed_before_the_drop(
        self, docker: Docker, manager: MySQLManager, tienda: Cluster, tmp_path: Path
    ) -> None:
        dump = tmp_path / "tienda.sql.gz"
        dump.write_bytes(gzip.compress(b"CREATE TABLE t (id int);\n"))

        manager.restore("tienda", dump, drop_existing=True)

        decompress = next(i for i, c in enumerate(docker.calls) if c[:2] == ("gzip", "-dc"))
        (drop,) = drops(docker)
        assert decompress < drop
        no_host_path_inside_a_container(docker, tmp_path)


def test_a_mongo_archive_is_decompressed_on_the_host_before_the_drop(
    docker: Docker, tmp_path: Path
) -> None:
    docker.script(
        ["docker", "inspect"],
        stdout=json.dumps(
            [
                container(
                    "shop-mongo-1",
                    "mongo:7",
                    env=["MONGO_INITDB_ROOT_USERNAME=root", "MONGO_INITDB_ROOT_PASSWORD=pw"],
                    project="shop",
                    service="mongo",
                )
            ]
        ),
    )
    docker.script(["docker", "ps"], stdout="shop-mongo-1-id\tmongo:7\n")
    (instance,) = discover(docker)
    docker.script(["docker", "exec"], stdout='["shop"]\n')
    manager = MongoDBManager().bind(instance)
    manager.BACKUP_DIR = tmp_path / "backups"
    archive = tmp_path / "shop.archive.gz"
    archive.write_bytes(gzip.compress(b"archive"))
    docker.calls.clear()

    manager.restore("shop", archive, drop_existing=True)

    no_host_path_inside_a_container(docker, tmp_path)
    decompress = next(i for i, c in enumerate(docker.calls) if c[:2] == ("gzip", "-dc"))
    (drop,) = sent_with(docker, "dropDatabase")
    assert decompress < drop


# ==================== Finding 7: a deadline that fits the dump ====================


def test_a_restore_deadline_grows_with_the_dump_up_to_a_cap(tmp_path: Path) -> None:
    small = tmp_path / "small.sql"
    small.write_text("x")
    assert restore_timeout(small) == TRANSFER_TIMEOUT >= 7200

    class Huge:
        def stat(self) -> Any:
            return type("S", (), {"st_size": 10 * 1024**3})()

    big = restore_timeout(Huge())  # type: ignore[arg-type]
    assert TRANSFER_TIMEOUT < big <= RESTORE_TIMEOUT_CAP == 6 * 3600


# ==================== Finding 2: a container's dump is checked by its own pg_restore ====================


class TestCheckingAContainersDump:
    """The host may have no pg_restore, or an older one than the container's."""

    @pytest.fixture
    def archive(self, tmp_path: Path) -> Path:
        dump = tmp_path / "postgresql.empleo-arennalabs-com.db.empleo-20261003_010101.dump"
        dump.write_bytes(b"PGDMP\x01\x0e\x00archive")
        return dump

    def test_it_is_listed_inside_the_container_on_stdin(
        self, docker: Docker, instances, archive: Path
    ) -> None:
        from noust.managers.database.backup_verify import check_dump

        docker.script(["docker", "exec"], stdout="; Archive created\n1; 2 TABLE public t\n")
        docker.script(["pg_restore"], stderr="pg_restore: unsupported version (1.16)", exit_code=1)
        manager = PostgresManager().bind(instances[PG_KEY])

        outcome = check_dump(manager, archive)

        assert (outcome.ok, outcome.method) == (True, "pg_restore --list")
        (listing,) = [call for call in docker.calls if "pg_restore" in call]
        assert inner(listing) == ("pg_restore", "--list")
        assert docker.stdin_paths == [archive]
        assert not any(call[0] == "pg_restore" for call in docker.calls)

    def test_a_stopped_container_leaves_the_hosts(
        self, docker: Docker, instances, archive: Path
    ) -> None:
        from dataclasses import replace

        from noust.managers.database.backup_verify import check_dump

        docker.script(["pg_restore"], stdout="1; 2 TABLE public t\n")
        stopped = replace(instances[PG_KEY], state="exited")
        manager = PostgresManager().bind(stopped)

        outcome = check_dump(manager, archive)

        assert outcome.ok
        assert docker.calls == [("pg_restore", "--list", str(archive))]

    def test_the_hosts_engine_keeps_its_check(self, docker: Docker, archive: Path) -> None:
        from noust.managers.database.backup_verify import check_dump

        docker.script(["pg_restore"], stdout="1; 2 TABLE public t\n")

        assert check_dump(PostgresManager(), archive).ok
        assert docker.calls == [("pg_restore", "--list", str(archive))]


# ==================== Finding 3: the sampler never holds the collector's tick ====================


class TestTheSamplerKeepsToItsBudget:
    """One container that does not answer costs the tick a few seconds, once."""

    @pytest.fixture
    def release(self) -> Iterator[Any]:
        import threading

        event = threading.Event()
        yield event
        event.set()

    @pytest.fixture
    def asked(self, monkeypatch: pytest.MonkeyPatch, release: Any) -> list[str]:
        from noust.managers.database.metrics import DatabaseSampler

        asked: list[str] = []

        def engine_pairs(self, reader, manager, now):
            asked.append(manager.ENGINE_NAME)
            if manager.instance is not None:
                release.wait(10)
                return [(f"db.{manager.ENGINE_NAME}.connections", 2.0)]
            return [("db.postgresql.connections", 1.0)]

        monkeypatch.setattr(DatabaseSampler, "_engine_pairs", engine_pairs)
        return asked

    def service(self, tmp_path: Path, found: Callable[[], list[DatabaseInstance]]) -> Any:
        from noust.core.secrets import SecretStore
        from noust.core.store import NoustStore
        from noust.managers.database.service import DatabaseService

        return DatabaseService(
            store=NoustStore(tmp_path / "noust.db"),
            secrets=SecretStore(root=tmp_path / "secrets"),
            resolve=lambda name: PostgresManager() if name == "postgresql" else None,
            engines=lambda: ["postgresql"],
            instances=found,
        )

    def test_a_hung_container_is_left_out_and_not_asked_again_while_it_waits(
        self, docker: Docker, instances, asked: list[str], release: Any, tmp_path: Path
    ) -> None:
        import time

        from noust.managers.database.metrics import DatabaseSampler

        docker.script(["systemctl", "is-active"], stdout="active\n")
        service = self.service(tmp_path, lambda: [instances[PG_KEY]])
        sampler = DatabaseSampler(service=service, budget=0.3, backoff=600)

        started = time.monotonic()
        pairs = sampler.sample(0.0)
        took = time.monotonic() - started

        assert took < 2.0
        assert pairs == [("db.postgresql.connections", 1.0)]
        assert sorted(asked) == ["postgresql", PG_KEY]

        # A minute later its last ask still waits: it is not asked twice.
        assert sampler.sample(60.0) == [("db.postgresql.connections", 1.0)]
        assert asked.count(PG_KEY) == 1

        # It answers at last, but stays out until the back-off has passed.
        release.set()
        for _ in range(100):
            if not any(t.is_alive() for t, _since in sampler._stalled.values()):
                break
            time.sleep(0.01)
        sampler.sample(120.0)
        assert asked.count(PG_KEY) == 1
        sampler.sample(700.0)
        assert asked.count(PG_KEY) == 2

    def test_a_docker_that_does_not_answer_leaves_the_hosts_engines_sampled(
        self, docker: Docker, asked: list[str], release: Any, tmp_path: Path
    ) -> None:
        import time

        from noust.managers.database.metrics import DatabaseSampler

        docker.script(["systemctl", "is-active"], stdout="active\n")
        listings: list[int] = []

        def hung() -> list[DatabaseInstance]:
            listings.append(1)
            release.wait(10)
            return []

        sampler = DatabaseSampler(service=self.service(tmp_path, hung), budget=0.3)

        started = time.monotonic()
        assert sampler.sample(0.0) == [("db.postgresql.connections", 1.0)]
        assert time.monotonic() - started < 2.0
        assert sampler.sample(60.0) == [("db.postgresql.connections", 1.0)]
        assert len(listings) == 1

    def test_a_failure_is_logged_when_it_starts_and_when_it_ends(
        self,
        docker: Docker,
        instances,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
    ) -> None:
        import logging

        from noust.core.exceptions import DatabaseQueryError
        from noust.managers.database.metrics import DatabaseSampler

        docker.script(["systemctl", "is-active"], stdout="inactive\n")
        broken = [True]

        def engine_pairs(self, reader, manager, now):
            if broken[0]:
                raise DatabaseQueryError("psql: error: connection refused")
            return []

        monkeypatch.setattr(DatabaseSampler, "_engine_pairs", engine_pairs)
        sampler = DatabaseSampler(service=self.service(tmp_path, lambda: [instances[PG_KEY]]))

        with caplog.at_level(logging.INFO, logger="noust.managers.database.metrics"):
            for minute in range(5):
                sampler.sample(minute * 60.0)
            broken[0] = False
            sampler.sample(300.0)
            sampler.sample(360.0)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1 and "connection refused" in warnings[0].getMessage()
        recovered = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(recovered) == 1 and PG_KEY in recovered[0].getMessage()
