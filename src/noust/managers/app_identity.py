# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Each application its own system account (Noust 3.2, spec section 4.2).

Until 3.2 every application ran as the one configured service account
(``www-data``), so a compromised application could read and write every other
application's tree. From 3.2 an application Noust creates runs as its own
account, ``noust-app-<name>``: a system account with its own group, no home
and no login shell, owning the application's tree, its ``.env`` and its
caches. Its unit says ``User=`` that account, and a PHP application's pool
runs as it (one pool per account). The web server still reads what it serves:
a deployed tree is world-readable but for its ``.env`` files.

**One answer to "as whom".** :func:`service_account` is the only place that
says which account an application runs as: its own when the store records one
(``apps.identity``), the configured service account otherwise. Everything that
hands files to an application or writes the account into a unit or a pool
asks it, so an application without an identity behaves exactly as before 3.2.

**Existing applications** keep the shared account until an operator moves
them, with ``noust app identity migrate DOMAIN`` (or its endpoint):
:func:`migrate` creates the account, hands it the files the shared account
owned, rewrites the unit or the pool, and restarts the application behind its
health gate. When it does not answer, everything is put back exactly - the
files' owners (only those it changed), the unit or the pool, the store - and
restarted as it was.

**What does not get one.** A Docker Compose stack (its processes are the
containers'), a monorepo (several units, kept on the shared account for now)
and a site with nothing running (a static site, which the web server serves).
"""

from __future__ import annotations

import grp
import hashlib
import os
import pwd
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from noust.core.config import Config
from noust.core.exceptions import DeploymentError, NoustError, ValidationError
from noust.core.logger import Logger
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import App, NoustStore, get_store

if TYPE_CHECKING:
    from noust.deployers.helpers.health_gate import HealthGate
    from noust.managers.service_manager import ServiceManager

#: Every application account starts with this, which is also what marks one
#: as Noust's to remove.
ACCOUNT_PREFIX = "noust-app-"

#: The longest account name useradd and systemd accept.
MAX_ACCOUNT_LENGTH = 32

#: Application types whose processes are not the application's account's.
EXCLUDED_TYPES = frozenset({"docker-compose", "monorepo", "static"})

#: The PHP-FPM type, whose account is its pool's, not a unit's.
PHP_TYPE = "php-fpm"

#: Deadline of each account and ownership command.
_ACCOUNT_TIMEOUT = 60
_CHOWN_TIMEOUT = 600


def running_as_root() -> bool:
    """
    Tell whether this process can create accounts and hand files to them.

    Returns:
        True for root.
    """
    return os.geteuid() == 0


def account_name(app_name: str) -> str:
    """
    Name the account an application runs as.

    Args:
        app_name: The application's name (its directory's).

    Returns:
        ``noust-app-<name>``; a long name is shortened and given a hash of
        the whole, so it stays within 32 characters and unique.
    """
    name = f"{ACCOUNT_PREFIX}{app_name}"
    if len(name) <= MAX_ACCOUNT_LENGTH:
        return name
    digest = hashlib.sha256(app_name.encode()).hexdigest()[:6]
    keep = MAX_ACCOUNT_LENGTH - len(ACCOUNT_PREFIX) - len(digest) - 1
    return f"{ACCOUNT_PREFIX}{app_name[:keep].rstrip('-')}-{digest}"


def service_account(app: App | None, config: Config | None = None) -> tuple[str, str]:
    """
    Say which account and group an application runs as.

    Args:
        app: Its row; None for one not registered yet.
        config: Where the shared service account comes from.

    Returns:
        ``(account, group)``: its own when it has one, the configured
        ``service_user`` and ``service_group`` otherwise.
    """
    if app is not None and app.identity:
        return app.identity, app.identity
    config = config or Config()
    return config.service_user, config.service_group


def service_account_for(domain: str, config: Config | None = None) -> tuple[str, str]:
    """
    Say which account an application runs as, by its domain.

    Args:
        domain: The application.
        config: Where the shared service account comes from.

    Returns:
        What :func:`service_account` says for its row.
    """
    return service_account(get_store().get_app(domain) if domain else None, config)


def ineligible(app: App) -> str | None:
    """
    Say why an application cannot run as its own account.

    Args:
        app: Its row.

    Returns:
        The reason, or None when it can.
    """
    kind = app.app_type or ""
    if kind == "docker-compose":
        return "its processes run in containers, as the users their images say"
    if kind == "monorepo":
        return "a monorepo runs several units, which stay on the shared account for now"
    if kind == "static" or app.is_static:
        return "nothing runs for it: the web server serves its files"
    return None


def _nologin() -> str:
    """
    Find the shell that refuses a login.

    Returns:
        Its path.
    """
    return next(
        (
            candidate
            for candidate in ("/usr/sbin/nologin", "/sbin/nologin")
            if Path(candidate).exists()
        ),
        shutil.which("nologin") or "/usr/sbin/nologin",
    )


def ensure_account(account: str, runner: CommandRunner, *, domain: str) -> None:
    """
    Create an application's account when it does not exist.

    A system account with its own group, no home and no login shell.

    Args:
        account: The account.
        runner: The runner.
        domain: The application, for the error.

    Raises:
        DeploymentError: ``useradd`` failed, with its own words.
    """
    probe = runner.run(["getent", "passwd", account], timeout=_ACCOUNT_TIMEOUT)
    if probe.success and probe.stdout.strip():
        return
    nologin = _nologin()
    created = runner.run(
        [
            "useradd",
            "--system",
            "--user-group",
            "--no-create-home",
            "--home-dir",
            "/nonexistent",
            "--shell",
            nologin,
            "--comment",
            f"Noust application {domain}",
            account,
        ],
        timeout=_ACCOUNT_TIMEOUT,
    )
    # 9: the name is taken, which means a deploy that failed created it.
    if created.success or created.exit_code == 9:
        return
    raise DeploymentError(
        f"Could not create the account {account} that {domain} runs as",
        details=(
            f"{(created.stderr or created.stdout).strip()}\n\n"
            f"Create it by hand and deploy again: useradd --system --user-group "
            f"--no-create-home --home-dir /nonexistent --shell {nologin} {account}"
        ),
    )


def remove_account(account: str | None, runner: CommandRunner, logger: Logger) -> bool:
    """
    Remove an application's account, once the application is gone.

    Only an account Noust names (``noust-app-*``); a failure is reported and
    left, since the application itself is already removed.

    Args:
        account: What the store recorded; None for none.
        runner: The runner.
        logger: Where a failure is reported.

    Returns:
        Whether an account was removed.
    """
    if not account or not account.startswith(ACCOUNT_PREFIX):
        return False
    removed = runner.run(["userdel", account], timeout=_ACCOUNT_TIMEOUT)
    # 6: it did not exist, which is the state wanted.
    if removed.success or removed.exit_code == 6:
        return removed.success
    logger.warning(
        f"Could not remove the account {account}: {(removed.stderr or removed.stdout).strip()}. "
        f"Remove it by hand: userdel {account}"
    )
    return False


def _audit(domain: str, outcome: str = "ok", **details: Any) -> None:
    """
    Record a change to an application's account in the audit trail.

    Args:
        domain: The application.
        outcome: ``ok`` or ``failure``.
        **details: What changed.
    """
    from noust.core import audit

    audit.record("apps.identity", target=f"app:{domain}", outcome=outcome, details=details)


def adopt_new_app(
    domain: str,
    *,
    app_name: str,
    app_type: str,
    is_static: bool,
    runner: CommandRunner,
    store: NoustStore,
    new: bool,
) -> str | None:
    """
    Give an application created now its own account; check one that has one still does.

    Called by a deploy once the application's row exists and before anything
    is handed to its account. An application deployed before 3.2 keeps the
    shared account through any redeploy: only :func:`migrate` moves it. One
    that has an account gets it created again when it is missing (its store
    restored on another server).

    Args:
        domain: The application.
        app_name: Its name, which its account is named after.
        app_type: Its type.
        is_static: Whether nothing runs for it.
        runner: The runner.
        store: The store.
        new: Whether this is its first deployment.

    Returns:
        The account it runs as when it has its own, else None.

    Raises:
        DeploymentError: The account could not be created.
    """
    app = store.get_app(domain)
    if app is None:
        return None
    if app.identity:
        if running_as_root():
            ensure_account(app.identity, runner, domain=domain)
        return app.identity
    if not new or not running_as_root():
        return None
    if ineligible(App(domain=domain, app_type=app_type, is_static=is_static)) is not None:
        return None
    account = account_name(app_name)
    ensure_account(account, runner, domain=domain)
    store.set_app_identity(domain, account)
    _audit(domain, action="create", account=account)
    return account


# ---------------------------------------------------------------------------
# Moving an existing application to its own account
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdentityMigration:
    """
    What moving an application to its own account did.

    Attributes:
        domain: The application.
        account: The account it runs as now.
        previous: The account and group it ran as.
        units: The units rewritten (none for a PHP pool).
        pool: The PHP-FPM pool rewritten, if it has one.
    """

    domain: str
    account: str
    previous: tuple[str, str]
    units: tuple[str, ...] = ()
    pool: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Return it as plain data, for JSON.

        Returns:
            Every field.
        """
        return {
            "domain": self.domain,
            "account": self.account,
            "previous": f"{self.previous[0]}:{self.previous[1]}",
            "units": list(self.units),
            "pool": self.pool,
        }


