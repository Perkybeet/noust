# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Replicas of the host's PostgreSQL, from the primary's preparation to a promoted replica (C-14).

The engine is a runner that behaves like one for exactly the statements and tools the manager
uses, with real files for the data directory, ``pg_hba.conf`` and the passfile, so what is pinned
is what Noust writes and in what order: the account, the slot and the one ``hostssl`` line on a
primary; the copy, the passfile, the settings of the standby and the swap of the data directory
on a replica; what is undone when a step fails; and that the replication password is never an
argument or an environment variable of any process. The real engine, two of them on a network, is
``tests/integration/engines_run.py`` (scenario ``replication_postgresql``).
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core.applock import app_lock
from noust.core.exceptions import (
    DatabaseBackupError,
    DatabaseEngineError,
    DatabaseUserError,
    ValidationError,
)
from noust.core.runner import CommandResult, FakeRunner, set_runner
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore, get_store
from noust.managers.database import flavours, pg_hba, replication_files
from noust.managers.database.cluster_swap import LOCK_KEY
from noust.managers.database.postgres import PostgresManager
from noust.managers.database.replication import ReplicationManager
from noust.managers.database.replication_records import PRIMARY, REPLICA, ReplicationRecords
from noust.managers.database.replication_token import REQUIRED_SETTINGS, parse
from noust.managers.server.host import HostPaths

START = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)
REPLICA_ADDRESS = "203.0.113.7"
HOST = "db.example.com"
CA = (
    "-----BEGIN CERTIFICATE-----\n"
    + "MIIBszCCAVmgAwIBAgIUQ2xhdWRlVGVzdENlcnRpZmljYXRlMDAwMDEwCgYIKoZI\n" * 2
    + "-----END CERTIFICATE-----\n"
)
DEBIAN_HBA = (
    "# PostgreSQL Client Authentication Configuration File\n"
    "# TYPE  DATABASE        USER            ADDRESS                 METHOD\n"
    "local   all             postgres                                peer\n"
    "host    all             all             127.0.0.1/32            scram-sha-256\n"
    "host    replication     all             127.0.0.1/32            scram-sha-256\n"
)


