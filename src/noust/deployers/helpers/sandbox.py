# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Who builds in a sandbox, as whom, seeing what (backlog 44; ENS G14).

Installing dependencies, building and a deployer's own hooks run code Noust did
not write: an npm ``postinstall`` of a dependency of a dependency. Until 3.1
that code ran as root with Noust's whole environment, and could read
``/root/.ssh``, the console's token, the store and every other application's
``.env``. From 3.1 it runs in a transient systemd unit (the runner's
``sandbox=``, rule 1), as the ``noust-build`` account, able to write its
release and its own caches and nothing else. This module is the policy the
deployer's ``_run`` applies at that one chokepoint (rule 4); the mechanism is
:func:`noust.core.runner.sandbox_prefix`.

**Phases.** Fetching the source stays root, outside the sandbox: it needs the
deploy key and the git credentials, and it exports a release without ``.git``.
Install, build and hooks are sandboxed as ``noust-build``, with per-application
caches in ``/var/cache/noust/build/<app>`` owned by that account (per
application, never shared: a shared cache is how one application's build
would poison another's). Migrations (``prisma migrate deploy``, the release
phase) are not a build: they run with the application's own identity and
secrets, as its unit would. The release is handed to ``noust-build`` before
the build and to the service account after it, so the build writes and the
service reads. The application's ``.env`` reaches the build through
``EnvironmentFile=``, which systemd reads as root; the build account cannot
open it, nor any other application's.

**The account.** ``noust-build`` is a system account with no home and no login
shell, created on the first sandboxed build with ``useradd --system
--user-group --no-create-home --home-dir /nonexistent --shell nologin``. It is
distinct from the service account on purpose: every application runs as the
same ``www-data``, and a build running as it could read the others' ``.env``.

**Fail closed.** Before the first sandboxed build of a process, a self-test
proves the sandbox works here: a canary run as ``noust-build`` must be able to
write its scratch directory (so a sandbox that cannot run at all is not
mistaken for one that blocks), must not be able to write a world-writable
directory outside it, nor ``/root``, nor read a world-readable file placed in
``/etc/noust``. Containers and WSL without mount namespaces ignore
``ProtectSystem`` silently; the self-test is what notices. When it fails the
build stops with the evidence; it never falls back to root. Building one
application as root anyway is an explicit, audited per-application setting
(:func:`disable`, with a reason).

**Activation.** Applications and previews created from 3.1 build in the
sandbox. Applications from before keep building as root, with the warning
:func:`sandbox_warning` gives the health report and the application page,
until an operator tests the sandbox with a build of the current commit
(``noust app sandbox test``) and enables it. Previews build with the network,
as they always did, and without the production secrets their variables are a
copy of (:func:`preview_build_variables`). The strict network profile is
opt-in, per application (``noust app sandbox enable --network strict``), and
its previews follow it: dependencies install with the network and without the
application's variables, and the build runs with them and without a network.
A strict build that fails on the network says how to allow it.

**Where the protection stops.** An application still ``in place`` builds in its
live tree, as its service account (changing the owner of a tree the service
runs from would cost more than it protects); the sandbox still hides the rest
of the machine. ``noust app migrate`` is the way to the full separation. A
monorepo is always in place, so it is the same case: its ``pnpm install`` and
``pnpm build`` run in the sandbox as its units' account. Docker Compose builds
run inside the Docker daemon and are guarded by the compose file check instead
(:mod:`noust.deployers.helpers.compose_guard`).
"""

from __future__ import annotations

import grp
import logging
import os
import pwd
import re
import secrets
import shutil
import sqlite3
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from noust.core import paths
from noust.core.exceptions import BuildError, NoustError, ValidationError
from noust.core.fs import FileSystem, get_fs, is_rehearsal
from noust.core.logger import Logger
from noust.core.runner import SANDBOX_UNIT_PREFIX, CommandRunner, SandboxSpec
from noust.core.store import App, NoustStore, get_store

#: The account every sandboxed build runs as.
BUILD_USER = "noust-build"
BUILD_GROUP = "noust-build"

#: Application types whose builds do not go through the sandbox: a compose
#: stack builds inside the Docker daemon.
UNSUPPORTED_TYPES = frozenset({"docker-compose"})

#: Deadlines for the account and the self-test, which are quick local commands.
_ACCOUNT_TIMEOUT = 60
_SELF_TEST_TIMEOUT = 60

#: Where a trial build (``noust app sandbox test``) is built, inside the
#: application's cache.
TRIAL_DIR = "trial"

#: root's home: what ProtectHome hides, and where the self-test's canary tries
#: to write.
ROOT_HOME = Path("/root")

#: A build may start this many tasks: generous for a parallel build, a stop
#: for a fork bomb in a postinstall.
BUILD_TASKS_MAX = 4096

#: A preview's build limits. Its unit's are small on purpose; a build needs
#: more, but a pull request must not take the whole server.
PREVIEW_BUILD_MEMORY_MB = 2048
PREVIEW_BUILD_CPU_PERCENT = 200


class BuildPhase(str, Enum):
    """What a deployer's command is doing, which decides where it runs."""

    #: Dependencies. Network always; in the strict profile, without the
    #: application's secrets.
    INSTALL = "install"
    #: Compiling, and a deployer's hooks. With the application's variables;
    #: in the strict profile, without a network.
    BUILD = "build"
    #: Migrations: the application's own identity and secrets, not a build's.
    RELEASE = "release"
    #: Root, deliberately. Never repository code.
    PRIVILEGED = "privileged"


class SandboxMode(str, Enum):
    """How an application's builds run."""

    ON = "on"
    #: An operator decided, with a reason, that this application builds as root.
    OFF = "off"
    #: From before 3.1: builds as root, warned, until tested and enabled.
    LEGACY = "legacy"


class NetworkProfile(str, Enum):
    """The network a sandboxed build gets."""

    #: Both phases with the network, as builds always had.
    FULL = "full"
    #: Install with the network and without secrets; build without a network.
    STRICT = "strict"