def _owner_spec(user: str, group: str) -> str:
    """
    Name an owner for ``chown --from``, numerically when the account is known.

    Args:
        user: The account.
        group: Its group.

    Returns:
        ``uid:gid``, or the names when the system does not know them.
    """
    try:
        uid = pwd.getpwnam(user).pw_uid
        gid = grp.getgrnam(group).gr_gid
    except KeyError:
        return f"{user}:{group}"
    return f"{uid}:{gid}"


def owned_paths(app: App) -> list[Path]:
    """
    List what an application's account owns: its tree, its build cache, its pool's files.

    Args:
        app: Its row.

    Returns:
        The paths that exist.
    """
    from noust.deployers.helpers.layout import app_root
    from noust.deployers.helpers.php_fpm import pool_tmp_dir
    from noust.deployers.helpers.sandbox import cache_dir_for

    root = app_root(app)
    candidates = [root, cache_dir_for(root.name)]
    if (app.app_type or "") == PHP_TYPE:
        candidates.append(pool_tmp_dir(root))
    return [path for path in candidates if path.is_dir() and not path.is_symlink()]


def _hand_over(paths: list[Path], *, owner: str, to: str, runner: CommandRunner) -> list[str]:
    """
    Give what one owner has under some paths to another, and nothing else.

    ``chown --from`` changes only the files the old owner had, so what the
    application did not own (a release's repository cache, root's) keeps its
    owner, and going back with the owners swapped restores exactly what
    changed. ``-R`` does not follow links.

    Args:
        paths: The trees.
        owner: ``user:group`` the files must have to change.
        to: ``user:group`` they get.
        runner: The runner.

    Returns:
        The failures, each with chown's own words.
    """
    failures = []
    for path in paths:
        result = runner.run(
            ["chown", "-R", f"--from={owner}", to, str(path)], timeout=_CHOWN_TIMEOUT
        )
        if not result.success:
            failures.append(f"{path}: {(result.stderr or result.stdout).strip()}")
    return failures