class Clock:
    """A clock a test moves."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@dataclass
class FakeChange:
    """What the firewall answers to a rule."""

    backend: str = "ufw"
    active: bool = True
    changed: bool = True


class FakeFirewall:
    """Records the rules Noust asked for."""

    def __init__(self) -> None:
        self.allowed: list[Any] = []
        self.removed: list[Any] = []

    def allow_direct(self, rule: Any) -> FakeChange:
        self.allowed.append(rule)
        return FakeChange()

    def remove_direct(self, rule: Any) -> FakeChange:
        self.removed.append(rule)
        return FakeChange()


class Server(FakeRunner):
    """
    A runner that is a PostgreSQL server and the tools around it, as far as the manager can tell.

    Attributes:
        data: The data directory (real files).
        hba: ``pg_hba.conf`` (a real file).
        running: Whether the server runs.
        in_recovery: Whether it is a standby.
        facts: What the cluster says about itself.
        databases: The databases that are not templates.
        roles: The accounts that exist.
        slots: The replication slots that exist.
        statements: Every SQL text sent, in order.
        tls_problem: What openssl says when the certificate would be refused; None to accept.
        basebackup_problem: What ``pg_basebackup`` fails with; None to succeed.
        standby_dies: Whether a server started with ``standby.signal`` dies.
        no_stream: Whether a standby starts but never streams.
        hba_errors: What ``pg_hba_file_rules`` counts as errors.
        restart_dies: Whether a restart leaves the server down.
        slot_problem: What creating a slot fails with; None to succeed.
        copy_major: The PostgreSQL major version of the cluster ``pg_basebackup`` copies.
        statuses: The rows the primary's view of its replicas reads.
    """

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.data = root / "main"
        self.hba = root / "etc" / "pg_hba.conf"
        self.running = True
        self.in_recovery = False
        self.facts: dict[str, str] = {
            "version": "160004",
            "ssl": "on",
            "wal_level": "replica",
            "max_wal_senders": "10",
            "max_replication_slots": "10",
            "listen_addresses": "localhost",
            "port": "5432",
            "system_id": "7430000000000000001",
            "size": "33554432",
            "max_connections": "100",
            "max_locks_per_transaction": "64",
            "max_prepared_transactions": "0",
            "max_worker_processes": "8",
        }
        self.databases = ["shop"]
        self.roles: set[str] = set()
        self.slots: set[str] = set()
        self.statements: list[str] = []
        self.tls_problem: str | None = None
        self.basebackup_problem: str | None = None
        self.standby_dies = False
        self.no_stream = False
        self.hba_errors = "0"
        self.restart_dies = False
        self.slot_problem: str | None = None
        self.copy_major = "16"
        self.statuses: list[str] = []
        self.restarts = 0
        self.only_knows("psql", "systemctl", "pg_basebackup", "openssl", "journalctl")
        self.data.mkdir(parents=True)
        self.hba.parent.mkdir(parents=True)
        self.hba.write_text(DEBIAN_HBA)
        self.pid_file()

    def pid_file(self) -> None:
        """Write the file a running server keeps in its data directory."""
        (self.data / "postmaster.pid").write_text(f"{os.getpid()}\n")

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = 120,
        input: str | None = None,
        stdin_path: Path | None = None,
        user: str | None = None,
        check: bool = False,
        secrets: Sequence[str] = (),
    ) -> CommandResult:
        scripted = super().run(
            argv, cwd=cwd, env=env, timeout=timeout, input=input, user=user, check=check
        )
        words = tuple(argv)
        if words[0] == "openssl":
            if self.tls_problem is not None:
                return CommandResult(
                    argv=words, exit_code=1, stdout="", stderr=f"verify error:{self.tls_problem}\n"
                )
            return CommandResult(argv=words, exit_code=0, stdout="Verify return code: 0 (ok)\n")
        if words[0] == "pg_basebackup":
            return self.copy(words, scripted)
        if words[:2] == ("systemctl", "is-active"):
            return CommandResult(
                argv=words,
                exit_code=0 if self.running else 3,
                stdout="active\n" if self.running else "inactive\n",
            )
        if words[:2] == ("systemctl", "stop"):
            self.running = False
            (self.data / "postmaster.pid").unlink(missing_ok=True)
            return scripted
        if words[:2] in (("systemctl", "start"), ("systemctl", "restart")):
            return self.start(words, scripted)
        if words[0] != "psql" or input is None:
            return scripted
        self.statements.append(input)
        if not self.running:
            return CommandResult(argv=words, exit_code=2, stderr="could not connect to server")
        return self.answer(words, input)

    def start(self, words: tuple[str, ...], scripted: CommandResult) -> CommandResult:
        """
        Start the server the way the unit does.

        Args:
            words: The command.
            scripted: What the base runner answered.

        Returns:
            The result.
        """
        if words[1] == "restart":
            self.restarts += 1
            if self.restart_dies:
                self.running = False
                (self.data / "postmaster.pid").unlink(missing_ok=True)
                return scripted
        standby = (self.data / "standby.signal").exists()
        if standby and self.standby_dies:
            self.running = False
            return scripted
        self.running = True
        self.in_recovery = standby
        self.pid_file()
        return scripted

    def copy(self, words: tuple[str, ...], scripted: CommandResult) -> CommandResult:
        """
        Make the copy ``pg_basebackup`` makes.

        Args:
            words: The command.
            scripted: What the base runner answered.

        Returns:
            The result.
        """
        if self.basebackup_problem is not None:
            return CommandResult(argv=words, exit_code=1, stderr=self.basebackup_problem)
        target = Path(words[words.index("-D") + 1])
        target.mkdir(parents=True)
        (target / "PG_VERSION").write_text(self.copy_major + "\n")
        (target / "standby.signal").write_text("")
        (target / "postgresql.auto.conf").write_text(
            "# Do not edit this file manually!\n"
            "# It will be overwritten by the ALTER SYSTEM command.\n"
            "ssl_cert_file = '/etc/ssl/noust-db/postgresql/fullchain.pem'\n"
            "archive_command = 'cp %p /wal/%f'\n"
            "max_connections = '200'\n"
            "primary_conninfo = 'user=noust_repl_reports passfile=''/home/pg/.noust-replica/"
            "reports.pgpass'' host=db.example.com port=5432 sslmode=verify-full'\n"
            "primary_slot_name = 'noust_reports'\n"
        )
        return scripted

    def answer(self, words: tuple[str, ...], sql: str) -> CommandResult:
        """
        Answer a statement the way the engine would.

        Args:
            words: The command.
            sql: The statement sent on standard input.

        Returns:
            The result.
        """
        out = ""
        for value in re.findall(r"ALTER SYSTEM SET listen_addresses = '([^']*)'", sql):
            self.facts["listen_addresses"] = value
        if "pg_control_system" in sql and "server_version_num" in sql:
            out = "|".join(
                [
                    self.facts["version"],
                    "t" if self.in_recovery else "f",
                    self.facts["ssl"],
                    self.facts["wal_level"],
                    self.facts["max_wal_senders"],
                    self.facts["max_replication_slots"],
                    self.facts["listen_addresses"],
                    self.facts["port"],
                    str(self.data),
                    self.facts["system_id"],
                    self.facts["size"],
                    *(self.facts[key] for key in REQUIRED_SETTINGS),
                ]
            )
        elif sql.strip() == "SHOW hba_file;":
            out = str(self.hba)
        elif "pg_hba_file_rules" in sql:
            out = self.hba_errors
        elif "SELECT count(*) FROM pg_roles WHERE rolname" in sql:
            role = re.search(r"rolname = '([^']+)'", sql)
            slot = re.search(r"slot_name = '([^']+)'", sql)
            assert role and slot
            out = str((role.group(1) in self.roles) + (slot.group(1) in self.slots))
        elif "CREATE ROLE" in sql:
            self.roles.add(re.search(r'CREATE ROLE "?(\w+)"?', sql).group(1))  # type: ignore[union-attr]
        elif "DROP ROLE IF EXISTS" in sql:
            self.roles.discard(re.search(r'DROP ROLE IF EXISTS "?(\w+)"?', sql).group(1))  # type: ignore[union-attr]
        elif "pg_create_physical_replication_slot" in sql:
            if self.slot_problem is not None:
                return CommandResult(argv=words, exit_code=1, stderr=self.slot_problem)
            self.slots.add(
                re.search(r"pg_create_physical_replication_slot\('([^']+)'", sql).group(1)
            )  # type: ignore[union-attr]
        elif "pg_drop_replication_slot" in sql:
            self.slots.discard(re.search(r"slot_name = '([^']+)'", sql).group(1))  # type: ignore[union-attr]
        elif "SELECT active FROM pg_replication_slots" in sql:
            out = ""
        elif "SELECT datname FROM pg_database" in sql:
            out = "\n".join(self.databases)
        elif "pg_is_in_recovery()" in sql and "pg_stat_wal_receiver" not in sql:
            out = "t" if self.in_recovery else "f"
        elif "SELECT coalesce((SELECT status FROM pg_stat_wal_receiver LIMIT 1), 'none')" in sql:
            out = "streaming" if self.in_recovery and not self.no_stream else "none"
        elif "pg_promote" in sql:
            self.in_recovery = False
            (self.data / "standby.signal").unlink(missing_ok=True)
            out = "t"
        elif "pg_last_xact_replay_timestamp" in sql:
            out = (
                "streaming|db.example.com:5432|2|1024|user=noust_repl_reports "
                "host=db.example.com sslmode='verify-full'"
            )
        elif "FROM pg_replication_slots s" in sql:
            out = "\n".join(self.statuses)
        return CommandResult(argv=words, exit_code=0, stdout=out + "\n" if out else "")


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def clock() -> Clock:
    return Clock(START)


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A system root on Ubuntu with the engine's units, so the manager finds its service."""
    (tmp_path / "usr/lib/systemd/system").mkdir(parents=True)
    (tmp_path / "usr/lib/systemd/system/postgresql.service").write_text("")
    monkeypatch.setattr(flavours, "HOST", HostPaths(tmp_path))
    return tmp_path


