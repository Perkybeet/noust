# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where a built release waits to be fetched, and how one that arrives is held (checklist A-49).

A release built on one server runs on another (:mod:`noust.deployers.release_bundles`). When a
central moves it, the bundle crosses an SSH tunnel through the node's own API, in chunks, because a
request through a tunnel is cut after a few minutes and a bundle is hundreds of megabytes:

- **On the builder**, :class:`BundleSpool` keeps the bundle that ``POST /api/apps/{d}/releases/bundle``
  built, in a directory of its own (owner-only, under the backup directory: the disk sized for
  dumps) until the central has taken it and says so, or :data:`SPOOL_TTL` passes. It is read by
  ranges, never whole.
- **On the receiver**, a bundle arrives as an upload session (:data:`BUNDLE_UPLOADS`, the protocol of
  the dumps and the source archives: the chunks, the offsets, the digest, the owner, the sweep),
  and the finished file is what :func:`~noust.deployers.release_bundles.receive` checks against the
  SHA-256 the builder reported before a byte of it is read.

Nothing here decides whether a bundle may run: the checksum, the extraction checks and the health
gate are :mod:`noust.deployers.release_bundles`. This only carries.
"""

from __future__ import annotations

import builtins
import json
import logging
import re
import secrets
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from noust.core.config import Config
from noust.core.exceptions import ValidationError
from noust.core.fs import SECRET_DIR_MODE, SECRET_MODE, FileSystem, get_fs
from noust.deployers.bundle import BUNDLE_SUFFIX, Bundle
from noust.managers.database.uploads import UploadProfile

__all__ = [
    "BUNDLE_UPLOADS",
    "MAX_READ_BYTES",
    "SPOOL_TTL",
    "BundleSpool",
    "SpooledBundle",
]

_log = logging.getLogger(__name__)

#: How long a built bundle waits to be fetched: a day. A central takes it within minutes; one that
#: never comes (it was restarted, the operator cancelled) must not keep hundreds of megabytes.
SPOOL_TTL = 24 * 3600

#: The most one range request may ask for. The client asks for less; this is the server's word.
MAX_READ_BYTES = 8 * 1024**2

_ID = re.compile(r"[0-9a-f]{32}")
_DATA = "bundle" + BUNDLE_SUFFIX
_META = "meta.json"

#: The bundles that arrive from a builder, as upload sessions.
BUNDLE_UPLOADS = UploadProfile(
    extensions=("gz",),
    limit_key="releases.bundle_max_bytes",
    default_max=8 * 1024**3,
    noun="a release bundle",
    extension_lead="A release bundle's name ends in ",
    name_details="Name the bundle with a plain file name, without a path.",
    extension_hint=f"Bundles are named like <app>{BUNDLE_SUFFIX}.",
    limit_details="Raise releases.bundle_max_bytes with 'noust config set', or build the "
    "release on this server.",
    event="releases.bundle.upload",
    target="release-bundle:upload",
    directory=".release-bundle-uploads",
)


@dataclass(frozen=True)
class SpooledBundle:
    """
    A built bundle waiting to be fetched.

    Attributes:
        id: Its identifier.
        domain: The application it was built for.
        release_id: The release it holds.
        commit: The commit it was built from.
        sha256: Its digest, which whoever receives it checks.
        size: Its size in bytes.
        files: Files in the release.
        built_at: When it was packed, ISO 8601 UTC.
        builder: The machine that packed it.
        noust_version: The Noust that packed it.
        app_type: The application type it was built as.
    """

    id: str
    domain: str
    release_id: str
    commit: str | None
    sha256: str
    size: int
    files: int
    built_at: str
    builder: str
    noust_version: str
    app_type: str

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The bundle as plain data, JSON-ready.
        """
        return asdict(self)


