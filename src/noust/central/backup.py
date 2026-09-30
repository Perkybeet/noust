# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust central backup``: the central's store, secrets and keys, in one encrypted file.

A central holds the keys to every server it manages (ENS op.cont.1: losing it
loses the management of the fleet; mp.info.6: its copies; mp.si.2: a copy that
leaves the machine is encrypted). Before 3.1 the documented backup was ``tar``
of the data directory: consistent only if the console was stopped, and as
readable as the disk it landed on.

This takes:

- every file of the configuration and state directories (``/etc/noust`` and
  ``/var/lib/noust``, or ``/data/config`` and ``/data/state`` in the
  container): ``config.yaml``, the console's signing key, token hash, sessions
  and audit log with its key, the TLS pair, the secrets (node keys, fleet
  tokens, destination keys), the TOTP key;
- every SQLite database among them as a consistent snapshot, taken with
  SQLite's backup API while the central keeps running;

writes a ``MANIFEST.json`` with the SHA-256 of each file, packs it all in a
``.tar.gz`` and seals that in an envelope (:mod:`noust.core.ens.envelope`:
scrypt, AES-256-CBC by openssl, HMAC-SHA256) under a passphrase the operator
types and nothing stores. A sealed store's files stay sealed inside it:
restoring them then needs both passphrases.

Symlinks are never followed (they are listed as skipped), nor are the
incident packages, which are evidence kept apart, nor the backups directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sqlite3
import stat
import tarfile
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from noust.core import paths
from noust.core.audit import record
from noust.core.ens.envelope import (
    EnvelopeError,
    open_envelope,
    seal_file,
    verify_envelope,
)
from noust.core.fs import SECRET_DIR_MODE, FileSystem, get_fs
from noust.core.runner import CommandRunner

#: The envelope's label, so a file says what it is without a passphrase.
LABEL = "noust-central-backup"

#: The top directory inside the archive.
ROOT_NAME = "noust-central"

#: The manifest's name inside it.
MANIFEST_NAME = "MANIFEST.json"

#: Directory names never copied: evidence kept apart, downloads in progress.
SKIPPED_DIRS = frozenset({"incidents", ".remote-staging", "__pycache__"})

#: SQLite's companions, folded into the snapshot of their database.
SQLITE_COMPANIONS = (".db-wal", ".db-shm", ".db-journal", "-wal", "-shm", "-journal")

_SQLITE_MAGIC = b"SQLite format 3\x00"
_CHUNK = 1024 * 1024


