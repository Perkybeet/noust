# Copyright (c) 2024-2026 Yago López Prado
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

**Where it writes** (4.1): the move used to ask only whether the application answers. One that
writes outside its own tree, into a folder the shared account owns (an upload directory the web
server also serves), would pass that and fail days later at the first upload. So the paths its
``.env`` and its unit name are read first (:func:`outside_paths`): a folder the shared account can
write and the new account could not is refused by name, and ``--shared-group`` lets the new
account join the group that can write it.

**A monorepo** gets one account for all its workspaces' units (4.0, checklist
L-14): its workspaces are one application, and what separates it from the other
applications is that account.

**What does not get one.** A Docker Compose stack or a container application (its
processes are the containers', kept to its own files by the compose check) and a
site with nothing running (a static site, which the web server serves and no
account can write).

**Finishing the move** (4.0, checklist B-59): an application in zero-downtime
mode moves too, its template rewritten and the idle instance started under the
new account and switched to like an activation, so nothing is cut;
:func:`migrate_all` moves every application that can, one at a time, and
:func:`shared_account_apps` is what ``noust health`` warns about until none is
left. This is isolation between applications on one server, not a multi-tenant
boundary: Noust says so in ``docs/security.md`` and promises no more.
"""

from __future__ import annotations

import grp
import hashlib
import os
import pwd
import re
import shlex
import shutil
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from noust.core.config import Config
from noust.core.exceptions import DeploymentError, NoustError, ValidationError
from noust.core.logger import Logger
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import App, NoustStore, get_store
from noust.validators.names import validate_group_name

if TYPE_CHECKING:
    from noust.deployers.helpers.health_gate import RestartingGate
    from noust.managers.service_manager import ServiceManager

#: Every application account starts with this, which is also what marks one
#: as Noust's to remove.
ACCOUNT_PREFIX = "noust-app-"

#: The longest account name useradd and systemd accept.
MAX_ACCOUNT_LENGTH = 32

#: Application types whose processes are not the application's account's.
EXCLUDED_TYPES = frozenset({"docker-compose", "container", "static"})

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


def ensure_service_account(
    domain: str, runner: CommandRunner, config: Config | None = None
) -> tuple[str, str]:
    """
    Say which account an application runs as, making it first when it is its own and missing.

    An application's own account lives in ``/etc/passwd``, not in the store or in a bundle of
    Noust's state, so a restore on a machine the application never ran on (or after the
    application was deleted, which removes its account) finds the store naming an account the
    system does not have: the files could not be handed over and the unit would not start
    (``status=217/USER``).

    Args:
        domain: The application.
        runner: The runner ``getent`` and ``useradd`` go through.
        config: Where the shared service account comes from.

    Returns:
        ``(account, group)``, as :func:`service_account_for` says.

    Raises:
        DeploymentError: The account is its own, is missing and could not be created.
    """
    app = get_store().get_app(domain) if domain else None
    if app is not None and app.identity:
        ensure_account(app.identity, runner, domain=domain)
    return service_account(app, config)


def ineligible(app: App) -> str | None:
    """
    Say why an application cannot run as its own account.

    Args:
        app: Its row.

    Returns:
        The reason, or None when it can.
    """
    kind = app.app_type or ""
    if kind == "adopted":
        return "its unit is yours: it runs as the account you gave it, which Noust does not change"
    if kind in ("docker-compose", "container"):
        return "its processes run in containers, as the users their images say"
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
    from noust.core.appdir.layout import app_root
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


@dataclass(frozen=True)
class OutsidePath:
    """
    A path an application names outside its own tree that its new account could not write.

    Attributes:
        path: The path as the application names it.
        found_in: Where it names it: ``.env (UPLOAD_DIR)``, ``shop.service (ReadWritePaths)``.
        owner: ``user:group`` of the path, or of the first folder above it that exists.
        mode: Its permission bits, such as ``0755``.
    """

    path: str
    found_in: str
    owner: str
    mode: str


def _ids(account: str) -> tuple[int, frozenset[int]] | None:
    """
    Args:
        account: A system account.

    Returns:
        Its user id and every group id it has, or None when the system does not know it.
    """
    try:
        entry = pwd.getpwnam(account)
    except KeyError:
        return None
    return entry.pw_uid, frozenset(os.getgrouplist(account, entry.pw_gid))


def _group_id(name: str) -> int | None:
    """
    Args:
        name: A group.

    Returns:
        Its id, or None when the system has no such group.
    """
    try:
        return grp.getgrnam(name).gr_gid
    except KeyError:
        return None


def _can_write(info: os.stat_result, uid: int, gids: frozenset[int]) -> bool:
    """
    Tell whether an account may write a file, or create inside a directory, by its mode bits.

    Args:
        info: ``stat`` of the file or directory.
        uid: The account's user id.
        gids: Every group the account is in.

    Returns:
        Whether the owner, group or other bits that apply to it give it write (and, for a
        directory, search) permission. Root can always write; ACLs are not read.
    """
    if uid == 0:
        return True
    need = stat.S_IWUSR | stat.S_IXUSR if stat.S_ISDIR(info.st_mode) else stat.S_IWUSR
    if info.st_uid == uid:
        return bool(info.st_mode & need == need)
    shift = need >> 3
    if info.st_gid in gids:
        return bool(info.st_mode & shift == shift)
    other = need >> 6
    return bool(info.st_mode & other == other)


#: A path as a word of a value: it starts with one ``/`` and has nothing a shell or a unit would
#: expand, so what is read is what would be opened.
_PATH_WORD = re.compile(r"/(?!/)[A-Za-z0-9_.@+,=-]+(?:/[A-Za-z0-9_.@+,=-]+)*/?")

#: SQLAlchemy's and Prisma's absolute SQLite file (``sqlite:////var/lib/app.db``).
_SQLITE_FILE = re.compile(r"sqlite:/{3}(/[^\s\"'?]+)")

#: The directives of a unit that name a place the application reads or writes.
_UNIT_PATH_DIRECTIVES = (
    "ReadWritePaths",
    "BindPaths",
    "WorkingDirectory",
    "ExecStart",
    "ExecStartPre",
)


def _paths_in(value: str) -> list[str]:
    """
    Read the absolute paths a setting names.

    Args:
        value: The value of a variable or a directive.

    Returns:
        The paths, each once. A URL, a relative path and anything with an expansion in it name
        none.
    """
    found = [match.group(1) for match in _SQLITE_FILE.finditer(value)]
    for word in re.split(r"[\s,;]+", value):
        word = word.strip("\"'")
        if "://" in word:
            continue
        for part in re.split(r"[:=]", word):
            if _PATH_WORD.fullmatch(part):
                found.append(os.path.normpath(part))
    return list(dict.fromkeys(found))


def _named_by_the_unit(unit: str, body: str) -> list[tuple[str, str]]:
    """
    List the places a unit names, and the directive each is in.

    Args:
        unit: The unit's name, for the label.
        body: Its text.

    Returns:
        ``(path, label)``: the label says where, such as ``shop.service (ReadWritePaths)``.
    """
    found: list[tuple[str, str]] = []
    for line in body.splitlines():
        key, _, value = line.strip().partition("=")
        if key == "Environment":
            try:
                words = shlex.split(value)
            except ValueError:
                continue
            for word in words:
                name, _, text = word.partition("=")
                found += [(p, f"{unit}.service (Environment {name})") for p in _paths_in(text)]
        elif key in _UNIT_PATH_DIRECTIVES:
            text = value.removeprefix("-").removeprefix("+")
            if key.startswith("ExecStart"):
                # The program is read, not written; its arguments may be a place to write.
                text = " ".join(text.split()[1:])
            found += [(p, f"{unit}.service ({key})") for p in _paths_in(text)]
    return found


def _owner_of(info: os.stat_result) -> str:
    """
    Args:
        info: ``stat`` of a path.

    Returns:
        ``user:group`` by name when the system knows them, by number otherwise.
    """
    try:
        user = pwd.getpwuid(info.st_uid).pw_name
    except KeyError:
        user = str(info.st_uid)
    try:
        group = grp.getgrgid(info.st_gid).gr_name
    except KeyError:
        group = str(info.st_gid)
    return f"{user}:{group}"


def outside_paths(
    app: App,
    services: ServiceManager | None = None,
    *,
    shared_group: str | None = None,
) -> list[OutsidePath]:
    """
    Find the places outside its own tree that an application writes today and would not after.

    The ``.env`` and the unit are read for absolute paths. One counts when it is not inside the
    application's tree or its build cache, the shared account can write it (or create inside it,
    for a path that does not exist yet) and the new account could not: it would not be the owner,
    would not be in the group, and the others cannot write it. A server where the shared account
    is not known has nothing to compare, and finds none.

    Args:
        app: Its row.
        services: The service manager, for the unit; the real one by default.
        shared_group: A group the new account will join, which counts for it.

    Returns:
        The paths, in the order they are named.
    """
    from noust.core.appdir.layout import app_root, env_file_for
    from noust.deployers.helpers.env_manager import EnvManager
    from noust.deployers.helpers.sandbox import cache_dir_for

    shared = _ids(service_account(app)[0])
    if shared is None:
        return []
    root = app_root(app)
    inside = (os.path.normpath(root), os.path.normpath(cache_dir_for(root.name)))
    new_account = account_name(Path(app.app_path).name or app.domain)
    known = _ids(new_account)
    new_uid, new_gids = known if known is not None else (-1, frozenset())
    if shared_group is not None and (gid := _group_id(shared_group)) is not None:
        new_gids = new_gids | {gid}

    named: list[tuple[str, str]] = []
    try:
        text = env_file_for(app).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    for key, value in EnvManager.parse_env_text(text).items():
        named += [(path, f".env ({key})") for path in _paths_in(value)]
    services = services or _service_manager()
    for unit in (*services.serving_units(app), Path(app.app_path).name):
        body = services.read_unit(unit)
        if body:
            named += _named_by_the_unit(unit, body)

    found: dict[str, OutsidePath] = {}
    for path, label in named:
        if path in found or any(path == base or path.startswith(base + os.sep) for base in inside):
            continue
        existing = Path(path)
        while not existing.exists() and existing != existing.parent:
            existing = existing.parent
        try:
            info = existing.stat()
        except OSError:
            continue
        if _can_write(info, *shared) and not _can_write(info, new_uid, new_gids):
            found[path] = OutsidePath(
                path, label, _owner_of(info), f"{stat.S_IMODE(info.st_mode):04o}"
            )
    return list(found.values())


def _service_manager() -> ServiceManager:
    """
    Returns:
        The real service manager, imported when it is needed.
    """
    from noust.managers.service_manager import ServiceManager

    return ServiceManager()


def refuse_migration(
    app: App,
    *,
    services: ServiceManager | None = None,
    shared_group: str | None = None,
) -> None:
    """
    Refuse to move an application that cannot or need not move.

    Args:
        app: Its row.
        services: The service manager, to read the unit; the real one by default.
        shared_group: A group the new account will join, so it can write what that group can.

    Raises:
        ValidationError: It already has its own account, cannot have one, this process is not
            root, the group is not one, or it writes where its own account could not.
    """
    if app.identity:
        raise ValidationError(
            f"{app.domain} already runs as its own account, {app.identity}",
            details="Nothing to migrate.",
        )
    reason = ineligible(app)
    if reason is not None:
        raise ValidationError(f"{app.domain} cannot run as its own account: {reason}")
    if app.zero_downtime and app.app_type == "monorepo":
        # The zero-downtime move switches one pair of instances; a monorepo's
        # workspaces are several units, and moving them is not done yet.
        raise ValidationError(
            f"{app.domain} is a monorepo in zero-downtime mode, whose workspaces are not moved yet",
            details=f"Turn it off, migrate, and turn it on again: noust app zero-downtime "
            f"{app.domain} off; noust app identity migrate {app.domain}",
        )
    if not running_as_root():
        raise ValidationError(
            "Moving an application to its own account needs root",
            details="Only root creates accounts and hands files to them. Run it as root.",
        )
    if shared_group is not None:
        validate_group_name(shared_group)
        if _group_id(shared_group) is None:
            raise ValidationError(
                f"There is no group called {shared_group}",
                details="Name a group that exists (getent group), or make it with groupadd first.",
                field="shared_group",
            )
    blocked = outside_paths(app, services, shared_group=shared_group)
    if blocked:
        raise ValidationError(
            f"{app.domain} writes outside its own folder, where its own account could not: "
            + ", ".join(item.path for item in blocked),
            details=_outside_details(app.domain, blocked, shared_group),
        )


def _outside_details(domain: str, blocked: list[OutsidePath], shared_group: str | None) -> str:
    """
    Say which places block the move, what owns each, and what to do about it.

    Args:
        domain: The application.
        blocked: The places.
        shared_group: The group the operator offered, if any, which did not cover them.

    Returns:
        The lines for the error.
    """
    lines = [
        f"{item.path} (named in {item.found_in}) belongs to {item.owner} ({item.mode})"
        for item in blocked
    ]
    if shared_group is not None:
        lines.append(
            f"The group {shared_group} cannot write them either: make the folder writable by "
            f"group {shared_group} (chgrp {shared_group} and chmod g+w) and run it again."
        )
    lines.append(
        "The application writes there today as the shared account; as its own account it would "
        "pass the health check and fail at the first file it wrote. Pick one:\n"
        "1. Keep that data inside the application, in shared/ (a persistent path), and point the "
        "setting at it.\n"
        "2. Let the new account in through a group that can write the folder: make a group for "
        "it, give the folder to that group with g+w, and run: "
        f"noust app identity migrate {domain} --shared-group GROUP\n"
        "3. Leave the application on the shared account."
    )
    return "\n".join(lines)


def migrate(
    domain: str,
    *,
    actor: str,
    logger: Logger | None = None,
    runner: CommandRunner | None = None,
    store: NoustStore | None = None,
    services: ServiceManager | None = None,
    gate_for: Callable[[App, NoustStore, Logger], RestartingGate] | None = None,
    shared_group: str | None = None,
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
        shared_group: A group the new account joins, so it can write the folders outside the
            application's tree that the group can write. It is left again when the move is put
            back.

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

    require_server_role("applications")
    store = store or get_store()
    runner = runner or get_runner()
    log = logger or Logger()
    app = _known(store, domain)
    refuse_migration(app, services=services, shared_group=shared_group)
    with app_lock(domain, "identity migration"):
        return _migrate(
            _known(store, domain),
            actor=actor,
            log=log,
            runner=runner,
            store=store,
            services=services,
            gate_for=gate_for,
            shared_group=shared_group,
        )


def _migrate(
    app: App,
    *,
    actor: str,
    log: Logger,
    runner: CommandRunner,
    store: NoustStore,
    services: ServiceManager | None,
    gate_for: Callable[[App, NoustStore, Logger], RestartingGate] | None,
    shared_group: str | None = None,
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
        shared_group: A group the new account joins.

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
    if shared_group is not None:
        _join_group(account, shared_group, runner, undo)
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
        # The service row is named after the application, not after the
        # instance of its template that happens to serve.
        rows = (Path(app.app_path).name,) if app.zero_downtime else units
        _record_service_accounts(store, rows, account, account)
        undo.append(("the service rows", lambda: _record_service_accounts(store, rows, *previous)))

        if app.zero_downtime:
            # The template is rewritten: the idle instance starts under the
            # new account, takes the traffic once it answers, and the one
            # that served stops. The serving instance is never restarted, so
            # a failure leaves it serving as before and there is nothing to
            # restart as it was (touched stays False).
            _switch_instances(moved, store, log)
        else:
            touched = True
            healthy, evidence = gate_builder(moved, store, log).restart_and_probe()
            if not healthy:
                raise DeploymentError(
                    f"{domain} did not answer running as {account}", details=evidence
                )
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
        shared_group=shared_group,
    )
    log.success(f"{domain} runs as its own account, {account}")
    return IdentityMigration(domain, account, previous, units, pool)


def _join_group(
    account: str, group: str, runner: CommandRunner, undo: list[tuple[str, Callable[[], None]]]
) -> None:
    """
    Add the new account to a group, and queue taking it out again.

    Args:
        account: The application's account.
        group: The group (already judged).
        runner: The runner.
        undo: What puts the move back, which gets the leaving of the group.

    Raises:
        DeploymentError: ``usermod`` failed, with its own words.
    """
    joined = runner.run(["usermod", "-aG", group, account], timeout=_ACCOUNT_TIMEOUT)
    if not joined.success:
        raise DeploymentError(
            f"{account} could not join the group {group}",
            details=(joined.stderr or joined.stdout).strip(),
        )

    def leave() -> None:
        left = runner.run(["gpasswd", "-d", account, group], timeout=_ACCOUNT_TIMEOUT)
        if not left.success:
            raise DeploymentError(
                f"{account} could not leave the group {group}",
                details=(left.stderr or left.stdout).strip(),
            )

    undo.append((f"membership of the group {group}", leave))


def _switch_instances(app: App, store: NoustStore, log: Logger) -> None:
    """
    Start the idle instance of a zero-downtime application under its new account.

    Args:
        app: The application, with the account its row now says.
        store: The store.
        log: Where the switch is reported.

    Raises:
        DeploymentError: The instance did not answer; the one that served
            still serves.
    """
    from noust.deployers.lifecycle import restart_blue_green

    # The template is put back by the caller's undo list; this has nothing of
    # its own to put back.
    restart_blue_green(app, store, log, lambda: None)


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


def shared_account_apps(store: NoustStore | None = None) -> list[App]:
    """
    List the applications that could run as their own account and still share one.

    What ``noust health`` warns about, and what :func:`migrate_all` moves.

    Args:
        store: The store.

    Returns:
        Applications with no account of their own and nothing that stops them
        having one.
    """
    return [
        app
        for app in (store or get_store()).list_apps()
        if isinstance(app, App) and not app.identity and ineligible(app) is None
    ]


@dataclass(frozen=True)
class BulkMigration:
    """
    What :func:`migrate_all` did.

    Attributes:
        moved: The applications that now run as their own account.
        failed: The applications that did not move, each with why; every one
            of them is back as it was.
    """

    moved: tuple[IdentityMigration, ...] = ()
    failed: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The outcome as JSON-ready data.
        """
        return {
            "moved": [done.to_dict() for done in self.moved],
            "failed": [{"domain": domain, "error": error} for domain, error in self.failed],
        }


