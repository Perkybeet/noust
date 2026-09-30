# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Single files on a backup destination: what database dumps are sent with.

:class:`~noust.managers.backup_destinations.BackupDestinationManager` sends an
application backup, an archive with a sidecar that describes it, and
:meth:`~noust.managers.backup_destinations.BackupDestinationManager.push`
insists on that shape. A database dump is a single file with the same needs:
the same destinations, the same crypt wrapper when the destination is
encrypted, the same check after the upload, and the same rule that a server
only ever deletes what it sent. This subclass adds those four verbs for any
file under a folder of the destination, and reuses everything below them
(``remote_env``, ``_list_remote``, ``_verify_uploaded``, ``_discard_upload``,
``_check_staging_space``) instead of a second rclone client.

**A sidecar per file.** ``<name>.json`` beside each dump says who sent it
(:func:`~noust.managers.backup_manager.server_id`), whether a schedule did, and
the dump's SHA-256. Retention deletes only what this server sent and a schedule
made; a download is checked against the digest, which is the only check an
encrypted destination allows (a crypt remote reports no hash).

Folders are one path per thing (``databases/<engine>/<database>``), each
segment validated where it becomes part of a remote path, so a request cannot
list or download from outside the destination's folder.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from noust.core.exceptions import BackupError, ValidationError
from noust.core.fs import SECRET_DIR_MODE, FileSystem, get_fs
from noust.core.logger import Logger
from noust.managers.backup_destinations import (
    _RCLONE_LIST_TIMEOUT,
    _RCLONE_TIMEOUT,
    _TRANSFER_TIMEOUT,
    BackupDestinationManager,
    _mod_time_or_min,
    _require_rclone,
    _scrub,
    decode_sidecars,
)
from noust.managers.backup_manager import server_id
from noust.managers.retention import select_expired
from noust.validators.names import validate_filename

__all__ = ["DestinationFileManager", "RemoteFile", "file_sha256"]

#: One segment of a remote folder. ``$`` is a legal character of a database
#: name; ``.`` is not, so ``..`` cannot be spelled.
_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_$-]{0,127}")

#: Bytes read at a time when hashing a dump.
_CHUNK = 1024 * 1024


