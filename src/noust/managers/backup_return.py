# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A copy the central held comes back to a server and is restored with the way a restore always goes (item 195).

The central keeps the copies of its servers and gives one back: to the server it came from, or to
another of the fleet (a replacement, a move). What this module is not is a restore. The restore of
an application, of a database's dump, of an application's data and of Noust's own state already
exist, each in the manager that knows its kind, with its safety copy, its health gate and its
refusals (rule 3); this is only the **receiving end**, which turns a file that arrived and what the
central says about it into the call of the manager that restores it:

- an application's backup is :meth:`~noust.managers.backup_manager.BackupManager.restore_archive`,
  which makes a file portable (the archive carries its own manifest), with the sidecar as what
  the archive does not say;
- a database's dump is :meth:`~noust.managers.database.backups.DatabaseBackups.restore_file`, into
  the database the operator names (or a new one beside it);
- an application's data is :meth:`~noust.managers.volume_backups.VolumeBackupManager.restore`, from
  the archive and its sidecar, into the application the sidecar says;
- Noust's own state is put in the folder of the server's bundles and **not restored**: a restore
  stops the console it runs on, so it is ``noust self-backup restore`` at a terminal, which this
  says (and the bundle is encrypted under its passphrase, which the central never had).

The file has already been checked against the digest the central sent it with (the upload's
``complete``), and each manager checks it again against what it records.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from noust.core.exceptions import BackupError, ValidationError
from noust.managers.backup_catalogue import KIND_APP, KIND_DATABASE, KIND_NOUST, KIND_VOLUME

if TYPE_CHECKING:
    from noust.managers.backup_manager import BackupManager
    from noust.managers.database.backups import DatabaseBackups
    from noust.managers.self_backup import SelfBackupManager
    from noust.managers.volume_backups import VolumeBackupManager

__all__ = ["BackupReturn", "ReturnRequest"]

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReturnRequest:
    """
    A copy that arrived, and what is to be done with it.

    Attributes:
        kind: The kind of copy.
        path: The file that arrived.
        sha256: Its digest, which the upload checked.
        file_name: Its name on the central.
        sidecar: What the copy's sidecar said, when it had one.
        meta: The small facts the central recorded of it (the application, the engine, the
            database, the target).
        target: Where to put it: ``domain`` (an application), ``engine`` and ``database``, and
            ``new_name`` for a database restored beside the original.
        options: ``restore_env``, ``safety_backup``, ``drop_existing`` and ``schema_changed_ok``.
    """

    kind: str
    path: Path
    sha256: str
    file_name: str
    sidecar: Mapping[str, Any] | None = None
    meta: Mapping[str, str] = field(default_factory=dict)
    target: Mapping[str, Any] = field(default_factory=dict)
    options: Mapping[str, Any] = field(default_factory=dict)