@dataclass
class CentralBackup:
    """
    What ``noust central backup`` wrote.

    Attributes:
        path: The encrypted file.
        size: Its size in bytes.
        sha256: Its SHA-256.
        manifest: What it holds: every file with its size and SHA-256, the
            directories it came from, and what was skipped and why.
    """

    path: Path
    size: int
    sha256: str
    manifest: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The fields, JSON-ready, with the file counts.
        """
        return {
            "path": str(self.path),
            "size": self.size,
            "sha256": self.sha256,
            "files": len(self.manifest.get("files", [])),
            "skipped": self.manifest.get("skipped", []),
            "sources": self.manifest.get("sources", {}),
        }


def default_sources() -> list[Path]:
    """
    The directories a central keeps its state in.

    Returns:
        The configuration and state directories, the console's state
        directory when ``NOUST_WEB_STATE_DIR`` moves it, and the store's own
        directory when it is elsewhere; each once.
    """
    from noust.core.store import get_store

    candidates = [paths.config_dir(), paths.state_dir()]
    web_state = paths.getenv("WEB_STATE_DIR")
    if web_state:
        candidates.append(Path(web_state))
    candidates.append(get_store().db_path.parent)
    chosen: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve() if candidate.exists() else candidate
        if any(resolved == seen or resolved.is_relative_to(seen) for seen in chosen):
            continue
        chosen = [seen for seen in chosen if not seen.is_relative_to(resolved)]
        chosen.append(resolved)
    return [directory for directory in chosen if directory.is_dir()]


def default_output_dir() -> Path:
    """
    Returns:
        ``central`` under the backups directory.
    """
    return paths.backup_dir() / "central"


def _labels(sources: Sequence[Path]) -> dict[str, Path]:
    """
    Name each source directory inside the archive.

    Args:
        sources: The directories.

    Returns:
        ``config``, ``state``, then ``extra-N``, to each directory.
    """
    labels: dict[str, Path] = {}
    known = {paths.config_dir().resolve(): "config", paths.state_dir().resolve(): "state"}
    for index, source in enumerate(sources):
        label = known.get(source.resolve()) or (
            ("config", "state")[index] if index < 2 else f"extra-{index - 1}"
        )
        while label in labels:
            label = f"{label}-{index}"
        labels[label] = source
    return labels


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(len(_SQLITE_MAGIC)) == _SQLITE_MAGIC
    except OSError:
        return False


def _snapshot(source: Path, destination: Path) -> None:
    """
    Copy a live SQLite database consistently, with SQLite's backup API.

    Args:
        source: The database, possibly in use in WAL mode.
        destination: Where the snapshot goes.

    Raises:
        sqlite3.Error: The database cannot be read.
    """
    reader = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
    writer = sqlite3.connect(destination)
    try:
        reader.backup(writer)
    finally:
        writer.close()
        reader.close()


def _copy_tree(
    label: str,
    source: Path,
    staging: Path,
    manifest: dict[str, Any],
    exclude: set[Path],
    fs: FileSystem,
) -> None:
    """
    Copy one directory into the staging tree, recording every file.

    Args:
        label: Its name inside the archive.
        source: The directory.
        staging: The archive's root directory.
        manifest: Filled with ``files`` and ``skipped``.
        exclude: Directories never entered (the output, the backups).
        fs: The filesystem seam.
    """
    for directory, dirnames, filenames in os.walk(source, followlinks=False):
        current = Path(directory)
        relative_dir = current.relative_to(source)
        kept_dirs = []
        for name in sorted(dirnames):
            full = current / name
            relative = (Path(label) / relative_dir / name).as_posix()
            if full.is_symlink():
                manifest["skipped"].append({"path": relative, "reason": "symlink"})
            elif name in SKIPPED_DIRS or full.resolve() in exclude:
                manifest["skipped"].append({"path": relative, "reason": "excluded"})
            else:
                kept_dirs.append(name)
        dirnames[:] = kept_dirs
        target_dir = staging / label / relative_dir
        fs.make_dir(target_dir, mode=SECRET_DIR_MODE, parents=True)
        for name in sorted(filenames):
            full = current / name
            relative = (Path(label) / relative_dir / name).as_posix()
            if name.endswith(SQLITE_COMPANIONS):
                continue
            mode = full.lstat().st_mode
            if stat.S_ISLNK(mode):
                manifest["skipped"].append({"path": relative, "reason": "symlink"})
                continue
            if not stat.S_ISREG(mode):
                manifest["skipped"].append({"path": relative, "reason": "not a regular file"})
                continue
            target = target_dir / name
            if _is_sqlite(full):
                _snapshot(full, target)
            else:
                shutil.copyfile(full, target, follow_symlinks=False)
            os.chmod(target, 0o600)
            manifest["files"].append(
                {"path": relative, "size": target.stat().st_size, "sha256": _sha256(target)}
            )


def create_central_backup(
    passphrase: str,
    *,
    output_dir: Path | None = None,
    sources: Sequence[Path] | None = None,
    runner: CommandRunner | None = None,
    fs: FileSystem | None = None,
) -> CentralBackup:
    """
    Back the central up into one encrypted file.

    Args:
        passphrase: What the envelope is sealed under; at least 12
            characters, never stored. Without it the backup is unreadable.
        output_dir: Where the file goes; :func:`default_output_dir` by default.
        sources: The directories to back up; :func:`default_sources` by default.
        runner: The command runner (openssl); the process's own by default.
        fs: The filesystem seam; the process's own by default.

    Returns:
        What was written.

    Raises:
        EnvelopeError: The passphrase is too short, or openssl failed.
        OSError: A file could not be read.
        sqlite3.Error: A database could not be snapshotted.
    """
    fs = fs or get_fs()
    directories = list(sources) if sources is not None else default_sources()
    output_dir = output_dir or default_output_dir()
    fs.make_dir(output_dir, mode=SECRET_DIR_MODE, parents=True)
    now = datetime.now(timezone.utc)
    host = socket.gethostname()
    from noust import __version__

    labels = _labels(directories)
    manifest: dict[str, Any] = {
        "format": LABEL,
        "version": 1,
        "created_at": now.isoformat(timespec="seconds"),
        "host": host,
        "noust_version": __version__,
        "sources": {label: str(path) for label, path in labels.items()},
        "files": [],
        "skipped": [],
    }
    exclude = {output_dir.resolve(), paths.backup_dir().resolve()}
    destination = output_dir / f"noust-central-{host}-{now:%Y%m%dT%H%M%SZ}.tar.gz.enc"
    with tempfile.TemporaryDirectory(prefix="noust-central-backup-") as scratch:
        staging = Path(scratch) / ROOT_NAME
        fs.make_dir(staging, mode=SECRET_DIR_MODE, parents=True)
        for label, directory in labels.items():
            _copy_tree(label, directory, staging, manifest, exclude, fs)
        fs.write_text(
            staging / MANIFEST_NAME, json.dumps(manifest, indent=2, sort_keys=True), mode=0o600
        )
        archive = Path(scratch) / "central.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(staging, arcname=ROOT_NAME)
        info = seal_file(archive, destination, passphrase, runner=runner, fs=fs, label=LABEL)
    record(
        "backups.create",
        target="central",
        details={
            "path": str(info.path),
            "size": info.size,
            "sha256": info.sha256,
            "files": len(manifest["files"]),
            "skipped": len(manifest["skipped"]),
        },
    )
    return CentralBackup(path=info.path, size=info.size, sha256=info.sha256, manifest=manifest)


def decrypt_central_backup(
    path: Path,
    passphrase: str,
    destination: Path,
    *,
    runner: CommandRunner | None = None,
) -> dict[str, Any]:
    """
    Decrypt a central backup to its ``.tar.gz``, for a restore by hand.

    Args:
        path: The encrypted file.
        passphrase: Its passphrase.
        destination: Where the archive goes, 0600.
        runner: The command runner; the process's own by default.

    Returns:
        The envelope's header.

    Raises:
        EnvelopeError: Wrong passphrase, altered file, or openssl failed.
    """
    return open_envelope(path, destination, passphrase, runner=runner)


def verify_central_backup(
    path: Path, passphrase: str, *, runner: CommandRunner | None = None
) -> dict[str, Any]:
    """
    Prove a central backup restorable: it opens, and every file matches its manifest.

    Nothing is written outside a temporary directory, which is removed.

    Args:
        path: The encrypted file.
        passphrase: Its passphrase.
        runner: The command runner; the process's own by default.

    Returns:
        ``ok``, ``files`` checked, ``problems`` (one sentence each) and the
        manifest's ``created_at`` and ``host``.

    Raises:
        EnvelopeError: Wrong passphrase, or the file was altered.
    """
    verify_envelope(path, passphrase)
    problems: list[str] = []
    checked = 0
    manifest: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="noust-central-verify-") as scratch:
        archive = Path(scratch) / "central.tar.gz"
        open_envelope(path, archive, passphrase, runner=runner, fs=get_fs())
        extracted = Path(scratch) / "extracted"
        # The hardened extractor a restore uses (rule 3), not tarfile's own.
        from noust.core.exceptions import SourceError
        from noust.managers.source_manager import extract_archive

        try:
            extract_archive(archive, extracted, archive_format="tar.gz")
        except SourceError as exc:
            raise EnvelopeError(
                f"{path.name} does not hold a readable archive: {exc.message}",
                details=exc.details,
            ) from exc
        root = extracted / ROOT_NAME
        try:
            manifest = json.loads((root / MANIFEST_NAME).read_text())
        except (OSError, ValueError) as exc:
            raise EnvelopeError(f"{path.name} has no readable manifest: {exc}") from exc
        for entry in manifest.get("files", []):
            file_path = root / str(entry.get("path", ""))
            if not file_path.is_file():
                problems.append(f"{entry.get('path')} is missing")
            elif _sha256(file_path) != entry.get("sha256"):
                problems.append(f"{entry.get('path')} does not match its SHA-256")
            else:
                checked += 1
    report = {
        "ok": not problems,
        "files": checked,
        "problems": problems,
        "created_at": manifest.get("created_at"),
        "host": manifest.get("host"),
    }
    record(
        "backups.verify",
        target="central",
        outcome="ok" if report["ok"] else "failure",
        details={"path": str(path), **report, "problems": problems[:5]},
    )
    return report