def _known(store: NoustStore, domain: str) -> App:
    """
    Return an application's row, refusing an unknown one.

    Args:
        store: The store.
        domain: The application.

    Returns:
        The row.

    Raises:
        ValidationError: Nothing is deployed there.
    """
    app = store.get_app(domain)
    if app is None:
        raise ValidationError(
            f"Application not found: {domain}", details="See the applications with: noust list"
        )
    return app


def refuse_migration(app: App) -> None:
    """
    Refuse to move an application that cannot or need not move.

    Args:
        app: Its row.

    Raises:
        ValidationError: It already has its own account, cannot have one,
            runs in zero-downtime mode, or this process is not root.
    """
    if app.identity:
        raise ValidationError(
            f"{app.domain} already runs as its own account, {app.identity}",
            details="Nothing to migrate.",
        )
    reason = ineligible(app)
    if reason is not None:
        raise ValidationError(f"{app.domain} cannot run as its own account: {reason}")
    if app.zero_downtime:
        raise ValidationError(
            f"{app.domain} runs in zero-downtime mode, whose two instances are not moved yet",
            details=f"Turn it off, migrate, and turn it on again: noust app zero-downtime "
            f"{app.domain} --off; noust app identity migrate {app.domain}",
        )
    if not running_as_root():
        raise ValidationError(
            "Moving an application to its own account needs root",
            details="Only root creates accounts and hands files to them. Run it as root.",
        )