@pytest.fixture
def engine(tmp_path: Path, host: Path) -> Iterator[Server]:
    """The simulated server, installed as the process-wide runner."""
    fake = Server(tmp_path / "server")
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture
def firewall() -> FakeFirewall:
    return FakeFirewall()


@pytest.fixture
def replication(
    tmp_path: Path, engine: Server, clock: Clock, firewall: FakeFirewall
) -> ReplicationManager:
    """The manager over the simulated engine, with a clock and a sleep that only move time."""
    home = tmp_path / "home"
    home.mkdir()
    return ReplicationManager(
        PostgresManager(),
        records=ReplicationRecords(get_store()),
        secrets=SecretStore(root=tmp_path / "secrets"),
        firewall=lambda: firewall,  # type: ignore[arg-type,return-value]
        clock=clock,
        sleep=clock.sleep,
        state_root=tmp_path / "state",
        home=home,
        disk_free=lambda _path: 10**12,
        local_address=lambda address: address.startswith("10."),
    )


def prepare(manager: ReplicationManager, name: str = "reports", **overrides: Any) -> dict[str, Any]:
    """
    Prepare a replica with sensible defaults.

    Args:
        manager: The manager.
        name: The replica's name.
        **overrides: Arguments of :meth:`ReplicationManager.prepare` to change.

    Returns:
        Its result.
    """
    arguments: dict[str, Any] = {
        "source": REPLICA_ADDRESS,
        "host": HOST,
        "listen": "10.0.0.5",
        "confirmed": True,
    }
    arguments.update(overrides)
    return manager.prepare(name, **arguments)


# ------------------------------------------------------------------ planning the primary


def test_planning_changes_nothing_and_says_what_would_be_done(
    replication: ReplicationManager, engine: Server
) -> None:
    plan = replication.plan_prepare("reports", source=REPLICA_ADDRESS, host=HOST, listen="10.0.0.5")

    assert plan.problems == []
    assert plan.listen == "localhost,10.0.0.5" and plan.restart
    assert plan.source == "203.0.113.7/32"
    assert any("hostssl replication noust_repl_reports 203.0.113.7/32" in s for s in plan.steps)
    assert not any("CREATE ROLE" in s or "ALTER SYSTEM" in s for s in engine.statements)
    assert engine.restarts == 0 and engine.roles == set() and engine.slots == set()
    assert engine.hba.read_text() == DEBIAN_HBA


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"ssl": "off"}, "not serving TLS"),
        ({"wal_level": "minimal"}, "wal_level"),
        ({"max_wal_senders": "1"}, "max_wal_senders"),
        ({"max_replication_slots": "0"}, "max_replication_slots"),
        ({"version": "110005"}, "too old"),
    ],
)
def test_a_server_that_cannot_feed_a_replica_says_why(
    replication: ReplicationManager, engine: Server, change: dict[str, str], message: str
) -> None:
    engine.facts.update(change)

    plan = replication.plan_prepare("reports", source=REPLICA_ADDRESS, host=HOST, listen="10.0.0.5")

    assert any(message in problem for problem in plan.problems), plan.problems


def test_a_certificate_the_replica_would_refuse_is_refused_first_in_the_words_of_openssl(
    replication: ReplicationManager, engine: Server
) -> None:
    engine.tls_problem = "num=18:self-signed certificate"

    plan = replication.plan_prepare("reports", source=REPLICA_ADDRESS, host=HOST, listen="10.0.0.5")

    problem = " ".join(plan.problems)
    assert "would not be accepted" in problem and "self-signed certificate" in problem
    assert "noust db tls enable" in problem and "--ca-file" in problem


def test_the_authority_is_given_to_the_proof_as_a_file_and_removed_after(
    replication: ReplicationManager, engine: Server
) -> None:
    replication.plan_prepare(
        "reports", source=REPLICA_ADDRESS, host=HOST, listen="10.0.0.5", ca_pem=CA
    )

    proof = next(call for call in engine.calls if call[0] == "openssl")
    assert proof[proof.index("-CAfile") + 1].endswith("/.proof/ca.pem")
    assert "-verify_hostname" in proof and HOST in proof
    assert not Path(proof[proof.index("-CAfile") + 1]).exists()


def test_an_address_as_the_host_is_verified_as_an_address(
    replication: ReplicationManager, engine: Server
) -> None:
    replication.plan_prepare("reports", source=REPLICA_ADDRESS, host="203.0.113.1", listen="*")

    proof = next(call for call in engine.calls if call[0] == "openssl")
    assert "-verify_ip" in proof and "-verify_hostname" not in proof


def test_a_server_that_listens_on_the_loopback_only_asks_where_to_listen(
    replication: ReplicationManager,
) -> None:
    plan = replication.plan_prepare("reports", source=REPLICA_ADDRESS, host=HOST)

    assert any("listens on 'localhost' only" in problem for problem in plan.problems)
    assert plan.restart is False


def test_a_server_that_already_listens_where_it_can_be_reached_is_not_restarted(
    replication: ReplicationManager, engine: Server
) -> None:
    engine.facts["listen_addresses"] = "*"

    plan = replication.plan_prepare("reports", source=REPLICA_ADDRESS, host=HOST)

    assert plan.problems == [] and plan.listen is None and plan.restart is False


