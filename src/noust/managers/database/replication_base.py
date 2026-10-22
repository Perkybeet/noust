# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a replica of any host engine shares: the records, the secret, the proof of TLS, the firewall.

PostgreSQL copies a cluster and follows its log
(:mod:`~noust.managers.database.replication`); MySQL and MariaDB load a dump and follow the binary
log (:mod:`~noust.managers.database.replication_mysql`). The engines differ in everything that
touches the engine and agree on everything around it, and that part is written once (rule 3):

- the plan a ``prepare`` shows and the plan a ``join`` shows;
- the proof that the certificate the primary presents would be accepted by the replica, run from
  the primary with the handshake of the engine's protocol, before anything is made;
- the replica's password, kept in the secret store, and the authority it will trust, kept so the
  join code can be shown again;
- the firewall rule the primary asks for, recorded so that taking the replica away takes away that
  rule and no other;
- undoing, in reverse, what a failed preparation had made.
"""

from __future__ import annotations

import ipaddress
import re
import shutil
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from noust.core import audit, paths
from noust.core.exceptions import NoustError
from noust.core.fs import FileSystem, get_fs
from noust.core.secrets import SecretStore
from noust.core.store import get_store
from noust.managers.database.replication_records import ReplicaRecord, ReplicationRecords
from noust.managers.database.replication_token import ReplicaToken
from noust.managers.server.security_firewall import Firewall, RuleRequest
from noust.managers.server.security_probe import SecurityProbe
from noust.validators.replica import check_replica_name

__all__ = [
    "CONNECT_TIMEOUT",
    "SYSTEM_ROOTS",
    "JoinPlan",
    "PreparePlan",
    "ReplicationBase",
    "clip",
    "literal",
    "merge_listen",
]

#: Seconds a connection to the primary may take to be made.
CONNECT_TIMEOUT = 15

#: Where the distributions keep the certificate authorities they trust, in the order tried.
SYSTEM_ROOTS = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/ca-bundle.pem",
)

#: ``*`` or a list of IP addresses: what an engine may be told to listen on.
LISTEN = re.compile(r"(?:\*|[0-9A-Fa-f:.]{2,45})(?:,\s*(?:\*|[0-9A-Fa-f:.]{2,45}))*")

#: What a loopback-only engine listens on.
LOOPBACK = ("localhost", "127.0.0.1", "::1", "")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def literal(value: str) -> str:
    """
    Quote a value written into SQL by this area.

    Args:
        value: A name or a number this area chose or validated.

    Returns:
        It as a SQL string literal.
    """
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def clip(text: str, *, tail: int = 2000, width: int = 300) -> str:
    """
    Shorten a client's output for an error, keeping its own words.

    A failing load echoes the statement it failed on, which can be a megabyte; the error is the
    lines around it, so long lines are cut and only the end is kept.

    Args:
        text: What the tool printed.
        tail: How many characters of the end to keep.
        width: The longest line kept whole.

    Returns:
        The end of the output, with each long line cut and counted.
    """
    lines = [
        line if len(line) <= width else f"{line[: width // 2]} ... ({len(line) - width // 2} more)"
        for line in text.strip().splitlines()
    ]
    return "\n".join(lines)[-tail:]


def merge_listen(current: str, wanted: str) -> str:
    """
    Work out the addresses an engine listens on once the replica can reach it.

    Args:
        current: What the engine listens on now (comma separated).
        wanted: What the operator asked to add: ``*`` or addresses.

    Returns:
        The new list: everything it listened on and what was asked, or ``*`` when either is.
    """
    entries = [item.strip() for item in current.split(",") if item.strip()]
    asked = [item.strip() for item in wanted.split(",") if item.strip()]
    merged = ["*"] if "*" in asked or "*" in entries else [*entries, *asked]
    return ",".join(dict.fromkeys(merged))


@dataclass
class PreparePlan:
    """
    What preparing a replica would do, worked out before anything is touched.

    Attributes:
        name: The replica's name.
        account: The replication account.
        slot: The physical slot (PostgreSQL); empty for an engine without slots.
        host: The name the replica connects to.
        port: The primary's port.
        source: Where the replica connects from, in the form the engine has it.
        listen: The address the engine is told to listen on, or None when it already reaches the
            replica.
        restart: Whether the engine restarts for it.
        firewall: Whether a firewall rule is added.
        settings: The settings the engine's configuration is given (MySQL and MariaDB).
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
    settings: dict[str, str] = field(default_factory=dict)
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
            "settings": dict(self.settings),
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
        data_directory: The data directory that is replaced (PostgreSQL) or read (MySQL).
        saved_as: Where this server's own data is set aside (PostgreSQL) or dumped to (MySQL).
        databases: The databases on this server that the join replaces.
        free_bytes: Free space where the copy is made.
        needed_bytes: What the primary said it holds, with a margin.
        capacity: The settings the replica starts with to match its primary.
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


