# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Importing a dump of every database of an engine, and its accounts (4.0, checklist H-18).

Whoever leaves another provider or server has one file, ``pg_dumpall``'s, with the databases
and the roles that own them and are granted on them. Loading one database
(:class:`~noust.managers.database.imports.DatabaseImports`) does not bring that, and handing the
file to ``psql`` would run it as the superuser, create whatever roles it names and connect to
whatever database it says. This does the work in the pieces Noust can judge:

- **Read** (:func:`~noust.managers.database.cluster_dump.read_cluster`): the accounts as data, and
  one file per database cut out of it.
- **Plan** (:meth:`ClusterImporter.plan`): what would be created, left as it is, loaded over or
  replaced, and what is not carried, without changing anything. The console shows it before it asks.
- **Run** (:meth:`ClusterImporter.run`): the accounts first, only when asked for (``restore_users``)
  and confirmed, written from data and never overwriting an account that exists; then each database
  through the one import, so each has its check, its safety copy and its way back, is loaded as its
  owner (H-19) and, if it was made for this, is dropped again when its load fails. A database that
  fails does not stop the others: the result says which did and why, in the engine's own words.

What an account is allowed to be is narrower than what the source had: no superuser, no
``CREATEROLE``, no replication, no ``BYPASSRLS`` (they are reported, not carried); a password only as
the hash the server stores; a membership only between accounts made now. A database whose dump names
owners and privileges keeps them for the roles that exist, and leaves out, statement by statement,
those that name a role that does not (the source's ``postgres``), which a loader that is not a
superuser could not apply anyway.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from noust.core.exceptions import (
    ConfirmationRequired,
    DatabaseError,
    ValidationError,
)
from noust.managers.database.base import BaseDatabaseManager
from noust.managers.database.cluster_dump import (
    BUILTIN_ROLES_CARRIED,
    AccountSpec,
    ClusterDump,
    DatabaseSpec,
    MembershipSpec,
    RoleSpec,
    read_cluster,
    render_membership,
    render_role,
    usable_name,
)
from noust.managers.database.imports import (
    CLUSTER_ACCEPTS,
    DatabaseImports,
    DumpFormat,
    detect_format,
    prepare,
)
from noust.managers.database.mysql_cluster import (
    MysqlAccount,
    read_accounts,
    read_mysql_cluster,
    render_account_create,
    render_account_grants,
)
from noust.managers.database.psql_script import RoleFilter

__all__ = [
    "ClusterImporter",
    "ClusterOutcome",
    "ClusterPlan",
    "DatabasePlan",
    "DatabaseResult",
    "RolePlan",
]

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RolePlan:
    """
    What would happen to one account of the dump.

    Attributes:
        name: The account.
        action: ``create``, ``exists`` (left exactly as it is) or ``skip``.
        reason: Why, when it is not created.
        dropped: Attributes the dump gave it that are not carried.
        has_password: The dump holds a password hash for it.
        login: It can sign in.
    """

    name: str
    action: str
    reason: str | None = None
    dropped: tuple[str, ...] = ()
    has_password: bool = False
    login: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The plan as plain data.
        """
        return {
            "name": self.name,
            "action": self.action,
            "reason": self.reason,
            "dropped": list(self.dropped),
            "has_password": self.has_password,
            "login": self.login,
        }


@dataclass(frozen=True)
class DatabasePlan:
    """
    What would happen to one database of the dump.

    Attributes:
        name: The database.
        action: ``create``, ``load`` (over what it holds, after a safety copy), ``replace`` (dropped
            and recreated after a safety copy) or ``skip``.
        owner: The account that will own it when it is created; None for the server's own.
        reason: Why, when it is skipped.
        size: Bytes of its dump.
    """

    name: str
    action: str
    owner: str | None = None
    reason: str | None = None
    size: int = 0

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The plan as plain data.
        """
        return {
            "name": self.name,
            "action": self.action,
            "owner": self.owner,
            "reason": self.reason,
            "size": self.size,
        }


@dataclass
class ClusterPlan:
    """
    What importing a dump of all databases would do. Nothing has been done.

    Attributes:
        engine: The engine.
        format: What the file is.
        restore_users: The accounts would be made.
        roles: The accounts, each with what happens to it.
        memberships: How many role-to-role grants would be kept.
        databases: The databases, each with what happens to it.
        ignored: What the dump holds that is not carried.
        warnings: What the operator accepts by going ahead.
    """

    engine: str
    format: DumpFormat
    restore_users: bool
    roles: list[RolePlan] = field(default_factory=list)
    memberships: int = 0
    databases: list[DatabasePlan] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def to_create(self) -> list[str]:
        """The accounts that would be made."""
        return [role.name for role in self.roles if role.action == "create"]

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The plan as plain data.
        """
        return {
            "engine": self.engine,
            "format": self.format.to_dict(),
            "restore_users": self.restore_users,
            "roles": [role.to_dict() for role in self.roles],
            "memberships": self.memberships,
            "databases": [database.to_dict() for database in self.databases],
            "ignored": list(self.ignored),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class DatabaseResult:
    """
    What happened to one database.

    Attributes:
        name: The database.
        status: ``created``, ``replaced``, ``loaded``, ``skipped`` or ``failed``.
        detail: The reason for a skip, or the engine's own words for a failure.
        safety_copy: The dump taken of what it held, kept, if any.
        notes: What was done on the way that the operator may want to know.
    """

    name: str
    status: str
    detail: str | None = None
    safety_copy: str | None = None
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The result as plain data.
        """
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "safety_copy": self.safety_copy,
            "notes": list(self.notes),
        }


