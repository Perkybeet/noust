# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust incident freeze``: evidence first, then a locked door (ENS G17: op.exp.7.r2, op.exp.9).

op.exp.7.r2 asks, for an incident, to "detener servicios, aislar el sistema,
recoger evidencias, proteger registros"; op.exp.9.2 asks for evidence that
can stand up. A freeze:

1. **Collects** into one owner-only directory under the store's
   ``incidents/``: the audit log files and the chain's verification, journal
   excerpts (the console, the monitor, SSH, and every warning of the period),
   the configuration with its secrets replaced, a consistent snapshot of the
   store, the running units, the listening sockets, and the console's
   sessions and tokens (no hash, no token).
2. **Hashes** every file into ``MANIFEST.sha256`` (``sha256sum -c`` reads it)
   and records the manifest's own SHA-256 in the ``incident.freeze`` audit
   event. The event is shipped off the machine (journald, syslog), so a
   package altered afterwards no longer matches what the receiver holds.
3. **Locks the console down** (unless told not to): every new session of an
   account is refused at the one place sessions are made
   (:meth:`noust.web.auth.TokenManager.create_session`, rule 4), while the
   master token, the break-glass way in, still signs in. Sessions already
   open can be revoked too.

``noust incident unfreeze`` lifts the lockdown; it is an audit event. A tool
missing from the machine (``ss``, ``journalctl``) makes its part of the
package a note in ``problems``, never a freeze that did not happen.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from noust.core import paths
from noust.core.audit import get_log, record
from noust.core.exceptions import NoustError
from noust.core.exceptions import PermissionError as NoustPermissionError
from noust.core.fs import SECRET_DIR_MODE, SECRET_MODE, FileSystem, get_fs
from noust.core.runner import CommandRunner, get_runner

if TYPE_CHECKING:
    from noust.core.store import NoustStore

#: The lockdown marker, beside the store, read on every new session.
LOCKDOWN_FILE = "incident-lockdown.json"

#: Where packages go, beside the store.
INCIDENTS_DIR = "incidents"

#: Journal excerpts, by unit (the ``.service`` suffix added).
JOURNAL_UNITS: tuple[str, ...] = (paths.WEB_UNIT, paths.MONITOR_UNIT, "ssh", "sshd")

#: How long a probe of the machine may take.
PROBE_TIMEOUT = 60

#: The files the manifest is written to.
MANIFEST_JSON = "manifest.json"
MANIFEST_SHA256 = "MANIFEST.sha256"


class IncidentLockdownError(NoustPermissionError):
    """The console is locked down for an incident: new sessions are refused."""


@dataclass
class FreezeResult:
    """
    What a freeze did.

    Attributes:
        directory: The package.
        manifest_sha256: SHA-256 of ``MANIFEST.sha256``, also in the audit event.
        files: The files in the package, relative, with their SHA-256.
        problems: What could not be collected, in the tool's own words.
        locked: Whether the console was locked down.
        sessions_revoked: Sessions revoked, when that was asked for.
    """

    directory: Path
    manifest_sha256: str
    files: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    locked: bool = False
    sessions_revoked: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "directory": str(self.directory),
            "manifest_sha256": self.manifest_sha256,
            "files": self.files,
            "problems": self.problems,
            "locked": self.locked,
            "sessions_revoked": self.sessions_revoked,
        }


def _store(store: NoustStore | None) -> NoustStore:
    if store is not None:
        return store
    from noust.core.store import get_store

    return get_store()


def lockdown_path(store: NoustStore | None = None) -> Path:
    """
    Args:
        store: The store; the process-wide one by default.

    Returns:
        Where the lockdown marker lives: beside the store, which the console
        and the CLI both reach.
    """
    return _store(store).db_path.parent / LOCKDOWN_FILE


def lockdown_state(store: NoustStore | None = None) -> dict[str, Any] | None:
    """
    Read the lockdown, if the console is locked down.

    Args:
        store: The store; the process-wide one by default.

    Returns:
        ``since``, ``by``, ``reason`` and ``package``; None when not locked.
        A marker that cannot be read counts as a lockdown: it fails closed.
    """
    path = lockdown_path(store)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError as exc:
        return {"since": None, "by": None, "reason": f"unreadable lockdown marker: {exc}"}
    with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
        text = handle.read()
    try:
        data = json.loads(text)
    except ValueError:
        return {"since": None, "by": None, "reason": "unreadable lockdown marker"}
    return data if isinstance(data, dict) else {"reason": "unreadable lockdown marker"}


def refuse_new_session(
    *, account_id: int | None, client_ip: str, store: NoustStore | None = None
) -> None:
    """
    Refuse a new console session of an account while the console is locked down.

    Called where every session is made. The master token (``account_id``
    None) is the break-glass way in and still signs in.

    Args:
        account_id: The account signing in; None for the master token.
        client_ip: Where from, for the record.
        store: The store; the process-wide one by default.

    Raises:
        IncidentLockdownError: The console is locked down and this is an account.
    """
    if account_id is None:
        return
    state = lockdown_state(store)
    if state is None:
        return
    record(
        "auth.lockdown.denied",
        target=f"account:{account_id}",
        outcome="denied",
        details={"client_ip": client_ip, "since": state.get("since")},
    )
    raise IncidentLockdownError(
        "The console is locked down for a security incident: sign-in is suspended",
        details=(
            "Only the emergency master token signs in until the security officer lifts the "
            "lockdown with 'noust incident unfreeze' on the server."
        ),
    )