@pytest.mark.parametrize("listen", ["10.9.9.9; drop", "not an address", "10.0.0.5,"])
def test_what_to_listen_on_is_an_allowlist(replication: ReplicationManager, listen: str) -> None:
    plan = replication.plan_prepare("reports", source=REPLICA_ADDRESS, host=HOST, listen=listen)

    assert any("is not '*' or a list of addresses" in problem for problem in plan.problems)


def test_an_address_that_is_not_the_servers_would_stop_it_starting_so_it_is_refused(
    replication: ReplicationManager,
) -> None:
    plan = replication.plan_prepare(
        "reports", source=REPLICA_ADDRESS, host=HOST, listen="192.0.2.9"
    )

    assert any("is not an address of this server" in problem for problem in plan.problems)


@pytest.mark.parametrize("source", ["0.0.0.0/0", "::/0", "everywhere", "10.0.0.0/33"])
def test_a_replica_is_allowed_from_somewhere_in_particular(
    replication: ReplicationManager, source: str
) -> None:
    with pytest.raises(ValidationError) as caught:
        replication.plan_prepare("reports", source=source, host=HOST, listen="*")

    assert caught.value.field == "source"


def test_a_server_that_is_itself_a_replica_does_not_feed_one(
    replication: ReplicationManager, engine: Server
) -> None:
    engine.in_recovery = True

    plan = replication.plan_prepare("reports", source=REPLICA_ADDRESS, host=HOST, listen="*")

    assert any("itself a replica" in problem for problem in plan.problems)


def test_an_account_or_slot_noust_did_not_make_is_never_taken_over(
    replication: ReplicationManager, engine: Server
) -> None:
    engine.roles.add("noust_repl_reports")

    plan = replication.plan_prepare("reports", source=REPLICA_ADDRESS, host=HOST, listen="*")

    assert any("no record of making them" in problem for problem in plan.problems)


# ------------------------------------------------------------------ preparing the primary


def test_a_restart_has_to_be_confirmed_where_it_happens(
    replication: ReplicationManager, engine: Server
) -> None:
    with pytest.raises(ValidationError, match="restarts PostgreSQL") as caught:
        prepare(replication, confirmed=False)

    assert caught.value.field == "confirmed"
    assert engine.restarts == 0 and engine.roles == set()


def test_a_plan_with_problems_is_not_carried_out(
    replication: ReplicationManager, engine: Server
) -> None:
    engine.facts["ssl"] = "off"

    with pytest.raises(ValidationError, match="cannot be prepared") as caught:
        prepare(replication)

    assert "not serving TLS" in str(caught.value.details)
    assert engine.roles == set() and engine.restarts == 0


def test_preparing_makes_the_account_the_slot_and_the_one_line(
    replication: ReplicationManager, engine: Server, firewall: FakeFirewall
) -> None:
    result = prepare(replication, open_firewall=True)

    assert engine.facts["listen_addresses"] == "localhost,10.0.0.5" and engine.restarts == 1
    assert engine.roles == {"noust_repl_reports"}
    assert engine.slots == {"noust_reports"}
    create = next(s for s in engine.statements if "CREATE ROLE" in s)
    assert "SET password_encryption = 'scram-sha-256'" in create
    assert "REPLICATION LOGIN" in create and "SUPERUSER" not in create
    reserved = next(s for s in engine.statements if "pg_create_physical_replication_slot" in s)
    assert "'noust_reports', true" in reserved, "the log is held from the moment the slot exists"
    text = engine.hba.read_text()
    assert "hostssl replication noust_repl_reports 203.0.113.7/32 scram-sha-256" in text
    assert text.index("noust replication") < text.index("local   all")
    assert [(r.port, r.source) for r in firewall.allowed] == [(5432, "203.0.113.7/32")]
    record = replication.records.get(PRIMARY, "reports")
    assert record is not None and record.listen_was == "localhost" and record.state == "prepared"
    assert record.firewall["created"] is True
    assert result["plan"]["ok"] is True


def test_the_password_is_in_the_code_and_in_the_secret_store_and_nowhere_else(
    replication: ReplicationManager, engine: Server, tmp_path: Path
) -> None:
    result = prepare(replication, ca_pem=CA)

    code = parse(result["token"])
    assert code.name == "reports" and code.role == "noust_repl_reports"
    assert code.host == HOST and code.port == 5432 and code.major == 16
    assert code.size == 33554432 and code.ca == CA
    assert code.requires == {
        "max_connections": 100,
        "max_locks_per_transaction": 64,
        "max_prepared_transactions": 0,
        "max_wal_senders": 10,
        "max_worker_processes": 8,
    }
    assert replication.secrets.read("databases/postgresql/replication/reports") == code.password
    for call in engine.calls:
        assert code.password not in " ".join(call), call
    for env in engine.envs:
        assert code.password not in " ".join((env or {}).values())
    stored = replication.records.get(PRIMARY, "reports")
    assert stored is not None and code.password not in str(stored.to_dict())
    assert code.password not in (tmp_path / "state" / "reports" / "ca.pem").read_text()


def test_the_code_can_be_shown_again_with_the_same_content(
    replication: ReplicationManager,
) -> None:
    first = parse(prepare(replication, ca_pem=CA)["token"])

    again = parse(replication.token("reports"))

    assert again == first


def test_a_code_for_a_replica_that_was_never_prepared_says_so(
    replication: ReplicationManager,
) -> None:
    with pytest.raises(ValidationError, match="No replica named reports"):
        replication.token("reports")


def test_a_failed_slot_undoes_the_account_and_the_widened_listening(
    replication: ReplicationManager, engine: Server
) -> None:
    engine.slot_problem = "ERROR:  all replication slots are in use"

    with pytest.raises(DatabaseEngineError, match="refused a statement"):
        prepare(replication)

    assert engine.roles == set() and engine.slots == set()
    assert engine.facts["listen_addresses"] == "localhost"
    assert engine.hba.read_text() == DEBIAN_HBA
    assert replication.records.get(PRIMARY, "reports") is None
    assert replication.secrets.read("databases/postgresql/replication/reports") is None


