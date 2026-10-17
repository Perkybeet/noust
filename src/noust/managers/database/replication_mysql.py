# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Read replicas of the host's MySQL or MariaDB, over verified TLS, with an account of their own
(checklist C-14).

The same two commands as PostgreSQL's (:mod:`~noust.managers.database.replication`) and the same
promises, over the engine's own mechanism: asynchronous replication with GTIDs. On the primary,
``prepare`` gives the server what replication needs (binary logging, GTIDs, an address to listen
on, in a file of Noust's that sorts after the package's), makes an account that can replicate and
read for the first copy (``REQUIRE SSL``, from one address), and prints a join code. On the replica,
``join`` takes a consistent dump of the user databases from the primary over TLS, loads it, points
the server at the primary with ``MASTER_SSL_VERIFY_SERVER_CERT`` and starts replicating.

**What is copied, and what is not.** A dump is logical: the user databases come across, with their
routines, triggers and events, at one GTID position; the replica's own copy of them is dumped first
with the ordinary backup (``noust db backup``), so the previous contents can be put back, and is
dropped only when the primary's dump is in hand. The accounts of the primary are not copied (they
live in the ``mysql`` schema, which a dump of the user databases does not carry); those created on
the primary after the join replicate like any statement.

**The account reads.** A logical copy needs ``SELECT`` on everything, so the replication account is
not one that can only replicate, as PostgreSQL's is. It connects from one address, over TLS, with a
generated password, and ``remove`` drops it; the replica holds all the data anyway.

**TLS is not optional.** Before anything is made the primary runs the handshake the replica will
run, for the name the replica uses; the account requires TLS, so the engine itself refuses it
without; the replica verifies the certificate and the name.

**Failing is safe.** Nothing on the replica changes until the primary's dump is in hand; after
that, a load or a connection that fails stops replicating, drops what was loaded, loads the
replica's own dumps back and takes Noust's settings off.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from noust.core import paths
from noust.core.exceptions import (
    DatabaseBackupError,
    DatabaseEngineError,
    NoustError,
    ValidationError,
)
from noust.core.fs import is_rehearsal
from noust.managers.database.base import restore_timeout
from noust.managers.database.cluster_swap import cluster_lock
from noust.managers.database.mysql import MySQLManager
from noust.managers.database.mysql_include import include_directory, parse_include, render_include
from noust.managers.database.replication_base import (
    CONNECT_TIMEOUT,
    LOOPBACK,
    SYSTEM_ROOTS,
    JoinPlan,
    PreparePlan,
    ReplicationBase,
    clip,
    literal,
)
from noust.managers.database.replication_mysql_text import (
    account_host,
    change_source,
    dump_options,
    forget_binary_log,
    parse_version,
    parse_vertical,
    pick,
    start_statement,
    status_statement,
    stop_statement,
)
from noust.managers.database.replication_records import PRIMARY, REPLICA, ReplicaRecord
from noust.managers.database.replication_token import ReplicaToken, encode, parse
from noust.validators.replica import (
    check_replica_host,
    check_replica_name,
    replica_address,
    role_for,
)

__all__ = ["ENGINE", "LOCK_KEY", "MySqlReplication"]

#: The engine's key.
ENGINE = "mysql"

#: The one-at-a-time lock of this area (a replica joining replaces what the engine holds).
LOCK_KEY = "database-mysql-replica"

#: The files of Noust's settings, in the engine's include directory.
PRIMARY_INCLUDE = "99-noust-replication.cnf"
REPLICA_INCLUDE = "99-noust-replica.cnf"

#: Where a replica keeps the authority it verifies its primary with: a directory the engine's
#: account can enter (``/etc/noust`` is root's).
AUTHORITY_ROOT = Path("/etc/ssl/noust-db/mysql-replica")

#: The oldest servers replication is set up on: MariaDB 10.4 (GTIDs, ``REQUIRE SSL``, verified
#: TLS) and MySQL 8.0.23 (``CHANGE REPLICATION SOURCE TO``).
MIN_MARIADB = (10, 4, 0)
MIN_MYSQL = (8, 0, 23)

#: The longest password MySQL takes for a replication user.
MAX_PASSWORD = 32

#: Seconds the engine is given to answer after a restart, and a replica to start replicating.
READY_TIMEOUT = 180
STREAM_TIMEOUT = 120

#: Seconds the dump and the load may take.
COPY_TIMEOUT = 6 * 3600

#: Seconds a stopping replica is given to apply what it already read.
DRAIN_TIMEOUT = 60

#: Seconds between two looks at the engine while waiting for it.
POLL_SECONDS = 2.0

#: Seconds the cutover of a join may spend on one statement.
STATEMENT_TIMEOUT = 600

#: What the account is granted: the replication itself, and what a consistent dump needs.
GRANTS = (
    "REPLICATION SLAVE, REPLICATION CLIENT, RELOAD, SELECT, SHOW VIEW, TRIGGER, EVENT, LOCK TABLES"
)

#: A log that is more than this old is what ``expire_logs_days`` lets a replica lag behind.
EXPIRE_DAYS = "7"


def derive_server_id(name: str, primary: int) -> int:
    """
    Choose a replica's ``server_id``: stable for its name, never its primary's.

    Args:
        name: The replica's name.
        primary: The primary's ``server_id``.

    Returns:
        A number from 2 up, different from ``primary``.
    """
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    value = int.from_bytes(digest[:4], "big") % (2**31 - 3) + 2
    return value + 1 if value == primary else value