def lock_down(
    *,
    actor: str,
    reason: str,
    package: Path | None = None,
    store: NoustStore | None = None,
    fs: FileSystem | None = None,
) -> dict[str, Any]:
    """
    Lock the console down: no new session of an account until it is lifted.

    Args:
        actor: Who locked it.
        reason: Why: the incident reference.
        package: The evidence package taken with it, if any.
        store: The store; the process-wide one by default.
        fs: The filesystem seam; the process's own by default.

    Returns:
        The lockdown as recorded.
    """
    state = {
        "since": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "by": actor,
        "reason": reason,
        "package": str(package) if package else None,
    }
    fs = fs or get_fs()
    fs.write_text(lockdown_path(store), json.dumps(state, indent=2), mode=SECRET_MODE)
    record("incident.lockdown", target="console", details=state)
    return state


def lift_lockdown(
    *,
    actor: str,
    reason: str,
    store: NoustStore | None = None,
    fs: FileSystem | None = None,
) -> dict[str, Any] | None:
    """
    Lift the lockdown.

    Args:
        actor: Who lifted it.
        reason: Why.
        store: The store; the process-wide one by default.
        fs: The filesystem seam; the process's own by default.

    Returns:
        The lockdown that was lifted, or None when there was none.
    """
    state = lockdown_state(store)
    if state is None:
        return None
    fs = fs or get_fs()
    fs.remove(lockdown_path(store))
    record(
        "incident.unfreeze",
        target="console",
        details={"reason": reason, "by": actor, "locked_since": state.get("since")},
    )
    return state


# -- the package --------------------------------------------------------------------


class _Package:
    """Writes files into the package, each 0600, and remembers what failed."""

    def __init__(self, root: Path, fs: FileSystem) -> None:
        self.root = root
        self.fs = fs
        self.problems: list[str] = []

    def path(self, relative: str) -> Path:
        target = self.root / relative
        self.fs.make_dir(target.parent, mode=SECRET_DIR_MODE, parents=True)
        return target

    def text(self, relative: str, content: str) -> None:
        self.fs.write_text(self.path(relative), content, mode=SECRET_MODE)

    def json(self, relative: str, value: Any) -> None:
        self.text(relative, json.dumps(value, indent=2, sort_keys=True, default=str))

    def copy(self, source: Path, relative: str) -> None:
        target = self.path(relative)
        # Owner-only from the moment it exists; never through a symlink.
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        with (
            source.open("rb") as reader,
            os.fdopen(os.open(target, flags, SECRET_MODE), "wb") as writer,
        ):
            while chunk := reader.read(1024 * 1024):
                writer.write(chunk)
        self.fs.chmod(target, SECRET_MODE)


def _run(package: _Package, runner: CommandRunner, argv: Sequence[str], relative: str) -> None:
    """
    Run a read-only probe and keep its output, verbatim.

    Args:
        package: The package.
        runner: The command runner.
        argv: The probe.
        relative: Where its output goes.
    """
    if not runner.exists(argv[0]):
        package.problems.append(f"{argv[0]} is not installed: {relative} was not collected")
        return
    result = runner.run(list(argv), timeout=PROBE_TIMEOUT)
    body = result.stdout or ""
    if not result.success:
        package.problems.append(
            f"{' '.join(argv)} exited {result.exit_code}: {(result.stderr or '').strip()[:300]}"
        )
        body += f"\n--- stderr (exit {result.exit_code}) ---\n{result.stderr or ''}"
    package.text(relative, body)


def _collect_audit(package: _Package) -> None:
    log = get_log()
    for index, path in enumerate(log.files_oldest_first()):
        try:
            package.copy(path, f"audit/{index:03d}-{path.name}.log")
        except OSError as exc:
            package.problems.append(f"Could not copy the audit file {path}: {exc}")
    try:
        result = log.verify()
    except OSError as exc:
        package.problems.append(f"Could not verify the audit chain: {exc}")
        return
    package.json("audit/verify.json", result.to_dict())


def _collect_journal(package: _Package, runner: CommandRunner, hours: int) -> None:
    since = f"-{hours}h"
    for unit in JOURNAL_UNITS:
        _run(
            package,
            runner,
            [
                "journalctl",
                "-u",
                f"{unit}.service",
                "--since",
                since,
                "--no-pager",
                "-o",
                "short-iso-precise",
            ],
            f"journal/{unit}.log",
        )
    _run(
        package,
        runner,
        ["journalctl", "--since", since, "-p", "warning", "--no-pager", "-o", "short-iso-precise"],
        "journal/warnings.log",
    )