def test_a_pg_hba_file_the_server_rejects_undoes_everything_made_before_it(
    replication: ReplicationManager, engine: Server
) -> None:
    engine.hba_errors = "1"

    with pytest.raises(DatabaseUserError, match=r"did not accept the pg_hba\.conf"):
        prepare(replication)

    assert engine.hba.read_text() == DEBIAN_HBA, "put back as it was"
    assert engine.roles == set() and engine.slots == set()
    assert engine.facts["listen_addresses"] == "localhost"


def test_a_firewall_rule_made_by_a_failed_preparation_is_closed(
    replication: ReplicationManager, engine: Server, firewall: FakeFirewall
) -> None:
    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise DatabaseEngineError("the secret store is read-only")

    replication.secrets.write = refuse  # type: ignore[method-assign]

    with pytest.raises(DatabaseEngineError, match="read-only"):
        prepare(replication, open_firewall=True)

    assert len(firewall.allowed) == 1 and len(firewall.removed) == 1
    assert engine.roles == set() and engine.slots == set()


def test_a_server_that_does_not_come_back_after_widening_gets_its_setting_back(
    replication: ReplicationManager, engine: Server
) -> None:
    engine.restart_dies = True
    # The server is down, so the SQL path is closed: the file is edited by hand.
    (engine.data / "postgresql.auto.conf").write_text(
        "listen_addresses = 'localhost,10.0.0.5'\nmax_connections = '100'\n"
    )

    with pytest.raises(DatabaseEngineError):
        prepare(replication)

    assert "listen_addresses" not in (engine.data / "postgresql.auto.conf").read_text()
    assert "max_connections" in (engine.data / "postgresql.auto.conf").read_text()
    assert engine.restarts == 2, "restarted again to take the address off"
    assert replication.records.get(PRIMARY, "reports") is None


def test_preparing_while_another_operation_changes_the_cluster_is_refused(
    replication: ReplicationManager,
) -> None:
    # A second thread is how a second operation looks to the lock: one thread re-enters.
    outcome: list[BaseException] = []
    held = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with app_lock(LOCK_KEY, "recovery"):
            held.set()
            release.wait(10)

    thread = threading.Thread(target=hold)
    thread.start()
    held.wait(10)
    try:
        try:
            prepare(replication)
        except DatabaseBackupError as exc:
            outcome.append(exc)
    finally:
        release.set()
        thread.join()

    assert outcome and "recovery" in str(outcome[0])


# ------------------------------------------------------------------ taking a replica away


def test_removing_needs_a_confirmation_and_changes_nothing_without_one(
    replication: ReplicationManager, engine: Server
) -> None:
    prepare(replication)

    with pytest.raises(ValidationError, match="cuts its replica off"):
        replication.remove("reports", confirmed=False)

    assert engine.roles == {"noust_repl_reports"}


def test_removing_undoes_what_preparing_made_and_nothing_more(
    replication: ReplicationManager, engine: Server, firewall: FakeFirewall, tmp_path: Path
) -> None:
    prepare(replication, open_firewall=True, ca_pem=CA)
    engine.hba.write_text(engine.hba.read_text() + "host all someone 10.1.1.1/32 md5\n")

    result = replication.remove("reports", confirmed=True)

    assert result["removed"] is True
    assert engine.roles == set() and engine.slots == set()
    assert "noust replication" not in engine.hba.read_text()
    assert "host all someone 10.1.1.1/32 md5" in engine.hba.read_text(), "the operator's line stays"
    assert len(firewall.removed) == 1
    assert replication.records.get(PRIMARY, "reports") is None
    assert replication.secrets.read("databases/postgresql/replication/reports") is None
    assert not (tmp_path / "state" / "reports").exists()
    assert engine.facts["listen_addresses"] == "localhost,10.0.0.5", "left as it is"
    assert any("--restore-listen" in step for step in result["steps"])


def test_the_listening_is_put_back_when_asked_and_no_other_replica_needs_it(
    replication: ReplicationManager, engine: Server
) -> None:
    prepare(replication)

    replication.remove("reports", confirmed=True, restore_listen=True)

    assert engine.facts["listen_addresses"] == "localhost"
    assert engine.restarts == 2


def test_the_listening_stays_while_another_replica_is_prepared(
    replication: ReplicationManager, engine: Server
) -> None:
    prepare(replication)
    prepare(replication, "standby", source="203.0.113.8", listen=None)

    replication.remove("reports", confirmed=True, restore_listen=True)

    assert engine.facts["listen_addresses"] == "localhost,10.0.0.5"


def test_a_replica_that_was_never_prepared_is_not_removed_from_under_someone(
    replication: ReplicationManager, engine: Server
) -> None:
    result = replication.remove("reports", confirmed=True)

    assert result["removed"] is False
    assert engine.hba.read_text() == DEBIAN_HBA


def test_an_account_left_by_an_interrupted_preparation_is_still_removable(
    replication: ReplicationManager, engine: Server
) -> None:
    engine.roles.add("noust_repl_reports")
    engine.slots.add("noust_reports")

    result = replication.remove("reports", confirmed=True)

    assert result["removed"] is True
    assert engine.roles == set() and engine.slots == set()


# ------------------------------------------------------------------ planning a join


@pytest.fixture
def code(replication: ReplicationManager, engine: Server, tmp_path: Path) -> str:
    """A join code from a primary like the simulated one, made by its own manager."""
    other = Server(tmp_path / "primary")
    other.facts["ssl"] = "on"
    set_runner(other)
    try:
        maker = ReplicationManager(
            PostgresManager(),
            records=ReplicationRecords(get_store()),
            secrets=SecretStore(root=tmp_path / "primary-secrets"),
            state_root=tmp_path / "primary-state",
            home=tmp_path / "home",
            local_address=lambda _a: True,
        )
        token = prepare(maker, ca_pem=CA)["token"]
        # The maker's rows are on the primary; this server has none.
        ReplicationRecords(get_store()).delete(PRIMARY, "reports")
    finally:
        set_runner(engine)
    return str(token)