class BundleSpool:
    """
    The built bundles on this server.

    Args:
        root: The directory holding them; under the backup directory by default.
        fs: The filesystem they are made and removed through.
        config: The configuration the default root is read from.
    """

    def __init__(
        self,
        root: Path | None = None,
        *,
        fs: FileSystem | None = None,
        config: Config | None = None,
    ) -> None:
        self._root = root
        self._fs = fs
        self._config = config

    @property
    def fs(self) -> FileSystem:
        """The filesystem every change goes through."""
        return self._fs or get_fs()

    @property
    def root(self) -> Path:
        """The directory holding every bundle."""
        if self._root is not None:
            return self._root
        return (self._config or Config()).backup_directory / ".release-bundles"

    def reserve(self) -> tuple[str, Path]:
        """
        Make a place for a bundle that is about to be built.

        Returns:
            Its identifier and the file to write: in an owner-only directory of its own, so the
            bundle is never briefly readable by anybody else.

        Raises:
            ValidationError: There is no room for even a small bundle on that disk.
        """
        self.sweep()
        self.fs.make_dir(self.root, mode=SECRET_DIR_MODE, parents=True)
        if shutil.disk_usage(self.root).free < 64 * 1024**2:
            raise ValidationError(
                f"There is almost no room for a bundle on the disk of {self.root}",
                details="Free some space, or remove the bundles waiting there.",
            )
        bundle_id = secrets.token_hex(16)
        directory = self.root / bundle_id
        self.fs.make_dir(directory, mode=SECRET_DIR_MODE)
        return bundle_id, directory / _DATA

    def keep(self, bundle_id: str, domain: str, bundle: Bundle) -> SpooledBundle:
        """
        Record a bundle that was just built into the place :meth:`reserve` made.

        Args:
            bundle_id: The identifier :meth:`reserve` returned.
            domain: The application.
            bundle: What the packing produced.

        Returns:
            The bundle as it will be listed.
        """
        manifest = bundle.manifest
        spooled = SpooledBundle(
            id=bundle_id,
            domain=domain,
            release_id=manifest.release_id,
            commit=manifest.commit,
            sha256=bundle.sha256,
            size=bundle.size,
            files=manifest.files,
            built_at=manifest.built_at,
            builder=manifest.builder,
            noust_version=manifest.noust_version,
            app_type=manifest.app_type,
        )
        self.fs.write_text(
            self._directory(bundle_id) / _META,
            json.dumps(spooled.to_dict(), sort_keys=True),
            mode=SECRET_MODE,
        )
        return spooled

    def _directory(self, bundle_id: str) -> Path:
        """
        Locate a bundle's directory by its identifier.

        Args:
            bundle_id: The identifier.

        Returns:
            Its directory.

        Raises:
            ValidationError: It is not an identifier this spool issues, so no request names a path.
        """
        if not isinstance(bundle_id, str) or not _ID.fullmatch(bundle_id):
            raise ValidationError("That is not a bundle", field="bundle_id")
        return self.root / bundle_id

    def get(self, bundle_id: str, domain: str) -> SpooledBundle:
        """
        Read a bundle's record.

        Args:
            bundle_id: The identifier.
            domain: The application it must be for; another's is not found.

        Returns:
            The bundle.

        Raises:
            ValidationError: There is no such bundle for that application (it may have been taken,
                removed or swept).
        """
        try:
            data = json.loads((self._directory(bundle_id) / _META).read_text(encoding="utf-8"))
            spooled = SpooledBundle(**data)
        except (OSError, ValueError, TypeError) as exc:
            raise ValidationError(
                "There is no such bundle",
                details="It may have been fetched and removed, or swept after a day. Build it "
                "again.",
                field="bundle_id",
            ) from exc
        if spooled.domain != domain:
            raise ValidationError("There is no such bundle", field="bundle_id")
        return spooled

    def path(self, bundle_id: str, domain: str) -> Path:
        """
        Name the file of a bundle.

        Args:
            bundle_id: The identifier.
            domain: The application.

        Returns:
            The bundle file.

        Raises:
            ValidationError: There is no such bundle.
        """
        self.get(bundle_id, domain)
        return self._directory(bundle_id) / _DATA

    def list(self, domain: str) -> list[SpooledBundle]:
        """
        List the bundles waiting for an application.

        Args:
            domain: The application.

        Returns:
            Its bundles, newest first.
        """
        found: list[SpooledBundle] = []
        try:
            entries = [entry for entry in self.root.iterdir() if entry.is_dir()]
        except OSError:
            return found
        for entry in entries:
            if not _ID.fullmatch(entry.name):
                continue
            try:
                found.append(self.get(entry.name, domain))
            except ValidationError:
                continue
        return sorted(found, key=lambda item: item.built_at, reverse=True)

    def read(self, bundle_id: str, domain: str, offset: int, length: int) -> bytes:
        """
        Read a range of a bundle.

        Args:
            bundle_id: The identifier.
            domain: The application.
            offset: Where to start.
            length: How many bytes at most, never more than :data:`MAX_READ_BYTES`.

        Returns:
            The bytes; fewer at the end of the file, none past it.

        Raises:
            ValidationError: There is no such bundle, or the range is not one.
        """
        spooled = self.get(bundle_id, domain)
        if offset < 0 or length <= 0 or length > MAX_READ_BYTES:
            raise ValidationError(
                "That is not a range of the bundle",
                details=f"Ask for 1 to {MAX_READ_BYTES} bytes from an offset of 0 or more "
                f"(it has {spooled.size}).",
                field="length",
            )
        with (self._directory(bundle_id) / _DATA).open("rb") as handle:
            handle.seek(offset)
            return handle.read(length)

    def abandon(self, bundle_id: str) -> None:
        """
        Drop the place :meth:`reserve` made for a bundle that was not built.

        Args:
            bundle_id: The identifier :meth:`reserve` returned.
        """
        self._remove(self._directory(bundle_id))

    def discard(self, bundle_id: str, domain: str) -> None:
        """
        Remove a bundle: the central has it, or does not want it.

        Args:
            bundle_id: The identifier.
            domain: The application.

        Raises:
            ValidationError: There is no such bundle.
        """
        self.get(bundle_id, domain)
        self._remove(self._directory(bundle_id))

    def sweep(self, *, now: float | None = None) -> builtins.list[str]:
        """
        Remove the bundles nobody fetched in :data:`SPOOL_TTL`.

        Args:
            now: The moment to judge at, as ``time.time()``; the clock by default.

        Returns:
            The identifiers removed.
        """
        removed: list[str] = []
        try:
            entries = [entry for entry in self.root.iterdir() if entry.is_dir()]
        except OSError:
            return removed
        horizon = (now if now is not None else time.time()) - SPOOL_TTL
        for entry in entries:
            if not _ID.fullmatch(entry.name):
                continue
            try:
                newest = max(child.stat().st_mtime for child in [entry, *entry.iterdir()])
            except OSError:
                continue
            if newest < horizon:
                self._remove(entry)
                removed.append(entry.name)
        return removed

    def _remove(self, directory: Path) -> None:
        """
        Args:
            directory: A bundle's directory.
        """
        try:
            self.fs.remove_tree(directory)
        except OSError as exc:
            _log.warning("Could not remove the bundle %s: %s", directory, exc)
