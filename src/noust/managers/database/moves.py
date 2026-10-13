# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Move a database to another engine: another version, the host's or an instance, with its applications.

The one implementation behind ``noust db move`` and the major-version assistant
(:mod:`noust.managers.database.instance_migration`). It builds on what already
exists instead of reaching around it (rule 3): the dump is
:meth:`DatabaseService.dump`, the load is :meth:`DatabaseService.restore` (which
refuses a dump it cannot read before it touches anything), accounts are made
through :meth:`DatabaseService.create_user`, and each application's variables
are rewritten by :func:`~noust.managers.database.linking.change_app_env`, the
path a link and a password rotation take, so every application restarts behind
the same health gate.

What the order buys:

1. **The source is never touched.** It is dumped, and it stays as it is until
   the operator drops it. Whatever fails afterwards, the way back is the source
   and the applications' previous variables.
2. **Accounts come first.** A PostgreSQL archive names the roles that own its
   objects, so they exist on the target before the dump loads. An account whose
   password Noust keeps is made with the same password, so an application's
   connection string changes only in its host and port; one it does not know is
   made with a new password and named in the outcome.
3. **Applications are pointed last, one at a time.** An application that does
   not come up on the target gets its previous variables back, and so does every
   one changed before it. What was made on the target is removed again.
4. **Only a database Noust can rewrite is moved.** An application that reaches
   it through a variable Noust did not write is a blocker: moving the data
   from under it would break it silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from noust.core.database_exposures import ExposureStore
from noust.core.db_instance_store import DbInstanceStore
from noust.core.exceptions import (
    DatabaseError,
    DatabaseNotFoundError,
    NoustError,
    ValidationError,
)
from noust.core.store import App, Database
from noust.deployers.helpers.app_env import read_app_env
from noust.deployers.helpers.databases import _password_secret
from noust.managers.database.backups import DatabaseBackups
from noust.managers.database.base import BaseDatabaseManager
from noust.managers.database.linking import change_app_env, restore_app_env
from noust.managers.database.records import DatabaseLink
from noust.managers.database.service import LINK_PROFILE, AccessView, DatabaseService
from noust.managers.database.tls_links import check_link_tls

__all__ = ["DatabaseMover", "MoveOutcome", "MovePlan"]

#: Engines whose databases can be dumped and loaded into another server of the
#: same engine. A Redis snapshot is the whole instance and Noust does not
#: install one into a container, so Redis and Valkey are not moved this way.
MOVABLE = frozenset({"postgresql", "mysql", "mongodb"})

#: Engines whose dump format is the same across versions up to the next, so a
#: newer server loads an older one's and an older one refuses a newer one's.
_DOWNWARD = "A dump of a newer server does not load into an older one."