class MySqlReplication(ReplicationBase):
    """
    Prepare, join, watch, promote and remove replicas of the host's MySQL or MariaDB.
    """

    ENGINE = ENGINE
    STARTTLS = "mysql"

    manager: MySQLManager

    def __init__(
        self,
        manager: MySQLManager | None = None,
        *,
        scratch_root: Path | None = None,
        authority_root: Path | None = None,
        **collaborators: Any,
    ) -> None:
        """
        Args:
            manager: The MySQL or MariaDB manager. A default one when None.
            scratch_root: Where the dump of the primary is held while it is loaded. A private
                directory of the backups directory when None.
            authority_root: Where the authority the replica verifies its primary with is kept.
                :data:`AUTHORITY_ROOT` when None.
            **collaborators: The rest, see :class:`ReplicationBase`.
        """
        super().__init__(manager or MySQLManager(), **collaborators)
        self.DISPLAY = "MariaDB" if self.manager.is_mariadb else "MySQL"
        self._scratch_root = scratch_root
        self._authority_root = authority_root

    @property
    def mariadb(self) -> bool:
        """Whether the server is MariaDB."""
        return bool(self.manager.is_mariadb)

    @property
    def flavour(self) -> str:
        """``mariadb`` or ``mysql``."""
        return "mariadb" if self.mariadb else "mysql"

    # ==================== Asking the engine ====================

    def _sql(self, sql: str, *, secrets: tuple[str, ...] = (), timeout: int = 120) -> str:
        """
        Run SQL on the server as its administrator.

        Args:
            sql: The statement, built from this module's constants and checked names.
            secrets: Values the statement carries that must not be logged.
            timeout: Deadline in seconds.

        Returns:
            The client's output, stripped.

        Raises:
            DatabaseEngineError: When the server does not answer or refuses, with its words.
        """
        ok, output = self.manager._execute_sql(sql, secrets=secrets, timeout=timeout)
        if not ok:
            raise DatabaseEngineError(
                f"{self.DISPLAY} refused a statement of the replica setup",
                details=str(output).strip()[-2000:] or "The client returned no output.",
            )
        return str(output).strip()

    def _vertical(self, sql: str) -> dict[str, str]:
        """
        Run a statement and read its first row by column name.

        Args:
            sql: The statement.

        Returns:
            The row; empty when there is none.

        Raises:
            DatabaseEngineError: When the server refuses.
        """
        with self.manager._credentials() as credentials:
            result = self.manager._exec(["mysql", *credentials, "-E"], input=sql, timeout=60)
        if not result.success:
            raise DatabaseEngineError(
                f"{self.DISPLAY} refused a statement of the replica setup",
                details=(result.stderr or result.stdout).strip()[-2000:] or "No output.",
            )
        return parse_vertical(result.stdout)

    def _replica_row(self) -> dict[str, str]:
        """
        Returns:
            What the server says about following a primary; empty when it follows none.
        """
        return self._vertical(f"{status_statement(self.mariadb)};")

    def _require_engine(self) -> None:
        """
        Check there is a MySQL or MariaDB of this server to work on.

        Raises:
            DatabaseEngineError: When it is not installed, is an instance, or is not running.
        """
        if not self.manager.is_installed():
            raise DatabaseEngineError(
                "MySQL or MariaDB is not installed on this server",
                details="Install it first: noust db install mariadb",
            )
        if self.manager.instance is not None:
            raise DatabaseEngineError(
                "Replicas are for the MySQL or MariaDB of this server, not an instance",
                details="An instance keeps its data in a Docker volume; a replica of it is not "
                "offered yet.",
            )
        if not self.manager.is_running():
            raise DatabaseEngineError(
                f"{self.DISPLAY} is not running",
                details=f"Start it first: noust db start {self.ENGINE}",
            )

    def facts(self) -> dict[str, Any]:
        """
        Read what the server says about itself that replication depends on.

        Returns:
            ``version`` (a tuple), ``number`` (``major * 100 + minor``), ``server_id``,
            ``log_bin``, ``port``, ``bind_address``, ``datadir``, ``read_only``, ``gtid_mode``
            and ``enforce`` (MySQL), and ``size`` in bytes.

        Raises:
            DatabaseEngineError: When the server is not there, does not answer or answers
                something unexpected.
        """
        self._require_engine()
        columns = [
            "VERSION()",
            "@@server_id",
            "@@log_bin",
            "@@port",
            "@@bind_address",
            "@@datadir",
            "@@read_only",
            *(["'-'", "'-'"] if self.mariadb else ["@@gtid_mode", "@@enforce_gtid_consistency"]),
        ]
        output = self._sql(
            f"SELECT {', '.join(columns)}, "  # noqa: S608 - the columns are this module's constants
            "(SELECT coalesce(sum(data_length + index_length), 0) FROM information_schema.tables "
            "WHERE table_schema NOT IN ('mysql', 'information_schema', 'performance_schema', "
            "'sys'));"
        )
        fields = output.splitlines()[-1].split("\t") if output else []
        version = parse_version(fields[0]) if fields else None
        numeric = (1, 2, 3, 6, 9)
        if len(fields) != 10 or version is None or not all(fields[at].isdigit() for at in numeric):
            raise DatabaseEngineError(
                f"{self.DISPLAY} answered something the replica setup cannot read",
                details=output[-500:] or "No answer.",
            )
        return {
            "version": version,
            "number": version[0] * 100 + version[1],
            "server_id": int(fields[1]),
            "log_bin": fields[2] == "1",
            "port": int(fields[3]),
            "bind_address": fields[4],
            "datadir": fields[5],
            "read_only": fields[6] == "1",
            "gtid_mode": fields[7],
            "enforce": fields[8],
            "size": int(fields[9]),
        }

    def _wait_ready(self) -> None:
        """
        Wait for the server to answer after a restart.

        Raises:
            DatabaseEngineError: When it does not, with where to look.
        """
        deadline = self._clock().timestamp() + READY_TIMEOUT
        while True:
            ok, _output = self.manager._execute_sql("SELECT 1;", timeout=15)
            if ok:
                return
            if self._clock().timestamp() >= deadline:
                raise DatabaseEngineError(
                    f"{self.DISPLAY} did not answer after the restart",
                    details=f"Inspect it with: journalctl -u {self.manager.service_unit()} -n 50",
                )
            self._sleep(POLL_SECONDS)

    def _restart(self) -> None:
        """
        Restart the server and wait for it.

        Raises:
            DatabaseEngineError: When it does not come back.
        """
        self.manager.restart()
        self._wait_ready()

    # ==================== Preparing the primary ====================

    def _include(self, leaf: str) -> Path:
        """
        Name one of Noust's settings files.

        Args:
            leaf: ``PRIMARY_INCLUDE`` or ``REPLICA_INCLUDE``.

        Returns:
            Its path in the server's include directory.
        """
        return include_directory(self.mariadb) / leaf

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
            listen: The address to listen on when the server listens on the loopback only:
                ``*`` or an address of this server.
            ca_pem: The authority the replica will trust, PEM; the system's when empty.
            open_firewall: Allow the engine's port from ``source`` in the firewall.

        Returns:
            The plan; ``problems`` says what stops it.

        Raises:
            ValidationError: When a name or an address is not acceptable.
            DatabaseEngineError: When the server is not there to ask.
        """
        name = check_replica_name(name)
        try:
            network = replica_address(source)
            account_host(network)
        except ValueError as exc:
            raise ValidationError(str(exc), field="source") from exc
        host = check_replica_host(host)
        facts = self.facts()
        account = role_for(name)
        plan = PreparePlan(
            name=name,
            account=account,
            slot="",
            host=host,
            port=facts["port"],
            source=network,
            firewall=open_firewall,
        )
        problems = plan.problems
        floor = MIN_MARIADB if self.mariadb else MIN_MYSQL
        if facts["version"] < floor:
            problems.append(
                f"{self.DISPLAY} {'.'.join(map(str, facts['version']))} is too old: replicas "
                f"need {'.'.join(map(str, floor))} or newer."
            )
        if self._replica_row():
            problems.append(
                "This server is itself a replica. A replica of a replica is not offered: "
                "prepare the replica on the primary."
            )
        if not facts["log_bin"] and not self.mariadb:
            problems.append(
                "Binary logging is turned off (skip-log-bin or disable_log_bin), and a replica "
                "follows the binary log. Turn it on in the server's configuration first."
            )
        if self._row(PRIMARY, name) is not None:
            problems.append(f"A replica named {name} is already prepared here.")
        elif (
            self._sql(
                f"SELECT count(*) FROM mysql.user WHERE user = {literal(account)};"  # noqa: S608
            )
            != "0"
        ):
            problems.append(
                f"The account {account} exists already, and Noust has no record of making it. "
                "Pick another name, or drop it."
            )
        self._plan_tls(plan, ca_pem)
        current = facts["bind_address"]
        entries = [item.strip() for item in current.split(",")]
        reaches = "*" in entries or any(item not in (*LOOPBACK, "") for item in entries)
        self._plan_listen(plan, current, listen, reaches=reaches)
        if plan.listen and self.mariadb and "," in plan.listen:
            problems.append(
                "MariaDB listens on one address. Use --listen '*', and let the firewall and the "
                "account (one address, TLS required) decide who gets in."
            )
        plan.settings = self._primary_settings(facts, plan.listen)
        plan.restart = bool(plan.settings)
        if plan.settings and not plan.listen:
            plan.warnings.append(
                f"{self.DISPLAY} restarts once (a few seconds) to turn on what replication needs: "
                + ", ".join(plan.settings)
                + "."
            )
        plan.steps = []
        if plan.settings:
            plan.steps.append(
                f"Write {PRIMARY_INCLUDE} ({', '.join(plan.settings)}) and restart {self.DISPLAY}"
            )
        plan.steps += [
            f"Create the account {account}@{account_host(network)} (REQUIRE SSL, a generated "
            "password): replicate, and read for the first copy",
        ]
        if open_firewall:
            plan.steps.append(f"Allow port {plan.port}/tcp from {network} in the firewall")
        else:
            plan.warnings.append(
                f"The firewall is not changed: allow port {plan.port}/tcp from {network} "
                "yourself, or pass --open-firewall."
            )
        plan.steps.append("Print the join code for the replica")
        return plan

    def _primary_settings(self, facts: dict[str, Any], listen: str | None) -> dict[str, str]:
        """
        Work out the settings the server's configuration is given.

        Settings the server already has are not written; those a first replica already got are
        kept so that a second does not take them away.

        Args:
            facts: What the server says about itself.
            listen: The address it must listen on, when that changes.

        Returns:
            The whole content of Noust's file for a primary, or nothing when the server needs
            nothing.
        """
        needed: dict[str, str] = {}
        if self.mariadb:
            if not facts["log_bin"]:
                needed["log_bin"] = "noust-bin"
                needed["expire_logs_days"] = EXPIRE_DAYS
        else:
            if facts["gtid_mode"] != "ON":
                needed["gtid_mode"] = "ON"
            if facts["enforce"] != "ON":
                needed["enforce_gtid_consistency"] = "ON"
        if facts["server_id"] == 0:
            needed["server_id"] = "1"
        if listen:
            # MariaDB has no "*": every IPv4 address is spelled out.
            all_addresses = "0.0.0.0"  # noqa: S104 - what the operator asked for with '*'
            needed["bind-address"] = all_addresses if self.mariadb and listen == "*" else listen
        if not needed:
            return {}
        include = self._include(PRIMARY_INCLUDE)
        existing = parse_include(include.read_text(encoding="utf-8")) if include.is_file() else {}
        return {**existing, **needed}

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
            listen: The address to listen on when the server listens on the loopback only.
            ca_pem: The authority the replica will trust, PEM; the system's when empty.
            open_firewall: Allow the engine's port from ``source`` in the firewall.
            confirmed: The operator accepted the server's restart, if one is needed (the guard is
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
        with cluster_lock("replica preparation", LOCK_KEY):
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
                    f"Preparing this replica restarts {self.DISPLAY}",
                    details=f"{', '.join(plan.settings)} only take effect with a restart. "
                    "Confirm to go ahead.",
                    field="confirmed",
                )
            return self._prepare(plan, self.facts(), ca_pem)

    def _prepare(self, plan: PreparePlan, facts: dict[str, Any], ca_pem: str) -> dict[str, Any]:
        """
        Do what a plan says, undoing it in reverse when a step fails.

        Args:
            plan: The plan, with no problems.
            facts: What the server says about itself.
            ca_pem: The authority the replica will trust, PEM; empty for the system's.

        Returns:
            What :meth:`prepare` returns.

        Raises:
            DatabaseError: When a step fails; what had been made is undone first.
        """
        undo: list[tuple[str, Callable[[], object]]] = []
        # 32 characters is the longest password MySQL accepts for a replication user (the error
        # "exceeds the maximum length of 32 characters" is what a longer one earns on 8.4).
        password = self.manager.generate_password(MAX_PASSWORD)
        host_part = account_host(plan.source)
        account = f"{literal(plan.account)}@{literal(host_part)}"
        record = ReplicaRecord(
            side=PRIMARY,
            name=plan.name,
            account=plan.account,
            slot="",
            host=plan.host,
            port=plan.port,
            source=plan.source,
            engine=ENGINE,
        )
        try:
            if plan.settings:
                self._write_primary_include(plan, undo)
                # What the server listened on before Noust's file: the mark that this replica
                # is the reason for the file, whether or not the address changed.
                record.listen_was = facts["bind_address"]
            self._sql(
                f"CREATE USER {account} IDENTIFIED BY {literal(password)} REQUIRE SSL; "
                f"GRANT {GRANTS} ON *.* TO {account};",
                secrets=(password,),
            )
            undo.append(("drop the account", lambda: self._sql(f"DROP USER IF EXISTS {account};")))
            if plan.firewall:
                record.firewall = self._open_firewall(plan)
                undo.append(("close the firewall", lambda: self._close_firewall(record)))
            self.secrets.write(self._secret(plan.name), password)
            undo.append(
                ("forget the password", lambda: self.secrets.delete(self._secret(plan.name)))
            )
            self._keep_authority(plan.name, ca_pem)
            final = self.facts()
            token = ReplicaToken(
                name=plan.name,
                host=plan.host,
                port=final["port"],
                role=plan.account,
                password=password,
                slot="",
                major=final["number"],
                size=final["size"],
                requires={"server_id": final["server_id"]},
                ca=ca_pem,
                engine=ENGINE,
                flavour=self.flavour,
            )
            stored = self.records.save(record)
        except (NoustError, OSError) as exc:
            self._undo(undo)
            self._audit("db.replica.prepare", "failed", name=plan.name, error=str(exc)[:300])
            raise
        self._audit(
            "db.replica.prepare",
            name=plan.name,
            account=plan.account,
            source=plan.source,
            firewall=bool(plan.firewall),
            listen=plan.listen,
            settings=sorted(plan.settings),
        )
        self.manager.logger.success(f"Replica {plan.name} prepared")
        return {"token": encode(token), "plan": plan.to_dict(), "record": stored.to_dict()}

    def _write_primary_include(
        self, plan: PreparePlan, undo: list[tuple[str, Callable[[], object]]]
    ) -> None:
        """
        Give the server the settings replication needs, and restart it.

        Args:
            plan: The plan; its ``settings`` are written.
            undo: Where to add the undoing of this step.

        Raises:
            DatabaseEngineError: When the server does not come back; the file is put back first.
        """
        include = self._include(PRIMARY_INCLUDE)
        before = include.read_text(encoding="utf-8") if include.is_file() else None

        def put_back() -> None:
            if before is None:
                self.fs.remove(include)
            else:
                self.fs.write_text(include, before, mode=0o644)
            self._restart()

        self.fs.write_text(
            include,
            render_include("what a primary needs for its replicas", plan.settings),
            mode=0o644,
        )
        try:
            self._restart()
        except DatabaseEngineError:
            put_back()
            raise
        undo.append((f"put {include.name} back", put_back))

    def token(self, name: str) -> str:
        """
        Give a prepared replica's join code again.

        Args:
            name: The replica's name.

        Returns:
            The code (a secret).

        Raises:
            ValidationError: When no replica of that name was prepared here.
            DatabaseEngineError: When the server does not answer.
        """
        name = check_replica_name(name)
        record = self._row(PRIMARY, name)
        password = self.secrets.read(self._secret(name))
        if record is None or not password:
            raise ValidationError(
                f"No replica named {name} is prepared on this server",
                details="Prepare it first: noust db replica prepare",
                field="name",
            )
        facts = self.facts()
        return encode(
            ReplicaToken(
                name=name,
                host=record.host,
                port=facts["port"],
                role=record.account,
                password=password,
                slot="",
                major=facts["number"],
                size=facts["size"],
                requires={"server_id": facts["server_id"]},
                ca=self._kept_authority(name),
                engine=ENGINE,
                flavour=self.flavour,
            )
        )

    # ==================== Joining a primary ====================

    def _user_databases(self) -> list[str]:
        """
        Returns:
            The databases of this server that do not belong to the server itself.
        """
        names = self._sql("SELECT schema_name FROM information_schema.schemata ORDER BY 1;")
        return [
            line
            for line in names.splitlines()
            if line and line not in self.manager.SYSTEM_DATABASES
        ]

    def authority_directory(self, name: str) -> Path:
        """
        Name where a replica keeps the authority it verifies its primary with.

        Args:
            name: The replica's name.

        Returns:
            ``<authority root>/<name>``.
        """
        return (self._authority_root or AUTHORITY_ROOT) / check_replica_name(name)

    def _authority_path(self, token: ReplicaToken) -> Path | None:
        """
        Find the file of authorities a replica verifies its primary with.

        Args:
            token: The join code.

        Returns:
            Where the code's own authority is written, or the system's list; None when the code
            gives none and the system has no list.
        """
        if token.ca:
            return self.authority_directory(token.name) / "ca.pem"
        for candidate in SYSTEM_ROOTS:
            if Path(candidate).is_file():
                return Path(candidate)
        return None

    def plan_join(self, code: str) -> JoinPlan:
        """
        Work out what joining a primary would do to this server, touching nothing.

        Args:
            code: The join code the primary printed.

        Returns:
            The plan; ``problems`` says what stops it.

        Raises:
            ValidationError: When the code is not one Noust made.
            DatabaseEngineError: When the server is not there to ask.
        """
        token = parse(code)
        if token.engine != ENGINE:
            raise ValidationError(
                "This join code is for PostgreSQL",
                details="Join with --engine postgresql, or ask the primary for a code of this "
                "engine.",
                field="token",
            )
        facts = self.facts()
        datadir = Path(facts["datadir"])
        plan = JoinPlan(
            token=token,
            data_directory=str(datadir),
            saved_as=str(paths.backup_dir() / "databases"),
            needed_bytes=int(token.size * 2),
        )
        if self.flavour != token.flavour:
            plan.problems.append(
                f"The primary runs {token.flavour} and this server runs {self.flavour}. "
                "A replica follows the flavour of its primary: install the same one here."
            )
        elif facts["number"] < token.major:
            plan.problems.append(
                f"The primary runs version {token.major // 100}.{token.major % 100} and this "
                f"server {facts['version'][0]}.{facts['version'][1]}. A replica cannot be older "
                "than its primary."
            )
        floor = MIN_MARIADB if self.mariadb else MIN_MYSQL
        if facts["version"] < floor:
            plan.problems.append(
                f"{self.DISPLAY} {'.'.join(map(str, facts['version']))} is too old: replicas "
                f"need {'.'.join(map(str, floor))} or newer."
            )
        if self._replica_row():
            plan.problems.append(
                "This server already follows a primary. Promote it (noust db replica promote) "
                "or remove it from there before following another."
            )
        try:
            plan.free_bytes = self._disk_free(datadir)
        except OSError:
            plan.free_bytes = 0
        if plan.free_bytes < plan.needed_bytes:
            plan.problems.append(
                f"Loading the copy needs about {plan.needed_bytes} bytes and {datadir} has "
                f"{plan.free_bytes}."
            )
        plan.databases = self._user_databases()
        if plan.databases:
            plan.warnings.append(
                "These databases of this server are replaced by the primary's (each is dumped "
                f"first, with 'noust db backup', and kept): {', '.join(plan.databases)}"
            )
        plan.capacity = {"server_id": derive_server_id(token.name, token.requires["server_id"])}
        if self._authority_path(token) is None:
            plan.problems.append(
                "The join code names no certificate authority and this server has no list of "
                "trusted ones: the primary's certificate cannot be checked."
            )
        plan.warnings.append(
            "The accounts of the primary are not copied; create the ones this server needs "
            "(accounts created on the primary from now on replicate)."
        )
        return plan

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

        Args:
            code: The join code.
            confirmed: The operator accepted that this server's databases are replaced (the guard
                is here, not in the caller that asks).
            discard_previous: Delete the dumps made of this server's own databases once the
                replica streams.
            timeout: Seconds the dump and the load may take.

        Returns:
            ``name``, ``primary``, ``saved_as`` (the dumps of this server's own databases, None
            when none were made or they were discarded), ``state`` and ``seconds``.

        Raises:
            ValidationError: When the code is not valid, something stops it, or it was not
                confirmed.
            DatabaseBackupError: When the copy or the start fails; this server holds what it
                held before.
        """
        if is_rehearsal():
            return {"name": None, "rehearsal": True}
        with cluster_lock("replica join", LOCK_KEY):
            plan = self.plan_join(code)
            if plan.problems:
                raise ValidationError(
                    "This server cannot join that primary yet",
                    details="\n".join(f"- {problem}" for problem in plan.problems),
                    field="plan",
                )
            if not confirmed:
                raise ValidationError(
                    f"Joining a primary replaces this server's {self.DISPLAY} databases",
                    details=f"{', '.join(plan.databases) or 'Its databases'} are dumped and then "
                    "replaced by the primary's. Confirm to go ahead.",
                    field="confirmed",
                )
            return self._join(plan, discard_previous=discard_previous, timeout=timeout)

    def _remote_client(self, token: ReplicaToken, authority: Path, program: str) -> list[str]:
        """
        Build a client invocation that connects to the primary over verified TLS.

        Args:
            token: The join code.
            authority: The file of authorities.
            program: ``mysql`` or ``mysqldump``.

        Returns:
            The program and the options that name the primary, the account and the TLS.
        """
        return [
            program,
            "-h",
            token.host,
            "-P",
            str(token.port),
            "-u",
            token.role,
            *dump_options(self.mariadb, str(authority)),
        ]

    def _remote_env(self, token: ReplicaToken) -> dict[str, str]:
        """
        Args:
            token: The join code.

        Returns:
            The environment that gives a client the password and a bounded wait.
        """
        return {"MYSQL_PWD": token.password, "MYSQL_CONNECT_TIMEOUT": str(CONNECT_TIMEOUT)}

    def _primary_databases(self, token: ReplicaToken, authority: Path) -> list[str]:
        """
        List the databases to copy, asking the primary over TLS.

        Args:
            token: The join code.
            authority: The file of authorities.

        Returns:
            The user databases of the primary.

        Raises:
            DatabaseBackupError: When the primary cannot be reached, trusted or signed in to, with
                the client's own words.
        """
        result = self.manager._exec(
            [*self._remote_client(token, authority, "mysql"), "-N", "-B", "-e", "SHOW DATABASES"],
            env=self._remote_env(token),
            secrets=(token.password,),
            timeout=60,
        )
        if not result.success:
            raise DatabaseBackupError(
                "The primary could not be reached",
                details=clip(result.stderr or result.stdout) or "No output.",
            )
        return [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip() and line.strip() not in self.manager.SYSTEM_DATABASES
        ]

    def _dump_primary(
        self, token: ReplicaToken, authority: Path, databases: list[str], target: Path, timeout: int
    ) -> None:
        """
        Take a consistent dump of the primary's user databases, over TLS, to a private file.

        Args:
            token: The join code.
            authority: The file of authorities.
            databases: The databases to dump.
            target: The file to write.
            timeout: Seconds the dump may take.

        Raises:
            DatabaseBackupError: When the dump fails, with the client's own words.
        """
        if self.mariadb:
            position = ["--master-data=1", "--gtid"]
        else:
            position = ["--no-tablespaces", "--set-gtid-purged=ON"]
        argv = [
            *self._remote_client(token, authority, "mysqldump"),
            "--single-transaction",
            "--routines",
            "--triggers",
            "--events",
            "--add-drop-database",
            *position,
            "--databases",
            *databases,
        ]
        result = self.manager.runner.capture_to_file(
            argv,
            target,
            env=self._remote_env(token),
            secrets=(token.password,),
            timeout=timeout,
        )
        if not result.success:
            raise DatabaseBackupError(
                "The primary's databases could not be dumped",
                details=clip(result.stderr or result.stdout) or "mysqldump returned no output.",
            )

    def _load_file(self, path: Path) -> None:
        """
        Load a dump into this server through the client's standard input.

        Args:
            path: The dump.

        Raises:
            DatabaseBackupError: When the client fails, with its own words.
        """
        with self.manager._credentials() as credentials:
            result = self.manager._exec(
                [*self.manager._client_argv(credentials), "--binary-mode"],
                stdin_path=path,
                timeout=restore_timeout(path),
            )
        if not result.success:
            raise DatabaseBackupError(
                "The primary's databases could not be loaded",
                details=clip(result.stderr or result.stdout) or "No output.",
            )

    def _scratch(self, name: str) -> Path:
        """
        Name the private file the primary's dump is held in.

        Args:
            name: The replica's name.

        Returns:
            ``<scratch root>/<name>.sql``.
        """
        root = self._scratch_root or (paths.backup_dir() / ".replica")
        return root / f"{name}.sql"

    def _join(self, plan: JoinPlan, *, discard_previous: bool, timeout: int) -> dict[str, Any]:
        """
        Do what a plan says.

        Args:
            plan: The plan, with no problems.
            discard_previous: Delete the dumps of this server's own databases once it streams.
            timeout: Seconds the dump and the load may take.

        Returns:
            What :meth:`join` returns.

        Raises:
            DatabaseBackupError: When the copy or the start fails.
        """
        token = plan.token
        started = self._clock()
        facts = self.facts()
        authority = self._lay_down_authority(token)
        scratch = self._scratch(token.name)
        safety: list[Path] = []
        try:
            # The primary is asked first: a primary that cannot be reached or trusted costs
            # nothing, not even the dumps of this server's own databases.
            databases = self._primary_databases(token, authority)
            if not databases:
                raise DatabaseBackupError(
                    "The primary holds no database to copy",
                    details="Create one there first, or this replica would follow nothing.",
                )
            for database in plan.databases:
                safety.append(self.manager.backup(database).path)
            self.fs.make_dir(scratch.parent, mode=0o700)
            self._dump_primary(token, authority, databases, scratch, timeout)
        except (NoustError, OSError):
            self._discard_scratch(scratch)
            self._forget_authority(token.name)
            raise
        include = self._include(REPLICA_INCLUDE)
        before = include.read_text(encoding="utf-8") if include.is_file() else None
        progress = {"restarted": False}
        try:
            self._cut_over(plan, facts, authority, scratch, databases, include, timeout, progress)
        except (NoustError, OSError) as exc:
            self._roll_back(plan, safety, include, before, restarted=progress["restarted"])
            self._discard_scratch(scratch)
            self._forget_authority(token.name)
            self._audit("db.replica.join", "failed", name=token.name, error=str(exc)[:300])
            details = exc.details if isinstance(exc, NoustError) and exc.details else str(exc)
            raise DatabaseBackupError(
                "The replica did not start following, and the previous databases are back",
                details=f"{details}\nThis server holds what it held before.",
            ) from exc
        self._discard_scratch(scratch)
        kept: str | None = "\n".join(str(path) for path in safety) or None
        if discard_previous:
            for path in safety:
                self.fs.remove(path)
            kept = None
        for stale in self._rows(REPLICA):
            self._forget(REPLICA, stale.name)
        self.records.save(
            ReplicaRecord(
                side=REPLICA,
                name=token.name,
                account=token.role,
                slot="",
                host=token.host,
                port=token.port,
                passfile=str(authority),
                saved_as=kept,
                state="following",
                engine=ENGINE,
            )
        )
        seconds = int((self._clock() - started).total_seconds())
        self._audit(
            "db.replica.join",
            name=token.name,
            primary=f"{token.host}:{token.port}",
            databases=databases,
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

    def _lay_down_authority(self, token: ReplicaToken) -> Path:
        """
        Write the authority the code carries where the server's account can read it.

        Args:
            token: The join code.

        Returns:
            The file of authorities the replica verifies its primary with.
        """
        authority = self._authority_path(token)
        assert authority is not None  # noqa: S101 - plan_join refused otherwise
        if token.ca:
            self.fs.make_dir(authority.parent, mode=0o755)
            self.fs.write_text(authority, token.ca, mode=0o644)
        return authority

    def _forget_authority(self, name: str) -> None:
        """
        Remove the authority a replica was given.

        Args:
            name: The replica's name.
        """
        self._discard(self.authority_directory(name))

    def _discard_scratch(self, path: Path) -> None:
        """
        Remove the private copy of the primary's data.

        Args:
            path: The file.
        """
        self.fs.remove(path)

    def _cut_over(
        self,
        plan: JoinPlan,
        facts: dict[str, Any],
        authority: Path,
        scratch: Path,
        databases: list[str],
        include: Path,
        timeout: int,
        progress: dict[str, bool],
    ) -> None:
        """
        Replace the databases and start replicating: the part that can leave this server half done.

        Args:
            plan: The plan.
            facts: What this server said about itself before.
            authority: The file of authorities.
            scratch: The dump of the primary.
            databases: The databases the dump holds.
            include: Noust's settings file for a replica.
            timeout: Seconds the load may take.
            progress: Where to note that the server was restarted, so that undoing it restarts it
                too (this raises when a step fails, and the caller must still know).

        Raises:
            DatabaseError: When a step fails.
        """
        token = plan.token
        server_id = plan.capacity["server_id"]
        settings = {"server_id": str(server_id)}
        if not self.mariadb:
            settings.update(gtid_mode="ON", enforce_gtid_consistency="ON")
        self.fs.write_text(
            include,
            render_include("what a replica needs to follow its primary", settings),
            mode=0o644,
        )
        if not self.mariadb and (facts["gtid_mode"] != "ON" or facts["enforce"] != "ON"):
            progress["restarted"] = True
            self._restart()
        else:
            self._sql(f"SET GLOBAL server_id = {server_id};")
        self._thaw()
        for database in plan.databases:
            self._sql(f"DROP DATABASE IF EXISTS {self.manager._escape_identifier(database)};")
        if self.mariadb:
            self._sql("STOP SLAVE; RESET SLAVE ALL;")
        else:
            self._sql(forget_binary_log(facts["version"]))
        self._load_file(scratch)
        self._sql(
            change_source(
                self.mariadb,
                host=token.host,
                port=token.port,
                account=token.role,
                password=token.password,
                authority=str(authority),
            )
            + start_statement(self.mariadb),
            secrets=(token.password,),
        )
        self._wait_streaming()
        settings.update(self._freeze())
        self.fs.write_text(
            include,
            render_include("what a replica needs to follow its primary", settings),
            mode=0o644,
        )

    def _thaw(self) -> None:
        """Let this server be written to, for the load (the dump is not a replicated write)."""
        if self.mariadb:
            self._sql("SET GLOBAL read_only = 0;")
        else:
            self._sql("SET GLOBAL super_read_only = 0; SET GLOBAL read_only = 0;")

    def _freeze(self) -> dict[str, str]:
        """
        Make this server refuse writes that do not come from its primary.

        Returns:
            The settings that keep it so across a restart.
        """
        if self.mariadb:
            self._sql("SET GLOBAL read_only = 1;")
            return {"read_only": "1"}
        self._sql("SET GLOBAL read_only = 1; SET GLOBAL super_read_only = 1;")
        return {"read_only": "ON", "super_read_only": "ON"}

    def _wait_streaming(self) -> None:
        """
        Wait for both replication threads to run, and say what the server says when they do not.

        Raises:
            DatabaseBackupError: When a thread stops with an error, or the time runs out.
        """
        deadline = self._clock().timestamp() + STREAM_TIMEOUT
        while True:
            row = self._replica_row()
            io = pick(row, "Replica_IO_Running", "Slave_IO_Running")
            sql = pick(row, "Replica_SQL_Running", "Slave_SQL_Running")
            problem = pick(row, "Last_IO_Error") or pick(row, "Last_SQL_Error")
            if io == "Yes" and sql == "Yes" and not problem:
                return
            if problem:
                raise DatabaseBackupError(
                    "The replica stopped with an error", details=problem[-2000:]
                )
            if self._clock().timestamp() >= deadline:
                raise DatabaseBackupError(
                    f"The replica did not start streaming from the primary in {STREAM_TIMEOUT} "
                    "seconds",
                    details=f"IO thread: {io or 'not running'}; SQL thread: {sql or 'not running'}.",
                )
            self._sleep(POLL_SECONDS)

    def _roll_back(
        self,
        plan: JoinPlan,
        safety: list[Path],
        include: Path,
        before: str | None,
        *,
        restarted: bool,
    ) -> None:
        """
        Put this server back as it was after a join that failed half way.

        Args:
            plan: The plan.
            safety: The dumps made of this server's own databases.
            include: Noust's settings file for a replica.
            before: What it held before, or None when it did not exist.
            restarted: Whether the server was restarted, so that it is restarted again.
        """
        undoing: list[tuple[str, Callable[[], object]]] = [
            ("stop replicating", self._stop_following),
            ("drop what was loaded", self._drop_loaded),
        ]
        for database, path in zip(plan.databases, safety, strict=False):
            undoing.append(
                (
                    f"load {database} back",
                    lambda d=database, p=path: self.manager.restore(  # type: ignore[misc]
                        d, p, drop_existing=True, safety_backup=False
                    ),
                )
            )

        def settings_back() -> None:
            if before is None:
                self.fs.remove(include)
            else:
                self.fs.write_text(include, before, mode=0o644)
            if restarted:
                self._restart()
            self._sql("SET GLOBAL read_only = 0;")

        undoing.append(("take the replica settings off", settings_back))
        for what, action in undoing:
            try:
                action()
            except (NoustError, OSError) as exc:
                self.manager.logger.warning(f"Could not {what}: {exc}. Undo it by hand.")

    def _stop_following(self) -> None:
        """Stop the replication threads and forget the primary."""
        if self.mariadb:
            self._sql("STOP SLAVE; RESET SLAVE ALL;")
        else:
            self._sql("STOP REPLICA; RESET REPLICA ALL;")

    def _drop_loaded(self) -> None:
        """Drop every user database, which at this point is what the failed load left."""
        for database in self._user_databases():
            self._sql(f"DROP DATABASE IF EXISTS {self.manager._escape_identifier(database)};")

    # ==================== Watching ====================

    def status(self) -> dict[str, Any]:
        """
        Say how each replica of this server, and this server as a replica, stand.

        Returns:
            ``role`` (``primary``, ``replica`` or ``standalone``), ``replicas``, ``follows`` and
            ``warnings``, in the shape PostgreSQL's gives.

        Raises:
            DatabaseEngineError: When the server is not there to ask.
        """
        self._require_engine()
        prepared = self._rows(PRIMARY)
        following = self._rows(REPLICA)
        row = self._replica_row()
        warnings: list[str] = []
        view: dict[str, Any] = {
            "engine": ENGINE,
            "role": "replica" if row else ("primary" if prepared else "standalone"),
            "replicas": [],
            "follows": None,
            "warnings": warnings,
        }
        if prepared and not row:
            view["replicas"] = self._primary_view(prepared, warnings)
        if row:
            view["follows"] = self._replica_view(row, following, warnings)
        return view

    def _primary_view(
        self, prepared: list[ReplicaRecord], warnings: list[str]
    ) -> list[dict[str, Any]]:
        """
        Read each prepared replica's connection from the server.

        Args:
            prepared: The replicas prepared here.
            warnings: Where to add what the operator should know.

        Returns:
            One entry per replica.
        """
        cipher = (
            "''"
            if self.mariadb
            else "coalesce((SELECT s.VARIABLE_VALUE FROM performance_schema.status_by_thread s "
            "JOIN performance_schema.threads t ON t.THREAD_ID = s.THREAD_ID "
            "WHERE t.PROCESSLIST_ID = p.ID AND s.VARIABLE_NAME = 'Ssl_cipher'), '')"
        )
        out: list[dict[str, Any]] = []
        for record in prepared:
            account = literal(record.account)
            line = self._sql(
                f"SELECT p.ID, p.HOST, p.TIME, {cipher}, "  # noqa: S608 - this module's constants
                "(SELECT u.ssl_type FROM mysql.user u "
                f"WHERE u.user = {account} LIMIT 1) "
                "FROM information_schema.PROCESSLIST p "
                f"WHERE p.COMMAND LIKE 'Binlog Dump%' AND p.USER = {account} LIMIT 1;"
            )
            fields = line.splitlines()[-1].split("\t") if line else []
            fields += [""] * (5 - len(fields)) if fields else []
            entry: dict[str, Any] = {
                "name": record.name,
                "account": record.account,
                "slot": "",
                "source": record.source,
                "host": record.host,
                "connected": bool(fields),
                "state": "streaming" if fields else "not connected",
                "tls": None,
                "tls_version": None,
                "client": None,
                "lag_bytes": None,
                "retained_bytes": None,
                "wal_status": None,
            }
            if fields:
                entry["client"] = fields[1].rsplit(":", 1)[0]
                if fields[3] and fields[3] != "NULL":
                    entry["tls"] = True
                    entry["tls_version"] = fields[3]
                else:
                    required = fields[4] not in ("", "NULL")
                    entry["tls"] = True if required else False
                    entry["tls_version"] = "required by the account" if required else None
                if entry["tls"] is False:
                    warnings.append(f"{record.name} is connected without TLS.")
            out.append(entry)
        return out

    def _replica_view(
        self, row: dict[str, str], following: list[ReplicaRecord], warnings: list[str]
    ) -> dict[str, Any]:
        """
        Read this server's connection to its primary.

        Args:
            row: What the server says about following.
            following: This server's record of the primary it follows.
            warnings: Where to add what the operator should know.

        Returns:
            What the threads say.
        """
        io = pick(row, "Replica_IO_Running", "Slave_IO_Running")
        sql = pick(row, "Replica_SQL_Running", "Slave_SQL_Running")
        error = pick(row, "Last_IO_Error") or pick(row, "Last_SQL_Error")
        delay = pick(row, "Seconds_Behind_Source", "Seconds_Behind_Master")
        verified = (
            pick(row, "Source_SSL_Allowed", "Master_SSL_Allowed") == "Yes"
            and pick(row, "Source_SSL_Verify_Server_Cert", "Master_SSL_Verify_Server_Cert") == "Yes"
        )
        record = following[0] if following else None
        host = pick(row, "Source_Host", "Master_Host")
        port = pick(row, "Source_Port", "Master_Port")
        view = {
            "name": record.name if record else None,
            "state": "streaming" if io == "Yes" and sql == "Yes" else "stopped",
            "primary": f"{host}:{port}" if host else None,
            "replay_delay_seconds": int(delay) if delay.isdigit() else None,
            "unreplayed_bytes": None,
            "sslmode": "verify-full" if verified else "not verified",
            "saved_as": record.saved_as if record else None,
        }
        if record is None:
            warnings.append(
                "This server follows a primary but Noust did not set it up as a replica "
                "(a replica made by hand)."
            )
        if view["state"] != "streaming":
            warnings.append(
                f"Replication is stopped (IO thread {io or 'off'}, SQL thread {sql or 'off'})"
                + (f": {error}" if error else ".")
            )
        if not verified:
            warnings.append("The connection to the primary does not verify its certificate.")
        return view

    # ==================== Promoting and removing ====================

    def promote(self, *, confirmed: bool) -> dict[str, Any]:
        """
        Make this replica a primary: it stops following and accepts writes.

        What the replica had already read from its primary is applied first.

        Args:
            confirmed: The operator accepted it (the guard is here, not in the caller that asks).

        Returns:
            ``promoted``, ``name`` and ``note``.

        Raises:
            ValidationError: When this server follows nothing Noust set up, or it was not
                confirmed.
        """
        if is_rehearsal():
            return {"promoted": False, "rehearsal": True}
        with cluster_lock("replica promotion", LOCK_KEY):
            self._require_engine()
            row = self._replica_row()
            records = self._rows(REPLICA)
            if not row:
                raise ValidationError(
                    "This server is not a replica",
                    details=f"{self.DISPLAY} here already accepts writes.",
                    field="plan",
                )
            if not records:
                raise ValidationError(
                    "Noust did not set this server up as a replica",
                    details="Stop it yourself (STOP SLAVE; RESET SLAVE ALL); Noust only promotes "
                    "what it joined.",
                    field="plan",
                )
            record = records[0]
            if not confirmed:
                raise ValidationError(
                    "Promoting stops this server following its primary",
                    details="It becomes a primary of its own and accepts writes. "
                    f"{record.host}:{record.port} keeps the account {record.account} until you "
                    f"remove the replica there (noust db replica remove {record.name}). "
                    "Confirm to go ahead.",
                    field="confirmed",
                )
            self._drain()
            self._stop_following()
            include = self._include(REPLICA_INCLUDE)
            kept = {
                key: value
                for key, value in parse_include(
                    include.read_text(encoding="utf-8") if include.is_file() else ""
                ).items()
                if key not in ("read_only", "super_read_only")
            }
            self.fs.write_text(include, render_include("what a replica needs", kept), mode=0o644)
            self._sql("SET GLOBAL read_only = 0;")
            if not self.mariadb:
                self._sql("SET GLOBAL super_read_only = 0;")
            self._forget_authority(record.name)
            self._forget(REPLICA, record.name)
            self._audit("db.replica.promote", name=record.name, was=f"{record.host}:{record.port}")
            self.manager.logger.success("This server accepts writes now")
            return {
                "promoted": True,
                "name": record.name,
                "note": f"{record.host}:{record.port} still has the account {record.account}: "
                f"remove the replica there (noust db replica remove {record.name}).",
            }

    def _drain(self) -> None:
        """Stop reading from the primary and wait for what was read to be applied."""
        self._sql(stop_statement(self.mariadb, "IO_THREAD"))
        deadline = self._clock().timestamp() + DRAIN_TIMEOUT
        while self._clock().timestamp() < deadline:
            state = pick(
                self._replica_row(), "Replica_SQL_Running_State", "Slave_SQL_Running_State"
            )
            if "read all relay log" in state or not state:
                return
            self._sleep(1)
        self.manager.logger.warning(
            f"The replica did not finish applying the log in {DRAIN_TIMEOUT} seconds; "
            "what it had not applied is lost with the following."
        )

    def remove(self, name: str, *, confirmed: bool, restore_listen: bool = False) -> dict[str, Any]:
        """
        Take a replica away: on a primary its account and firewall rule, on a replica its record.

        Args:
            name: The replica's name.
            confirmed: The operator accepted it (the guard is here, not in the caller that asks).
            restore_listen: Take Noust's settings off the server (the address it listens on,
                binary logging and GTIDs that Noust turned on), when no other replica needs them.
                Restarts the server.

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
        with cluster_lock("replica removal", LOCK_KEY):
            followed = self._row(REPLICA, name)
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
        self._require_engine()
        if self._replica_row():
            raise ValidationError(
                "This server still follows that primary",
                details="Promote it first (noust db replica promote).",
                field="name",
            )
        if not confirmed:
            raise ValidationError("Confirm to forget the primary", field="confirmed")
        self._forget_authority(record.name)
        self._forget(REPLICA, record.name)
        return {"removed": True, "side": REPLICA, "steps": ["forgot the record and the authority"]}

    def _remove_prepared(self, name: str, confirmed: bool, restore_listen: bool) -> dict[str, Any]:
        """
        Remove what preparing a replica made on this primary.

        Args:
            name: The replica's name.
            confirmed: The operator accepted it.
            restore_listen: Take Noust's settings off the server, when no other replica needs them.

        Returns:
            What :meth:`remove` returns.

        Raises:
            ValidationError: When it was not confirmed.
        """
        self._require_engine()
        account = role_for(name)
        record = self._row(PRIMARY, name)
        present = (
            record is not None
            or self._sql(
                f"SELECT count(*) FROM mysql.user WHERE user = {literal(account)};"  # noqa: S608
            )
            != "0"
        )
        if not present:
            return {"removed": False, "steps": [], "note": f"No replica named {name} here."}
        if not confirmed:
            raise ValidationError(
                f"Removing {name} cuts its replica off",
                details=f"The account {account} is dropped and its connection ends; the replica "
                "stops streaming and cannot resume. Confirm to go ahead.",
                field="confirmed",
            )
        steps: list[str] = []
        hosts = self._sql(
            f"SELECT host FROM mysql.user WHERE user = {literal(account)};"  # noqa: S608
        )
        for host in (line for line in hosts.splitlines() if line):
            self._sql(f"DROP USER IF EXISTS {literal(account)}@{literal(host)};")
        steps.append(f"dropped the account {account}")
        if record is not None and self._close_firewall(record):
            steps.append("closed the firewall rule")
        self.secrets.delete(self._secret(name))
        self._discard(self.state_directory(name))
        steps.append("forgot the password")
        restart = False
        if record is not None:
            self._forget(PRIMARY, name)
            if record.listen_was is not None and not self._rows(PRIMARY):
                if restore_listen:
                    include = self._include(PRIMARY_INCLUDE)
                    self.fs.remove(include)
                    self._restart()
                    restart = True
                    steps.append(f"took {include.name} off and restarted {self.DISPLAY}")
                else:
                    steps.append(
                        f"left {PRIMARY_INCLUDE} as it is (it was listening on "
                        f"{record.listen_was!r}; --restore-listen takes it off and restarts "
                        f"{self.DISPLAY})"
                    )
        self._audit("db.replica.remove", name=name, listen_restored=restart)
        self.manager.logger.success(f"Replica {name} removed")
        return {"removed": True, "side": PRIMARY, "steps": steps}