def join_with(replication: ReplicationManager, code: str, **overrides: Any) -> dict[str, Any]:
    """
    Join with the confirmation given.

    Args:
        replication: The manager.
        code: The join code.
        **overrides: Arguments of :meth:`ReplicationManager.join` to change.

    Returns:
        Its result.
    """
    arguments: dict[str, Any] = {"confirmed": True}
    arguments.update(overrides)
    return replication.join(code, **arguments)


def test_planning_a_join_lists_what_it_replaces_and_changes_nothing(
    replication: ReplicationManager, engine: Server, code: str
) -> None:
    plan = replication.plan_join(code)

    assert plan.problems == []
    assert plan.databases == ["shop"]
    assert plan.saved_as == f"{engine.data}.noust-before-replica"
    assert any("shop" in warning for warning in plan.warnings)
    assert "password" not in str(plan.to_dict()).lower().replace("password authentication", "")
    assert not (engine.data.parent / "main.noust-replica-new").exists()
    assert engine.restarts == 0 and engine.in_recovery is False


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        (lambda e: e.facts.update(version="150002"), "same major version"),
        (lambda e: setattr(e, "in_recovery", True), "already follows a primary"),
    ],
)
def test_a_server_that_cannot_follow_says_why(
    replication: ReplicationManager, engine: Server, code: str, setup: Any, message: str
) -> None:
    setup(engine)

    plan = replication.plan_join(code)

    assert any(message in problem for problem in plan.problems), plan.problems


def test_not_enough_disk_for_the_copy_is_refused_before_copying(
    replication: ReplicationManager, engine: Server, code: str
) -> None:
    replication._disk_free = lambda _path: 1024

    plan = replication.plan_join(code)

    assert any("copy needs about" in problem for problem in plan.problems)
    with pytest.raises(ValidationError, match="cannot join that primary yet"):
        join_with(replication, code)
    assert not any(call[0] == "pg_basebackup" for call in engine.calls)


def test_leftovers_of_an_earlier_attempt_are_never_overwritten(
    replication: ReplicationManager, engine: Server, code: str
) -> None:
    Path(f"{engine.data}.noust-before-replica").mkdir()

    plan = replication.plan_join(code)

    assert any("exists from an earlier join" in problem for problem in plan.problems)


def test_a_join_has_to_be_confirmed_where_it_happens(
    replication: ReplicationManager, engine: Server, code: str
) -> None:
    with pytest.raises(ValidationError, match="replaces this server's PostgreSQL data") as caught:
        join_with(replication, code, confirmed=False)

    assert caught.value.field == "confirmed"
    assert not any(call[0] == "pg_basebackup" for call in engine.calls)


@pytest.mark.parametrize(
    "text",
    ["", "noust-replica:v1:", "hello", "noust-replica:v1:!!!!", "noust-replica:v2:eyJ9"],
)
def test_a_text_that_is_not_a_code_is_refused_by_name(
    replication: ReplicationManager, text: str
) -> None:
    with pytest.raises(ValidationError, match="not a join code Noust made"):
        replication.plan_join(text)


# ------------------------------------------------------------------ joining


def test_joining_copies_the_primary_over_verified_tls_and_swaps_the_data_in(
    replication: ReplicationManager, engine: Server, code: str, tmp_path: Path
) -> None:
    (engine.data / "marker").write_text("mine")
    result = join_with(replication, code)

    token = parse(code)
    argv = next(call for call in engine.calls if "pg_basebackup" in call)
    assert argv[:4] == ("runuser", "-u", "postgres", "--")
    command = argv[4:]
    assert command[command.index("-h") + 1] == HOST
    assert command[command.index("-U") + 1] == "noust_repl_reports"
    assert command[command.index("-S") + 1] == "noust_reports"
    assert command[command.index("-X") + 1] == "stream"
    assert "-R" in command and "--no-password" in command
    env = engine.envs[engine.calls.index(argv)]
    assert env is not None
    assert env["PGSSLMODE"] == "verify-full"
    assert env["PGSSLROOTCERT"].endswith("/.noust-replica/reports.ca.pem")
    assert env["PGPASSFILE"].endswith("/.noust-replica/reports.pgpass")
    for call in engine.calls:
        assert token.password not in " ".join(call)
    for each in engine.envs:
        assert token.password not in " ".join((each or {}).values())
    assert (Path(f"{engine.data}.noust-before-replica") / "marker").read_text() == "mine"
    assert (engine.data / "standby.signal").exists()
    assert (
        result["state"] == "streaming"
        and result["saved_as"] == f"{engine.data}.noust-before-replica"
    )
    assert engine.in_recovery is True


def test_the_password_is_in_a_passfile_only_the_engines_account_can_read(
    replication: ReplicationManager, engine: Server, code: str, tmp_path: Path
) -> None:
    join_with(replication, code)

    token = parse(code)
    passfile = tmp_path / "home" / ".noust-replica" / "reports.pgpass"
    assert passfile.read_text() == f"{HOST}:5432:replication:noust_repl_reports:{token.password}\n"
    assert oct(passfile.stat().st_mode & 0o777) == "0o600"
    assert (tmp_path / "home" / ".noust-replica" / "reports.ca.pem").read_text() == CA
    record = replication.records.get(REPLICA, "reports")
    assert record is not None and record.state == "following"
    assert token.password not in str(record.to_dict())
    conf = (engine.data / "postgresql.auto.conf").read_text()
    assert token.password not in conf