@dataclass
class MovePlan:
    """
    What moving a database would do, decided before anything is touched.

    Attributes:
        source: The engine key it leaves.
        target: The engine key it goes to.
        database: Its name.
        engine: The engine both speak (``postgresql``).
        source_version: The server it leaves.
        target_version: The server it goes to.
        size: How big it is, as the engine reports it.
        exists_on_target: Whether the target already has a database of that name.
        apps: The applications that will be pointed at the target and restarted.
        accounts: The accounts that will exist on the target.
        new_passwords: The accounts among them Noust does not know the password
            of: they are made with a new one.
        policies: The backup policies that will follow the database.
        blockers: What stops the move. Nothing is moved while any is listed.
        warnings: What the operator should know before going ahead.
    """

    source: str
    target: str
    database: str
    engine: str
    source_version: str | None = None
    target_version: str | None = None
    size: str | None = None
    exists_on_target: bool = False
    apps: list[str] = field(default_factory=list)
    accounts: list[str] = field(default_factory=list)
    new_passwords: list[str] = field(default_factory=list)
    policies: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "source": self.source,
            "target": self.target,
            "database": self.database,
            "engine": self.engine,
            "source_version": self.source_version,
            "target_version": self.target_version,
            "size": self.size,
            "exists_on_target": self.exists_on_target,
            "apps": list(self.apps),
            "accounts": list(self.accounts),
            "new_passwords": list(self.new_passwords),
            "policies": list(self.policies),
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class MoveOutcome:
    """
    A database moved.

    Attributes:
        source: The engine key it left.
        target: The engine key it is on.
        database: Its name.
        dump: The dump the move was made from, kept in the source's directory;
            None when only the applications were pointed at the target.
        safety_copy: What the target held, dumped before it was replaced.
        apps: The applications now pointed at the target.
        accounts: The accounts that exist on the target for it.
        new_passwords: The accounts among them made with a new password.
        policies: The backup policies that followed it.
        warnings: What did not go as planned and does not stop the move.
        source_dropped: Whether the source was dropped, after a last dump.
    """

    source: str
    target: str
    database: str
    dump: str | None = None
    safety_copy: str | None = None
    apps: list[str] = field(default_factory=list)
    accounts: list[str] = field(default_factory=list)
    new_passwords: list[str] = field(default_factory=list)
    policies: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_dropped: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "source": self.source,
            "target": self.target,
            "database": self.database,
            "dump": self.dump,
            "safety_copy": self.safety_copy,
            "apps": list(self.apps),
            "accounts": list(self.accounts),
            "new_passwords": list(self.new_passwords),
            "policies": list(self.policies),
            "warnings": list(self.warnings),
            "source_dropped": self.source_dropped,
        }


@dataclass
class _Account:
    """
    One account that has to exist on the target.

    Attributes:
        username: The account.
        host: Its host restriction (MySQL).
        profile: The access it has on the database.
        password: The password Noust keeps for it, or None.
    """

    username: str
    host: str = "localhost"
    profile: str = LINK_PROFILE
    password: str | None = None


@dataclass
class _Journal:
    """
    What a move has changed, so a failure can undo exactly that.

    Attributes:
        created_database: The target database was made by this move.
        created_accounts: Accounts this move made on the target.
        pointed: Applications changed so far, with the variables they had.
    """

    created_database: bool = False
    created_accounts: list[_Account] = field(default_factory=list)
    pointed: list[tuple[App, dict[str, str]]] = field(default_factory=list)


def _numbers(version: str | None) -> tuple[int, ...]:
    """
    Read the leading numbers of a version.

    Args:
        version: ``16.4``, ``8.0.40``, or None.

    Returns:
        The numbers; empty when there are none.
    """
    found = re.match(r"\d+(?:\.\d+)*", version or "")
    return tuple(int(part) for part in found.group().split(".")) if found else ()


def release_of(engine: str, version: str | None) -> tuple[int, ...]:
    """
    Name the release a version belongs to: the part that decides what loads where.

    Args:
        engine: ``postgresql``, ``mysql`` or ``mongodb``.
        version: A version string.

    Returns:
        PostgreSQL's major number; the first two numbers of the others
        (``8.4``, ``10.11``, ``7.0``).
    """
    numbers = _numbers(version)
    return numbers[:1] if engine == "postgresql" else numbers[:2]