def migrate(
    domain: str,
    *,
    actor: str,
    logger: Logger | None = None,
    runner: CommandRunner | None = None,
    store: NoustStore | None = None,
    services: ServiceManager | None = None,
    gate_for: Callable[[App, NoustStore, Logger], HealthGate] | None = None,
) -> IdentityMigration:
    """
    Move an existing application to its own account, behind its health gate.

    Args:
        domain: The application.
        actor: Who asked, for the audit trail.
        logger: Where the steps are reported.
        runner: The runner.
        store: The store.
        services: The service manager.
        gate_for: Builds the application's health gate from its row; the
            store-based one activations use by default.

    Returns:
        What was done.

    Raises:
        ValidationError: The application cannot or need not move.
        DeploymentError: A step failed, or it did not answer with its own
            account; everything is back as it was, and the error carries the
            probes.
        AppBusyError: Another operation is running on the application.
    """
    from noust.central import require_server_role
    from noust.core.applock import app_lock

    require_server_role("Applications")
    store = store or get_store()
    runner = runner or get_runner()
    log = logger or Logger()
    app = _known(store, domain)
    refuse_migration(app)
    with app_lock(domain, "identity migration"):
        return _migrate(
            _known(store, domain),
            actor=actor,
            log=log,
            runner=runner,
            store=store,
            services=services,
            gate_for=gate_for,
        )


def _migrate(
    app: App,
    *,
    actor: str,
    log: Logger,
    runner: CommandRunner,
    store: NoustStore,
    services: ServiceManager | None,
    gate_for: Callable[[App, NoustStore, Logger], HealthGate] | None,
) -> IdentityMigration:
    """
    Move an application holding its lock; see :func:`migrate`.

    Args:
        app: Its row.
        actor: Who asked.
        log: Where the steps are reported.
        runner: The runner.
        store: The store.
        services: The service manager.
        gate_for: Builds its health gate.

    Returns:
        What was done.

    Raises:
        DeploymentError: A step failed or the gate did not pass; put back.
    """
    from noust.deployers.lifecycle import health_gate_for
    from noust.managers.service_manager import ServiceManager

    domain = app.domain
    services = services or ServiceManager()
    gate_builder = gate_for or (lambda row, st, lg: health_gate_for(row, st, lg))
    previous = service_account(app)
    account = account_name(Path(app.app_path).name or domain)
    old, new = _owner_spec(*previous), f"{account}:{account}"
    log.info(f"Moving {domain} from {previous[0]} to its own account, {account}")
    ensure_account(account, runner, domain=domain)

    undo: list[tuple[str, Callable[[], None]]] = []
    units: tuple[str, ...] = ()
    pool: str | None = None
    # Whether what runs was touched (a pool rewrite reloads FPM; a unit's
    # account applies at its restart): only then is it restarted as it was.
    touched = False
    try:
        trees = owned_paths(app)
        failures = _hand_over(trees, owner=old, to=new, runner=runner)
        new_ids = _owner_spec(account, account)
        undo.append(
            (
                "the files' owners",
                lambda: _raise_on(
                    _hand_over(
                        trees, owner=new_ids, to=f"{previous[0]}:{previous[1]}", runner=runner
                    )
                ),
            )
        )
        if failures:
            raise DeploymentError(
                f"The files of {domain} could not be handed to {account}",
                details="\n".join(failures),
            )
        log.substep(f"Files of {previous[0]} handed to {account}: {', '.join(map(str, trees))}")

        store.set_app_identity(domain, account)
        undo.append(("the store", lambda: _forget_identity(store, domain)))
        moved = _known(store, domain)

        if (app.app_type or "") == PHP_TYPE:
            touched = True
            pool, before = _rewrite_pool(moved, log)
            undo.append(("the PHP-FPM pool", lambda: _restore_pool(Path(pool or ""), before, log)))
            log.substep(f"Pool {pool} runs as {account}")
        else:
            units = tuple(services.serving_units(app)) or (Path(app.app_path).name,)
            for unit in units:
                body = services.set_unit_account(unit, account, account)
                undo.append((f"the unit {unit}", _restore_unit(services, unit, body)))
            log.substep(f"{', '.join(units)} run as {account}")
        _record_service_accounts(store, units, account, account)
        undo.append(("the service rows", lambda: _record_service_accounts(store, units, *previous)))

        touched = True
        healthy, evidence = gate_builder(moved, store, log).restart_and_probe()
        if not healthy:
            raise DeploymentError(f"{domain} did not answer running as {account}", details=evidence)
    except (NoustError, OSError) as exc:
        problems = _put_back(undo, log)
        state = "was not restarted"
        if touched:
            try:
                restored, _ = gate_builder(_known(store, domain), store, log).restart_and_probe()
            except NoustError as again:
                log.error(f"{domain} could not be restarted as it was: {again}")
                restored = False
            state = "answering again" if restored else "back, but it is not answering either"
        message = exc.message if isinstance(exc, NoustError) else str(exc)
        details = exc.details if isinstance(exc, NoustError) else None
        _audit(domain, "failure", action="migrate", account=account, by=actor, error=message)
        put_back = (
            "Everything was put back as it was"
            if not problems
            else "Some of it could not be put back:\n" + "\n".join(problems)
        )
        raise DeploymentError(
            f"{domain} was not moved to its own account: {message}. It runs as "
            f"{previous[0]}, as before, and {state}",
            details="\n\n".join(part for part in (details, put_back) if part),
        ) from exc

    _audit(
        domain,
        action="migrate",
        account=account,
        previous=f"{previous[0]}:{previous[1]}",
        by=actor,
    )
    log.success(f"{domain} runs as its own account, {account}")
    return IdentityMigration(domain, account, previous, units, pool)