@dataclass
class ClusterOutcome:
    """
    What an import of all databases did.

    Attributes:
        engine: The engine.
        accounts_created: The accounts made.
        databases: One result per database of the dump.
        ignored: What the dump held that was not carried.
        warnings: What went wrong after the databases were loaded that does not undo them: a
            privilege that could not be given, in the server's own words.
    """

    engine: str
    accounts_created: list[str] = field(default_factory=list)
    databases: list[DatabaseResult] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def failed(self) -> list[DatabaseResult]:
        """The databases that did not load."""
        return [database for database in self.databases if database.status == "failed"]

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The outcome as plain data.
        """
        return {
            "engine": self.engine,
            "accounts_created": list(self.accounts_created),
            "databases": [database.to_dict() for database in self.databases],
            "ignored": list(self.ignored),
            "warnings": list(self.warnings),
            "failed": len(self.failed),
        }


class ClusterImporter:
    """
    Import a dump of all databases through the one import.

    Construct one per request or command.
    """

    def __init__(self, imports: DatabaseImports) -> None:
        """
        Args:
            imports: The class every single-database import goes through, which carries the
                service, the restore, the audit trail and the uploads.
        """
        self.imports = imports
        self.service = imports.service

    # ------------------------------------------------------------------ reading

    def _manager(self, engine: str) -> BaseDatabaseManager:
        manager = self.service.running(engine)
        if manager.engine_type not in CLUSTER_ACCEPTS:
            raise ValidationError(
                f"{manager.DISPLAY_NAME} has no dump of all databases to import",
                details="PostgreSQL's pg_dumpall and MySQL's and MariaDB's mysqldump are read.",
                field="engine",
            )
        return manager

    def _read(
        self,
        manager: BaseDatabaseManager,
        source: Path,
        scratch: Path,
        accounts: Path | None = None,
    ) -> tuple[DumpFormat, ClusterDump]:
        """
        Detect and cut a dump of all databases.

        Args:
            manager: The engine.
            source: The dump, any compression.
            scratch: A private directory the caller removes.
            accounts: MySQL and MariaDB: a file of ``CREATE USER`` and ``GRANT`` statements
                (``mysqldump`` does not write the accounts as statements).

        Returns:
            What the file is, and what it holds.

        Raises:
            ValidationError: When it is not a dump of all databases of this engine.
        """
        fmt = detect_format(source)
        if fmt.kind not in CLUSTER_ACCEPTS[manager.engine_type]:
            raise ValidationError(
                f"{source.name} is not a dump of all databases",
                details=f"It is {fmt.description}. A dump of all databases is what 'pg_dumpall' "
                "(PostgreSQL) or 'mysqldump --all-databases' (MySQL, MariaDB) writes; for one "
                "database use 'noust db import'.",
                field="path",
            )
        prepared = prepare(
            source, fmt, manager.engine_type, scratch, fs=self.imports.fs, allow_cluster=True
        )
        sections = scratch / "databases"
        self.imports.fs.make_dir(sections, mode=0o700)
        if manager.engine_type == "mysql":
            cluster = read_mysql_cluster(prepared.path, sections)
            if any(spec.system for spec in cluster.databases):
                cluster.ignored.append(
                    "The server's own schemas (mysql, sys) were not loaded: they hold its accounts "
                    "and settings. To restore accounts give a file of CREATE USER and GRANT "
                    "statements."
                )
            if accounts is not None:
                found, ignored = read_accounts(accounts)
                cluster.roles = list(found)
                cluster.ignored.extend(ignored)
        else:
            cluster = read_cluster(prepared.path, sections)
        return fmt, cluster

    # ----------------------------------------------------------------- planning

    def _plan(
        self,
        manager: BaseDatabaseManager,
        fmt: DumpFormat,
        cluster: ClusterDump,
        *,
        restore_users: bool,
        only: Sequence[str],
        replace: bool,
    ) -> ClusterPlan:
        plan = ClusterPlan(
            engine=manager.ENGINE_NAME,
            format=fmt,
            restore_users=restore_users,
            ignored=list(cluster.ignored),
        )
        existing_roles = manager.role_names()
        made: set[str] = set()
        if restore_users:
            for role in cluster.roles:
                decided = _decide_role(role, existing_roles, manager)
                plan.roles.append(decided)
                if decided.action == "create":
                    made.add(role.name)
            kept = _kept_memberships(cluster.memberships, made)
            plan.memberships = len(kept)
            for grant in cluster.memberships:
                if grant not in kept:
                    plan.ignored.append(
                        f"The membership of {grant.member} in {grant.role} was not carried: "
                        "memberships are kept between the accounts made now"
                    )
            plan.warnings.extend(_account_warnings(plan, manager))
            if not cluster.roles and manager.engine_type == "mysql":
                plan.ignored.append(
                    "No accounts to restore: give a file of CREATE USER and GRANT statements"
                )
        elif cluster.roles:
            plan.ignored.append(
                f"{len(cluster.roles)} account(s) in the dump were not restored: "
                "ask for them to be (restore users) if the databases belong to them"
            )

        available = existing_roles | made
        wanted = {name.lower() for name in only}
        for spec in cluster.databases:
            plan.databases.append(
                self._decide_database(
                    manager,
                    spec,
                    available=available,
                    restore_users=restore_users,
                    wanted=wanted,
                    replace=replace,
                )
            )
        missing = wanted - {spec.name.lower() for spec in cluster.databases}
        for name in sorted(missing):
            plan.ignored.append(f"The dump holds no database called '{name}'")
        return plan

    def _decide_database(
        self,
        manager: BaseDatabaseManager,
        spec: DatabaseSpec,
        *,
        available: set[str],
        restore_users: bool,
        wanted: set[str],
        replace: bool,
    ) -> DatabasePlan:
        named = spec.name.lower() in wanted
        if wanted and not named:
            return DatabasePlan(spec.name, "skip", reason="Not one of the databases asked for")
        if spec.system and not named:
            return DatabasePlan(
                spec.name,
                "skip",
                reason="A database of the server itself; name it to load it",
                size=spec.size,
            )
        if spec.path is None:
            return DatabasePlan(spec.name, "skip", reason="The dump holds no contents for it")
        try:
            manager.validate_database_name(spec.name)
        except DatabaseError as exc:
            return DatabasePlan(spec.name, "skip", reason=exc.message, size=spec.size)
        if manager.database_exists(spec.name):
            return DatabasePlan(
                spec.name, "replace" if replace else "load", size=spec.size, reason=None
            )
        owner = spec.owner if restore_users and spec.owner in available else None
        return DatabasePlan(spec.name, "create", owner=owner, size=spec.size)

    # ------------------------------------------------------------------ public

    def plan(
        self,
        engine: str,
        source: Path,
        *,
        restore_users: bool = False,
        only: Sequence[str] = (),
        replace: bool = False,
        accounts: Path | None = None,
    ) -> ClusterPlan:
        """
        Say what importing a dump of all databases would do. Changes nothing.

        Args:
            engine: The engine.
            source: The dump, on this server.
            restore_users: Also make its accounts.
            only: Load only these databases (system ones included when named).
            replace: Replace the databases that exist instead of loading over them.
            accounts: MySQL and MariaDB: the file of ``CREATE USER`` and ``GRANT`` statements.

        Returns:
            The plan.

        Raises:
            ValidationError: When the engine has no such dump.
            DatabaseBackupError: When the file is not one, or cannot be read.
        """
        manager = self._manager(engine)
        with self.imports.scratch() as scratch:
            fmt, cluster = self._read(manager, source, scratch, accounts)
            return self._plan(
                manager, fmt, cluster, restore_users=restore_users, only=only, replace=replace
            )

    def run(
        self,
        engine: str,
        source: Path,
        *,
        restore_users: bool = False,
        confirm_users: bool = False,
        only: Sequence[str] = (),
        replace: bool = False,
        safety_backup: bool = True,
        on_step: Callable[[str], None] | None = None,
        as_superuser: bool = False,
        confirm_superuser: bool = False,
        accounts: Path | None = None,
    ) -> ClusterOutcome:
        """
        Import a dump of all databases.

        Args:
            engine: The engine.
            source: The dump, on this server.
            restore_users: Make its accounts too, before the databases.
            confirm_users: The operator accepted what making them means.
            only: Load only these databases.
            replace: Replace the databases that exist instead of loading over them.
            safety_backup: Dump a database first when it is loaded over.
            on_step: Told what is happening, for a job's progress.
            as_superuser: Load every database as the server's superuser, which can hand objects
                to any account there is and run whatever the file says: for a file the operator
                made or has read.
            confirm_superuser: The operator accepted what that costs.
            accounts: MySQL and MariaDB: the file of ``CREATE USER`` and ``GRANT`` statements.

        Returns:
            What was done, per database. A database that failed does not stop the others.

        Raises:
            ConfirmationRequired: ``restore_users`` without ``confirm_users``, or ``as_superuser``
                without ``confirm_superuser``; nothing is changed.
            ValidationError: When the engine has no such dump.
            DatabaseBackupError: When the file is not one, or cannot be read.
            DatabaseError: When the accounts cannot be made (none was, and no database is loaded).
        """
        manager = self._manager(engine)
        if as_superuser:
            # Before the file is opened: an unconfirmed request costs nothing.
            self.imports.backups.superuser_options(manager, None, confirm_superuser)

        def step(text: str) -> None:
            self.service.logger.info(text)
            if on_step:
                on_step(text)

        outcome = ClusterOutcome(engine=manager.ENGINE_NAME)
        with self.imports.scratch() as scratch:
            step(f"Reading {source.name}")
            fmt, cluster = self._read(manager, source, scratch, accounts)
            plan = self._plan(
                manager, fmt, cluster, restore_users=restore_users, only=only, replace=replace
            )
            outcome.ignored = list(plan.ignored)
            if restore_users and plan.warnings and not confirm_users:
                raise ConfirmationRequired(plan.warnings, field="confirm_users")
            made, privileges = (
                self._make_accounts(manager, cluster, plan, step) if restore_users else ([], "")
            )
            outcome.accounts_created = made
            available = manager.role_names()
            for decided in plan.databases:
                spec = next(s for s in cluster.databases if s.name == decided.name)
                outcome.databases.append(
                    self._load_database(
                        manager,
                        spec,
                        decided,
                        available=available,
                        restore_users=restore_users,
                        safety_backup=safety_backup,
                        as_superuser=as_superuser,
                        step=step,
                    )
                )
        if privileges:
            # After the databases: a grant on a table needs the table.
            step("Giving the accounts their privileges")
            try:
                manager.create_accounts(privileges)
            except DatabaseError as exc:
                outcome.warnings.append(
                    "\n".join(part for part in (exc.message, exc.details) if part)
                )
        self.service.audit(
            "db.import.cluster",
            manager.ENGINE_NAME,
            file=source.name,
            accounts=",".join(outcome.accounts_created) or None,
            created=sum(1 for d in outcome.databases if d.status == "created"),
            loaded=sum(1 for d in outcome.databases if d.status in ("loaded", "replaced")),
            failed=len(outcome.failed),
            as_superuser=as_superuser,
        )
        return outcome

    # -------------------------------------------------------------------- work

    def _make_accounts(
        self,
        manager: BaseDatabaseManager,
        cluster: ClusterDump,
        plan: ClusterPlan,
        step: Callable[[str], None],
    ) -> tuple[list[str], str]:
        """
        Make the accounts the plan says, in one transaction where the engine has one.

        Args:
            manager: The engine.
            cluster: What was read.
            plan: What was decided.
            step: Told what is happening.

        Returns:
            The accounts made, and the statements that give them their privileges, to run once
            the databases are loaded (MySQL and MariaDB: a grant on a table needs the table;
            PostgreSQL's are in the dumps themselves).
        """
        names = set(plan.to_create)
        if not names:
            return [], ""
        step(f"Making {len(names)} account(s)")
        roles = [role for role in cluster.roles if role.name in names]
        grants = _kept_memberships(cluster.memberships, names)
        mariadb = bool(getattr(manager, "is_mariadb", False))
        script = "".join(
            render_account_create(role, mariadb=mariadb)
            for role in roles
            if isinstance(role, MysqlAccount)
        ) + "".join(render_role(role) for role in roles if isinstance(role, RoleSpec))
        script += "".join(render_membership(grant) for grant in grants)
        privileges = "".join(
            render_account_grants(role) for role in roles if isinstance(role, MysqlAccount)
        )
        hashes = tuple(role.password_hash for role in roles if role.password_hash)
        manager.create_accounts(script, secrets=hashes)
        self.service.audit(
            "db.import.accounts",
            manager.ENGINE_NAME,
            accounts=",".join(sorted(names)),
            memberships=len(grants),
        )
        return sorted(names), privileges

    def _load_database(
        self,
        manager: BaseDatabaseManager,
        spec: DatabaseSpec,
        decided: DatabasePlan,
        *,
        available: set[str],
        restore_users: bool,
        safety_backup: bool,
        as_superuser: bool,
        step: Callable[[str], None],
    ) -> DatabaseResult:
        """
        Load one database, creating it first when the plan says so.

        Args:
            manager: The engine.
            spec: The database as cut out of the dump.
            decided: What the plan said.
            available: Every account the server has now.
            restore_users: The dump's ownership statements are kept for the roles that exist.
            safety_backup: Dump it first when it is loaded over.
            as_superuser: Load it as the superuser.
            step: Told what is happening.

        Returns:
            What happened.
        """
        if decided.action == "skip" or spec.path is None:
            return DatabaseResult(spec.name, "skipped", decided.reason)
        created = decided.action == "create"
        try:
            if created:
                step(f"Creating {spec.name}")
                self.service.create(
                    manager.ENGINE_NAME, spec.name, owner=decided.owner, encoding=spec.encoding
                )
            # Objects can be handed to the database's owner, whom an unprivileged loader
            # becomes (the manager adds it); the superuser can hand them to any account there is.
            role_filter = (
                RoleFilter(
                    owners=frozenset(available if as_superuser else ()),
                    grantees=frozenset(available),
                )
                if restore_users
                else None
            )
            outcome = self.imports.run(
                manager.ENGINE_NAME,
                spec.name,
                spec.path,
                replace=decided.action == "replace",
                keep_owners=restore_users,
                safety_backup=safety_backup and not created,
                file_name=f"{spec.name}.sql",
                on_step=step,
                as_superuser=as_superuser,
                confirm_superuser=as_superuser,
                role_filter=role_filter,
                create_owner=(
                    spec.owner
                    if restore_users and spec.owner in available and not spec.system
                    else None
                ),
            )
        except DatabaseError as exc:
            if created:
                self._forget(manager, spec.name)
            _log.warning("Importing '%s' from a dump of all databases failed: %s", spec.name, exc)
            return DatabaseResult(
                spec.name, "failed", "\n".join(part for part in (exc.message, exc.details) if part)
            )
        status = "created" if created else "replaced" if decided.action == "replace" else "loaded"
        notes = (
            self._give_to_owner(manager, spec, available) if restore_users and not created else []
        )
        return DatabaseResult(
            spec.name, status, safety_copy=outcome.safety_copy, notes=tuple(notes)
        )

    def _give_to_owner(
        self, manager: BaseDatabaseManager, spec: DatabaseSpec, available: set[str]
    ) -> list[str]:
        """
        Hand a database that was already here to the account that owned it at the source.

        A database that exists keeps its owner through the load; when the accounts of the dump
        were restored, the account that owned it there is what an application will sign in as.

        Args:
            manager: The engine.
            spec: The database as cut out of the dump.
            available: Every account the server has.

        Returns:
            What was done or could not be, one sentence each; none when nothing needed doing.
        """
        wanted = spec.owner
        if not wanted or wanted not in available or spec.system:
            return []
        try:
            current = manager.get_database_info(spec.name).owner
            if current == wanted:
                return []
            self.service.fix_owner(manager.ENGINE_NAME, spec.name, owner=wanted, apply=True)
        except DatabaseError as exc:
            return [f"It could not be given to {wanted}: {exc.message}"]
        return [f"It now belongs to {wanted}, as at the source (it was {current})"]

    def _forget(self, manager: BaseDatabaseManager, name: str) -> None:
        """
        Drop a database this import made and could not fill.

        Args:
            manager: The engine.
            name: The database.
        """
        try:
            self.service.drop(manager.ENGINE_NAME, name, force=True, keep_backup=False)
        except DatabaseError as exc:
            _log.warning("Could not drop '%s', made by an import that failed: %s", name, exc)


# ----------------------------------------------------------------------- helpers


def _decide_role(role: AccountSpec, existing: set[str], manager: BaseDatabaseManager) -> RolePlan:
    """
    Decide what happens to an account of the dump.

    Args:
        role: The account.
        existing: The accounts the server has.
        manager: The engine, which knows its own accounts.

    Returns:
        The plan: created, left as it is, or skipped with the reason.
    """

    def plan(action: str, reason: str | None = None) -> RolePlan:
        return RolePlan(
            role.name,
            action,
            reason,
            dropped=role.dropped,
            has_password=role.password_hash is not None,
            login=role.login,
        )

    if isinstance(role, MysqlAccount):
        if manager.is_internal_user(role.user):
            return plan("skip", "The server's own account")
    elif not usable_name(role.name):
        return plan(
            "skip",
            "The server's own account"
            if role.name == "postgres" or role.name.startswith("pg_")
            else "A name Noust will not create an account with",
        )
    if role.name in existing:
        return plan("exists", "Already here: its password and attributes are left as they are")
    return plan("create")


def _kept_memberships(grants: list[MembershipSpec], made: set[str]) -> list[MembershipSpec]:
    """
    Keep the role-to-role grants that give nothing beyond the accounts made now.

    Args:
        grants: What the dump grants.
        made: The accounts made for this import.

    Returns:
        The grants between those accounts, and of the built-in roles that only read statistics.
    """
    return [
        grant
        for grant in grants
        if grant.member in made and (grant.role in made or grant.role in BUILTIN_ROLES_CARRIED)
    ]


def _account_warnings(plan: ClusterPlan, manager: BaseDatabaseManager) -> list[str]:
    """
    Say what making the accounts means, for the operator to accept.

    Args:
        plan: The plan with its accounts.
        manager: The engine.

    Returns:
        One sentence per thing worth knowing; none when no account would be made.
    """
    created = plan.to_create
    if not created:
        return []
    shown = ", ".join(created[:8]) + (f" and {len(created) - 8} more" if len(created) > 8 else "")
    warnings = [
        f"{len(created)} account(s) will be created on this server: {shown}. "
        "They keep the password the dump holds for them, so whoever has the dump's hashes can "
        "try them against this server."
    ]
    reduced = sorted({role.name for role in plan.roles if role.action == "create" and role.dropped})
    if reduced:
        power = (
            "administrative privileges, the grant option"
            if manager.engine_type == "mysql"
            else "superuser, creating roles, replication, bypassing row security"
        )
        warnings.append(
            "These accounts had more power at the source and are created without it "
            f"({power}): {', '.join(reduced)}."
        )
    locked = sorted(
        {role.name for role in plan.roles if role.action == "create" and not role.login}
    )
    if locked and manager.engine_type == "mysql":
        warnings.append(
            "These accounts are created locked, because the password the source had cannot be "
            f"carried in a text file or because they were locked there: {', '.join(locked)}."
        )
    return warnings