def migrate_all(
    *,
    actor: str,
    logger: Logger | None = None,
    runner: CommandRunner | None = None,
    store: NoustStore | None = None,
    services: ServiceManager | None = None,
    gate_for: Callable[[App, NoustStore, Logger], RestartingGate] | None = None,
) -> BulkMigration:
    """
    Move every application that can have its own account, one at a time.

    Each move is :func:`migrate`: behind the application's health gate, and
    undone exactly when it does not answer. One that fails does not stop the
    rest, and is reported with its own words.

    Args:
        actor: Who asked, for the audit trail.
        logger: Where the steps are reported.
        runner: The runner.
        store: The store.
        services: The service manager.
        gate_for: Builds an application's health gate from its row.

    Returns:
        Who moved and who did not.
    """
    log = logger or Logger()
    target = store or get_store()
    moved: list[IdentityMigration] = []
    failed: list[tuple[str, str]] = []
    for app in shared_account_apps(target):
        try:
            moved.append(
                migrate(
                    app.domain,
                    actor=actor,
                    logger=log,
                    runner=runner,
                    store=target,
                    services=services,
                    gate_for=gate_for,
                )
            )
        except NoustError as exc:
            log.error(f"{app.domain} was not moved: {exc.message}")
            failed.append((app.domain, exc.message))
    return BulkMigration(moved=tuple(moved), failed=tuple(failed))