def test_the_replica_keeps_its_own_settings_and_the_capacity_the_primary_needs(
    replication: ReplicationManager, engine: Server, code: str
) -> None:
    engine.facts["max_connections"] = "300"
    (engine.data / "postgresql.auto.conf").write_text("ssl_cert_file = '/etc/ssl/own.pem'\n")
    join_with(replication, code)

    conf = (engine.data / "postgresql.auto.conf").read_text()
    assert "archive_command" not in conf, "the primary's archiving is not this server's"
    assert replication_files.auto_conf_value(conf, "ssl_cert_file") == "/etc/ssl/own.pem"
    assert replication_files.auto_conf_value(conf, "max_connections") == "300", "the larger"
    assert replication_files.auto_conf_value(conf, "max_worker_processes") == "8"
    options = replication_files.conninfo_options(
        replication_files.auto_conf_value(conf, "primary_conninfo") or ""
    )
    assert options["sslmode"] == "verify-full" and "password" not in options
    assert replication_files.auto_conf_value(conf, "primary_slot_name") == "noust_reports"


def test_configuration_files_inside_the_data_directory_stay_the_replicas_own(
    replication: ReplicationManager, engine: Server, code: str
) -> None:
    (engine.data / "postgresql.conf").write_text("# this server's\n")
    (engine.data / "pg_hba.conf").write_text("# this server's hba\n")

    join_with(replication, code)

    assert (engine.data / "postgresql.conf").read_text() == "# this server's\n"
    assert (engine.data / "pg_hba.conf").read_text() == "# this server's hba\n"


def test_a_stale_record_of_a_follower_that_no_longer_follows_is_replaced(
    replication: ReplicationManager, engine: Server, code: str
) -> None:
    from noust.managers.database.replication_records import ReplicaRecord

    replication.records.save(
        ReplicaRecord(side=REPLICA, name="old", account="a", slot="s", host="h", port=1)
    )

    join_with(replication, code)

    assert [r.name for r in replication.records.records(REPLICA)] == ["reports"]


def test_a_copy_that_cannot_be_made_changes_nothing(
    replication: ReplicationManager, engine: Server, code: str, tmp_path: Path
) -> None:
    engine.basebackup_problem = 'pg_basebackup: error: connection to server failed: FATAL:  password authentication failed for user "noust_repl_reports"'

    with pytest.raises(DatabaseBackupError, match="could not be copied") as caught:
        join_with(replication, code)

    assert "password authentication failed" in str(caught.value.details)
    assert engine.restarts == 0 and engine.running and engine.in_recovery is False
    assert engine.data.is_dir() and not Path(f"{engine.data}.noust-before-replica").exists()
    assert not Path(f"{engine.data}.noust-replica-new").exists()
    assert not list((tmp_path / "home" / ".noust-replica").iterdir()), "no credential is left"
    assert replication.records.records(REPLICA) == []


def test_a_copy_of_another_major_version_than_the_code_said_is_never_swapped_in(
    replication: ReplicationManager, engine: Server, code: str
) -> None:
    engine.copy_major = "15"

    with pytest.raises(DatabaseBackupError, match="another PostgreSQL version"):
        join_with(replication, code)

    assert engine.data.is_dir() and engine.running
    assert not Path(f"{engine.data}.noust-replica-new").exists()


def test_a_standby_that_dies_puts_the_previous_data_directory_back(
    replication: ReplicationManager, engine: Server, code: str, tmp_path: Path
) -> None:
    (engine.data / "marker").write_text("mine")
    engine.standby_dies = True

    with pytest.raises(DatabaseBackupError, match="previous data is back") as caught:
        join_with(replication, code)

    assert "PostgreSQL stopped during the start" in str(caught.value.details)
    assert "main.noust-failed-replica" in str(caught.value.details)
    assert (engine.data / "marker").read_text() == "mine"
    assert not (engine.data / "standby.signal").exists()
    assert (Path(f"{engine.data}.noust-failed-replica") / "standby.signal").exists()
    assert not Path(f"{engine.data}.noust-before-replica").exists()
    assert engine.running and engine.in_recovery is False
    assert not list((tmp_path / "home" / ".noust-replica").iterdir())
    assert replication.records.records(REPLICA) == []


def test_a_standby_that_starts_but_never_streams_is_put_back_too(
    replication: ReplicationManager, engine: Server, code: str
) -> None:
    (engine.data / "marker").write_text("mine")
    engine.no_stream = True

    with pytest.raises(DatabaseBackupError, match="previous data is back") as caught:
        join_with(replication, code)

    assert "did not start streaming from the primary" in str(caught.value.details)
    assert (engine.data / "marker").read_text() == "mine"
    assert (Path(f"{engine.data}.noust-failed-replica")).is_dir()


def test_the_old_data_can_be_discarded_once_the_replica_streams(
    replication: ReplicationManager, engine: Server, code: str
) -> None:
    result = join_with(replication, code, discard_previous=True)

    assert result["saved_as"] is None
    assert not Path(f"{engine.data}.noust-before-replica").exists()
    record = replication.records.get(REPLICA, "reports")
    assert record is not None and record.saved_as is None


# ------------------------------------------------------------------ promoting


def test_promoting_needs_a_confirmation_and_says_what_stays_behind(
    replication: ReplicationManager, engine: Server, code: str
) -> None:
    join_with(replication, code)

    with pytest.raises(ValidationError, match="stops this server following") as caught:
        replication.promote(confirmed=False)

    assert "noust_reports" in str(caught.value.details)
    assert engine.in_recovery is True


def test_promoting_makes_a_primary_forgets_the_credentials_and_names_the_slot_left_behind(
    replication: ReplicationManager, engine: Server, code: str, tmp_path: Path
) -> None:
    join_with(replication, code)

    result = replication.promote(confirmed=True)

    assert result["promoted"] is True and engine.in_recovery is False
    assert "noust db replica remove reports" in result["note"]
    assert any("RESET primary_conninfo" in s for s in engine.statements)
    assert any("RESET primary_slot_name" in s for s in engine.statements)
    assert not list((tmp_path / "home" / ".noust-replica").iterdir())
    assert replication.records.records(REPLICA) == []


