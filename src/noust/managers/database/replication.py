# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Read replicas of the host's PostgreSQL, over TLS, with an account of their own (checklist C-14).

A replica is another server's PostgreSQL that follows this one's write-ahead log and answers
read-only queries. Nothing here is a tool of its own: it is the engine's streaming replication
(``pg_basebackup``, a physical slot, ``primary_conninfo``, ``standby.signal``) driven through the
one runner, so a pair of servers that lose Noust keep replicating, and anyone can read what was set
up in the engine's own catalogs.

**Two servers, two commands.** On the primary, ``prepare`` makes room for one replica: an account
that can only replicate (``REPLICATION LOGIN``, a generated password), a physical slot so the
primary keeps the log the replica has not read yet, one marked line in ``pg_hba.conf`` that lets
that account in from the replica's address over TLS and nothing else
(:mod:`~noust.managers.database.pg_hba`), and, if the primary listened on the loopback only, the
address to listen on. It prints a join code (:mod:`~noust.managers.database.replication_token`).
On the replica, ``join`` reads the code, copies the primary's cluster with ``pg_basebackup`` while
its own keeps running, then swaps the copy in for its own data directory and starts it following.

**TLS is not optional.** The primary refuses to be prepared unless it presents a certificate the
replica will accept for the name it connects to (the same handshake a client does, run from the
primary first), the line in ``pg_hba.conf`` is ``hostssl`` so a connection without TLS finds no
line, and the replica connects with ``sslmode=verify-full``. The primary's ``pg_stat_ssl`` is
where ``status`` reads that the connection really is encrypted.

**What the replica keeps of itself.** The copy brings the primary's ``postgresql.auto.conf`` and,
with some layouts, its configuration files. A replica is another machine: it keeps its own
configuration files, takes only the capacity settings a standby must match
(:mod:`~noust.managers.database.replication_files`), and its own data directory is set aside, never
deleted, until the operator says so.

**Failing is safe.** The copy is made beside the data directory, so a join that cannot reach the
primary, trust its certificate or find disk for it changes nothing. After the swap, a replica that
does not start or does not stream puts the previous data directory back
(:class:`~noust.managers.database.cluster_swap.ClusterSwap`, the same code a point-in-time
recovery uses). A primary's preparation undoes, in reverse, what it had made.
"""

from __future__ import annotations

import ipaddress
import pwd
import re
import shutil
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from noust.core import audit, paths
from noust.core.exceptions import (
    DatabaseBackupError,
    DatabaseEngineError,
    DatabaseError,
    NoustError,
    ValidationError,
)
from noust.core.fs import FileSystem, get_fs, is_rehearsal
from noust.core.secrets import SecretStore
from noust.core.store import get_store
from noust.managers.database import pg_hba, replication_files
from noust.managers.database.cluster_swap import ClusterSwap, cluster_lock
from noust.managers.database.pitr_records import PitrRecords
from noust.managers.database.postgres import PostgresManager
from noust.managers.database.replication_records import (
    PRIMARY,
    REPLICA,
    ReplicaRecord,
    ReplicationRecords,
)
from noust.managers.database.replication_token import (
    REQUIRED_SETTINGS,
    ReplicaToken,
    encode,
    parse,
)
from noust.managers.server.security_firewall import Firewall, RuleRequest
from noust.managers.server.security_probe import SecurityProbe
from noust.validators.replica import check_replica_host, check_replica_name, role_for, slot_for

__all__ = ["ENGINE", "PreparePlan", "ReplicationManager"]

#: The engine replicas are offered for.
ENGINE = "postgresql"

#: The account the engine runs as, which owns the copy and reads the passfile.
ENGINE_ACCOUNT = "postgres"

#: Where the primary keeps a replica's password, under the secret store.
SECRET_PREFIX = "databases/postgresql/replication"  # noqa: S105 - a path, not a password

#: The directory of the engine's account that holds a replica's passfile and certificate authority.
PASSFILE_DIRECTORY = ".noust-replica"

#: Seconds a copy of the primary may take.
COPY_TIMEOUT = 6 * 3600

#: Seconds the replica is given to start in recovery, and then to be streaming.
START_TIMEOUT = 180
STREAM_TIMEOUT = 180

#: Seconds a connection to the primary may take to be made.
CONNECT_TIMEOUT = 15

#: Seconds a slot is given to become inactive after its sender was ended.
SLOT_RELEASE_SECONDS = 20

#: Free space a replica must have beyond the primary's size, as a fraction.
SPACE_MARGIN = 1.1

#: Retained log, in bytes, beyond which ``status`` warns that a slot is holding the primary's disk.
RETAINED_WARNING_BYTES = 2 * 1024**3

#: Where the distributions keep the certificate authorities they trust, in the order tried.
SYSTEM_ROOTS = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/ca-bundle.pem",
)

#: Settings that move the postmaster's listening sockets: ``listen_addresses`` takes a restart.
_LISTEN = re.compile(r"(?:\*|[0-9A-Fa-f:.]{2,45})(?:,\s*(?:\*|[0-9A-Fa-f:.]{2,45}))*")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _literal(value: str) -> str:
    """
    Quote a value written into SQL by this module.

    Args:
        value: A name or a number this module chose or validated.

    Returns:
        It as a SQL string literal.
    """
    return "'" + value.replace("'", "''") + "'"


@dataclass
class PreparePlan:
    """
    What preparing a replica would do, worked out before anything is touched.

    Attributes:
        name: The replica's name.
        account: The replication account.
        slot: The physical slot.
        host: The name the replica connects to.
        port: The primary's port.
        source: Where the replica connects from, in the form ``pg_hba.conf`` has it.
        listen: The ``listen_addresses`` to set, or None when it already reaches the replica.
        restart: Whether the engine restarts for it.
        firewall: Whether a firewall rule is added.
        steps: What will be done, in order.
        problems: What stops it. Nothing is done while there are any.
        warnings: What the operator should know.
    """

    name: str
    account: str
    slot: str
    host: str
    port: int
    source: str
    listen: str | None = None
    restart: bool = False
    firewall: bool = False
    steps: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The plan as plain data, JSON-ready.
        """
        return {
            "name": self.name,
            "account": self.account,
            "slot": self.slot,
            "host": self.host,
            "port": self.port,
            "source": self.source,
            "listen": self.listen,
            "restart": self.restart,
            "firewall": self.firewall,
            "steps": list(self.steps),
            "problems": list(self.problems),
            "warnings": list(self.warnings),
            "ok": not self.problems,
        }