def _raise_on(failures: list[str]) -> None:
    """
    Turn hand-over failures into an error, for an undo step.

    Args:
        failures: What :func:`_hand_over` returned.

    Raises:
        DeploymentError: There was at least one.
    """
    if failures:
        raise DeploymentError("Some files could not be handed back", details="\n".join(failures))


def _forget_identity(store: NoustStore, domain: str) -> None:
    """
    Put an application back on the shared account in the store.

    Args:
        store: The store.
        domain: The application.
    """
    store.set_app_identity(domain, None)


def _restore_unit(services: ServiceManager, unit: str, body: str) -> Callable[[], None]:
    """
    Build the undo of a unit's account change.

    Args:
        services: The service manager.
        unit: The unit.
        body: Its body before the change.

    Returns:
        What puts it back.
    """

    def restore() -> None:
        services.update_config(unit, body)

    return restore


def _rewrite_pool(app: App, log: Logger) -> tuple[str, str]:
    """
    Write a PHP application's pool again for the account its row says.

    Args:
        app: Its row, with its account.
        log: Where FPM's reload is reported.

    Returns:
        The pool file and what it said before.
    """
    from noust.deployers.php_fpm import rewrite_pool

    path, before = rewrite_pool(app, logger=log)
    return str(path), before


def _restore_pool(path: Path, before: str, log: Logger) -> None:
    """
    Put a PHP application's pool back as it was.

    Args:
        path: The pool file.
        before: What it said.
        log: Where FPM's reload is reported.
    """
    from noust.deployers.php_fpm import fpm_service

    fpm_service(logger=log).install_pool(path, before)


def _record_service_accounts(
    store: NoustStore, units: tuple[str, ...], user: str, group: str
) -> None:
    """
    Record in the services table the account units run as.

    Args:
        store: The store.
        units: The units.
        user: The account.
        group: Its group.
    """
    for unit in units:
        row = store.get_service(unit)
        if row is not None:
            row.user, row.group = user, group
            store.update_service(row)


def _put_back(undo: list[tuple[str, Callable[[], None]]], log: Logger) -> list[str]:
    """
    Undo what a migration did, newest first, carrying on past a failure.

    Args:
        undo: What each step registered to put itself back.
        log: Where a failure is reported.

    Returns:
        What could not be put back, each with why.
    """
    problems: list[str] = []
    for what, step in reversed(undo):
        try:
            step()
        except (NoustError, OSError) as exc:
            log.error(f"Could not put back {what}: {exc}")
            problems.append(f"{what}: {exc}")
    return problems


def status(domain: str, *, store: NoustStore | None = None) -> dict[str, Any]:
    """
    Describe the account an application runs as, for the CLI and the console.

    Args:
        domain: The application.
        store: The store.

    Returns:
        ``domain``, ``account``, ``group``, ``own`` (whether it is its own),
        ``eligible`` and ``reason`` (why not, when it cannot have one).

    Raises:
        ValidationError: The application is unknown.
    """
    app = _known(store or get_store(), domain)
    user, group = service_account(app)
    reason = ineligible(app)
    return {
        "domain": app.domain,
        "account": user,
        "group": group,
        "own": bool(app.identity),
        "eligible": reason is None,
        "reason": reason,
        "proposed": None if app.identity or reason else account_name(Path(app.app_path).name),
    }