def test_a_server_that_follows_nothing_cannot_be_promoted(
    replication: ReplicationManager, engine: Server
) -> None:
    with pytest.raises(ValidationError, match="not a replica"):
        replication.promote(confirmed=True)


def test_a_standby_noust_did_not_make_is_not_promoted_by_noust(
    replication: ReplicationManager, engine: Server
) -> None:
    engine.in_recovery = True

    with pytest.raises(ValidationError, match="did not set this server up"):
        replication.promote(confirmed=True)


def test_the_record_of_a_primary_still_followed_is_not_forgotten(
    replication: ReplicationManager, engine: Server, code: str
) -> None:
    join_with(replication, code)

    with pytest.raises(ValidationError, match="still follows that primary"):
        replication.remove("reports", confirmed=True)

    assert replication.records.get(REPLICA, "reports") is not None


def test_the_record_of_a_promoted_server_can_be_forgotten(
    replication: ReplicationManager, engine: Server, code: str
) -> None:
    join_with(replication, code)
    engine.in_recovery = False
    (engine.data / "standby.signal").unlink()

    result = replication.remove("reports", confirmed=True)

    assert result["side"] == REPLICA
    assert replication.records.records(REPLICA) == []


# ------------------------------------------------------------------ watching


def test_the_primary_reads_each_replicas_connection_from_the_engine(
    replication: ReplicationManager, engine: Server
) -> None:
    prepare(replication)
    engine.statuses = ["noust_reports|t|2048|reserved|streaming|203.0.113.7|true|TLSv1.3|512|async"]

    view = replication.status()

    assert view["role"] == "primary" and view["warnings"] == []
    entry = view["replicas"][0]
    assert entry["name"] == "reports" and entry["connected"] and entry["tls"] is True
    assert entry["tls_version"] == "TLSv1.3" and entry["state"] == "streaming"
    assert entry["lag_bytes"] == 512 and entry["retained_bytes"] == 2048


def test_a_replica_connected_without_tls_is_called_out(
    replication: ReplicationManager, engine: Server
) -> None:
    prepare(replication)
    engine.statuses = ["noust_reports|t|0|reserved|streaming|203.0.113.7|false||0|async"]

    view = replication.status()

    assert view["replicas"][0]["tls"] is False
    assert any("without TLS" in warning for warning in view["warnings"])


def test_a_slot_of_a_replica_that_is_gone_is_holding_the_disk_and_is_said_so(
    replication: ReplicationManager, engine: Server
) -> None:
    prepare(replication)
    engine.statuses = [f"noust_reports|f|{3 * 1024**3}|extended||||||"]

    view = replication.status()

    assert view["replicas"][0]["connected"] is False
    assert any("holds" in warning and "remove it" in warning for warning in view["warnings"])


def test_a_slot_that_vanished_from_the_engine_is_called_out(
    replication: ReplicationManager, engine: Server
) -> None:
    prepare(replication)

    view = replication.status()

    assert any("is gone from PostgreSQL" in warning for warning in view["warnings"])


def test_a_replica_reads_how_it_follows(
    replication: ReplicationManager, engine: Server, code: str
) -> None:
    join_with(replication, code)

    view = replication.status()

    assert view["role"] == "replica"
    follows = view["follows"]
    assert follows["name"] == "reports" and follows["state"] == "streaming"
    assert follows["sslmode"] == "verify-full" and follows["replay_delay_seconds"] == 2
    assert follows["saved_as"] == f"{engine.data}.noust-before-replica"


def test_a_server_with_nothing_to_do_with_replicas_is_standalone(
    replication: ReplicationManager,
) -> None:
    assert replication.status()["role"] == "standalone"


# ------------------------------------------------------------------ the engine under it


def test_a_cluster_that_is_an_instance_is_refused(
    replication: ReplicationManager, engine: Server
) -> None:
    class Bound:
        pass

    replication.manager.instance = Bound()  # type: ignore[assignment]

    with pytest.raises(DatabaseEngineError, match="not an instance"):
        replication.facts()


def test_a_rehearsal_changes_nothing(
    replication: ReplicationManager, engine: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.managers.database import replication as module

    monkeypatch.setattr(module, "is_rehearsal", lambda: True)

    assert prepare(replication)["rehearsal"] is True
    assert replication.join("noust-replica:v1:x", confirmed=True)["rehearsal"] is True
    assert replication.promote(confirmed=True)["rehearsal"] is True
    assert replication.remove("reports", confirmed=True)["rehearsal"] is True
    assert engine.statements == [] and engine.restarts == 0


def test_the_hba_block_goes_above_the_connection_lines_and_beside_the_other_noust_block() -> None:
    first = pg_hba.with_block(DEBIAN_HBA, "shop", "shop_remote")

    both = pg_hba.with_replication_block(first, "noust_repl_reports", "203.0.113.7")

    lines = both.splitlines()
    assert lines.index("# BEGIN noust remote access: shop_remote") < lines.index(
        "# BEGIN noust replication: noust_repl_reports"
    ) or lines.index("# BEGIN noust replication: noust_repl_reports") < lines.index(
        "# BEGIN noust remote access: shop_remote"
    )
    for marker in ("# BEGIN noust replication", "# BEGIN noust remote access"):
        assert both.index(marker) < both.index("local   all")
    assert pg_hba.without_replication_block(both, "noust_repl_reports") == first


def test_a_server_that_archives_its_log_does_not_join_until_it_stops(
    replication: ReplicationManager, engine: Server, code: str
) -> None:
    from noust.managers.database.pitr_records import PitrPolicy, PitrRecords

    PitrRecords(get_store()).save(
        PitrPolicy(
            engine="postgresql",
            system_id="1",
            archive_path="/x",
            base_every_hours=168,
        )
    )

    plan = replication.plan_join(code)

    assert any("Point-in-time recovery is on" in problem for problem in plan.problems)