@dataclass
class JoinPlan:
    """
    What joining a primary would do to this server, worked out before anything is touched.

    Attributes:
        token: The checked join code.
        data_directory: The data directory that is replaced.
        saved_as: Where it is set aside.
        databases: The databases on this server that the join replaces.
        free_bytes: Free space where the copy is made.
        needed_bytes: What the primary said it holds, with a margin.
        capacity: The capacity settings the replica starts with.
        problems: What stops it.
        warnings: What the operator should know.
    """

    token: ReplicaToken
    data_directory: str
    saved_as: str
    databases: list[str] = field(default_factory=list)
    free_bytes: int = 0
    needed_bytes: int = 0
    capacity: dict[str, int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The plan as plain data, JSON-ready; the join code's password is not in it.
        """
        return {
            "name": self.token.name,
            "primary": f"{self.token.host}:{self.token.port}",
            "account": self.token.role,
            "slot": self.token.slot,
            "data_directory": self.data_directory,
            "saved_as": self.saved_as,
            "databases": list(self.databases),
            "free_bytes": self.free_bytes,
            "needed_bytes": self.needed_bytes,
            "capacity": dict(self.capacity),
            "problems": list(self.problems),
            "warnings": list(self.warnings),
            "ok": not self.problems,
        }


class ReplicationManager:
    """
    Prepare, join, watch, promote and remove replicas of the host's PostgreSQL.
    """

    def __init__(
        self,
        manager: PostgresManager | None = None,
        *,
        records: ReplicationRecords | None = None,
        secrets: SecretStore | None = None,
        firewall: Callable[[], Firewall] | None = None,
        fs: FileSystem | None = None,
        clock: Callable[[], datetime] = _utcnow,
        sleep: Callable[[float], None] = time.sleep,
        state_root: Path | None = None,
        home: Path | None = None,
        disk_free: Callable[[Path], int] | None = None,
        local_address: Callable[[str], bool] | None = None,
    ) -> None:
        """
        Args:
            manager: The PostgreSQL manager. A default one when None.
            records: The store of replicas. The process-wide store's when None.
            secrets: Where a primary keeps a replica's password. The default store when None.
            firewall: Builds the firewall manager. ufw and firewalld by default.
            fs: The filesystem seam. The process-wide one when None.
            clock: The current instant, injected so a test controls it.
            sleep: Waits between two looks at the engine, injected for the same reason.
            state_root: The directory a primary keeps a replica's certificate authority under.
                ``<state dir>/replication`` when None.
            home: The engine account's home, where a replica's passfile lives. Its entry in the
                account database when None.
            disk_free: Says how many bytes are free where a path is; ``shutil.disk_usage``'s
                when None.
            local_address: Says whether an address is one of this machine's; a bind attempt
                when None.
        """
        self.manager = manager or PostgresManager()
        self._records = records
        self._secrets = secrets
        self._firewall = firewall
        self._fs = fs
        self._clock = clock
        self._sleep = sleep
        self._state_root = state_root
        self._home = home
        self._disk_free = disk_free or (lambda path: shutil.disk_usage(path).free)
        self._local_address = local_address or self._can_bind
        self._swap: ClusterSwap | None = None

    # ==================== Collaborators ====================

    @property
    def records(self) -> ReplicationRecords:
        """The replicas' store."""
        if self._records is None:
            self._records = ReplicationRecords(get_store())
        return self._records

    @property
    def secrets(self) -> SecretStore:
        """Where a replica's password is kept on the primary."""
        if self._secrets is None:
            self._secrets = SecretStore()
        return self._secrets

    @property
    def fs(self) -> FileSystem:
        """The filesystem seam every change goes through."""
        return self._fs or get_fs()

    @property
    def swap(self) -> ClusterSwap:
        """The steps of replacing the cluster, shared with point-in-time recovery."""
        if self._swap is None:
            self._swap = ClusterSwap(
                self.manager, fs=self._fs, clock=self._clock, sleep=self._sleep
            )
        return self._swap

    def firewall(self) -> Firewall:
        """
        Build the firewall manager.

        Returns:
            One over the manager's runner, with no confirmation ledger: only the direct allow and
            remove paths are used here.
        """
        if self._firewall is not None:
            return self._firewall()
        return Firewall(SecurityProbe(runner=self.manager.runner), None, actor="noust db replica")

    def _audit(self, event: str, outcome: str = "ok", **details: Any) -> None:
        """
        Record a change in the audit trail.

        Args:
            event: A catalog name, such as ``db.replica.prepare``.
            outcome: ``ok`` or ``failed``.
            **details: What the entry says; never a password.
        """
        audit.record(event, target=f"database:{ENGINE}", outcome=outcome, details=details)

    def state_directory(self, name: str) -> Path:
        """
        Name where a primary keeps what it needs for a replica besides the secret store.

        Args:
            name: The replica's name.

        Returns:
            ``<state dir>/replication/<name>``.
        """
        root = self._state_root or (paths.state_dir() / "replication")
        return root / check_replica_name(name)

    def passfile_directory(self) -> Path:
        """
        Name where a replica keeps its passfile: a directory of the engine account's home.

        Returns:
            ``<home>/.noust-replica``.

        Raises:
            DatabaseEngineError: When the engine's account does not exist.
        """
        if self._home is not None:
            return self._home / PASSFILE_DIRECTORY
        try:
            return Path(pwd.getpwnam(ENGINE_ACCOUNT).pw_dir) / PASSFILE_DIRECTORY
        except KeyError as exc:
            raise DatabaseEngineError(
                f"The account {ENGINE_ACCOUNT!r} does not exist",
                details="PostgreSQL runs as it; install the engine first: noust db install "
                "postgresql",
            ) from exc

    def _discard(self, path: Path) -> None:
        """
        Delete a directory tree if it is there.

        Args:
            path: The directory.
        """
        if path.exists():
            self.fs.remove_tree(path)

    @staticmethod
    def _can_bind(address: str) -> bool:
        """
        Say whether an address belongs to this machine, by trying to bind it.

        Args:
            address: An IP address.

        Returns:
            Whether a socket can be bound to it.
        """
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.bind((address, 0))
        except OSError:
            return False
        return True

    # ==================== Asking the engine ====================

    def _sql(self, sql: str, *, secrets: tuple[str, ...] = (), timeout: int = 120) -> str:
        """
        Run SQL on the cluster as its superuser.

        Args:
            sql: The statement, built from this module's constants and checked names.
            secrets: Values the statement carries that must not be logged.
            timeout: Deadline in seconds.

        Returns:
            psql's output.

        Raises:
            DatabaseEngineError: When the engine does not answer or refuses, with its words.
        """
        ok, output = self.manager._execute_sql(sql, secrets=secrets, timeout=timeout)
        if not ok:
            raise DatabaseEngineError(
                "PostgreSQL refused a statement of the replica setup",
                details=output.strip()[-2000:] or "psql returned no output.",
            )
        return output.strip()

    def _require_engine(self) -> None:
        """
        Check there is a PostgreSQL of this server to work on.

        Raises:
            DatabaseEngineError: When it is not installed, is an instance, or is not running.
        """
        if not self.manager.is_installed():
            raise DatabaseEngineError(
                "PostgreSQL is not installed on this server",
                details="Install it first: noust db install postgresql",
            )
        if self.manager.instance is not None:
            raise DatabaseEngineError(
                "Replicas are for the PostgreSQL of this server, not an instance",
                details="An instance keeps its data in a Docker volume; a replica of it is not "
                "offered yet.",
            )
        if not self.manager.is_running():
            raise DatabaseEngineError(
                "PostgreSQL is not running",
                details="Start it first: noust db start postgresql",
            )

    def facts(self) -> dict[str, Any]:
        """
        Read what the cluster says about itself that replication depends on.

        Returns:
            ``version`` (a number such as 160004), ``major``, ``in_recovery``, ``ssl``,
            ``wal_level``, ``max_wal_senders``, ``max_replication_slots``, ``listen_addresses``,
            ``port``, ``data_directory``, ``system_id``, ``size`` (bytes) and the five capacity
            settings of :data:`REQUIRED_SETTINGS`.

        Raises:
            DatabaseEngineError: When the engine is not there, does not answer or answers
                something unexpected.
        """
        self._require_engine()
        output = self._sql(
            "SELECT current_setting('server_version_num'), pg_is_in_recovery(), "  # noqa: S608 - names are checked by check_replica_name and quoted as literals
            "current_setting('ssl'), current_setting('wal_level'), "
            "current_setting('max_wal_senders'), current_setting('max_replication_slots'), "
            "current_setting('listen_addresses'), current_setting('port'), "
            "current_setting('data_directory'), "
            "(SELECT system_identifier FROM pg_control_system()), "
            "(SELECT coalesce(sum(pg_database_size(datname)), 0) FROM pg_database), "
            + ", ".join(f"current_setting('{key}')" for key in REQUIRED_SETTINGS)
            + ";"
        )
        fields = output.splitlines()[-1].split("|") if output else []
        expected = 11 + len(REQUIRED_SETTINGS)
        numeric = (0, 4, 5, 7, 9, 10, *range(11, expected))
        if len(fields) != expected or not all(fields[at].isdigit() for at in numeric):
            raise DatabaseEngineError(
                "PostgreSQL answered something the replica setup cannot read",
                details=output[-500:] or "No answer.",
            )
        version = int(fields[0])
        return {
            "version": version,
            "major": version // 10000,
            "in_recovery": fields[1] == "t",
            "ssl": fields[2],
            "wal_level": fields[3],
            "max_wal_senders": int(fields[4]),
            "max_replication_slots": int(fields[5]),
            "listen_addresses": fields[6],
            "port": int(fields[7]),
            "data_directory": fields[8],
            "system_id": fields[9],
            "size": int(fields[10]),
            **{key: int(fields[11 + at]) for at, key in enumerate(REQUIRED_SETTINGS)},
        }

    # ==================== Preparing the primary ====================

    def _prove_tls(self, host: str, port: int, ca: Path | None) -> str | None:
        """
        Run the handshake a replica does, from the primary, and say why it would be refused.

        The engine is asked, with the name the replica will use, for its certificate, and the
        certificate is checked the way ``sslmode=verify-full`` checks it: signed by the authority
        the replica trusts and valid for that name.

        Args:
            host: The name or address the replica connects to.
            port: The engine's port.
            ca: The authority the replica will trust; the system's when None.

        Returns:
            None when the certificate would be accepted, otherwise what openssl said.
        """
        try:
            ipaddress.ip_address(host)
            verify = ["-verify_ip", host]
        except ValueError:
            verify = ["-verify_hostname", host]
        argv = [
            "openssl",
            "s_client",
            "-connect",
            f"127.0.0.1:{port}",
            "-starttls",
            "postgres",
            "-servername",
            host,
            "-verify_return_error",
            *verify,
        ]
        if ca is not None:
            argv += ["-CAfile", str(ca)]
        result = self.manager.runner.run(argv, input="", timeout=30)
        text = f"{result.stdout}\n{result.stderr}"
        if result.success and re.search(r"Verify return code: 0 \(ok\)", text):
            return None
        reasons = [
            line.strip()
            for line in text.splitlines()
            if re.match(r"\s*(verify error|Verify return code|.*error:|.*handshake failure)", line)
        ]
        return "\n".join(reasons[-4:]) or text.strip()[-500:] or "No answer to the handshake."

    def plan_prepare(
        self,
        name: str,
        *,
        source: str,
        host: str,
        listen: str | None = None,
        ca_pem: str = "",
        open_firewall: bool = False,
    ) -> PreparePlan:
        """
        Work out what preparing a replica would do, touching nothing.

        Args:
            name: The replica's name.
            source: Where it connects from: an address or a network.
            host: The name or address it connects to; the certificate must cover it.
            listen: ``listen_addresses`` to add when the engine listens on the loopback only:
                ``*`` or addresses of this machine.
            ca_pem: The authority the replica will trust, PEM; the system's when empty.
            open_firewall: Allow the engine's port from ``source`` in the firewall.

        Returns:
            The plan; ``problems`` says what stops it.

        Raises:
            ValidationError: When a name or an address is not acceptable.
            DatabaseEngineError: When the engine is not there to ask.
        """
        name = check_replica_name(name)
        try:
            network = pg_hba.replica_address(source)
        except ValueError as exc:
            raise ValidationError(str(exc), field="source") from exc
        host = check_replica_host(host)
        facts = self.facts()
        plan = PreparePlan(
            name=name,
            account=role_for(name),
            slot=slot_for(name),
            host=host,
            port=facts["port"],
            source=network,
            firewall=open_firewall,
        )
        problems = plan.problems
        if facts["in_recovery"]:
            problems.append(
                "This server is itself a replica. A replica of a replica is not offered: "
                "prepare the replica on the primary."
            )
        if facts["version"] < 120000:
            problems.append(
                f"PostgreSQL {facts['version'] // 10000} is too old: replicas need 12 or newer."
            )
        if facts["wal_level"] not in ("replica", "logical"):
            problems.append(
                f"wal_level is {facts['wal_level']!r}, and a replica needs at least 'replica'. "
                "Set it (ALTER SYSTEM SET wal_level = 'replica') and restart PostgreSQL."
            )
        existing = self.records.records(PRIMARY)
        if facts["max_wal_senders"] < len(existing) + 2:
            problems.append(
                f"max_wal_senders is {facts['max_wal_senders']}: raise it to at least "
                f"{len(existing) + 2} and restart PostgreSQL."
            )
        if facts["max_replication_slots"] < len(existing) + 1:
            problems.append(
                f"max_replication_slots is {facts['max_replication_slots']}: raise it to at least "
                f"{len(existing) + 1} and restart PostgreSQL."
            )
        if self.records.get(PRIMARY, name) is not None:
            problems.append(f"A replica named {name} is already prepared here.")
        else:
            taken = self._sql(
                f"SELECT (SELECT count(*) FROM pg_roles WHERE rolname = {_literal(plan.account)}) "  # noqa: S608 - names are checked by check_replica_name and quoted as literals
                f"+ (SELECT count(*) FROM pg_replication_slots "
                f"WHERE slot_name = {_literal(plan.slot)});"
            )
            if taken != "0":
                problems.append(
                    f"The account {plan.account} or the slot {plan.slot} exists already, and "
                    "Noust has no record of making them. Pick another name, or remove them."
                )
        if facts["ssl"] != "on":
            problems.append(
                "PostgreSQL is not serving TLS, and a replica connects over TLS only. Give it a "
                "certificate first: noust db tls enable --engine postgresql --host <name>"
            )
        else:
            self._plan_tls(plan, facts, ca_pem)
        self._plan_listen(plan, facts, listen)
        plan.steps = [
            f"Create the account {plan.account} (REPLICATION LOGIN, a generated password)",
            f"Create the physical replication slot {plan.slot}",
            f"Add one line to pg_hba.conf: hostssl replication {plan.account} {network} "
            "scram-sha-256",
        ]
        if plan.listen:
            plan.steps.insert(0, f"Set listen_addresses to {plan.listen} and restart PostgreSQL")
        if open_firewall:
            plan.steps.append(f"Allow port {plan.port}/tcp from {network} in the firewall")
        else:
            plan.warnings.append(
                f"The firewall is not changed: allow port {plan.port}/tcp from {network} "
                "yourself, or pass --open-firewall."
            )
        plan.steps.append("Print the join code for the replica")
        return plan

    def _plan_tls(self, plan: PreparePlan, facts: dict[str, Any], ca_pem: str) -> None:
        """
        Prove the engine presents a certificate the replica will accept.

        Args:
            plan: The plan being worked out; a refusal is added to it.
            facts: What the cluster says about itself.
            ca_pem: The authority the replica will trust; the system's when empty.
        """
        if not ca_pem:
            reason = self._prove_tls(plan.host, facts["port"], None)
        else:
            reason = self._prove_tls_with(plan.host, facts["port"], ca_pem)
        if reason is not None:
            plan.problems.append(
                f"The certificate PostgreSQL presents would not be accepted by a replica "
                f"connecting to {plan.host}: {reason}\n"
                "Give PostgreSQL a certificate for that name (noust db tls enable), or pass the "
                "authority that signed it (--ca-file)."
            )

    def _prove_tls_with(self, host: str, port: int, ca_pem: str) -> str | None:
        """
        Run :meth:`_prove_tls` against a certificate authority given as text.

        Args:
            host: The name or address the replica connects to.
            port: The engine's port.
            ca_pem: The authority, PEM.

        Returns:
            None when the certificate would be accepted, otherwise what openssl said.
        """
        scratch = (self._state_root or (paths.state_dir() / "replication")) / ".proof"
        self.fs.make_dir(scratch, mode=0o700)
        authority = scratch / "ca.pem"
        try:
            self.fs.write_text(authority, ca_pem, mode=0o600)
            return self._prove_tls(host, port, authority)
        finally:
            self._discard(scratch)

    def _plan_listen(self, plan: PreparePlan, facts: dict[str, Any], listen: str | None) -> None:
        """
        Decide whether the engine must be told to listen where the replica can reach it.

        Args:
            plan: The plan being worked out; ``listen`` and ``restart`` are set on it.
            facts: What the cluster says about itself.
            listen: What the operator asked to listen on, if anything.
        """
        current = facts["listen_addresses"]
        entries = [item.strip() for item in current.split(",") if item.strip()]
        reaches = "*" in entries or any(
            item not in ("localhost", "127.0.0.1", "::1", "") for item in entries
        )
        if listen is None:
            if not reaches:
                plan.problems.append(
                    f"PostgreSQL listens on {current!r} only, so no other server can connect. "
                    "Say where it should listen: --listen '*' or --listen <this server's "
                    "address>. That restarts PostgreSQL once."
                )
            return
        if _LISTEN.fullmatch(listen.strip()) is None:
            plan.problems.append(
                f"{listen!r} is not '*' or a list of addresses (--listen 10.0.0.5,10.0.1.5)."
            )
            return
        wanted = [item.strip() for item in listen.split(",")]
        for address in wanted:
            if address != "*" and not self._local_address(address):
                plan.problems.append(
                    f"{address} is not an address of this server, so PostgreSQL could not "
                    "listen on it and would not start."
                )
        merged = ["*"] if "*" in wanted or "*" in entries else [*entries, *wanted]
        deduplicated = list(dict.fromkeys(merged))
        value = ",".join(deduplicated)
        if value != current:
            plan.listen = value
            plan.restart = True
            plan.warnings.append(
                "PostgreSQL restarts once (a few seconds) to listen on the new addresses; "
                "connections are dropped."
            )
            plan.warnings.append(
                f"Port {facts['port']} is then reachable from the network. pg_hba.conf lets in "
                "only the replication account, from the replica's address, over TLS; the "
                "firewall decides who reaches the port at all."
            )

    def prepare(
        self,
        name: str,
        *,
        source: str,
        host: str,
        listen: str | None = None,
        ca_pem: str = "",
        open_firewall: bool = False,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """
        Make room on this primary for one replica and give back its join code.

        Args:
            name: The replica's name.
            source: Where it connects from: an address or a network.
            host: The name or address it connects to; the certificate must cover it.
            listen: ``listen_addresses`` to add when the engine listens on the loopback only.
            ca_pem: The authority the replica will trust, PEM; the system's when empty.
            open_firewall: Allow the engine's port from ``source`` in the firewall.
            confirmed: The operator accepted the engine's restart, if one is needed (the guard is
                here, not in the caller that asks).

        Returns:
            ``token`` (the join code: a secret), ``plan`` and ``record``.

        Raises:
            ValidationError: When something stops it (the plan's problems, verbatim), or a
                restart was needed and not confirmed.
            DatabaseError: When a step fails; what had been made is undone first.
        """
        name = check_replica_name(name)
        if is_rehearsal():
            return {"token": None, "rehearsal": True, "plan": None, "record": None}
        with cluster_lock("replica preparation"):
            plan = self.plan_prepare(
                name,
                source=source,
                host=host,
                listen=listen,
                ca_pem=ca_pem,
                open_firewall=open_firewall,
            )
            if plan.problems:
                raise ValidationError(
                    "A replica cannot be prepared on this server yet",
                    details="\n".join(f"- {problem}" for problem in plan.problems),
                    field="plan",
                )
            if plan.restart and not confirmed:
                raise ValidationError(
                    "Preparing this replica restarts PostgreSQL",
                    details=f"listen_addresses only changes with a restart. Confirm to go ahead: "
                    f"PostgreSQL will listen on {plan.listen}.",
                    field="confirmed",
                )
            facts = self.facts()
            return self._prepare(plan, facts, ca_pem)

    def _prepare(self, plan: PreparePlan, facts: dict[str, Any], ca_pem: str) -> dict[str, Any]:
        """
        Do what a plan says, undoing it in reverse when a step fails.

        Args:
            plan: The plan, with no problems.
            facts: What the cluster says about itself.
            ca_pem: The authority the replica will trust, PEM; empty for the system's.

        Returns:
            What :meth:`prepare` returns.

        Raises:
            DatabaseError: When a step fails; what had been made is undone first.
        """
        undo: list[tuple[str, Callable[[], object]]] = []
        listen_was: str | None = None
        password = self.manager.generate_password(40)
        record = ReplicaRecord(
            side=PRIMARY,
            name=plan.name,
            account=plan.account,
            slot=plan.slot,
            host=plan.host,
            port=plan.port,
            source=plan.source,
        )
        try:
            if plan.listen:
                listen_was = facts["listen_addresses"]
                self._set_listen(plan.listen, listen_was, facts["data_directory"])
                record.listen_was = listen_was
                undo.append(
                    (
                        "put listen_addresses back",
                        lambda: self._restore_listen(listen_was, facts["data_directory"]),
                    )
                )
            self._sql(
                "SET password_encryption = 'scram-sha-256'; "
                f"CREATE ROLE {self.manager._escape_identifier(plan.account)} "
                f"WITH REPLICATION LOGIN PASSWORD {self.manager._escape_literal(password)};",
                secrets=(password,),
            )
            undo.append(("drop the account", lambda: self._drop_account(plan.account)))
            self._sql(f"SELECT pg_create_physical_replication_slot({_literal(plan.slot)}, true);")
            undo.append(("drop the slot", lambda: self._drop_slot(plan.slot)))
            self.manager.edit_hba(
                lambda text: pg_hba.with_replication_block(text, plan.account, plan.source)
            )
            undo.append(
                (
                    "remove the pg_hba.conf line",
                    lambda: self.manager.edit_hba(
                        lambda text: pg_hba.without_replication_block(text, plan.account)
                    ),
                )
            )
            if plan.firewall:
                record.firewall = self._open_firewall(plan)
                undo.append(("close the firewall", lambda: self._close_firewall(record)))
            self.secrets.write(f"{SECRET_PREFIX}/{plan.name}", password)
            undo.append(
                ("forget the password", lambda: self.secrets.delete(self._secret(plan.name)))
            )
            token = ReplicaToken(
                name=plan.name,
                host=plan.host,
                port=plan.port,
                role=plan.account,
                password=password,
                slot=plan.slot,
                major=facts["major"],
                size=facts["size"],
                requires={key: facts[key] for key in REQUIRED_SETTINGS},
                ca=ca_pem,
            )
            self._keep_authority(plan.name, ca_pem)
            stored = self.records.save(record)
        except (NoustError, OSError) as exc:
            self._undo(undo)
            self._audit("db.replica.prepare", "failed", name=plan.name, error=str(exc)[:300])
            raise
        self._audit(
            "db.replica.prepare",
            name=plan.name,
            account=plan.account,
            slot=plan.slot,
            source=plan.source,
            firewall=bool(plan.firewall),
            listen=plan.listen,
        )
        self.manager.logger.success(f"Replica {plan.name} prepared")
        return {"token": encode(token), "plan": plan.to_dict(), "record": stored.to_dict()}

    @staticmethod
    def _secret(name: str) -> str:
        """
        Args:
            name: The replica's name.

        Returns:
            The secret's name.
        """
        return f"{SECRET_PREFIX}/{name}"

    def _undo(self, steps: list[tuple[str, Callable[[], object]]]) -> None:
        """
        Run the undoing of what a failed step left, newest first, saying what cannot be undone.

        Args:
            steps: The undoing of each step done so far, in the order they were done.
        """
        for what, action in reversed(steps):
            try:
                action()
            except (NoustError, OSError) as exc:
                self.manager.logger.warning(f"Could not {what}: {exc}. Undo it by hand.")

    def _set_listen(self, value: str, previous: str, data_directory: str) -> None:
        """
        Make the engine listen on more addresses, restarting it.

        Args:
            value: The new ``listen_addresses``.
            previous: What it was, put back if the engine does not come up.
            data_directory: The cluster's data directory, to put the file back by hand if the
                engine does not come up.

        Raises:
            DatabaseEngineError: When the engine does not come back; the setting is put back.
        """
        self._sql(f"ALTER SYSTEM SET listen_addresses = {_literal(value)};")
        try:
            self.manager.restart()
            self.swap.wait_ready()
        except (DatabaseEngineError, DatabaseError):
            self._restore_listen(previous, data_directory)
            raise

    def _restore_listen(self, previous: str, data_directory: str) -> None:
        """
        Put ``listen_addresses`` back and restart, even when the engine is down.

        An engine that does not start cannot be asked to ``ALTER SYSTEM RESET``, so the setting is
        also taken out of ``postgresql.auto.conf`` by hand.

        Args:
            previous: What the setting was.
            data_directory: The cluster's data directory.
        """
        try:
            self._sql(f"ALTER SYSTEM SET listen_addresses = {_literal(previous)};")
        except DatabaseEngineError:
            auto = Path(data_directory) / "postgresql.auto.conf"
            try:
                kept = [
                    line
                    for line in auto.read_text(encoding="utf-8").splitlines()
                    if replication_files.auto_conf_value(line, "listen_addresses") is None
                ]
                self.fs.write_text(auto, "\n".join(kept) + "\n", mode=0o600)
                self.fs.set_owner(auto, user=ENGINE_ACCOUNT, group=ENGINE_ACCOUNT, mode=0o600)
            except OSError as exc:
                self.manager.logger.error(
                    f"Could not take listen_addresses out of {auto}: {exc}. Do it by hand."
                )
        try:
            self.manager.restart()
            self.swap.wait_ready()
        except (DatabaseEngineError, DatabaseError) as exc:
            self.manager.logger.error(f"PostgreSQL did not come back: {exc}")

    def _drop_account(self, account: str) -> None:
        """
        Drop a replication account if it is there.

        Args:
            account: The account (checked by construction).
        """
        self._sql(f"DROP ROLE IF EXISTS {self.manager._escape_identifier(account)};")

    def _drop_slot(self, slot: str) -> None:
        """
        End the sender using a slot, wait for it to be released, and drop the slot if it is there.

        Args:
            slot: The slot (checked by construction).

        Raises:
            DatabaseEngineError: When the slot stays in use.
        """
        quoted = _literal(slot)
        self._sql(
            "SELECT pg_terminate_backend(active_pid) FROM pg_replication_slots "  # noqa: S608 - names are checked by check_replica_name and quoted as literals
            f"WHERE slot_name = {quoted} AND active_pid IS NOT NULL;"
        )
        deadline = self._clock().timestamp() + SLOT_RELEASE_SECONDS
        while True:
            state = self._sql(
                f"SELECT active FROM pg_replication_slots WHERE slot_name = {quoted};"  # noqa: S608 - names are checked by check_replica_name and quoted as literals
            )
            if state in ("", "f"):
                break
            if self._clock().timestamp() >= deadline:
                raise DatabaseEngineError(
                    f"The slot {slot} is still in use",
                    details="Its replica keeps reconnecting. Take away its pg_hba.conf line "
                    "first, or stop the replica.",
                )
            self._sleep(1)
        self._sql(
            f"SELECT pg_drop_replication_slot(slot_name) FROM pg_replication_slots "  # noqa: S608 - names are checked by check_replica_name and quoted as literals
            f"WHERE slot_name = {quoted};"
        )

    def _firewall_rule(self, port: int, source: str, name: str) -> RuleRequest:
        """
        Build the rule a replica asks of the firewall.

        Args:
            port: The engine's port.
            source: Where the replica connects from.
            name: The replica's name.

        Returns:
            The allow rule.
        """
        return RuleRequest("allow", port, "tcp", source, f"db replica {name}")

    def _open_firewall(self, plan: PreparePlan) -> dict[str, Any]:
        """
        Allow the engine's port from the replica, and say whether the rule is Noust's to remove.

        Args:
            plan: The plan.

        Returns:
            ``backend``, ``active`` and ``created``.
        """
        change = self.firewall().allow_direct(
            self._firewall_rule(plan.port, plan.source, plan.name)
        )
        return {"backend": change.backend, "active": change.active, "created": change.changed}

    def _close_firewall(self, record: ReplicaRecord) -> bool:
        """
        Remove the firewall rule this replica added, and only that one.

        Args:
            record: The replica's record.

        Returns:
            Whether a rule was removed.
        """
        if not record.firewall.get("created") or not record.source:
            return False
        rule = self._firewall_rule(record.port, record.source, record.name)
        return bool(self.firewall().remove_direct(rule).changed)

    def _keep_authority(self, name: str, ca_pem: str) -> None:
        """
        Keep the authority a replica will trust, so the join code can be shown again.

        Args:
            name: The replica's name.
            ca_pem: The authority, PEM; nothing is kept when empty.
        """
        if not ca_pem:
            return
        directory = self.state_directory(name)
        self.fs.make_dir(directory, mode=0o700)
        self.fs.write_text(directory / "ca.pem", ca_pem, mode=0o600)

    def token(self, name: str) -> str:
        """
        Give a prepared replica's join code again.

        Args:
            name: The replica's name.

        Returns:
            The code (a secret).

        Raises:
            ValidationError: When no replica of that name was prepared here.
            DatabaseEngineError: When the engine does not answer.
        """
        name = check_replica_name(name)
        record = self.records.get(PRIMARY, name)
        password = self.secrets.read(self._secret(name))
        if record is None or not password:
            raise ValidationError(
                f"No replica named {name} is prepared on this server",
                details="Prepare it first: noust db replica prepare",
                field="name",
            )
        facts = self.facts()
        authority = self.state_directory(name) / "ca.pem"
        ca = authority.read_text(encoding="utf-8") if authority.is_file() else ""
        return encode(
            ReplicaToken(
                name=name,
                host=record.host,
                port=facts["port"],
                role=record.account,
                password=password,
                slot=record.slot,
                major=facts["major"],
                size=facts["size"],
                requires={key: facts[key] for key in REQUIRED_SETTINGS},
                ca=ca,
            )
        )

    # ==================== Joining a primary ====================

    def plan_join(self, code: str) -> JoinPlan:
        """
        Work out what joining a primary would do to this server, touching nothing.

        Args:
            code: The join code the primary printed.

        Returns:
            The plan; ``problems`` says what stops it.

        Raises:
            ValidationError: When the code is not one Noust made.
            DatabaseEngineError: When the engine is not there to ask.
        """
        token = parse(code)
        facts = self.facts()
        data = Path(facts["data_directory"])
        plan = JoinPlan(
            token=token,
            data_directory=str(data),
            saved_as=str(data.parent / f"{data.name}.noust-before-replica"),
            needed_bytes=int(token.size * SPACE_MARGIN),
        )
        if facts["in_recovery"]:
            plan.problems.append(
                "This server already follows a primary. Promote it (noust db replica promote) "
                "or remove it from there before following another."
            )
        if facts["major"] != token.major:
            plan.problems.append(
                f"The primary runs PostgreSQL {token.major} and this server runs "
                f"{facts['major']}. A replica is a physical copy and needs the same major "
                f"version: install PostgreSQL {token.major} here first."
            )
        if PitrRecords(get_store()).get(ENGINE) is not None:
            plan.problems.append(
                "Point-in-time recovery is on for this server. The copy has the primary's "
                "cluster identity, so the archive and the base backups kept here would no longer "
                "match it. Turn it off first (noust db pitr disable), and on again after "
                "promoting."
            )
        if Path(plan.saved_as).exists():
            plan.problems.append(
                f"{plan.saved_as} exists from an earlier join. Move or delete it first."
            )
        scratch = data.parent
        try:
            plan.free_bytes = self._disk_free(scratch)
        except OSError:
            plan.free_bytes = 0
        if plan.free_bytes < plan.needed_bytes:
            plan.problems.append(
                f"The copy needs about {plan.needed_bytes} bytes and {scratch} has "
                f"{plan.free_bytes}."
            )
        if Path(f"{data}.noust-replica-new").exists():
            plan.problems.append(
                f"{data}.noust-replica-new exists from an earlier attempt. Delete it first."
            )
        plan.databases = [
            line
            for line in self._sql(
                "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname <> 'postgres' "
                "ORDER BY 1;"
            ).splitlines()
            if line
        ]
        if plan.databases:
            plan.warnings.append(
                "These databases of this server are replaced by the primary's (the data "
                f"directory is set aside, not deleted): {', '.join(plan.databases)}"
            )
        plan.capacity = {key: max(facts[key], token.requires[key]) for key in REQUIRED_SETTINGS}
        if self._authority_path(token) is None:
            plan.problems.append(
                "The join code names no certificate authority and this server has no list of "
                "trusted ones: the primary's certificate cannot be checked."
            )
        return plan

    def _authority_path(self, token: ReplicaToken) -> Path | None:
        """
        Find the file of authorities a replica trusts for its primary.

        Args:
            token: The join code.

        Returns:
            Where the code's own authority is written, or the system's list; None when the code
            gives none and the system has no list.
        """
        if token.ca:
            return self.passfile_directory() / f"{token.name}.ca.pem"
        for candidate in SYSTEM_ROOTS:
            if Path(candidate).is_file():
                return Path(candidate)
        return None

    def join(
        self,
        code: str,
        *,
        confirmed: bool,
        discard_previous: bool = False,
        timeout: int = COPY_TIMEOUT,
    ) -> dict[str, Any]:
        """
        Make this server a replica of the primary the code names.

        The primary's cluster is copied beside this server's own, which keeps running meanwhile; then
        the engine is stopped, the data directory is set aside, the copy takes its place and the
        engine starts following. When it does not start or does not stream, the previous directory
        is put back.

        Args:
            code: The join code.
            confirmed: The operator accepted that this server's data is replaced (the guard is
                here, not in the caller that asks).
            discard_previous: Delete the data directory that was set aside once the replica
                streams.
            timeout: Seconds the copy may take.

        Returns:
            ``name``, ``primary``, ``saved_as`` (None when discarded), ``state`` and ``seconds``.

        Raises:
            ValidationError: When the code is not valid, something stops it, or it was not
                confirmed.
            DatabaseBackupError: When the copy or the start fails; this server runs on what it
                held before.
        """
        if is_rehearsal():
            return {"name": None, "rehearsal": True}
        with cluster_lock("replica join"):
            plan = self.plan_join(code)
            if plan.problems:
                raise ValidationError(
                    "This server cannot join that primary yet",
                    details="\n".join(f"- {problem}" for problem in plan.problems),
                    field="plan",
                )
            if not confirmed:
                raise ValidationError(
                    "Joining a primary replaces this server's PostgreSQL data",
                    details=f"{plan.data_directory} is set aside as {plan.saved_as} and replaced "
                    "by a copy of the primary's cluster. Every database here is replaced by the "
                    "primary's, and PostgreSQL restarts once. Confirm to go ahead.",
                    field="confirmed",
                )
            return self._join(plan, discard_previous=discard_previous, timeout=timeout)

    def _join(self, plan: JoinPlan, *, discard_previous: bool, timeout: int) -> dict[str, Any]:
        """
        Do what a plan says.

        Args:
            plan: The plan, with no problems.
            discard_previous: Delete the set-aside data directory once the replica streams.
            timeout: Seconds the copy may take.

        Returns:
            What :meth:`join` returns.

        Raises:
            DatabaseBackupError: When the copy or the start fails.
        """
        token = plan.token
        started = self._clock()
        data = Path(plan.data_directory)
        saved = Path(plan.saved_as)
        fresh = Path(f"{data}.noust-replica-new")
        directory = self.passfile_directory()
        passfile = directory / f"{token.name}.pgpass"
        authority = self._lay_down_credentials(token, directory, passfile)
        try:
            self._copy_primary(token, fresh, passfile, authority, timeout)
            self._tidy_copy(plan, fresh)
            self.swap.stop_engine(data)
            self.swap.set_aside(data, saved)
        except (DatabaseBackupError, OSError):
            self._discard(fresh)
            self._forget_credentials(token.name)
            raise
        try:
            self.fs.rename(fresh, data)
        except OSError as exc:
            self.fs.rename(saved, data)
            self.manager.start()
            self._discard(fresh)
            self._forget_credentials(token.name)
            raise DatabaseBackupError(
                "The copy could not take the place of the data directory",
                details=f"{exc}. The previous data directory is back.",
            ) from exc
        try:
            self.manager.start()
            self.swap.wait_until(
                data, "SELECT pg_is_in_recovery();", "t", timeout=START_TIMEOUT, what="the start"
            )
            self.swap.wait_until(
                data,
                "SELECT coalesce((SELECT status FROM pg_stat_wal_receiver LIMIT 1), 'none');",
                "streaming",
                timeout=STREAM_TIMEOUT,
                what="the connection to the primary",
                unfinished=f"The replica did not start streaming from the primary in "
                f"{STREAM_TIMEOUT} seconds",
            )
        except (DatabaseEngineError, DatabaseBackupError, OSError) as exc:
            self.swap.put_back(data, saved, ".noust-failed-replica")
            self._forget_credentials(token.name)
            details = (
                exc.details
                if isinstance(exc, DatabaseEngineError | DatabaseBackupError) and exc.details
                else str(exc)
            )
            self._audit("db.replica.join", "failed", name=token.name, error=str(details)[:300])
            raise DatabaseBackupError(
                "The replica did not start following, and the previous data is back",
                details=f"{details}\nThe failed attempt is kept at {data}.noust-failed-replica "
                "and PostgreSQL runs on what it held before.",
            ) from exc
        kept: str | None = str(saved)
        if discard_previous:
            self._discard(saved)
            kept = None
        # A row left by a server that stopped following (its PostgreSQL was reinstalled, say)
        # describes nothing: this one is the only primary it follows.
        for stale in self.records.records(REPLICA):
            self.records.delete(REPLICA, stale.name)
        self.records.save(
            ReplicaRecord(
                side=REPLICA,
                name=token.name,
                account=token.role,
                slot=token.slot,
                host=token.host,
                port=token.port,
                passfile=str(passfile),
                saved_as=kept,
                state="following",
            )
        )
        seconds = int((self._clock() - started).total_seconds())
        self._audit(
            "db.replica.join",
            name=token.name,
            primary=f"{token.host}:{token.port}",
            slot=token.slot,
            saved_as=kept,
            seconds=seconds,
        )
        self.manager.logger.success(f"Following {token.host}:{token.port} as {token.name}")
        return {
            "name": token.name,
            "primary": f"{token.host}:{token.port}",
            "saved_as": kept,
            "state": "streaming",
            "seconds": seconds,
        }

    def _lay_down_credentials(self, token: ReplicaToken, directory: Path, passfile: Path) -> Path:
        """
        Write the passfile (and the authority the code carries) where the engine's account reads.

        Args:
            token: The join code.
            directory: The engine account's directory for them.
            passfile: The passfile's path.

        Returns:
            The file of authorities the replica trusts.
        """
        authority = self._authority_path(token)
        assert authority is not None  # noqa: S101 - plan_join refused otherwise
        self.fs.make_dir(directory, mode=0o700)
        self.fs.set_owner(directory, user=ENGINE_ACCOUNT, group=ENGINE_ACCOUNT, mode=0o700)
        self.fs.write_text(
            passfile,
            replication_files.passfile_line(token.host, token.port, token.role, token.password),
            mode=0o600,
        )
        self.fs.set_owner(passfile, user=ENGINE_ACCOUNT, group=ENGINE_ACCOUNT, mode=0o600)
        if token.ca:
            self.fs.write_text(authority, token.ca, mode=0o644)
            self.fs.set_owner(authority, user=ENGINE_ACCOUNT, group=ENGINE_ACCOUNT, mode=0o644)
        return authority

    def _forget_credentials(self, name: str) -> None:
        """
        Remove a replica's passfile and authority.

        Args:
            name: The replica's name.
        """
        directory = self.passfile_directory()
        for leaf in (f"{name}.pgpass", f"{name}.ca.pem"):
            self.fs.remove(directory / leaf)

    def _copy_primary(
        self, token: ReplicaToken, fresh: Path, passfile: Path, authority: Path, timeout: int
    ) -> None:
        """
        Copy the primary's cluster into a directory beside this server's own.

        Args:
            token: The join code.
            fresh: The directory the copy is made in; it must not exist.
            passfile: The passfile the connection reads its password from.
            authority: The file of authorities the primary's certificate is checked against.
            timeout: Seconds the copy may take.

        Raises:
            DatabaseBackupError: When the copy fails, with the tool's own words.
        """
        result = self.manager._exec(
            [
                "pg_basebackup",
                "-h",
                token.host,
                "-p",
                str(token.port),
                "-U",
                token.role,
                "-D",
                str(fresh),
                "-X",
                "stream",
                "-S",
                token.slot,
                "-R",
                "-c",
                "fast",
                "--no-password",
            ],
            env={
                "PGPASSFILE": str(passfile),
                "PGSSLMODE": "verify-full",
                "PGSSLROOTCERT": str(authority),
                "PGCONNECT_TIMEOUT": str(CONNECT_TIMEOUT),
            },
            user=ENGINE_ACCOUNT,
            timeout=timeout,
        )
        if not result.success:
            raise DatabaseBackupError(
                "The primary's cluster could not be copied",
                details=(result.stderr or result.stdout).strip()[-2000:]
                or "pg_basebackup returned no output.",
            )

    def _tidy_copy(self, plan: JoinPlan, fresh: Path) -> None:
        """
        Make the copy a replica of this server: its own settings, not the primary's.

        Args:
            plan: The plan.
            fresh: The copy.

        Raises:
            DatabaseBackupError: When the copy is of another major version than this server's.
            OSError: When a file cannot be written.
        """
        version = (fresh / "PG_VERSION").read_text(encoding="utf-8").strip()
        if version.split(".")[0] != str(plan.token.major):
            raise DatabaseBackupError(
                "The copy is of another PostgreSQL version than the code said",
                details=f"The copy holds version {version}; the code said {plan.token.major}.",
            )
        auto = fresh / "postgresql.auto.conf"
        before = Path(plan.data_directory) / "postgresql.auto.conf"
        self.fs.write_text(
            auto,
            replication_files.standby_auto_conf(
                before.read_text(encoding="utf-8") if before.is_file() else "",
                auto.read_text(encoding="utf-8") if auto.is_file() else "",
                plan.capacity,
            ),
            mode=0o600,
        )
        self.fs.set_owner(auto, user=ENGINE_ACCOUNT, group=ENGINE_ACCOUNT, mode=0o600)
        data = Path(plan.data_directory)
        for leaf in ("postgresql.conf", "pg_hba.conf", "pg_ident.conf"):
            own = data / leaf
            # A layout that keeps its configuration inside the data directory (the RPM one)
            # would otherwise lose this server's own and run on the primary's.
            if own.is_file():
                target = fresh / leaf
                self.fs.write_bytes(target, own.read_bytes(), mode=0o640)
                self.fs.set_owner(target, user=ENGINE_ACCOUNT, group=ENGINE_ACCOUNT, mode=0o640)

    # ==================== Watching ====================

    def status(self) -> dict[str, Any]:
        """
        Say how each replica of this server, and this server as a replica, stand.

        Returns:
            ``role`` (``primary``, ``replica`` or ``standalone``), ``replicas`` (one entry per
            replica prepared here, with what the engine says about its connection), ``follows``
            (the primary this server follows, when it does) and ``warnings``.

        Raises:
            DatabaseEngineError: When the engine is not there to ask.
        """
        facts = self.facts()
        prepared = self.records.records(PRIMARY)
        following = self.records.records(REPLICA)
        warnings: list[str] = []
        view: dict[str, Any] = {
            "engine": ENGINE,
            "role": "replica"
            if facts["in_recovery"]
            else ("primary" if prepared else "standalone"),
            "replicas": [],
            "follows": None,
            "warnings": warnings,
        }
        if prepared and not facts["in_recovery"]:
            view["replicas"] = self._primary_view(facts, prepared, warnings)
        if facts["in_recovery"]:
            view["follows"] = self._replica_view(following, warnings)
        return view

    def _primary_view(
        self, facts: dict[str, Any], prepared: list[ReplicaRecord], warnings: list[str]
    ) -> list[dict[str, Any]]:
        """
        Read each prepared replica's connection from the engine.

        Args:
            facts: What the cluster says about itself.
            prepared: The replicas prepared here.
            warnings: Where to add what the operator should know.

        Returns:
            One entry per replica.
        """
        wal_status = "s.wal_status" if facts["version"] >= 130000 else "''"
        rows: dict[str, list[str]] = {}
        for line in self._sql(
            "SELECT s.slot_name, s.active, "  # noqa: S608 - names are checked by check_replica_name and quoted as literals
            "coalesce(pg_wal_lsn_diff(pg_current_wal_lsn(), s.restart_lsn), 0)::bigint, "
            f"{wal_status}, coalesce(r.state, ''), coalesce(host(r.client_addr), ''), "
            "coalesce(ssl.ssl::text, ''), coalesce(ssl.version, ''), "
            "coalesce(pg_wal_lsn_diff(r.sent_lsn, r.replay_lsn), 0)::bigint, "
            "coalesce(r.sync_state, '') "
            "FROM pg_replication_slots s "
            "LEFT JOIN pg_stat_replication r ON r.pid = s.active_pid "
            "LEFT JOIN pg_stat_ssl ssl ON ssl.pid = r.pid "
            "WHERE s.slot_name LIKE 'noust\\_%';"
        ).splitlines():
            fields = line.split("|")
            if len(fields) == 10:
                rows[fields[0]] = fields
        out: list[dict[str, Any]] = []
        for record in prepared:
            row = rows.get(record.slot)
            entry: dict[str, Any] = {
                "name": record.name,
                "account": record.account,
                "slot": record.slot,
                "source": record.source,
                "host": record.host,
                "connected": False,
                "state": "not connected",
                "tls": None,
                "tls_version": None,
                "client": None,
                "lag_bytes": None,
                "retained_bytes": None,
                "wal_status": None,
            }
            if row is None:
                warnings.append(f"The slot {record.slot} is gone from PostgreSQL.")
            else:
                entry["connected"] = row[1] == "t"
                entry["retained_bytes"] = int(row[2])
                entry["wal_status"] = row[3] or None
                if entry["connected"]:
                    entry["state"] = row[4] or "connected"
                    entry["client"] = row[5] or None
                    entry["tls"] = row[6] == "true" or row[6] == "t"
                    entry["tls_version"] = row[7] or None
                    entry["lag_bytes"] = int(row[8])
                    if not entry["tls"]:
                        warnings.append(f"{record.name} is connected without TLS.")
                elif entry["retained_bytes"] and entry["retained_bytes"] > RETAINED_WARNING_BYTES:
                    warnings.append(
                        f"{record.name} is not connected and its slot holds "
                        f"{entry['retained_bytes']} bytes of the log. Bring it back, or remove "
                        "it: noust db replica remove"
                    )
                if entry["wal_status"] in ("unreserved", "lost"):
                    warnings.append(
                        f"The log {record.name} needs is {entry['wal_status']} on this server."
                    )
            out.append(entry)
        return out

    def _replica_view(self, following: list[ReplicaRecord], warnings: list[str]) -> dict[str, Any]:
        """
        Read this server's connection to its primary from the engine.

        Args:
            following: This server's record of the primary it follows.
            warnings: Where to add what the operator should know.

        Returns:
            What the receiver and the replay say.
        """
        line = self._sql(
            "SELECT coalesce((SELECT status FROM pg_stat_wal_receiver LIMIT 1), 'not connected'), "
            "coalesce((SELECT sender_host || ':' || sender_port FROM pg_stat_wal_receiver LIMIT 1), ''), "
            "coalesce(extract(epoch FROM now() - pg_last_xact_replay_timestamp())::bigint, -1), "
            "coalesce(pg_wal_lsn_diff(pg_last_wal_receive_lsn(), pg_last_wal_replay_lsn()), 0)::bigint, "
            "current_setting('primary_conninfo');"
        ).splitlines()[-1]
        fields = line.split("|", 4)
        conninfo = replication_files.conninfo_options(fields[4]) if len(fields) == 5 else {}
        record = following[0] if following else None
        view = {
            "name": record.name if record else None,
            "state": fields[0],
            "primary": fields[1] or (f"{record.host}:{record.port}" if record else None),
            "replay_delay_seconds": int(fields[2]) if fields[2].lstrip("-").isdigit() else None,
            "unreplayed_bytes": int(fields[3]) if fields[3].lstrip("-").isdigit() else None,
            "sslmode": conninfo.get("sslmode"),
            "saved_as": record.saved_as if record else None,
        }
        if record is None:
            warnings.append(
                "This server is in recovery but Noust did not set it up as a replica "
                "(a point-in-time recovery under way, or a replica made by hand)."
            )
        if fields[0] != "streaming":
            warnings.append(f"The receiver is {fields[0]!r}, not streaming.")
        if conninfo.get("sslmode") not in (None, "verify-full", "verify-ca"):
            warnings.append(
                f"The connection to the primary uses sslmode={conninfo.get('sslmode')}."
            )
        return view

    # ==================== Promoting and removing ====================

    def promote(self, *, confirmed: bool) -> dict[str, Any]:
        """
        Make this replica a primary: it stops following and accepts writes.

        Args:
            confirmed: The operator accepted it (the guard is here, not in the caller that asks).

        Returns:
            ``promoted``, ``name`` and ``note`` (the slot the old primary still holds).

        Raises:
            ValidationError: When this server follows nothing Noust set up, or it was not
                confirmed.
            DatabaseBackupError: When the engine does not become a primary.
        """
        if is_rehearsal():
            return {"promoted": False, "rehearsal": True}
        with cluster_lock("replica promotion"):
            facts = self.facts()
            records = self.records.records(REPLICA)
            if not facts["in_recovery"]:
                raise ValidationError(
                    "This server is not a replica",
                    details="PostgreSQL here already accepts writes.",
                    field="plan",
                )
            if not records:
                raise ValidationError(
                    "Noust did not set this server up as a replica",
                    details="Promote it with pg_promote() yourself; Noust only promotes what it "
                    "joined.",
                    field="plan",
                )
            record = records[0]
            if not confirmed:
                raise ValidationError(
                    "Promoting stops this server following its primary",
                    details=f"It becomes a primary of its own and accepts writes. "
                    f"{record.host}:{record.port} keeps the slot {record.slot} and the log it "
                    "holds until you remove the replica there (noust db replica remove "
                    f"{record.name}); until then its disk fills. Confirm to go ahead.",
                    field="confirmed",
                )
            data = Path(facts["data_directory"])
            self._sql("SELECT pg_promote(true, 60);", timeout=120)
            self.swap.wait_until(
                data, "SELECT pg_is_in_recovery();", "f", timeout=90, what="the promotion"
            )
            for name in ("primary_conninfo", "primary_slot_name"):
                self._sql(f"ALTER SYSTEM RESET {name};")
            self._sql("SELECT pg_reload_conf();")
            self._forget_credentials(record.name)
            self.records.delete(REPLICA, record.name)
            self._audit("db.replica.promote", name=record.name, was=f"{record.host}:{record.port}")
            self.manager.logger.success("This server accepts writes now")
            return {
                "promoted": True,
                "name": record.name,
                "note": f"{record.host}:{record.port} still holds the slot {record.slot}: remove "
                f"the replica there (noust db replica remove {record.name}) or its disk fills.",
            }

    def remove(self, name: str, *, confirmed: bool, restore_listen: bool = False) -> dict[str, Any]:
        """
        Take a replica away: on a primary its account, slot and line; on a replica, its record.

        Args:
            name: The replica's name.
            confirmed: The operator accepted it (the guard is here, not in the caller that asks).
            restore_listen: Put ``listen_addresses`` back as it was before the replica was
                prepared (restarts PostgreSQL), when no other replica needs it.

        Returns:
            ``removed`` and what was done.

        Raises:
            ValidationError: When it was not confirmed, or this server still follows that
                primary.
            DatabaseError: When a step fails; the record stays, so running it again finishes it.
        """
        name = check_replica_name(name)
        if is_rehearsal():
            return {"removed": False, "rehearsal": True}
        with cluster_lock("replica removal"):
            followed = self.records.get(REPLICA, name)
            if followed is not None:
                return self._forget_followed(followed, confirmed)
            return self._remove_prepared(name, confirmed, restore_listen)

    def _forget_followed(self, record: ReplicaRecord, confirmed: bool) -> dict[str, Any]:
        """
        Forget the primary a server followed, once it no longer follows it.

        Args:
            record: The record.
            confirmed: The operator accepted it.

        Returns:
            What :meth:`remove` returns.

        Raises:
            ValidationError: When the server still follows its primary.
        """
        facts = self.facts()
        if facts["in_recovery"]:
            raise ValidationError(
                "This server still follows that primary",
                details="Promote it first (noust db replica promote), or the replica would keep "
                "streaming with credentials that are no longer recorded.",
                field="name",
            )
        if not confirmed:
            raise ValidationError("Confirm to forget the primary", field="confirmed")
        self._forget_credentials(record.name)
        self.records.delete(REPLICA, record.name)
        return {"removed": True, "side": REPLICA, "steps": ["forgot the record and the passfile"]}

    def _remove_prepared(self, name: str, confirmed: bool, restore_listen: bool) -> dict[str, Any]:
        """
        Remove what preparing a replica made on this primary.

        Args:
            name: The replica's name.
            confirmed: The operator accepted it.
            restore_listen: Put ``listen_addresses`` back, when no other replica needs it.

        Returns:
            What :meth:`remove` returns.

        Raises:
            ValidationError: When it was not confirmed.
        """
        account, slot = role_for(name), slot_for(name)
        record = self.records.get(PRIMARY, name)
        facts = self.facts()
        present = (
            record is not None
            or self._sql(
                f"SELECT (SELECT count(*) FROM pg_roles WHERE rolname = {_literal(account)}) "  # noqa: S608 - names are checked by check_replica_name and quoted as literals
                f"+ (SELECT count(*) FROM pg_replication_slots WHERE slot_name = {_literal(slot)});"
            )
            != "0"
        )
        if not present:
            return {"removed": False, "steps": [], "note": f"No replica named {name} here."}
        if not confirmed:
            raise ValidationError(
                f"Removing {name} cuts its replica off",
                details=f"The account {account}, the slot {slot} and the pg_hba.conf line go; "
                "the replica stops streaming and cannot resume. Confirm to go ahead.",
                field="confirmed",
            )
        steps: list[str] = []
        self.manager.edit_hba(lambda text: pg_hba.without_replication_block(text, account))
        steps.append("removed the pg_hba.conf line")
        self._drop_slot(slot)
        steps.append(f"dropped the slot {slot}")
        self._drop_account(account)
        steps.append(f"dropped the account {account}")
        if record is not None and self._close_firewall(record):
            steps.append("closed the firewall rule")
        self.secrets.delete(self._secret(name))
        self._discard(self.state_directory(name))
        steps.append("forgot the password")
        restart = False
        if record is not None:
            self.records.delete(PRIMARY, name)
            others = self.records.records(PRIMARY)
            if record.listen_was is not None and not others:
                if restore_listen:
                    self._restore_listen(record.listen_was, facts["data_directory"])
                    restart = True
                    steps.append(f"put listen_addresses back to {record.listen_was!r}")
                else:
                    steps.append(
                        f"left listen_addresses as it is (it was {record.listen_was!r}; "
                        "--restore-listen puts it back and restarts PostgreSQL)"
                    )
        self._audit("db.replica.remove", name=name, listen_restored=restart)
        self.manager.logger.success(f"Replica {name} removed")
        return {"removed": True, "side": PRIMARY, "steps": steps}