def file_sha256(path: Path) -> str:
    """
    Hash a file without reading it into memory.

    Args:
        path: The file.

    Returns:
        The SHA-256 digest, hex.

    Raises:
        OSError: When the file cannot be read.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class RemoteFile:
    """
    A file on a destination, with what its sidecar says.

    Attributes:
        name: The file's name.
        size: Bytes, as the destination reports them.
        modified: When it was last modified, as rclone prints it.
        sidecar: The parsed ``<name>.json``, or None when there is none.
    """

    name: str
    size: int | None
    modified: str | None
    sidecar: dict[str, Any] | None

    @property
    def own(self) -> bool:
        """Whether this server sent it, by its sidecar."""
        return bool(self.sidecar) and (self.sidecar or {}).get("origin") == server_id()


class DestinationFileManager(BackupDestinationManager):
    """
    A destination manager that also sends, lists, fetches and deletes single files.
    """

    def _folder_target(self, destination_name: str, folder: str) -> str:
        """
        Return where a folder of files lives on a destination.

        Args:
            destination_name: Destination name.
            folder: ``a/b/c``, every segment validated.

        Returns:
            The remote reference of the folder.

        Raises:
            BackupError: When a segment is not a plain name.
        """
        segments = [segment for segment in folder.split("/") if segment]
        if not segments or any(not _SEGMENT.fullmatch(segment) for segment in segments):
            raise BackupError(
                f"Invalid folder on the destination: {folder!r}",
                details="A folder is made of names with letters, digits, '_', '$' and '-'.",
            )
        return f"{self.target(destination_name).rstrip('/')}/{'/'.join(segments)}"

    def _read_sidecars(
        self, env: dict[str, str], target_dir: str, secrets_literal: list[str]
    ) -> dict[str, dict[str, Any]]:
        """
        Read every sidecar of a folder in one call.

        Args:
            env: Environment built by :meth:`remote_env`.
            target_dir: The folder.
            secrets_literal: Values to scrub from any error text.

        Returns:
            The sidecars by the file name each describes.

        Raises:
            BackupError: When rclone cannot read the folder.
        """
        result = self.runner.run(
            ["rclone", "cat", target_dir, "--include", "*.json", "--max-depth", "1"],
            env=env,
            timeout=_RCLONE_LIST_TIMEOUT,
            secrets=secrets_literal,
        )
        if not result.success:
            raise BackupError(
                f"Could not read the metadata in {target_dir}",
                details=_scrub((result.stderr or result.stdout).strip(), secrets_literal),
            )
        return {
            data["name"]: data
            for data in decode_sidecars(result.stdout or "")
            if isinstance(data.get("name"), str)
        }

    def push_file(
        self,
        local: Path,
        destination_name: str,
        folder: str,
        *,
        sidecar: dict[str, Any],
        retention_count: int | None = None,
        retention_days: int | None = None,
    ) -> dict[str, Any]:
        """
        Copy one file to a destination, verify it, and apply retention there.

        Args:
            local: The file to send.
            destination_name: Destination to send it to.
            folder: The folder under the destination's path, such as
                ``databases/postgresql/shop``.
            sidecar: What to record about it: at least ``sha256``; a
                ``scheduled`` flag makes it eligible for retention. The file's
                name, size and this server's id are added.
            retention_count: This server's scheduled files in the folder to
                keep, newest first; None for no limit.
            retention_days: Maximum age in days of those; None for no limit.

        Returns:
            ``destination``, ``folder``, ``file``, ``uploaded`` (the names),
            ``verified_by`` (a hash name, or ``"size"`` when the destination
            reports none) and ``retention_deleted`` (the names removed).

        Raises:
            DependencyError: When rclone is not installed.
            BackupError: When the file is missing, the upload fails,
                verification fails (the bad copy is removed first), or
                retention cannot be applied.
        """
        _require_rclone(self.runner)
        self.require_encrypted_upload(destination_name)
        if not local.is_file():
            raise BackupError(f"Cannot send {local}: it is not a file")
        env = self.remote_env(destination_name)
        secrets_literal = self._secret_literals(destination_name)
        target_dir = self._folder_target(destination_name, folder)
        name = local.name
        sidecar_name = f"{name}.json"
        body = json.dumps(
            {**sidecar, "name": name, "origin": server_id(), "size": local.stat().st_size},
            sort_keys=True,
        )
        uploaded = [name, sidecar_name]

        result = self.runner.run(
            ["rclone", "copyto", str(local), f"{target_dir}/{name}"],
            env=env,
            timeout=_TRANSFER_TIMEOUT,
            secrets=secrets_literal,
        )
        if not result.success:
            raise BackupError(
                f"Failed to upload {name} to {destination_name}",
                details=_scrub((result.stderr or result.stdout).strip(), secrets_literal),
            )
        result = self.runner.run(
            ["rclone", "rcat", f"{target_dir}/{sidecar_name}"],
            env=env,
            input=body,
            timeout=_RCLONE_TIMEOUT,
            secrets=secrets_literal,
        )
        if not result.success:
            # A dump with no sidecar is one nothing can attribute to this
            # server or check on the way back: it is removed, not left behind.
            failure = BackupError(
                f"Failed to upload {sidecar_name} to {destination_name}",
                details=_scrub((result.stderr or result.stdout).strip(), secrets_literal),
            )
            raise self._discard_upload(env, target_dir, uploaded, secrets_literal, failure)

        entries = self._list_remote(env, target_dir, secrets_literal)
        try:
            verified_by = self._verify_uploaded(local, entries)
            self._verify_sidecar(sidecar_name, body, entries)
        except BackupError as exc:
            raise self._discard_upload(env, target_dir, uploaded, secrets_literal, exc) from exc

        if verified_by == "size":
            Logger(verbose=False).warning(
                f"{destination_name} reports no hash Noust can compare for {name} (an encrypted "
                "destination never does): the upload was verified by its size only."
            )

        try:
            deleted = self._apply_file_retention(
                env, target_dir, entries, retention_count, retention_days, secrets_literal
            )
        except BackupError as exc:
            raise BackupError(
                f"{name} was uploaded to {destination_name} and verified, but retention could "
                "not be applied there",
                details=exc.details or exc.message,
            ) from exc
        return {
            "destination": destination_name,
            "folder": folder,
            "file": name,
            "uploaded": uploaded,
            "verified_by": verified_by,
            "retention_deleted": deleted,
        }

    @staticmethod
    def _verify_sidecar(name: str, body: str, entries: list[dict[str, Any]]) -> None:
        """
        Confirm a sidecar reached the destination whole.

        Args:
            name: The sidecar's file name.
            body: What was sent.
            entries: The folder's entries after the upload.

        Raises:
            BackupError: When it is not listed, or its size differs.
        """
        entry = next((e for e in entries if e.get("Name") == name), None)
        if entry is None:
            raise BackupError(
                f"Upload of {name} did not reach the destination",
                details="It was not listed there right after being copied.",
            )
        size = entry.get("Size")
        if isinstance(size, int) and size != len(body.encode()):
            raise BackupError(
                f"Size mismatch after uploading {name}",
                details=f"Sent {len(body.encode())} bytes; the destination reports {size}.",
            )

    def _apply_file_retention(
        self,
        env: dict[str, str],
        target_dir: str,
        entries: list[dict[str, Any]],
        retention_count: int | None,
        retention_days: int | None,
        secrets_literal: list[str],
    ) -> list[str]:
        """
        Delete this server's own scheduled files beyond the limits.

        Another server sharing the folder, a file sent by hand and a file whose
        sidecar cannot be read are all left alone: retention bounds this
        server's own scheduled usage, and does not decide what anyone else
        keeps.

        Args:
            env: Environment built by :meth:`remote_env`.
            target_dir: The folder.
            entries: Its entries, listed after the upload.
            retention_count: Files to keep, newest first; None for no limit.
            retention_days: Maximum age in days; None for no limit.
            secrets_literal: Values to scrub from any error text.

        Returns:
            The file names removed.

        Raises:
            BackupError: When a sidecar cannot be read or a file cannot be
                deleted for a reason other than being gone already.
        """
        if not retention_count and not retention_days:
            return []
        sidecars = self._read_sidecars(env, target_dir, secrets_literal)
        own = server_id()
        candidates = [
            (entry["Name"], _mod_time_or_min(entry))
            for entry in entries
            if entry.get("Name") in sidecars
            and sidecars[entry["Name"]].get("origin") == own
            and sidecars[entry["Name"]].get("scheduled") is True
        ]
        expired = select_expired(
            candidates, count=retention_count, days=retention_days, now=datetime.now(timezone.utc)
        )
        for name in expired:
            self._delete_pair(env, target_dir, name, secrets_literal)
        return expired

    def _delete_pair(
        self, env: dict[str, str], target_dir: str, name: str, secrets_literal: list[str]
    ) -> None:
        """
        Delete a file and its sidecar, tolerating either being gone.

        Args:
            env: Environment built by :meth:`remote_env`.
            target_dir: The folder.
            name: The file's name.
            secrets_literal: Values to scrub from any error text.

        Raises:
            BackupError: When rclone fails for another reason.
        """
        for victim in (name, f"{name}.json"):
            result = self.runner.run(
                ["rclone", "deletefile", f"{target_dir}/{victim}"],
                env=env,
                timeout=_RCLONE_TIMEOUT,
                secrets=secrets_literal,
            )
            if not result.success and "not found" not in (result.stderr or "").lower():
                raise BackupError(
                    f"Failed to remove {victim} from the destination",
                    details=_scrub((result.stderr or result.stdout).strip(), secrets_literal),
                )

    def list_files(self, destination_name: str, folder: str) -> list[RemoteFile]:
        """
        List the files of a folder, newest first, with their sidecars.

        Args:
            destination_name: Destination to list.
            folder: The folder under the destination's path.

        Returns:
            The files. A folder that does not exist yet is empty.

        Raises:
            DependencyError: When rclone is not installed.
            BackupError: When the destination cannot be listed.
        """
        _require_rclone(self.runner)
        self._require(destination_name)
        env = self.remote_env(destination_name)
        secrets_literal = self._secret_literals(destination_name)
        target_dir = self._folder_target(destination_name, folder)
        entries = self._list_remote(env, target_dir, secrets_literal, hashes=False)
        files = [
            e
            for e in entries
            if not e.get("IsDir") and not str(e.get("Name", "")).endswith(".json")
        ]
        has_sidecars = any(str(e.get("Name", "")).endswith(".json") for e in entries)
        sidecars = self._read_sidecars(env, target_dir, secrets_literal) if has_sidecars else {}
        files.sort(key=_mod_time_or_min, reverse=True)
        return [
            RemoteFile(
                name=str(entry.get("Name", "")),
                size=entry.get("Size") if isinstance(entry.get("Size"), int) else None,
                modified=entry.get("ModTime"),
                sidecar=sidecars.get(str(entry.get("Name", ""))),
            )
            for entry in files
        ]

    def list_folders(self, destination_name: str, folder: str) -> list[str]:
        """
        List the sub-folders of a folder.

        Args:
            destination_name: Destination to list.
            folder: The folder under the destination's path.

        Returns:
            The sub-folder names, sorted.

        Raises:
            DependencyError: When rclone is not installed.
            BackupError: When the destination cannot be listed.
        """
        _require_rclone(self.runner)
        self._require(destination_name)
        env = self.remote_env(destination_name)
        secrets_literal = self._secret_literals(destination_name)
        target_dir = self._folder_target(destination_name, folder)
        entries = self._list_remote(env, target_dir, secrets_literal, hashes=False)
        return sorted(str(e.get("Name", "")) for e in entries if e.get("IsDir"))

    def download_file(
        self,
        destination_name: str,
        folder: str,
        name: str,
        staging_dir: Path,
        *,
        fs: FileSystem | None = None,
    ) -> tuple[Path, dict[str, Any] | None]:
        """
        Download one file and check it against its sidecar.

        Before anything is transferred the file's size is compared with the
        space free where it is going. After, the sidecar (when there is one)
        must describe this file, and the digest it records must match: an
        encrypted destination cannot vouch for a file, so the digest is what
        catches a copy that rotted or was swapped.

        Args:
            destination_name: Destination to download from.
            folder: The folder the file is in.
            name: The file's name.
            staging_dir: Where to put it; created if missing.
            fs: Filesystem the directory is created through.

        Returns:
            The downloaded path and the sidecar, None when the file has none.

        Raises:
            DependencyError: When rclone is not installed.
            BackupError: When the file is not there, would not fit, the
                download fails, or the digest or size does not match.
        """
        _require_rclone(self.runner)
        self._require(destination_name)
        try:
            validate_filename(name)
        except ValidationError as exc:
            raise BackupError(
                f"Invalid file name: {name!r}", details="A dump is named by its file name alone."
            ) from exc
        env = self.remote_env(destination_name)
        secrets_literal = self._secret_literals(destination_name)
        target_dir = self._folder_target(destination_name, folder)
        (fs or get_fs()).make_dir(staging_dir, mode=SECRET_DIR_MODE, parents=True)
        destination = staging_dir / name

        self._check_staging_space(env, target_dir, name, staging_dir, secrets_literal)
        result = self.runner.run(
            ["rclone", "copyto", f"{target_dir}/{name}", str(destination)],
            env=env,
            timeout=_TRANSFER_TIMEOUT,
            secrets=secrets_literal,
        )
        if not result.success:
            raise BackupError(
                f"Failed to download {name} from {destination_name}",
                details=_scrub((result.stderr or result.stdout).strip(), secrets_literal),
            )
        if not destination.is_file():
            raise BackupError(f"Download of {name} did not produce a file at {destination}")

        sidecar = self._read_one_sidecar(env, target_dir, name, secrets_literal)
        if sidecar is None:
            return destination, None
        if sidecar.get("name") != name:
            raise BackupError(
                f"The metadata downloaded for {name} describes another file",
                details=f"It names {sidecar.get('name')!r}. Nothing was restored.",
            )
        expected = sidecar.get("sha256")
        if expected and file_sha256(destination) != expected:
            raise BackupError(
                f"Checksum mismatch after downloading {name} from {destination_name}",
                details="The downloaded file does not match the digest recorded when it was "
                "sent. Restoring it would restore corrupted data. Nothing was restored.",
            )
        return destination, sidecar

    def _read_one_sidecar(
        self, env: dict[str, str], target_dir: str, name: str, secrets_literal: list[str]
    ) -> dict[str, Any] | None:
        """
        Read one file's sidecar.

        Args:
            env: Environment built by :meth:`remote_env`.
            target_dir: The folder.
            name: The file the sidecar describes.
            secrets_literal: Values to scrub from any error text.

        Returns:
            The sidecar, or None when there is none.

        Raises:
            BackupError: When rclone fails for another reason, or the sidecar
                is not a JSON object.
        """
        result = self.runner.run(
            ["rclone", "cat", f"{target_dir}/{name}.json"],
            env=env,
            timeout=_RCLONE_LIST_TIMEOUT,
            secrets=secrets_literal,
        )
        if not result.success:
            combined = f"{result.stderr}\n{result.stdout}".lower()
            if "not found" in combined:
                return None
            raise BackupError(
                f"Could not read the metadata of {name}",
                details=_scrub((result.stderr or result.stdout).strip(), secrets_literal),
            )
        found = decode_sidecars(result.stdout or "")
        if not found:
            raise BackupError(f"The metadata of {name} is not a JSON object")
        return found[0]

    def delete_file(self, destination_name: str, folder: str, name: str) -> None:
        """
        Delete a file and its sidecar from a destination.

        Args:
            destination_name: Destination to delete from.
            folder: The folder the file is in.
            name: The file's name.

        Raises:
            DependencyError: When rclone is not installed.
            BackupError: When the name is not a plain file name, or rclone
                fails for a reason other than the file being gone already.
        """
        _require_rclone(self.runner)
        self._require(destination_name)
        try:
            validate_filename(name)
        except ValidationError as exc:
            raise BackupError(
                f"Invalid file name: {name!r}", details="A dump is named by its file name alone."
            ) from exc
        env = self.remote_env(destination_name)
        secrets_literal = self._secret_literals(destination_name)
        target_dir = self._folder_target(destination_name, folder)
        self._delete_pair(env, target_dir, name, secrets_literal)
