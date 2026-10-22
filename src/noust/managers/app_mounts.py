# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Files and directories an application is given beyond its repository (checklist B-53).

A configuration file that is not in git (a ``.htpasswd``, a certificate, a
``config/production.json`` with a token) has to be somewhere every release finds
it, and an operator has to be able to read and change it from the console. Two
kinds of mount:

- **A file** Noust keeps for the application. On the release layout it lives in
  ``shared/<target>`` and is linked into every release exactly like a persistent
  path (:meth:`~noust.deployers.releases.ReleaseManager.link_shared`), so a new
  release has it before it starts; in place it is written at ``<app>/<target>``;
  for a container application it is a file under ``<app>/mounts/`` bound at
  ``target`` in the container. Its content is the file on disk, never a column:
  there is one copy to edit, back up and restore. It is written with the mode the
  operator names (0600 by default) and handed to the application's account, and
  never through a link: a path whose parent is one is refused.
- **A path of the server** bound into a container application, read-only unless
  asked. It is root-equivalent (a container that writes ``/etc`` owns the host), so
  it refuses the server's own state and the Docker socket and, under the ENS
  profile, anything but read-only.

An environment file is not a file mount: the Environment validates every name and
value and masks them, and this would be a way around it. A static site and a Compose
stack take none (the stack's own compose file mounts what it needs).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from noust.core import app_mount_store, audit, paths, retired_names
from noust.core.app_mount_store import DEFAULT_MODE, KIND_FILE, KIND_PATH, MountRecord
from noust.core.applock import app_lock
from noust.core.exceptions import DeploymentError, NoustError, ServiceError, ValidationError
from noust.core.fs import FileSystem, get_fs
from noust.core.logger import Logger
from noust.core.store import App, NoustStore, get_store
from noust.deployers.helpers.layout import RELEASES, app_root
from noust.deployers.helpers.permissions import hand_over_file
from noust.deployers.releases import ReleaseManager, first_obstacle, persistent_path
from noust.managers.app_files import is_env_file
from noust.managers.app_identity import service_account
from noust.validators.domain import validate_domain

logger = logging.getLogger(__name__)

#: The largest file mount, in bytes: what Coolify allows, and a config file by far.
MAX_FILE_BYTES = 5 * 1024 * 1024

#: Where the files of a container application's mounts are kept, beside its compose file.
CONTAINER_MOUNTS_DIR = "mounts"

#: What a bound path may not be: the server's own state and the keys to the machine.
_FORBIDDEN_SOURCES = (
    "/",
    "/boot",
    "/dev",
    "/proc",
    "/sys",
    "/root",
    "/etc/noust",
    "/etc/shadow",
    "/etc/sudoers",
    "/etc/ssh",
    "/var/lib/noust",
    "/var/backups/noust",
    "/var/run/docker.sock",
    "/run/docker.sock",
)


@dataclass(frozen=True)
class MountView:
    """
    A mount as the console shows it.

    Attributes:
        record: What is stored.
        path: Where its file is on this server (a ``file``), or its source (a ``path``).
        exists: Whether that is there.
        size: A file's size in bytes.
        modified: When a file last changed, as an ISO timestamp in UTC.
    """

    record: MountRecord
    path: str
    exists: bool
    size: int | None = None
    modified: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The mount as JSON-ready data. Never a file's content.
        """
        record = self.record
        return {
            "kind": record.kind,
            "target": record.target,
            "source": record.source,
            "mode": f"{record.mode:o}",
            "read_only": record.read_only,
            "path": self.path,
            "exists": self.exists,
            "size": self.size,
            "modified": self.modified,
            "updated_by": record.updated_by,
            "updated_at": record.updated_at,
        }


def unsupported_reason(app: App) -> str | None:
    """
    Say why an application takes no mounts.

    Args:
        app: Its row.

    Returns:
        The reason, or None when it does.
    """
    if app.app_type == "docker-compose":
        return "a stack mounts what it needs in its own compose file"
    if app.app_type == "static" or (app.is_static and app.app_type != "php-fpm"):
        return "a static site serves its files straight from disk"
    return None


def is_container(app: App) -> bool:
    """
    Tell whether an application runs as a container Noust describes.

    Args:
        app: Its row.

    Returns:
        True for the ``container`` type.
    """
    return app.app_type == "container"


def clean_target(app: App, raw: str, kind: str) -> str:
    """
    Check where a mount appears in the application.

    Args:
        app: The application.
        raw: What the operator gave.
        kind: ``file`` or ``path``.

    Returns:
        A path inside the application (relative, no ``..``), or inside the
        container (absolute) for a container application.

    Raises:
        ValidationError: It is not such a path, or names an environment file.
    """
    how = (
        "Give the absolute path inside the container, such as /etc/app/config.json."
        if is_container(app)
        else "Give a path inside the application, such as config/production.json."
    )
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw or "\n" in raw:
        raise ValidationError("That is not a path", details=how, field="target")
    text = raw.strip()
    if is_container(app):
        candidate = PurePosixPath(text)
        if not candidate.is_absolute() or ".." in candidate.parts or str(candidate) == "/":
            raise ValidationError(
                f"{text!r} is not a path inside the container", details=how, field="target"
            )
        if any(char in text for char in ":,"):
            raise ValidationError(
                f"{text!r} cannot be mounted: it holds a ':' or a ','",
                details="Those separate the parts of a mount.",
                field="target",
            )
        cleaned = str(candidate)
    else:
        try:
            cleaned = str(persistent_path(text))
        except DeploymentError as exc:
            raise ValidationError(exc.message, details=how, field="target") from exc
    if kind == KIND_FILE and is_env_file(PurePosixPath(cleaned)):
        raise ValidationError(
            "An environment file is not a file mount",
            details="Use the application's Environment: it validates every name and value, "
            "masks the secrets and records who revealed them.",
            field="target",
        )
    return cleaned


def clean_source(raw: str) -> str:
    """
    Check a path of the server that is to be bound into a container.

    Args:
        raw: What the operator gave.

    Returns:
        The path without a trailing slash.

    Raises:
        ValidationError: It is relative, climbs, holds a character a mount would
            misread, is the server's own state or the Docker socket, or is not there.
    """
    how = "Name an existing path on this server, such as /srv/shared-assets."
    if not isinstance(raw, str) or not raw.startswith("/") or "\x00" in raw or "\n" in raw:
        raise ValidationError(f"{raw!r} is not an absolute path", details=how, field="source")
    if any(char in raw for char in ":,"):
        raise ValidationError(
            f"{raw!r} cannot be mounted: it holds a ':' or a ','",
            details="Those separate the parts of a mount.",
            field="source",
        )
    cleaned = raw.rstrip("/") or "/"
    if ".." in cleaned.split("/"):
        raise ValidationError("A path cannot climb with ..", details=how, field="source")
    protected = (
        *_FORBIDDEN_SOURCES,
        *(str(old) for old in retired_names.old_data_roots()),
        str(paths.config_dir()),
        str(paths.state_dir()),
        str(paths.backup_dir()),
    )
    for guarded in protected:
        if cleaned == guarded or (guarded != "/" and cleaned.startswith(guarded + "/")):
            raise ValidationError(
                f"{cleaned} cannot be bound into a container",
                details="It is the server's own state or a key to the machine: a container "
                "that reads it owns the server.",
                field="source",
            )
    if not os.path.lexists(cleaned):
        raise ValidationError(
            f"{cleaned} does not exist on this server", details=how, field="source"
        )
    return cleaned


class MountManager:
    """
    Defines, writes and removes the mounts of applications.

    Every public method that changes anything takes the application's lock.
    """

    def __init__(
        self,
        *,
        store: NoustStore | None = None,
        fs: FileSystem | None = None,
        log: Logger | None = None,
    ) -> None:
        """
        Initialize the manager.

        Args:
            store: The store; the process-wide one by default.
            fs: The filesystem every change goes through; the process-wide one
                by default, so ``--dry-run`` reaches it.
            log: Where a failed hand-over is reported.
        """
        self._store = store
        self._fs = fs
        self.log = log or Logger()

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    @property
    def fs(self) -> FileSystem:
        """The filesystem."""
        return self._fs or get_fs()

    # -- Where things are --------------------------------------------------------

    def _app(self, domain: str) -> App:
        """
        Return an application's row.

        Args:
            domain: The application.

        Returns:
            The row.

        Raises:
            NoustError: Nothing is deployed there.
            ValidationError: The application takes no mounts.
        """
        app = self.store.get_app(validate_domain(domain))
        if app is None:
            raise NoustError(
                f"Application not found: {domain}",
                details="Run 'noust list' to see what is deployed.",
            )
        reason = unsupported_reason(app)
        if reason is not None:
            raise ValidationError(
                f"{domain} takes no mounts: {reason}",
                details="Nothing was changed.",
                field="domain",
            )
        return app

    def file_path(self, app: App, target: str) -> Path:
        """
        Name where a file mount's file is on this server.

        Args:
            app: The application.
            target: Where it appears in the application.

        Returns:
            ``shared/<target>`` on the release layout, ``<app>/<target>`` in place,
            ``<app>/mounts/<slug>`` for a container application.
        """
        root = app_root(app)
        if is_container(app):
            slug = target.strip("/").replace("/", "__")
            return root / CONTAINER_MOUNTS_DIR / slug
        if app.layout == RELEASES:
            return root / "shared" / target
        return root / target

    def _view(self, app: App, record: MountRecord) -> MountView:
        """
        Look at a mount on disk.

        Args:
            app: The application.
            record: The mount.

        Returns:
            Where it is and whether it is there.
        """
        if record.kind == KIND_PATH:
            where = record.source or ""
            return MountView(record=record, path=where, exists=os.path.lexists(where))
        path = self.file_path(app, record.target)
        if not path.is_file() or path.is_symlink():
            return MountView(record=record, path=str(path), exists=False)
        info = path.stat()
        return MountView(
            record=record,
            path=str(path),
            exists=True,
            size=info.st_size,
            modified=datetime.fromtimestamp(info.st_mtime, tz=timezone.utc).isoformat(),
        )

    # -- Reading -------------------------------------------------------------------

    def list_mounts(self, domain: str) -> list[MountView]:
        """
        List an application's mounts. Never their content.

        Args:
            domain: The application.

        Returns:
            Each mount and whether it is there.

        Raises:
            NoustError: The application is unknown.
        """
        app = self._app(domain)
        return [
            self._view(app, record)
            for record in app_mount_store.list_mounts(app.domain, store=self.store)
        ]

    def read_file(self, domain: str, target: str, *, actor: str = "system") -> str:
        """
        Read the content of a file mount, for editing.

        Args:
            domain: The application.
            target: Where it appears in the application.
            actor: Who asked, for the audit trail.

        Returns:
            Its text.

        Raises:
            ValidationError: There is no such file mount, or the file is not text.
        """
        app = self._app(domain)
        record = app_mount_store.get_mount(app.domain, target, store=self.store)
        if record is None or record.kind != KIND_FILE:
            raise ValidationError(f"{app.domain} has no file mounted at {target!r}", field="target")
        path = self.file_path(app, record.target)
        if path.is_symlink() or not path.is_file():
            raise ValidationError(f"The file of {target} is not there", field="target")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError(f"{target} is not a text file", field="target") from exc
        audit.record(
            "apps.mounts",
            target=f"app:{app.domain}",
            details={"action": "read", "target": target, "by": actor},
        )
        return text

    def bind_mounts(self, domain: str) -> tuple[tuple[str, str, bool], ...]:
        """
        Name what the compose file of a container application binds.

        Args:
            domain: The application.

        Returns:
            ``(source on the server, target in the container, read_only)`` for
            every mount; empty for an application that is not a container.
        """
        app = self.store.get_app(domain)
        if app is None or not is_container(app):
            return ()
        binds = []
        for record in app_mount_store.list_mounts(domain, store=self.store):
            source = (
                record.source
                if record.kind == KIND_PATH
                else str(self.file_path(app, record.target))
            )
            if source:
                binds.append((source, record.target, record.read_only))
        return tuple(binds)

    def host_targets(self, domain: str) -> list[str]:
        """
        Name the files a release must be linked to, like persistent paths.

        Args:
            domain: The application.

        Returns:
            The targets of its file mounts; empty for a container application,
            which binds them instead.
        """
        app = self.store.get_app(domain)
        if app is None or is_container(app):
            return []
        return [
            record.target
            for record in app_mount_store.list_mounts(domain, store=self.store)
            if record.kind == KIND_FILE
        ]

    # -- Changing --------------------------------------------------------------------

    def put_file(
        self,
        domain: str,
        target: str,
        content: str,
        *,
        mode: int = DEFAULT_MODE,
        actor: str = "system",
    ) -> MountView:
        """
        Create a file mount, or change the content of one.

        Args:
            domain: The application.
            target: Where it appears in the application.
            content: Its text.
            mode: Its permissions.
            actor: Who asked, for the audit trail.

        Returns:
            The mount, as it is on disk.

        Raises:
            ValidationError: The target, the content or the mode is not one a file can have.
            ServiceError: The file could not be written or handed to the application's account.
            AppBusyError: Another operation is running on the application.
        """
        app = self._app(domain)
        cleaned = clean_target(app, target, KIND_FILE)
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES or "\x00" in content:
            raise ValidationError(
                f"A file mount is text up to {MAX_FILE_BYTES // 1024 // 1024} MiB",
                details="Upload a larger or binary file with the file browser.",
                field="content",
            )
        if not 0o400 <= mode <= 0o777:
            raise ValidationError(f"{mode:o} is not a mode a file can have", field="mode")
        path = self.file_path(app, cleaned)
        with app_lock(app.domain, "mounts change"):
            self._refuse_obstacles(app, path)
            self.fs.make_dir(path.parent)
            self.fs.write_text(path, content, mode=mode)
            user, group = service_account(app)
            if not hand_over_file(
                path, user=user, group=group, mode=mode, logger=self.log, fs=self.fs
            ):
                raise ServiceError(
                    f"The file at {cleaned} could not be handed to {user}",
                    details="Without it the application cannot read what it was given.",
                )
            record = MountRecord(id=0, kind=KIND_FILE, target=cleaned, mode=mode, read_only=True)
            app_mount_store.put_mount(app.domain, record, updated_by=actor, store=self.store)
            self._link_into_active_release(app)
        audit.record(
            "apps.mounts",
            target=f"app:{app.domain}",
            details={"action": "write", "target": cleaned, "bytes": len(data), "by": actor},
        )
        return self._view(app, record)

    def put_path(
        self,
        domain: str,
        source: str,
        target: str,
        *,
        read_only: bool = True,
        actor: str = "system",
    ) -> MountView:
        """
        Bind a path of the server into a container application.

        Args:
            domain: The application.
            source: The path on this server.
            target: Where it appears in the container.
            read_only: Mount it read-only. A writable bind is refused under the ENS profile.
            actor: Who asked, for the audit trail.

        Returns:
            The mount.

        Raises:
            ValidationError: The application is not a container, or a path is not one
                that can be bound.
        """
        app = self._app(domain)
        if not is_container(app):
            raise ValidationError(
                f"{domain} is not a container application",
                details="Only a container application is given paths of the server; "
                "the others already run on it.",
                field="domain",
            )
        cleaned_source = clean_source(source)
        cleaned_target = clean_target(app, target, KIND_PATH)
        if not read_only:
            from noust.core import compliance

            if compliance.is_on("mounts_read_only"):
                raise ValidationError(
                    "A server path cannot be bound writable on this server (compliance control "
                    "mounts_read_only)",
                    details="Bind it read-only, or write through a volume.",
                    field="read_only",
                )
        record = MountRecord(
            id=0,
            kind=KIND_PATH,
            target=cleaned_target,
            source=cleaned_source,
            mode=DEFAULT_MODE,
            read_only=read_only,
        )
        with app_lock(app.domain, "mounts change"):
            app_mount_store.put_mount(app.domain, record, updated_by=actor, store=self.store)
        audit.record(
            "apps.mounts",
            target=f"app:{app.domain}",
            details={
                "action": "bind",
                "target": cleaned_target,
                "source": cleaned_source,
                "read_only": read_only,
                "by": actor,
            },
        )
        return self._view(app, record)

    def remove(self, domain: str, target: str, *, actor: str = "system") -> bool:
        """
        Forget a mount and, for a file, delete it.

        Args:
            domain: The application.
            target: Where it appears.
            actor: Who asked, for the audit trail.

        Returns:
            True when there was one.

        Raises:
            ValidationError: There is no such mount.
        """
        app = self._app(domain)
        record = app_mount_store.get_mount(app.domain, target, store=self.store)
        if record is None:
            raise ValidationError(f"{app.domain} has nothing mounted at {target!r}", field="target")
        with app_lock(app.domain, "mounts change"):
            app_mount_store.delete_mount(app.domain, target, store=self.store)
            if record.kind == KIND_FILE:
                self._unlink_from_releases(app, record)
                path = self.file_path(app, record.target)
                if not path.is_symlink():
                    self.fs.remove(path)
        audit.record(
            "apps.mounts",
            target=f"app:{app.domain}",
            details={"action": "remove", "target": target, "by": actor},
        )
        return True

    # -- The release -------------------------------------------------------------------

    def _refuse_obstacles(self, app: App, path: Path) -> None:
        """
        Refuse to write through a link or over a directory.

        Args:
            app: The application.
            path: Where the file goes.

        Raises:
            DeploymentError: A parent is a link, or the file is a directory or a link.
        """
        root = app_root(app)
        relative = PurePosixPath(*path.relative_to(root).parts)
        blocked = first_obstacle(root, relative)
        if blocked is not None:
            raise DeploymentError(
                f"Refusing to write {path}: {blocked} is not a plain directory",
                details="A link or a file there would make Noust write somewhere else.",
            )
        if path.is_symlink() or path.is_dir():
            raise DeploymentError(
                f"Refusing to write {path}: it is a link or a directory",
                details="Move it away first with the file browser.",
            )

    def _link_into_active_release(self, app: App) -> None:
        """
        Make the release that serves see the files now, not at the next deploy.

        Args:
            app: The application.
        """
        if app.layout != RELEASES or is_container(app):
            return
        releases = ReleaseManager(app_root(app), fs=self.fs, logger=self.log)
        active = releases.current()
        if active is None:
            return
        try:
            releases.link_shared(active.path, self.host_targets(app.domain))
        except DeploymentError as exc:
            self.log.warning(f"A mounted file was not linked into release {active.id}: {exc}")

    def _unlink_from_releases(self, app: App, record: MountRecord) -> None:
        """
        Remove the link a file mount left in the release that serves.

        Args:
            app: The application.
            record: The mount.
        """
        if app.layout != RELEASES or is_container(app):
            return
        active = ReleaseManager(app_root(app), fs=self.fs, logger=self.log).current()
        if active is None:
            return
        link = active.path / record.target
        if link.is_symlink():
            self.fs.remove(link)