def _collect_config(package: _Package) -> None:
    from noust.core.config import Config, redact_secrets

    try:
        package.json("config/config.redacted.json", redact_secrets(Config().to_dict()))
    except (OSError, NoustError) as exc:
        package.problems.append(f"Could not read the configuration: {exc}")


def _collect_store(package: _Package, store: NoustStore) -> None:
    target = package.path("store/noust.db")
    try:
        reader = sqlite3.connect(f"file:{store.db_path}?mode=ro", uri=True, timeout=30)
        writer = sqlite3.connect(target)
        try:
            reader.backup(writer)
        finally:
            writer.close()
            reader.close()
        package.fs.chmod(target, SECRET_MODE)
    except (sqlite3.Error, OSError) as exc:
        package.problems.append(f"Could not snapshot the store {store.db_path}: {exc}")


def _collect_console(
    package: _Package,
    sessions: Callable[[], list[dict[str, Any]]] | None,
    tokens: Callable[[], list[dict[str, Any]]] | None,
) -> None:
    for name, source in (("sessions", sessions), ("tokens", tokens)):
        if source is None:
            continue
        try:
            package.json(f"console/{name}.json", source())
        except (OSError, NoustError, sqlite3.Error) as exc:
            package.problems.append(f"Could not list the console's {name}: {exc}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def freeze(
    *,
    reason: str,
    actor: str,
    lock: bool = True,
    journal_hours: int = 24,
    output_root: Path | None = None,
    runner: CommandRunner | None = None,
    fs: FileSystem | None = None,
    store: NoustStore | None = None,
    sessions: Callable[[], list[dict[str, Any]]] | None = None,
    tokens: Callable[[], list[dict[str, Any]]] | None = None,
    revoke: Callable[[], int] | None = None,
) -> FreezeResult:
    """
    Take the evidence package, then lock the console down.

    Args:
        reason: The incident reference, recorded with everything.
        actor: Who froze it.
        lock: Lock the console down after the package is taken.
        journal_hours: How far back the journal excerpts go.
        output_root: Where packages go; ``incidents/`` beside the store.
        runner: The command runner; the process's own by default.
        fs: The filesystem seam; the process's own by default.
        store: The store; the process-wide one by default.
        sessions: Lists the console's sessions, when it can be reached.
        tokens: Lists its API tokens, without hashes.
        revoke: Revokes every console session; called when given, after the
            lists were taken, and returns how many.

    Returns:
        What was done.
    """
    runner = runner or get_runner()
    fs = fs or get_fs()
    store = _store(store)
    now = datetime.now(timezone.utc)
    root = (output_root or store.db_path.parent / INCIDENTS_DIR) / now.strftime("%Y%m%dT%H%M%SZ")
    fs.make_dir(root.parent, mode=SECRET_DIR_MODE, parents=True)
    fs.make_dir(root, mode=SECRET_DIR_MODE, parents=True, exist_ok=False)
    package = _Package(root, fs)

    _collect_audit(package)
    _collect_journal(package, runner, journal_hours)
    _collect_config(package)
    _collect_store(package, store)
    _run(
        package,
        runner,
        ["systemctl", "list-units", "--type=service", "--state=running", "--no-pager", "--plain"],
        "system/running-units.txt",
    )
    _run(package, runner, ["ss", "-tulpn"], "system/listening-sockets.txt")
    _collect_console(package, sessions, tokens)

    from noust import __version__

    head = get_log().head()
    files = sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    entries = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256(path),
            "size": path.stat().st_size,
        }
        for path in files
    ]
    package.json(
        MANIFEST_JSON,
        {
            "format": "noust-incident-package",
            "version": 1,
            "created_at": now.isoformat(timespec="seconds"),
            "host": socket.gethostname(),
            "noust_version": __version__,
            "reason": reason,
            "actor": actor,
            "journal_hours": journal_hours,
            "audit_head": {"seq": head[0], "mac": head[1]} if head else None,
            "files": entries,
            "problems": package.problems,
        },
    )
    manifest_path = root / MANIFEST_JSON
    entries.append(
        {
            "path": MANIFEST_JSON,
            "sha256": _sha256(manifest_path),
            "size": manifest_path.stat().st_size,
        }
    )
    package.text(MANIFEST_SHA256, "".join(f"{e['sha256']}  {e['path']}\n" for e in entries))
    manifest_sha256 = _sha256(root / MANIFEST_SHA256)

    record(
        "incident.freeze",
        target=f"incident:{root.name}",
        details={
            "reason": reason,
            "package": str(root),
            "manifest_sha256": manifest_sha256,
            "files": len(entries),
            "problems": package.problems[:10],
            "lock": lock,
        },
    )
    result = FreezeResult(
        directory=root,
        manifest_sha256=manifest_sha256,
        files=entries,
        problems=package.problems,
    )
    if lock:
        lock_down(actor=actor, reason=reason, package=root, store=store, fs=fs)
        result.locked = True
    if revoke is not None:
        result.sessions_revoked = revoke()
    return result