class DatabaseMover:
    """
    Move a database between two engines Noust drives.

    Args:
        service: The database service, which finds both engines, dumps, loads
            and writes the applications' variables.
        backups: The backup policies; the service's by default.
    """

    def __init__(self, service: DatabaseService, backups: DatabaseBackups | None = None) -> None:
        self.service = service
        self._backups = backups

    @property
    def backups(self) -> DatabaseBackups:
        """The backup policies, which follow a database to its new engine."""
        if self._backups is None:
            self._backups = DatabaseBackups(self.service)
        return self._backups

    @property
    def logger(self) -> Any:
        """Where progress is reported."""
        return self.service.logger

    def _domain(self, app_id: int | None) -> str:
        """
        Name an application for a sentence.

        Args:
            app_id: Its store id.

        Returns:
            Its domain, or a stand-in for one that is gone.
        """
        app = self.service.store.get_app_by_id(app_id) if app_id is not None else None
        return app.domain if app is not None else f"application {app_id}"

    # -------------------------------------------------------------- planning

    def plan(
        self,
        source: str,
        database: str,
        target: str,
        *,
        replace: bool = False,
        carry: bool = True,
    ) -> MovePlan:
        """
        Decide what moving a database would do, changing nothing.

        Args:
            source: The engine it is on (``postgresql``, ``postgresql@pg16``).
            database: Its name.
            target: The engine it goes to.
            replace: Replace a database of that name the target already holds
                (a database moved back), after a safety copy.
            carry: Carry the data. False points the applications at a database
                the target already holds, which is the way back of a migration:
                nothing is dumped or loaded, so an older server is no obstacle.

        Returns:
            The plan; ``blockers`` says whether it can go ahead.

        Raises:
            DatabaseError: Either engine is unknown, not installed or not running.
            DatabaseNotFoundError: The database is not on the source.
        """
        origin = self.service.running(source)
        destination = self.service.running(target)
        database = origin.validate_database_name(database)
        plan = MovePlan(origin.ENGINE_NAME, destination.ENGINE_NAME, database, origin.engine_type)
        blockers, warnings = plan.blockers, plan.warnings

        if plan.source == plan.target:
            blockers.append("The source and the target are the same engine")
        if plan.engine != destination.engine_type:
            blockers.append(
                f"{origin.DISPLAY_NAME} data does not load into {destination.DISPLAY_NAME}"
            )
        if not origin.database_exists(database):
            raise DatabaseNotFoundError(
                f"{database} is not on {plan.source}",
                details=f"List its databases with: noust db list --engine {plan.source}",
            )
        plan.exists_on_target = destination.database_exists(database)
        plan.source_version = origin.get_version()
        plan.target_version = destination.get_version()
        if carry:
            if plan.exists_on_target and not replace:
                blockers.append(
                    f"{plan.target} already has a database called {database}: choose another "
                    "engine, or move over it with --replace (it is dumped first)"
                )
            if replace and not plan.exists_on_target:
                warnings.append(f"{database} is not on {plan.target}: it is made, not replaced")
            self._compare_versions(plan, origin, destination)
        elif not plan.exists_on_target:
            blockers.append(f"{plan.target} has no database called {database} to point at")

        self.inspect(plan, origin, carry=carry)
        self._check_tls(plan)
        return plan

    def _check_tls(self, plan: MovePlan) -> None:
        """
        Refuse a move that would take an application to an engine that cannot keep its TLS mode.

        Args:
            plan: The plan, whose blockers are added to.
        """
        instances = DbInstanceStore(self.service.store)
        for link in self.service.records.links(engine=plan.source, db_name=plan.database):
            if link.tls_mode is None:
                continue
            try:
                check_link_tls(plan.target, link.tls_mode, instances=instances)
            except NoustError as exc:
                plan.blockers.append(
                    f"{self._domain(link.app_id)} connects with TLS ({link.tls_mode}) and "
                    f"{plan.target} cannot keep that: {exc.message}"
                )

    def inspect(self, plan: MovePlan, origin: BaseDatabaseManager, *, carry: bool = True) -> None:
        """
        Read what a database's move depends on at its source, and note what stops it.

        The part of a plan that needs no target, so a whole engine can be
        checked before the instance its databases will go to exists.

        Args:
            plan: The plan, whose applications, accounts, policies, blockers and
                warnings are filled in.
            origin: The engine the database is on.
            carry: Whether the data is carried (see :meth:`plan`).
        """
        database, blockers, warnings = plan.database, plan.blockers, plan.warnings
        if carry and plan.engine not in MOVABLE:
            blockers.append(
                f"Noust moves PostgreSQL, MySQL, MariaDB and MongoDB databases, not "
                f"{origin.DISPLAY_NAME}: a snapshot is the whole instance"
            )
        if ExposureStore(self.service.store).get(plan.source, database) is not None:
            blockers.append(
                f"{database} is reachable from the network: make it private first "
                f"(noust db public close {database} --engine {plan.source})"
            )

        links = self.service.records.links(engine=plan.source, db_name=database)
        written: set[str] = set()
        for link in links:
            domain = self._domain(link.app_id)
            if link.username is None:
                blockers.append(
                    f"{domain} reaches it through a variable Noust did not write: unlink "
                    "the detected link and link it again with Noust, then move"
                )
            else:
                written.add(domain)
        row = self.service.store.get_database(database, plan.source)
        owner = self._domain(row.app_id) if row is not None and row.app_id is not None else None
        if owner and owner not in written and not any(owner in text for text in blockers):
            blockers.append(
                f"{owner} owns it without a link Noust wrote: 'noust db link' it first, so "
                "its variables can be pointed at the target"
            )
        plan.apps = sorted(written)

        signing_in = {link.username for link in links if link.username}
        if carry:
            for account in self._accounts(plan.source, database, links):
                plan.accounts.append(account.username)
                if account.password is None:
                    plan.new_passwords.append(account.username)
            if plan.new_passwords:
                warnings.append(
                    "Noust does not know the password of "
                    + ", ".join(plan.new_passwords)
                    + ": the target gets a new one, which Noust keeps. Anything outside Noust "
                    "that signs in as it needs the new password"
                )
            for name in sorted(signing_in & set(plan.new_passwords)):
                blockers.append(
                    f"{name} signs an application in and Noust does not know its password: "
                    f"rotate it first (noust db user-password {name} --engine {plan.source})"
                )
        else:
            for name in sorted(signing_in):
                if self._known_password(plan.target, name) is None:
                    blockers.append(
                        f"Noust does not know the password of {name} on {plan.target}, which "
                        "the applications would sign in with"
                    )

        policy = self.backups.get_policy(plan.source, database).policy
        if policy is not None:
            plan.policies.append(policy.schedule)
        try:
            plan.size = self.service.get(plan.source, database).size
        except DatabaseError:
            plan.size = None
        if carry:
            warnings.append(
                f"{database} is dumped and loaded again: writes made to it while that runs "
                "are not carried over. Stop what writes to it for the length of the move"
                + (", or accept losing those writes." if plan.apps else ".")
            )

    def _known_password(self, engine: str, username: str) -> str | None:
        """
        Read the password Noust keeps for an account.

        Args:
            engine: The engine key the account is on.
            username: The account.

        Returns:
            The password, or None when Noust does not know it.
        """
        return self.service.secrets.read(_password_secret(engine, username))

    @staticmethod
    def _compare_versions(
        plan: MovePlan, origin: BaseDatabaseManager, destination: BaseDatabaseManager
    ) -> None:
        """
        Refuse a move to an older server.

        Args:
            plan: The plan, whose blockers and warnings are added to.
            origin: The source engine.
            destination: The target engine.
        """
        try:
            same_server = origin.installed_flavour() == destination.installed_flavour()
        except (DatabaseError, OSError):
            same_server = True
        if not same_server:
            plan.warnings.append(
                f"{origin.DISPLAY_NAME} and the target are different servers (MySQL and "
                "MariaDB): the dump loads, but check what the application relies on"
            )
            return
        before = release_of(plan.engine, plan.source_version)
        after = release_of(plan.engine, plan.target_version)
        if before and after and after < before:
            plan.blockers.append(
                f"The target ({plan.target_version}) is older than the source "
                f"({plan.source_version}). {_DOWNWARD}"
            )

    def _accounts(self, source: str, database: str, links: list[DatabaseLink]) -> list[_Account]:
        """
        List the accounts a database needs on its new engine.

        Args:
            source: The engine it leaves.
            database: The database.
            links: Its links, whose accounts sign applications in.

        Returns:
            The accounts with access, the engine's own and Noust's excluded,
            each with the password Noust keeps for it, if any.
        """
        found: dict[tuple[str, str], _Account] = {}
        views: list[AccessView] = self.service.access(source, database)
        for view in views:
            if view.internal:
                continue
            found[(view.username, view.host)] = _Account(
                view.username, view.host, view.profile if view.profile != "custom" else "read_write"
            )
        for link in links:
            if link.username and not any(key[0] == link.username for key in found):
                found[(link.username, "localhost")] = _Account(link.username)
        for account in found.values():
            account.password = self.service.secrets.read(_password_secret(source, account.username))
        return sorted(found.values(), key=lambda item: (item.username, item.host))

    # ------------------------------------------------------------------ move

    def move(
        self,
        source: str,
        database: str,
        target: str,
        *,
        replace: bool = False,
        drop_source: bool = False,
        carry: bool = True,
    ) -> MoveOutcome:
        """
        Move a database, its accounts, its applications and its backup policy.

        Args:
            source: The engine it is on.
            database: Its name.
            target: The engine it goes to.
            replace: Replace a database of that name the target holds, after a
                safety copy.
            drop_source: Drop the database from the source once the move is
                done, after a last dump. It stays otherwise, untouched, for the
                way back.
            carry: Carry the data. False only points the applications and the
                records at a database the target already holds: the way back
                to an older server, which a dump cannot take.

        Returns:
            What was done.

        Raises:
            ValidationError: The plan has blockers; nothing was changed.
            DatabaseError: The dump or the load failed; the target is as it was.
            DeploymentError: An application did not come up on the target; every
                change was undone and the error carries the gate's evidence.
        """
        plan = self.plan(source, database, target, replace=replace, carry=carry)
        if plan.blockers:
            self.service.audit(
                "db.move", f"{plan.source}/{plan.database}", "denied", to=plan.target
            )
            raise ValidationError(
                f"{plan.database} cannot be moved to {plan.target}",
                details="\n".join(f"- {blocker}" for blocker in plan.blockers),
            )
        origin = self.service.running(plan.source)
        destination = self.service.running(plan.target)
        label = f"{plan.source}/{plan.database}"
        journal = _Journal()
        warnings: list[str] = []
        self.logger.info(f"Moving {plan.database} from {plan.source} to {plan.target}")
        dump: Path | None = None
        safety: Path | None = None
        try:
            if carry:
                dump = self._dump(plan)
                accounts = self._accounts(
                    plan.source,
                    plan.database,
                    self.service.records.links(engine=plan.source, db_name=plan.database),
                )
                self._make_accounts(destination, accounts, journal)
                safety = self._load(plan, dump, journal, replace=replace)
                self._grant(plan, accounts)
            links = self.service.records.links(engine=plan.source, db_name=plan.database)
            self._point(plan, links, journal)
        except NoustError as exc:
            problems = self._undo(plan, destination, journal)
            self.service.audit("db.move", label, "failure", to=plan.target)
            if problems:
                exc.details = "\n".join(
                    part
                    for part in (
                        exc.details,
                        "These could not be undone and need a look: " + "; ".join(problems),
                    )
                    if part
                )
            raise

        self._record(plan, links, origin, destination)
        moved_policies = self._follow_policy(plan, warnings)
        dropped = False
        if drop_source:
            self.service.drop(plan.source, plan.database, force=True, keep_backup=True, unlink=True)
            dropped = True
        self.service.audit(
            "db.move",
            label,
            to=plan.target,
            apps=",".join(plan.apps) or None,
            dropped=dropped,
        )
        return MoveOutcome(
            source=plan.source,
            target=plan.target,
            database=plan.database,
            dump=dump.name if dump else None,
            safety_copy=safety.name if safety else None,
            apps=list(plan.apps),
            accounts=list(plan.accounts),
            new_passwords=list(plan.new_passwords),
            policies=moved_policies,
            warnings=warnings,
            source_dropped=dropped,
        )

    def _dump(self, plan: MovePlan) -> Path:
        """
        Dump the database on the source.

        Args:
            plan: The move.

        Returns:
            The dump.
        """
        self.logger.substep(f"Dumping {plan.database} on {plan.source}")
        return self.service.dump(plan.source, plan.database).path

    def _make_accounts(
        self, destination: BaseDatabaseManager, accounts: list[_Account], journal: _Journal
    ) -> None:
        """
        Make the accounts the database needs on the target.

        Args:
            destination: The target engine.
            accounts: What to make.
            journal: Where each account made is noted for an undo.
        """
        for account in accounts:
            if destination.user_exists(account.username, host=account.host):
                if account.password is not None:
                    self.logger.warning(
                        f"{account.username} exists on {destination.ENGINE_NAME} already: "
                        "its password is left as it is"
                    )
                continue
            self.logger.substep(
                f"Making the account {account.username} on {destination.ENGINE_NAME}"
            )
            self.service.create_user(
                destination.ENGINE_NAME,
                account.username,
                password=account.password,
                host=account.host,
            )
            journal.created_accounts.append(account)

    def _load(self, plan: MovePlan, dump: Path, journal: _Journal, *, replace: bool) -> Path | None:
        """
        Load the dump into the target.

        Args:
            plan: The move.
            dump: The dump to load.
            journal: Notes that the database was made here.
            replace: Replace one the target holds.

        Returns:
            The safety copy of what was replaced, if any.
        """
        self.logger.substep(f"Loading {plan.database} into {plan.target}")
        existed = plan.exists_on_target
        outcome = self.service.restore(
            plan.target,
            plan.database,
            dump,
            drop_existing=replace and existed,
            safety_backup=replace and existed,
        )
        journal.created_database = not existed
        return outcome.safety_copy

    def _grant(self, plan: MovePlan, accounts: list[_Account]) -> None:
        """
        Give each account its access on the target database.

        Args:
            plan: The move.
            accounts: The accounts, with the profile each had.
        """
        for account in accounts:
            self.service.set_profile(
                plan.target, plan.database, account.username, account.profile, host=account.host
            )

    def _point(self, plan: MovePlan, links: list[DatabaseLink], journal: _Journal) -> None:
        """
        Point each linked application at the target, behind its health gate.

        Args:
            plan: The move.
            links: The links to rewrite.
            journal: Where each application changed is noted, with what it had.

        Raises:
            DeploymentError: An application did not come up; the one that failed
                has its previous variables back, the others are undone by the
                caller.
        """
        for link in links:
            app = self.service.store.get_app_by_id(link.app_id)
            if app is None or link.username is None:
                continue
            moved = DatabaseLink(
                app_id=link.app_id,
                engine=plan.target,
                db_name=link.db_name,
                username=link.username,
                env_var=link.env_var,
                extra_vars=link.extra_vars,
                tls_mode=link.tls_mode,
            )
            values = self.service.link_variables(moved)
            self.logger.substep(f"Pointing {app.domain} at {plan.target}")

            def edit(env: dict[str, str], values: dict[str, str] = values) -> dict[str, str]:
                env.update(values)
                return env

            before = read_app_env(app)
            try:
                change = change_app_env(
                    app,
                    edit,
                    store=self.service.store,
                    secret_names=values,
                    restart=True,
                    restore_on_failure=False,
                    operation="database move",
                    log=self.logger,
                )
            except (NoustError, OSError):
                # Its file was written before the gate failed: it is undone with the rest.
                journal.pointed.append((app, before))
                raise
            if change.changed:
                journal.pointed.append((app, change.previous))

    def _record(
        self,
        plan: MovePlan,
        links: list[DatabaseLink],
        origin: BaseDatabaseManager,
        destination: BaseDatabaseManager,
    ) -> None:
        """
        Write the move into the store: the database, its links and its owner.

        Args:
            plan: The move.
            links: The links that were rewritten.
            origin: The source engine.
            destination: The target engine.
        """
        store = self.service.store
        records = self.service.records
        old = store.get_database(plan.database, plan.source)
        new = store.get_database(plan.database, plan.target)
        if new is None:
            store.create_database(
                Database(
                    app_id=old.app_id if old else None,
                    name=plan.database,
                    engine=plan.target,
                    host="localhost",
                    port=destination.server_port(),
                    username=old.username if old else None,
                    encoding=old.encoding if old else None,
                    project_id=old.project_id if old else None,
                    environment_id=old.environment_id if old else None,
                )
            )
        elif old is not None and new.app_id is None:
            new.app_id = old.app_id
            new.username = new.username or old.username
            store.update_database(new)
        if old is not None and old.app_id is not None:
            # The applications' backups include the database they use, and only that one.
            old.app_id = None
            store.update_database(old)
        for link in links:
            records.delete_link(link.app_id, plan.source, link.db_name)
            records.save_link(
                DatabaseLink(
                    app_id=link.app_id,
                    engine=plan.target,
                    db_name=link.db_name,
                    username=link.username,
                    env_var=link.env_var,
                    extra_vars=link.extra_vars,
                    tls_mode=link.tls_mode,
                )
            )

    def _follow_policy(self, plan: MovePlan, warnings: list[str]) -> list[str]:
        """
        Give the database's backup policy to its new engine.

        Args:
            plan: The move.
            warnings: Where a policy that could not follow is reported.

        Returns:
            The schedules that followed.
        """
        policy = self.backups.get_policy(plan.source, plan.database).policy
        if policy is None:
            return []
        try:
            self.backups.set_policy(
                plan.target,
                plan.database,
                schedule=policy.schedule,
                retention_count=policy.retention_count,
                retention_days=policy.retention_days,
                retention_gb=(
                    None
                    if policy.retention_bytes is None
                    else round(policy.retention_bytes / 1024**3, 3)
                ),
                destinations=list(policy.destinations),
                dump_format=policy.dump_format,
                verify_restore=policy.verify_restore,
                enabled=policy.enabled,
            )
        except NoustError as exc:
            warnings.append(
                f"The backup policy did not follow {plan.database} to {plan.target}: "
                f"{exc.message}. Set it again with 'noust db backup-policy'; the one on "
                f"{plan.source} is still there"
            )
            return []
        self.backups.remove_policy(plan.source, plan.database)
        return [policy.schedule]

    def _undo(
        self, plan: MovePlan, destination: BaseDatabaseManager, journal: _Journal
    ) -> list[str]:
        """
        Put back what a move that failed had changed.

        Applications first, because they point at what is removed next. The
        source was never touched, so it needs nothing.

        Args:
            plan: The move.
            destination: The target engine.
            journal: What the move changed.

        Returns:
            What could not be undone, as sentences; empty when all went.
        """
        problems: list[str] = []
        if journal.pointed:
            self.logger.warning("Putting the applications' variables back")
        for app, previous in reversed(journal.pointed):
            if not restore_app_env(
                app, previous, store=self.service.store, restart=True, log=self.logger
            ):
                problems.append(f"{app.domain} did not come back on its previous variables")
        if journal.created_database:
            try:
                destination.drop_database(plan.database, force=True)
                destination.drop_read_only_account(plan.database)
            except DatabaseError as exc:
                problems.append(f"{plan.database} on {plan.target}: {exc.message}")
        for account in reversed(journal.created_accounts):
            try:
                self.service.drop_user(plan.target, account.username, host=account.host)
            except DatabaseError as exc:
                problems.append(f"the account {account.username} on {plan.target}: {exc.message}")
        return problems