class BackupReturn:
    """
    Restore a copy that arrived from the central, with the manager of its kind.

    Args:
        apps: The application backups.
        databases: The database dumps.
        volumes: The data backups of applications.
        bundles: The bundles of Noust's own state.
    """

    def __init__(
        self,
        *,
        apps: BackupManager | None = None,
        databases: DatabaseBackups | None = None,
        volumes: VolumeBackupManager | None = None,
        bundles: SelfBackupManager | None = None,
    ) -> None:
        self._apps = apps
        self._databases = databases
        self._volumes = volumes
        self._bundles = bundles

    def restore(self, request: ReturnRequest) -> dict[str, Any]:
        """
        Restore a copy that arrived.

        Args:
            request: The copy and what to do with it.

        Returns:
            What was done, for the job's result.

        Raises:
            ValidationError: The kind is not one, or what the restore needs to know (the database,
                the application) was not said.
            BackupError: The manager refused or the restore failed; its own words.
        """
        handlers = {
            KIND_APP: self._app,
            KIND_VOLUME: self._volume,
            KIND_DATABASE: self._database,
            KIND_NOUST: self._bundle,
        }
        handler = handlers.get(request.kind)
        if handler is None:
            raise ValidationError(f"Unknown kind of copy: {request.kind!r}", field="kind")
        return handler(request)

    # ------------------------------------------------------------------- kinds

    def _app(self, request: ReturnRequest) -> dict[str, Any]:
        """
        Restore an application's backup.

        Args:
            request: The copy.

        Returns:
            What was done.
        """
        from noust.managers.backup_manager import BackupManager, BackupMetadata

        manager = self._apps or BackupManager(verbose=False)
        domain = str(request.target.get("domain") or request.meta.get("domain") or "")
        if not domain:
            raise ValidationError(
                "Say which application the backup goes back into",
                details="The copy does not name one: give its domain.",
                field="domain",
            )
        fallback: BackupMetadata | None = None
        if request.sidecar:
            try:
                fallback = BackupMetadata.from_dict(dict(request.sidecar))
            except (KeyError, TypeError, ValueError) as exc:
                _log.info(
                    "The sidecar of the copy is not usable (%s); the archive says what it is", exc
                )
        manager.restore_archive(
            request.path,
            target_domain=domain,
            restore_env=bool(request.options.get("restore_env", True)),
            expected_checksum=request.sha256,
            fallback=fallback,
            schema_changed_ok=bool(request.options.get("schema_changed_ok", False)),
        )
        return {"kind": KIND_APP, "restored": domain, "file": request.file_name}

    def _volume(self, request: ReturnRequest) -> dict[str, Any]:
        """
        Restore an application's data.

        Args:
            request: The copy.

        Returns:
            What the manager said.

        Raises:
            BackupError: The copy has no usable sidecar, its MAC does not match, or it is not of
                the application it is asked to go into.
        """
        from noust.managers.backup_manager import sidecar_mac_state
        from noust.managers.volume_backups import VolumeBackupManager, VolumeBackupMetadata

        manager = self._volumes or VolumeBackupManager()
        if not request.sidecar:
            raise BackupError(
                "A copy of an application's data needs the metadata that says what it is",
                details="The central holds the archive without its sidecar, so what it holds and "
                "where it goes are not known. Nothing was restored.",
            )
        known = set(VolumeBackupMetadata.__dataclass_fields__)
        signed = {key: value for key, value in request.sidecar.items() if key in known}
        if sidecar_mac_state(signed) == "invalid":
            raise BackupError(
                f"The metadata of {request.file_name} does not match its MAC; nothing was restored"
            )
        try:
            metadata = VolumeBackupMetadata.from_dict(signed)
        except (KeyError, TypeError) as exc:
            raise BackupError(
                f"The metadata of {request.file_name} cannot be read: {exc}",
                details="Nothing was restored.",
            ) from exc
        wanted = str(request.target.get("domain") or metadata.domain)
        if metadata.domain != wanted:
            raise BackupError(
                f"{request.file_name} is the data of {metadata.domain}, not of {wanted}",
                details="Restore it into the application it belongs to.",
            )
        outcome = manager.restore(
            metadata.id,
            safety_backup=bool(request.options.get("safety_backup", True)),
            archive=request.path,
            metadata=metadata,
        )
        return {"kind": KIND_VOLUME, "restored": metadata.domain, "result": outcome}

    def _database(self, request: ReturnRequest) -> dict[str, Any]:
        """
        Load a database's dump.

        Args:
            request: The copy.

        Returns:
            What the service did.

        Raises:
            ValidationError: The database to restore into was not named.
        """
        from noust.managers.database.backups import DatabaseBackups
        from noust.managers.database.service import DatabaseService

        engine = str(request.target.get("engine") or request.meta.get("engine") or "")
        database = str(request.target.get("database") or request.meta.get("database") or "")
        if not engine or not database:
            raise ValidationError(
                "Say which database the dump is loaded into",
                details="The copy names neither its engine nor its database: give them.",
                field="database",
            )
        backups = self._databases or DatabaseBackups(DatabaseService())
        new_name = request.target.get("new_name") or None
        outcome = backups.restore_file(
            engine,
            database,
            request.path,
            drop_existing=bool(request.options.get("drop_existing", False)),
            safety_backup=bool(request.options.get("safety_backup", True)),
            new_name=str(new_name) if new_name else None,
        )
        return {
            "kind": KIND_DATABASE,
            "restored": f"{engine}/{outcome.database}",
            "safety_copy": outcome.safety_copy.name if outcome.safety_copy else None,
            "replaced": outcome.replaced,
        }

    def _bundle(self, request: ReturnRequest) -> dict[str, Any]:
        """
        Put a bundle of Noust's own state in the folder of this server's bundles.

        Args:
            request: The copy.

        Returns:
            Where it is and the command that restores it.

        Raises:
            BackupError: What arrived is not a bundle of Noust's state, or one is already there.
        """
        from noust.core.ens.envelope import EnvelopeError, read_header
        from noust.core.fs import SECRET_MODE, get_fs
        from noust.managers.self_backup import LABEL, SelfBackupManager

        manager = self._bundles or SelfBackupManager()
        try:
            header = read_header(request.path)
        except EnvelopeError as exc:
            raise BackupError(
                f"{request.file_name} is not a bundle of Noust's own state: {exc}",
                details="Nothing was put in the bundles' folder.",
            ) from exc
        if header.get("label") != LABEL:
            raise BackupError(f"{request.file_name} is not a bundle of Noust's own state")
        fs = get_fs()
        destination = manager.directory / request.file_name
        if destination.exists():
            raise BackupError(
                f"This server already has a bundle called {request.file_name}",
                details="Nothing was replaced.",
            )
        fs.make_dir(manager.directory, mode=0o700, parents=True)
        fs.move(request.path, destination)
        fs.chmod(destination, SECRET_MODE)
        if request.sidecar:
            fs.write_text(
                manager.sidecar_path(destination),
                json.dumps(dict(request.sidecar), indent=2),
                mode=SECRET_MODE,
            )
        return {
            "kind": KIND_NOUST,
            "placed": request.file_name,
            "command": f"noust self-backup restore {request.file_name}",
            "note": "Noust's own state is restored at a terminal, with the bundle's passphrase: "
            "the restore stops the console it runs on.",
        }