class ReplicationBase:
    """
    The collaborators and the checks every engine's replicas use.

    Subclasses set :attr:`ENGINE`, :attr:`STARTTLS` and :attr:`DISPLAY`, and own everything that
    touches the engine.
    """

    #: The engine's key, as the store and the audit trail name it.
    ENGINE: ClassVar[str] = ""

    #: What ``openssl s_client -starttls`` is told, to start TLS the way the engine's protocol does.
    STARTTLS: ClassVar[str] = ""

    #: The engine's name in a message (an instance names the flavour that is installed).
    DISPLAY: str = ""

    def __init__(
        self,
        manager: Any,
        *,
        records: ReplicationRecords | None = None,
        secrets: SecretStore | None = None,
        firewall: Callable[[], Firewall] | None = None,
        fs: FileSystem | None = None,
        clock: Callable[[], datetime] = _utcnow,
        sleep: Callable[[float], None] = time.sleep,
        state_root: Path | None = None,
        disk_free: Callable[[Path], int] | None = None,
        local_address: Callable[[str], bool] | None = None,
    ) -> None:
        """
        Args:
            manager: The engine's manager.
            records: The store of replicas. The process-wide store's when None.
            secrets: Where a primary keeps a replica's password. The default store when None.
            firewall: Builds the firewall manager. ufw and firewalld by default.
            fs: The filesystem seam. The process-wide one when None.
            clock: The current instant, injected so a test controls it.
            sleep: Waits between two looks at the engine, injected for the same reason.
            state_root: The directory a primary keeps a replica's certificate authority under.
                ``<state dir>/replication`` when None.
            disk_free: Says how many bytes are free where a path is; ``shutil.disk_usage``'s
                when None.
            local_address: Says whether an address is one of this machine's; a bind attempt
                when None.
        """
        self.manager = manager
        self._records = records
        self._secrets = secrets
        self._firewall = firewall
        self._fs = fs
        self._clock = clock
        self._sleep = sleep
        self._state_root = state_root
        self._disk_free = disk_free or (lambda path: shutil.disk_usage(path).free)
        self._local_address = local_address or self._can_bind

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
        audit.record(event, target=f"database:{self.ENGINE}", outcome=outcome, details=details)

    # ==================== The store ====================

    def _row(self, side: str, name: str) -> ReplicaRecord | None:
        """
        Args:
            side: ``primary`` or ``replica``.
            name: The replica's name.

        Returns:
            This engine's record of it, or None.
        """
        return self.records.get(side, name, self.ENGINE)

    def _rows(self, side: str | None = None) -> list[ReplicaRecord]:
        """
        Args:
            side: Only this end's rows; both when None.

        Returns:
            This engine's records.
        """
        return self.records.records(side, self.ENGINE)

    def _forget(self, side: str, name: str) -> bool:
        """
        Args:
            side: ``primary`` or ``replica``.
            name: The replica's name.

        Returns:
            Whether there was a record to forget.
        """
        return self.records.delete(side, name, self.ENGINE)

    def _secret(self, name: str) -> str:
        """
        Args:
            name: The replica's name.

        Returns:
            The name of the secret that holds its password.
        """
        return f"databases/{self.ENGINE}/replication/{name}"

    # ==================== Places and files ====================

    def state_directory(self, name: str) -> Path:
        """
        Name where a primary keeps what it needs for a replica besides the secret store.

        Args:
            name: The replica's name.

        Returns:
            ``<state dir>/replication/<engine>/<name>``.
        """
        root = self._state_root or (paths.state_dir() / "replication")
        return root / self.ENGINE / check_replica_name(name)

    def _discard(self, path: Path) -> None:
        """
        Delete a directory tree if it is there.

        Args:
            path: The directory.
        """
        if path.exists():
            self.fs.remove_tree(path)

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

    def _kept_authority(self, name: str) -> str:
        """
        Args:
            name: The replica's name.

        Returns:
            The authority :meth:`_keep_authority` kept, or an empty string.
        """
        authority = self.state_directory(name) / "ca.pem"
        return authority.read_text(encoding="utf-8") if authority.is_file() else ""

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

    # ==================== Planning ====================

    def _prove_tls(self, host: str, port: int, ca: Path | None) -> str | None:
        """
        Run the handshake a replica does, from the primary, and say why it would be refused.

        The engine is asked, with the name the replica will use, for its certificate, and the
        certificate is checked the way a verifying client checks it: signed by the authority the
        replica trusts and valid for that name.

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
            self.STARTTLS,
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

    def _plan_tls(self, plan: PreparePlan, ca_pem: str) -> None:
        """
        Prove the engine presents a certificate the replica will accept.

        Args:
            plan: The plan being worked out; a refusal is added to it.
            ca_pem: The authority the replica will trust; the system's when empty.
        """
        if not ca_pem:
            reason = self._prove_tls(plan.host, plan.port, None)
        else:
            reason = self._prove_tls_with(plan.host, plan.port, ca_pem)
        if reason is not None:
            plan.problems.append(
                f"The certificate {self.DISPLAY} presents would not be accepted by a replica "
                f"connecting to {plan.host}: {reason}\n"
                f"Give {self.DISPLAY} a certificate for that name (noust db tls enable), or pass "
                "the authority that signed it (--ca-file)."
            )

    def _plan_listen(
        self, plan: PreparePlan, current: str, listen: str | None, *, reaches: bool
    ) -> None:
        """
        Decide whether the engine must be told to listen where the replica can reach it.

        Args:
            plan: The plan being worked out; ``listen`` and ``restart`` are set on it.
            current: What the engine listens on now.
            listen: What the operator asked to listen on, if anything.
            reaches: Whether what it listens on now already reaches other servers.
        """
        if listen is None:
            if not reaches:
                plan.problems.append(
                    f"{self.DISPLAY} listens on {current!r} only, so no other server can connect. "
                    "Say where it should listen: --listen '*' or --listen <this server's "
                    "address>. That restarts it once."
                )
            return
        if LISTEN.fullmatch(listen.strip()) is None:
            plan.problems.append(
                f"{listen!r} is not '*' or a list of addresses (--listen 10.0.0.5,10.0.1.5)."
            )
            return
        for address in (item.strip() for item in listen.split(",")):
            if address != "*" and not self._local_address(address):
                plan.problems.append(
                    f"{address} is not an address of this server, so {self.DISPLAY} could not "
                    "listen on it and would not start."
                )
        value = merge_listen(current, listen)
        if value != current:
            plan.listen = value
            plan.restart = True
            plan.warnings.append(
                f"{self.DISPLAY} restarts once (a few seconds) to listen on the new addresses; "
                "connections are dropped."
            )
            plan.warnings.append(
                f"Port {plan.port} is then reachable from the network. The engine lets in only "
                "the replication account, from the replica's address, over TLS; the firewall "
                "decides who reaches the port at all."
            )

    # ==================== The firewall ====================

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

    # ==================== Undoing ====================

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