@dataclass(frozen=True)
class SandboxState:
    """
    One application's build regime, as the store records it.

    Attributes:
        domain: The application.
        mode: ``on``, ``off`` or ``legacy``.
        network: ``full`` or ``strict``.
        pty: Build on a pseudo-terminal (the compatibility mode).
        reason: Why it was turned off, when it was.
        changed_by: Who last changed the regime.
        changed_at: When, ISO 8601.
        tested_at: When the last trial build ran.
        tested_commit: The commit it built.
        test_passed: Whether it built.
        test_detail: Its output when it did not, verbatim.
    """

    domain: str
    mode: str = SandboxMode.LEGACY.value
    network: str = NetworkProfile.FULL.value
    pty: bool = False
    reason: str | None = None
    changed_by: str | None = None
    changed_at: str | None = None
    tested_at: str | None = None
    tested_commit: str | None = None
    test_passed: bool | None = None
    test_detail: str | None = None

    @property
    def enabled(self) -> bool:
        """Whether its builds run in the sandbox."""
        return self.mode == SandboxMode.ON.value

    def to_dict(self) -> dict[str, Any]:
        """
        Return the state as plain data, for JSON.

        Returns:
            Every field, plus ``enabled``.
        """
        return {**asdict(self), "enabled": self.enabled}


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

_COLUMNS = (
    "mode",
    "network",
    "pty",
    "reason",
    "changed_by",
    "changed_at",
    "tested_at",
    "tested_commit",
    "test_passed",
    "test_detail",
)


def _now() -> str:
    """
    Return the current time for a record.

    Returns:
        ISO 8601, UTC.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _audit(domain: str, outcome: str = "ok", **details: Any) -> None:
    """
    Record a change to a build regime in the audit trail.

    Here rather than in the CLI and the API, so both are recorded the same
    way and neither can forget (rule 4). The actor is the one bound for the
    request or the command.

    Args:
        domain: The application.
        outcome: ``ok`` or ``failure``.
        **details: What changed.
    """
    from noust.core import audit

    audit.record("apps.sandbox", target=f"app:{domain}", outcome=outcome, details=details)


def _app(store: NoustStore, domain: str) -> App:
    """
    Return an application's row, refusing an unknown one.

    Args:
        store: The store.
        domain: The application.

    Returns:
        The row.

    Raises:
        ValidationError: Nothing is deployed at that domain.
    """
    app = store.get_app(domain)
    if app is None or app.id is None:
        raise ValidationError(
            f"Application not found: {domain}",
            details="See the applications with: noust list",
        )
    return app


def get_state(domain: str, *, store: NoustStore | None = None) -> SandboxState:
    """
    Read an application's build regime.

    Args:
        domain: The application.
        store: The store; the process-wide one by default.

    Returns:
        Its state; ``legacy`` when it has no row (it predates 3.1).

    Raises:
        ValidationError: The application is unknown.
    """
    store = store or get_store()
    app = _app(store, domain)
    row = (
        store._get_connection()
        .execute(
            f"SELECT {', '.join(_COLUMNS)} FROM build_sandbox WHERE app_id = ?",  # noqa: S608 - column names are this module's constants
            (app.id,),
        )
        .fetchone()
    )
    if row is None:
        return SandboxState(domain=domain)
    values = dict(zip(_COLUMNS, tuple(row), strict=True))
    return SandboxState(
        domain=domain,
        mode=values["mode"],
        network=values["network"],
        pty=bool(values["pty"]),
        reason=values["reason"],
        changed_by=values["changed_by"],
        changed_at=values["changed_at"],
        tested_at=values["tested_at"],
        tested_commit=values["tested_commit"],
        test_passed=None if values["test_passed"] is None else bool(values["test_passed"]),
        test_detail=values["test_detail"],
    )


def _write(store: NoustStore, domain: str, **fields: Any) -> SandboxState:
    """
    Create or update an application's row with some fields.

    Args:
        store: The store.
        domain: The application.
        **fields: Columns to set.

    Returns:
        The state after the write (or, under ``--dry-run``, as it would be).
    """
    app = _app(store, domain)
    names = sorted(fields)
    assignments = ", ".join(f"{name} = excluded.{name}" for name in names)
    with store._transaction() as cursor:
        cursor.execute(
            f"INSERT INTO build_sandbox (app_id, {', '.join(names)}) "  # noqa: S608 - column names are this module's constants
            f"VALUES (?, {', '.join('?' for _ in names)}) "
            f"ON CONFLICT(app_id) DO UPDATE SET {assignments}",
            (app.id, *(fields[name] for name in names)),
        )
    current = get_state(domain, store=store)
    # A rehearsal rolled the write back; report what it would have been.
    return current if not is_rehearsal() else _merged(current, fields)


def _merged(state: SandboxState, fields: dict[str, Any]) -> SandboxState:
    """
    Apply fields to a state without writing them.

    Args:
        state: The state.
        fields: Columns and values.

    Returns:
        The state with them.
    """
    values = asdict(state)
    for name, value in fields.items():
        values[name] = bool(value) if name == "pty" else value
    if values.get("test_passed") is not None:
        values["test_passed"] = bool(values["test_passed"])
    return SandboxState(**values)


def adopt_new_app(domain: str, *, preview: bool, store: NoustStore | None = None) -> SandboxState:
    """
    Put an application created from 3.1 in the sandbox.

    A preview builds with the network, as previews always did (``next/font``
    and the like fetch at build time), and never with the production secrets
    it was copied (:func:`preview_build_variables`). The strict profile - no
    network for the build - is opt-in: a preview takes it when the
    application it previews builds strict (``noust app sandbox enable
    <domain> --network strict``, or the ``network`` field of the console's
    sandbox settings).

    An application that already has a row keeps it: this is called on a first
    deployment, and a row that exists was written by an operator.

    Args:
        domain: The application, whose row exists.
        preview: Whether it is a pull request preview.
        store: The store; the process-wide one by default.

    Returns:
        Its state.
    """
    store = store or get_store()
    network = NetworkProfile.FULL.value
    if is_rehearsal():
        # The application's own row was rolled back; so is this one.
        return SandboxState(domain=domain, mode=SandboxMode.ON.value, network=network)
    app = _app(store, domain)
    if preview and app.preview_parent:
        network = _preview_network(app.preview_parent, store)
    existing = (
        store._get_connection()
        .execute("SELECT 1 FROM build_sandbox WHERE app_id = ?", (app.id,))
        .fetchone()
    )
    if existing is not None:
        return get_state(domain, store=store)
    return _write(
        store,
        domain,
        mode=SandboxMode.ON.value,
        network=network,
        changed_by="noust",
        changed_at=_now(),
    )


def _preview_network(parent: str, store: NoustStore) -> str:
    """
    Name the network profile a new preview of an application builds in.

    Args:
        parent: The application previewed.
        store: The store.

    Returns:
        ``strict`` when that application opted in to it, else ``full``.
    """
    try:
        chosen = get_state(parent, store=store).network
    except ValidationError:
        return NetworkProfile.FULL.value
    return chosen if chosen == NetworkProfile.STRICT.value else NetworkProfile.FULL.value


def preview_build_variables(
    values: Mapping[str, str], marks: Mapping[str, bool] | None
) -> dict[str, str]:
    """
    Keep the variables a preview's build may see: none of production's secrets.

    A preview's variables are a copy of its application's, and a pull request
    builds with the network: a secret given to its build could be sent
    anywhere. A variable is withheld when the one classifier says it is
    secret, or when its name alone looks secret and the operator did not mark
    it otherwise; public ones (``NEXT_PUBLIC_*``) stay, since builds inline
    them.

    Args:
        values: The preview's variables.
        marks: Its secret marks, the operator's overrides.

    Returns:
        The variables that are not secrets.
    """
    from noust.core.secret_detection import classify, name_looks_secret

    kept: dict[str, str] = {}
    for name, value in values.items():
        verdict = classify(name, value, marks)
        if verdict.secret or (not verdict.marked and name_looks_secret(name)):
            continue
        kept[name] = value
    return kept


#: What a build's output says when it could not reach the network: DNS, a
#: refused or unreachable connection, a fetch that failed.
_NETWORK_FAILURE = re.compile(
    r"ENOTFOUND|EAI_AGAIN|ENETUNREACH|ECONNREFUSED|ETIMEDOUT|getaddrinfo|"
    r"Temporary failure in name resolution|Could not resolve host|"
    r"Network is unreachable|network request failed|Failed to fetch",
    re.IGNORECASE,
)


def network_hint(state: SandboxState | None, domain: str, output: str) -> str:
    """
    Say how to give a strict build the network, when its failure looks like it needed it.

    Args:
        state: The regime the build ran in; None when it was not sandboxed.
        domain: The application.
        output: What the build printed.

    Returns:
        The hint, or "" when the build was not strict or did not fail on
        the network.
    """
    if state is None or state.network != NetworkProfile.STRICT.value:
        return ""
    if not _NETWORK_FAILURE.search(output or ""):
        return ""
    return (
        f"{domain} builds without a network (the strict profile), and the build "
        "looks like it tried to reach one. Allow it with: "
        f"noust app sandbox enable {domain} --network full"
    )


def enable(
    domain: str,
    *,
    actor: str,
    force: bool = False,
    network: str | None = None,
    pty: bool | None = None,
    store: NoustStore | None = None,
) -> SandboxState:
    """
    Build an application in the sandbox from now on.

    Args:
        domain: The application.
        actor: Who asked, for the record.
        force: Enable without a passing trial build.
        network: ``full`` or ``strict``; unchanged when None.
        pty: The compatibility mode; unchanged when None.
        store: The store; the process-wide one by default.

    Returns:
        The new state.

    Raises:
        ValidationError: The application cannot build in the sandbox, has no
            passing trial build of its current regime and ``force`` was not
            given, or the network profile is unknown.
    """
    store = store or get_store()
    app = _app(store, domain)
    refuse_unsupported(app)
    state = get_state(domain, store=store)
    if network is not None and network not in {profile.value for profile in NetworkProfile}:
        raise ValidationError(
            f"Unknown network profile {network!r}", details="Use 'full' or 'strict'."
        )
    if not force and not state.enabled and state.test_passed is not True:
        raise ValidationError(
            f"{domain} has no passing trial build in the sandbox",
            details=(
                f"Test first: noust app sandbox test {domain} builds the current commit "
                "in the sandbox without activating it. Or enable it anyway with --force; "
                "the next build then fails if the sandbox breaks it."
            ),
        )
    fields: dict[str, Any] = {
        "mode": SandboxMode.ON.value,
        "reason": None,
        "changed_by": actor,
        "changed_at": _now(),
    }
    if network is not None:
        fields["network"] = network
    if pty is not None:
        fields["pty"] = int(pty)
    state = _write(store, domain, **fields)
    _audit(
        domain,
        action="enable",
        by=actor,
        forced=force and state.test_passed is not True,
        network=state.network,
        pty=state.pty,
    )
    return state


def disable(
    domain: str, *, actor: str, reason: str, store: NoustStore | None = None
) -> SandboxState:
    """
    Build an application as root from now on: an explicit, audited decision.

    Args:
        domain: The application.
        actor: Who decided, for the record.
        reason: Why, in the operator's words; required.
        store: The store; the process-wide one by default.

    Returns:
        The new state.

    Raises:
        ValidationError: No reason was given.
    """
    if not reason.strip():
        raise ValidationError(
            "Building as root needs a reason",
            details="Say why this application cannot build in the sandbox: --reason '...'. "
            "It is recorded with your name and shown wherever the application is.",
        )
    state = _write(
        store or get_store(),
        domain,
        mode=SandboxMode.OFF.value,
        reason=reason.strip(),
        changed_by=actor,
        changed_at=_now(),
    )
    _audit(domain, action="disable", by=actor, reason=state.reason)
    return state


def record_trial(
    domain: str,
    *,
    passed: bool,
    commit: str | None,
    detail: str | None,
    store: NoustStore | None = None,
) -> SandboxState:
    """
    Record the outcome of a trial build.

    Args:
        domain: The application.
        passed: Whether it built.
        commit: The commit it built.
        detail: The failure's output, verbatim; None when it passed.
        store: The store; the process-wide one by default.

    Returns:
        The new state.
    """
    state = _write(
        store or get_store(),
        domain,
        tested_at=_now(),
        tested_commit=commit,
        test_passed=int(passed),
        test_detail=detail,
    )
    _audit(domain, "ok" if passed else "failure", action="trial", commit=commit, passed=passed)
    return state


@dataclass(frozen=True)
class ComposeException:
    """
    A Docker Compose stack allowed what is root on the host.

    Attributes:
        domain: The application, deployed or about to be.
        reason: Why, in the operator's words.
        allowed_by: Who allowed it.
        allowed_at: When, ISO 8601.
    """

    domain: str
    reason: str
    allowed_by: str
    allowed_at: str


def get_compose_exception(
    domain: str, *, store: NoustStore | None = None
) -> ComposeException | None:
    """
    Read whether a compose stack may run privileged containers or mount the Docker socket.

    Args:
        domain: The application.
        store: The store; the process-wide one by default.

    Returns:
        The exception, or None.
    """
    row = (
        (store or get_store())
        ._get_connection()
        .execute(
            "SELECT reason, allowed_by, allowed_at FROM compose_exceptions WHERE domain = ?",
            (domain,),
        )
        .fetchone()
    )
    if row is None:
        return None
    return ComposeException(domain, row[0], row[1], row[2])


def set_compose_exception(
    domain: str,
    *,
    allowed: bool,
    actor: str,
    reason: str | None = None,
    store: NoustStore | None = None,
) -> ComposeException | None:
    """
    Allow, or stop allowing, a compose stack privileged containers and the Docker socket.

    An exception names a domain, so a stack can be allowed before its first
    deployment; it stays until it is revoked.

    Args:
        domain: The application, deployed or not yet.
        allowed: Whether it may.
        actor: Who decided.
        reason: Why; required to allow.
        store: The store; the process-wide one by default.

    Returns:
        The exception now in force, or None once revoked.

    Raises:
        ValidationError: Allowing without a reason.
    """
    store = store or get_store()
    if not allowed:
        with store._transaction() as cursor:
            cursor.execute("DELETE FROM compose_exceptions WHERE domain = ?", (domain,))
        _audit(domain, action="compose_exception_revoke", by=actor)
        return None
    if not (reason or "").strip():
        raise ValidationError(
            "A privileged container needs a reason",
            details="It is root on this server: say why this stack needs it, with --reason.",
        )
    exception = ComposeException(domain, (reason or "").strip(), actor, _now())
    with store._transaction() as cursor:
        cursor.execute(
            "INSERT INTO compose_exceptions (domain, reason, allowed_by, allowed_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(domain) DO UPDATE SET reason = excluded.reason, "
            "allowed_by = excluded.allowed_by, allowed_at = excluded.allowed_at",
            (exception.domain, exception.reason, exception.allowed_by, exception.allowed_at),
        )
    _audit(domain, action="compose_exception", by=actor, reason=exception.reason)
    return exception


def refuse_unsupported(app: App) -> None:
    """
    Refuse an application whose builds do not go through the sandbox.

    Args:
        app: Its row.

    Raises:
        ValidationError: A compose stack.
    """
    if (app.app_type or "") in UNSUPPORTED_TYPES:
        raise ValidationError(
            f"{app.domain} is a {app.app_type} application, whose builds do not run in the sandbox",
            details="A compose stack builds inside the Docker daemon, guarded by the compose "
            "file check: noust app sandbox compose-exception shows what it allows.",
        )


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------


def sandbox_warning(app: App, state: SandboxState | None = None) -> str | None:
    """
    Say what is unprotected about an application's builds, for the health report and its page.

    Args:
        app: Its row.
        state: Its state, when the caller already read it.

    Returns:
        One sentence and what to do, or None when its builds are sandboxed
        (or it builds nothing a sandbox could hold).
    """
    if (app.app_type or "") == "docker-compose":
        return _compose_warning(app)
    state = state or get_state(app.domain)
    if state.mode == SandboxMode.OFF.value:
        return (
            f"{app.domain} builds as root by decision of {state.changed_by or 'an operator'} "
            f"({state.reason}). Turn the sandbox back on with: noust app sandbox enable "
            f"{app.domain}"
        )
    if state.mode == SandboxMode.LEGACY.value:
        return (
            f"{app.domain} still builds as root, as applications created before 3.1 do. "
            f"Test the sandbox with: noust app sandbox test {app.domain}, then enable it."
        )
    return None


def _compose_warning(app: App) -> str | None:
    """
    Warn about a running compose stack that is root on the host without an exception.

    Args:
        app: The stack's row.

    Returns:
        The warning, or None.
    """
    from noust.deployers.helpers.compose_guard import stack_findings

    if not app.app_path:
        return None
    findings = stack_findings(Path(app.app_path))
    if not findings.refused or get_compose_exception(app.domain) is not None:
        return None
    return (
        f"{app.domain} runs containers that are root on this server "
        f"({'; '.join(findings.refused)}). A new stack is refused this; record why this one "
        f"needs it: noust app sandbox compose-exception {app.domain} --reason '...'"
    )


def sandbox_warnings(store: NoustStore | None = None) -> dict[str, str]:
    """
    Collect :func:`sandbox_warning` for every application.

    Args:
        store: The store; the process-wide one by default.

    Returns:
        Domain to warning, for the applications that have one.
    """
    store = store or get_store()
    warnings: dict[str, str] = {}
    for app in store.list_apps():
        try:
            warning = sandbox_warning(app, get_state(app.domain, store=store))
        except (NoustError, sqlite3.Error) as exc:
            warning = f"{app.domain}: the build regime could not be read ({exc})"
        if warning:
            warnings[app.domain] = warning
    return warnings


# ---------------------------------------------------------------------------
# The host: account, caches, self-test
# ---------------------------------------------------------------------------


def running_as_root() -> bool:
    """
    Tell whether this process can hand a build to another account.

    The sandbox takes root's privileges away from a build. A Noust that does
    not run as root has none to take (and cannot ask systemd for a system
    unit), so its builds run as the invoking account, as any command it runs.

    Returns:
        True for root.
    """
    return os.geteuid() == 0


def ensure_build_account(runner: CommandRunner) -> None:
    """
    Create the ``noust-build`` account when it does not exist.

    Args:
        runner: The runner.

    Raises:
        BuildError: ``useradd`` failed, with its output.
    """
    probe = runner.run(["getent", "passwd", BUILD_USER], timeout=_ACCOUNT_TIMEOUT)
    if probe.success and probe.stdout.strip():
        return
    nologin = next(
        (
            candidate
            for candidate in ("/usr/sbin/nologin", "/sbin/nologin")
            if Path(candidate).exists()
        ),
        shutil.which("nologin") or "/usr/sbin/nologin",
    )
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
            "Noust sandboxed builds",
            BUILD_USER,
        ],
        timeout=_ACCOUNT_TIMEOUT,
    )
    # 9: the name is taken, which means another build created it first.
    if created.success or created.exit_code == 9:
        return
    raise BuildError(
        f"Could not create the {BUILD_USER} account builds run as",
        details=(
            f"{(created.stderr or created.stdout).strip()}\n\n"
            f"Create it by hand and build again: useradd --system --user-group "
            f"--no-create-home --home-dir /nonexistent --shell {nologin} {BUILD_USER}"
        ),
    )


def _owner_ids(user: str, group: str) -> tuple[int, int] | None:
    """
    Look an account and a group up.

    Args:
        user: The account.
        group: Its group.

    Returns:
        ``(uid, gid)``, or None when the account does not exist (a test, a
        rehearsal before the account is created).
    """
    try:
        entry = pwd.getpwnam(user)
    except KeyError:
        return None
    try:
        gid = grp.getgrnam(group).gr_gid
    except KeyError:
        gid = entry.pw_gid
    return entry.pw_uid, gid


def cache_dir_for(app_name: str) -> Path:
    """
    Return an application's build cache.

    Args:
        app_name: The application's name.

    Returns:
        ``/var/cache/noust/build/<app>``.
    """
    return paths.BUILD_CACHE_DIR / app_name


def ensure_cache_dir(
    app_name: str,
    *,
    user: str,
    group: str,
    runner: CommandRunner,
    fs: FileSystem | None = None,
) -> Path:
    """
    Make an application's build cache exist and belong to the account that builds it.

    Args:
        app_name: The application's name.
        user: The account the build runs as.
        group: Its group.
        runner: The runner, for the ownership change.
        fs: The filesystem; the process-wide one by default.

    Returns:
        The cache directory.
    """
    fs = fs or get_fs()
    fs.make_dir(paths.BUILD_CACHE_DIR, mode=0o755)
    cache = cache_dir_for(app_name)
    fs.make_dir(cache, mode=0o700)
    wanted = _owner_ids(user, group)
    owned = cache.stat() if cache.exists() else None
    if owned is None or wanted is None or (owned.st_uid, owned.st_gid) != wanted:
        # -R only when the owner changes (an application moving from in place
        # to releases changes who builds it). chown -R does not follow links.
        runner.run(["chown", "-R", f"{user}:{group}", str(cache)], timeout=_ACCOUNT_TIMEOUT)
    return cache


def build_environment(cache: Path) -> dict[str, str]:
    """
    Point every package manager's cache and home at an application's cache.

    Args:
        cache: The application's build cache.

    Returns:
        The variables a sandboxed build starts with.
    """
    return {
        "HOME": str(cache),
        "XDG_CACHE_HOME": str(cache / "xdg"),
        "npm_config_cache": str(cache / "npm"),
        "npm_config_store_dir": str(cache / "pnpm-store"),
        "YARN_CACHE_FOLDER": str(cache / "yarn"),
        "BUN_INSTALL_CACHE_DIR": str(cache / "bun"),
        "COMPOSER_HOME": str(cache / "composer"),
        "COMPOSER_CACHE_DIR": str(cache / "composer" / "cache"),
        "PIP_CACHE_DIR": str(cache / "pip"),
        "UV_CACHE_DIR": str(cache / "uv"),
        "GOCACHE": str(cache / "go"),
    }


def protected_paths(extra: Iterable[Path] = ()) -> tuple[Path, ...]:
    """
    Return what a build must not open at all, whatever its permissions.

    Args:
        extra: More paths: other applications' ``.env`` outside the hidden
            applications directory.

    Returns:
        Noust's configuration, state, backups and logs (new and legacy
        names), the Docker socket, and ``extra``.
    """
    found: list[Path] = []
    for path in (
        paths.config_dir(),
        paths.CONFIG_DIR,
        paths.LEGACY_CONFIG_DIR,
        paths.state_dir(),
        paths.STATE_DIR,
        paths.LEGACY_STATE_DIR,
        paths.backup_dir(),
        paths.BACKUP_DIR,
        paths.LEGACY_BACKUP_DIR,
        paths.log_dir(),
        paths.LOG_DIR,
        paths.LEGACY_LOG_DIR,
        Path("/run/docker.sock"),
        *extra,
    ):
        if path not in found:
            found.append(path)
    return tuple(found)


@dataclass(frozen=True)
class SelfTest:
    """
    The outcome of proving the sandbox works on this server.

    Attributes:
        passed: Whether every check held.
        checks: ``(check, held, evidence)`` per check.
    """

    passed: bool
    checks: tuple[tuple[str, bool, str], ...] = ()

    @property
    def detail(self) -> str:
        """The checks, one per line, for an error."""
        return "\n".join(
            f"{'ok' if held else 'FAILED'}: {check}" + (f" ({evidence})" if evidence else "")
            for check, held, evidence in self.checks
        )


_self_test_lock = threading.Lock()
_self_test_passed = False


def forget_self_test() -> None:
    """Run the self-test again before the next sandboxed build; for tests and after a fix."""
    global _self_test_passed
    with _self_test_lock:
        _self_test_passed = False


def self_test(runner: CommandRunner, fs: FileSystem | None = None) -> SelfTest:
    """
    Prove, on this server, that the sandbox blocks what it must.

    A passing result is remembered for the life of the process; a failing one
    is not, so a fix is noticed on the next build.

    Args:
        runner: The runner.
        fs: The filesystem; the process-wide one by default.

    Returns:
        The outcome. Under ``--dry-run`` nothing runs and it passes, since
        nothing will be built either.
    """
    global _self_test_passed
    with _self_test_lock:
        if _self_test_passed:
            return SelfTest(passed=True, checks=(("verified earlier in this process", True, ""),))
        if is_rehearsal():
            return SelfTest(passed=True, checks=(("not verified: this is a rehearsal", True, ""),))
        outcome = _run_self_test(runner, fs or get_fs())
        _self_test_passed = outcome.passed
        return outcome


def _run_self_test(runner: CommandRunner, fs: FileSystem) -> SelfTest:
    """
    Run the canary and judge it from the host.

    Args:
        runner: The runner.
        fs: The filesystem.

    Returns:
        The outcome.
    """
    token = secrets.token_hex(8)
    root = paths.BUILD_CACHE_DIR / ".self-test"
    scratch = root / "scratch"
    # World-writable and outside every writable path: only ProtectSystem can
    # stop the canary writing here, which is what this proves.
    protected = paths.BUILD_CACHE_DIR.parent / "sandbox-canary"
    config_canary = paths.config_dir() / ".noust-sandbox-canary"
    root_canary = ROOT_HOME / f".noust-sandbox-canary-{token}"
    wrote = scratch / f"ok-{token}"
    escaped = protected / f"escaped-{token}"

    fs.make_dir(scratch, mode=0o755)
    fs.make_dir(protected, mode=0o777)
    fs.chmod(protected, 0o777)
    fs.write_text(config_canary, token, mode=0o644)
    runner.run(["chown", f"{BUILD_USER}:{BUILD_GROUP}", str(scratch)], timeout=_SELF_TEST_TIMEOUT)
    spec = SandboxSpec(
        user=BUILD_USER,
        group=BUILD_GROUP,
        writable_paths=(scratch,),
        inaccessible_paths=protected_paths(),
        name="self-test",
    )
    try:
        touched = runner.run(
            ["touch", str(wrote), str(escaped), str(root_canary)],
            sandbox=spec,
            timeout=_SELF_TEST_TIMEOUT,
        )
        read = runner.run(["cat", str(config_canary)], sandbox=spec, timeout=_SELF_TEST_TIMEOUT)
        checks = (
            (
                f"a sandboxed command runs as {BUILD_USER} and writes its own directory",
                wrote.exists(),
                "" if wrote.exists() else (touched.stderr or touched.stdout).strip(),
            ),
            (
                "it cannot write a world-writable directory outside it (ProtectSystem=strict)",
                not escaped.exists(),
                f"{escaped} was created" if escaped.exists() else "",
            ),
            (
                "it cannot write /root (ProtectHome)",
                not root_canary.exists(),
                f"{root_canary} was created" if root_canary.exists() else "",
            ),
            (
                f"it cannot read {paths.config_dir()} (InaccessiblePaths)",
                not read.success and token not in read.stdout,
                f"it read {config_canary}" if token in read.stdout else "",
            ),
        )
    finally:
        for leftover in (wrote, escaped, root_canary, config_canary):
            fs.remove(leftover)
    return SelfTest(passed=all(held for _check, held, _evidence in checks), checks=checks)


def require_working_sandbox(
    runner: CommandRunner, domain: str, fs: FileSystem | None = None
) -> None:
    """
    Make sure a sandboxed build can run here, and refuse the build otherwise.

    Creates the build account first, then runs the self-test.

    Args:
        runner: The runner.
        domain: The application about to build, for the error's advice.
        fs: The filesystem; the process-wide one by default.

    Raises:
        BuildError: The account could not be created or the self-test
            failed. Never falls back to root.
    """
    ensure_build_account(runner)
    outcome = self_test(runner, fs)
    if outcome.passed:
        return
    raise BuildError(
        "The build sandbox does not hold on this server, so the build was stopped "
        "instead of running as root",
        details=(
            f"{outcome.detail}\n\n"
            "systemd must run as PID 1 with mount namespaces available; containers and WSL "
            "often lack them. Fix that and build again. To build this application as root "
            f"anyway, record the decision: noust app sandbox disable {domain} --reason '...'"
        ),
    )


# ---------------------------------------------------------------------------
# Specs
# ---------------------------------------------------------------------------


def _under_home(path: Path) -> bool:
    """
    Tell whether a path is where ProtectHome would hide it.

    Args:
        path: The path.

    Returns:
        True under /home, /root or /run/user.
    """
    return any(
        path == base or base in path.parents
        for base in (Path("/home"), Path("/root"), Path("/run/user"))
    )


def other_env_files(app: App, apps_dir: Path, store: NoustStore | None = None) -> list[Path]:
    """
    List the ``.env`` of every other application that the hidden directory does not cover.

    Args:
        app: The application being built.
        apps_dir: The applications directory, hidden from the build.
        store: The store; the process-wide one by default.

    Returns:
        Their paths.
    """
    from noust.deployers.helpers.layout import env_file_for

    found = []
    for other in (store or get_store()).list_apps():
        if other.domain == app.domain or not other.app_path:
            continue
        env_file = env_file_for(other)
        if apps_dir != env_file and apps_dir not in env_file.parents:
            found.append(env_file)
    return found


def build_spec(
    *,
    app: App | None,
    app_name: str,
    phase: BuildPhase,
    state: SandboxState,
    user: str,
    group: str,
    build_path: Path,
    apps_dir: Path,
    cache: Path,
    env_file: Path | None,
    shared: Path | None,
    memory_max_mb: int | None = None,
    cpu_quota_percent: int | None = None,
    tasks_max: int | None = None,
) -> SandboxSpec:
    """
    Describe the sandbox one install or build command runs in.

    Args:
        app: The application's row, to find the other applications' ``.env``;
            None for one not registered yet.
        app_name: The application's name, which the unit is named after.
        phase: ``install`` or ``build``.
        state: Its build regime, for the network profile and the mode.
        user: The account that builds: ``noust-build`` on releases, the
            service account in place.
        group: Its group.
        build_path: The tree being built: the only application path it writes.
        apps_dir: The applications directory, hidden but for its own paths.
        cache: Its build cache.
        env_file: Its ``.env``, read by systemd for the build; None for none.
        shared: Its ``shared/`` on releases, visible read-only (the persistent
            paths are links into it); None in place.
        memory_max_mb: ``MemoryMax`` of the build.
        cpu_quota_percent: ``CPUQuota`` of the build.
        tasks_max: ``TasksMax`` of the build.

    Returns:
        The spec.
    """
    strict = state.network == NetworkProfile.STRICT.value
    read_only = (shared,) if shared is not None else ()
    writable = (build_path, cache)
    # A preview with the network gets its variables filtered through the
    # command's environment instead (preview_build_variables): the file holds
    # production's secrets.
    networked_preview = app is not None and app.preview_parent is not None and not strict
    env_files: tuple[Path, ...] = ()
    if (
        env_file is not None
        and not (strict and phase is BuildPhase.INSTALL)
        and not networked_preview
    ):
        env_files = (env_file,)
    # Readable by the service account only; bound empty so a dotenv loader
    # reads nothing instead of failing, and the values come from env_files.
    masked = (
        (env_file,) if env_file is not None and shared is not None and env_file.is_file() else ()
    )
    others = other_env_files(app, apps_dir) if app is not None else []
    return SandboxSpec(
        user=user,
        group=group,
        writable_paths=writable,
        read_only_paths=read_only,
        inaccessible_paths=protected_paths(others),
        hidden_paths=(apps_dir, paths.BUILD_CACHE_DIR),
        env_files=env_files,
        masked_files=masked,
        memory_max_mb=memory_max_mb,
        cpu_quota_percent=cpu_quota_percent,
        tasks_max=tasks_max,
        network="none" if strict and phase is BuildPhase.BUILD else "full",
        working_dir=build_path,
        protect_home=not any(_under_home(path) for path in (*writable, *read_only)),
        pty=state.pty,
        name=app_name,
    )


def release_spec(
    *, app_name: str, user: str, group: str, build_path: Path, env_file: Path | None
) -> SandboxSpec:
    """
    Describe how a migration runs: as the application, with its secrets.

    Args:
        app_name: The application's name.
        user: Its service account.
        group: Its group.
        build_path: The release being migrated for.
        env_file: Its ``.env``.

    Returns:
        The spec: its unit's own regime, not a build's.
    """
    return SandboxSpec(
        user=user,
        group=group,
        env_files=(env_file,) if env_file is not None else (),
        working_dir=build_path,
        strict=False,
        name=f"{app_name}-release",
    )


#: How old a sandbox's environment file must be before a sweep may remove it:
#: systemd reads it when the unit starts, and a build starting while the sweep
#: runs has written it and not started yet.
SWEEP_FILE_AGE_S = 300

#: Deadline of each ``systemctl`` of the sweep.
_SWEEP_TIMEOUT = 30


@dataclass
class SweepReport:
    """
    What a sweep of the build units found and did.

    Attributes:
        reset: Failed units whose state was cleared.
        stopped: Units stopped for running past their own RuntimeMaxSec.
        files: Environment files of units that are gone, removed.
    """

    reset: list[str] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


def _unit_properties(runner: CommandRunner, unit: str) -> dict[str, str]:
    """
    Read what the sweep needs to know about one unit.

    Args:
        runner: The runner.
        unit: The unit.

    Returns:
        ``ActiveState``, ``ActiveEnterTimestampMonotonic`` and
        ``RuntimeMaxUSec``; empty when systemd could not say.
    """
    result = runner.run(
        [
            "systemctl",
            "show",
            unit,
            "-p",
            "ActiveState,ActiveEnterTimestampMonotonic,RuntimeMaxUSec",
        ],
        timeout=_SWEEP_TIMEOUT,
    )
    if not result.success:
        return {}
    return dict(line.partition("=")[::2] for line in result.stdout.splitlines() if "=" in line)


def _overdue(properties: dict[str, str], now_us: int) -> bool:
    """
    Tell whether an active unit has run past its own RuntimeMaxSec.

    Args:
        properties: Its properties.
        now_us: The monotonic clock, in microseconds.

    Returns:
        True when it has; False when it has not or systemd did not say.
    """
    try:
        entered = int(properties.get("ActiveEnterTimestampMonotonic", "0"))
        limit = int(properties.get("RuntimeMaxUSec", ""))
    except ValueError:
        # "infinity", or nothing: no deadline to be past.
        return False
    return entered > 0 and now_us - entered > limit


def sweep_orphaned_builds(
    runner: CommandRunner, *, now_us: Callable[[], int] | None = None
) -> SweepReport:
    """
    Clean up the build units a process that died left behind.

    Called when the console and the monitor start. A failed unit is reset (it
    holds nothing but its state); an active one is stopped only when it is past
    its own ``RuntimeMaxSec``, which systemd should have enforced; one inside
    its deadline may be another process's build and is left alone. The
    environment file (the build's secrets) of a unit that is gone is removed.

    Args:
        runner: The runner.
        now_us: The monotonic clock in microseconds (tests); systemd's
            timestamps are on the same clock.

    Returns:
        What was done. Nothing, when systemd cannot be asked.
    """
    import time

    report = SweepReport()
    listed = runner.run(
        [
            "systemctl",
            "list-units",
            "--all",
            "--plain",
            "--no-legend",
            "--no-pager",
            f"{SANDBOX_UNIT_PREFIX}*.service",
        ],
        timeout=_SWEEP_TIMEOUT,
    )
    if not listed.success:
        return report
    clock = now_us or (lambda: int(time.monotonic() * 1_000_000))
    live: set[str] = set()
    for line in listed.stdout.splitlines():
        unit = line.split(maxsplit=1)[0] if line.strip() else ""
        if not unit.startswith(SANDBOX_UNIT_PREFIX):
            continue
        properties = _unit_properties(runner, unit)
        state = properties.get("ActiveState", "")
        if state == "failed":
            if runner.run(["systemctl", "reset-failed", unit], timeout=_SWEEP_TIMEOUT).success:
                report.reset.append(unit)
        elif state in ("active", "activating", "deactivating"):
            if _overdue(properties, clock()):
                if runner.run(["systemctl", "stop", unit], timeout=_SWEEP_TIMEOUT).success:
                    report.stopped.append(unit)
                    continue
            live.add(unit.removesuffix(".service"))
    report.files = _remove_stale_environment_files(live)
    return report


def sweep_at_start() -> SweepReport | None:
    """
    Sweep the orphaned build units when a long-lived process starts. Never raises.

    The console and the monitor call it once; a machine where systemd cannot
    be asked, or a failure of the sweep, is logged and left for the next start.

    Returns:
        What was done, or None when the sweep failed or this process is not
        root (only root starts a sandbox, so only root has one to sweep).
    """
    from noust.core.runner import get_runner

    if not running_as_root():
        return None
    log = logging.getLogger(__name__)
    try:
        report = sweep_orphaned_builds(get_runner())
    except (NoustError, OSError) as exc:
        log.warning("The orphaned build units could not be swept: %s", exc)
        return None
    if report.reset or report.stopped or report.files:
        log.info(
            "Swept orphaned build units: %d reset, %d stopped, %d environment file(s) removed",
            len(report.reset),
            len(report.stopped),
            len(report.files),
        )
    return report


def _remove_stale_environment_files(live: set[str]) -> list[str]:
    """
    Remove the environment files of build units that are gone.

    Args:
        live: The units still running, without ``.service``.

    Returns:
        The files removed.
    """
    import time

    from noust.core.runner import sandbox_runtime_dir

    directory = sandbox_runtime_dir()
    if not directory.is_dir():
        return []
    removed: list[str] = []
    horizon = time.time() - SWEEP_FILE_AGE_S
    fs = get_fs()
    for path in sorted(directory.glob(f"{SANDBOX_UNIT_PREFIX}*.env")):
        if path.name.removesuffix(".env") in live:
            continue
        try:
            if path.is_symlink() or path.stat().st_mtime > horizon:
                continue
            fs.remove(path)
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "The environment file %s could not be removed: %s", path, exc
            )
            continue
        removed.append(path.name)
    return removed


def decide_regime(
    domain: str, *, store: NoustStore, logger: Logger, forced: bool = False
) -> SandboxState | None:
    """
    Decide, once per deployment, whether a build runs in the sandbox, and say so in its log.

    The one decision every deployer's chokepoint takes (BaseDeployer's and the
    monorepo's ``_run``), so the two cannot disagree on who builds as root.

    Args:
        domain: The application.
        store: The store.
        logger: The deployment's logger, told the regime.
        forced: Build in the sandbox whatever the regime says: a trial build.

    Returns:
        The application's regime when its builds are sandboxed; None when
        they run as this process: its regime says root (with a warning in the
        log), or this process is not root and has no privilege to take away.
    """
    if not running_as_root() or not domain:
        return None
    try:
        state = get_state(domain, store=store)
    except ValidationError:
        # Not registered: a deployer driven by hand. Nothing records a regime
        # for it, so it keeps the one it always had.
        state = SandboxState(domain=domain)
    if forced:
        state = replace(state, mode=SandboxMode.ON.value)
    log_regime(logger, state, sandboxed=state.enabled)
    return state if state.enabled else None


def command_environment(
    state: SandboxState,
    phase: BuildPhase,
    *,
    configured: Mapping[str, str],
    given: Mapping[str, str] | None,
    cache: Path,
) -> dict[str, str]:
    """
    Compose the variables a sandboxed install or build is given.

    Args:
        state: The application's regime, for its network profile.
        phase: ``install`` or ``build``.
        configured: The application's variables with ``given`` merged over them.
        given: What the caller passed for this one command.
        cache: The application's build cache, which every package manager's
            home and cache point into.

    Returns:
        The variables. The strict profile installs without the application's
        variables: a preview's are a copy of production's.
    """
    strict_install = phase is BuildPhase.INSTALL and state.network == NetworkProfile.STRICT.value
    chosen = dict(given or {}) if strict_install else dict(configured)
    return {**build_environment(cache), **chosen}


def log_regime(logger: Logger, state: SandboxState, *, sandboxed: bool) -> None:
    """
    Say in the build log how this build runs.

    Args:
        logger: The deployment's logger.
        state: The application's regime.
        sandboxed: Whether this build is sandboxed.
    """
    if sandboxed:
        profile = " (strict network)" if state.network == NetworkProfile.STRICT.value else ""
        logger.substep(f"Building in the sandbox as {BUILD_USER}{profile}")
    elif state.mode == SandboxMode.OFF.value:
        logger.warning(
            f"Building as root: the sandbox is off for this application ({state.reason})"
        )
    elif state.mode == SandboxMode.LEGACY.value:
        logger.warning(
            "Building as root, as applications from before 3.1 do. Test the sandbox with: "
            f"noust app sandbox test {state.domain}"
        )
