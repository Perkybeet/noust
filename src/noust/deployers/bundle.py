# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A built release as one file: what lets one server build and another run.

A release is immutable once built, and that is what makes it possible to build it somewhere else.
A bundle is a gzip-compressed tar of one release, with a manifest first that says what it is
(the application type, the commit, the directory it was built in), and a SHA-256 beside it that
whoever receives it checks before a byte is read. The tree is built in the build sandbox of the
builder, packed here, carried by whatever the operator has (``scp``, a CI artifact, the fleet's
tunnel), and activated on the server that runs it behind the health gate, exactly as a release
promoted from another application is (:mod:`noust.deployers.promote`).

Two things make a bundle safe to take from another machine, and neither depends on trusting it:

- **The checksum is mandatory.** :func:`unpack` refuses a file whose digest is not the one the
  operator brought, so a truncated transfer and a swapped file are the same refusal.
- **The extraction is the archive extraction** (:func:`~noust.managers.source_manager.extract_archive`):
  no absolute path, no ``..``, nothing written through a link, no device file, no setuid bit, a
  link only where it stays inside the release, and caps on members and bytes. A bundle cannot
  carry a link that points out of the release either, so :func:`pack` refuses a tree that has one
  (a Python virtual environment links to the interpreter) instead of producing a bundle the
  receiving side would refuse.

The links a release has to the application's ``shared/`` directory (its ``.env``, its persistent
paths) are the builder's own and are not packed: the receiving application links its own.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import socket
import tarfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from noust import __version__
from noust.core.exceptions import ValidationError
from noust.core.fs import FileSystem, get_fs
from noust.deployers.releases import _match_release_id
from noust.managers.source_manager import extract_archive

__all__ = [
    "BUNDLE_SUFFIX",
    "FORMAT",
    "MANIFEST_NAME",
    "MAX_BYTES",
    "MAX_ENTRIES",
    "Bundle",
    "Manifest",
    "pack",
    "read_manifest",
    "sha256_of",
    "unpack",
    "verify",
]

#: The version of the layout: the manifest first, then the release under :data:`TREE`.
FORMAT = 1

#: The first member of a bundle, whose bytes say what the rest is.
MANIFEST_NAME = "MANIFEST.json"

#: The directory of the archive that holds the release.
TREE = "release"

#: What a bundle's file name ends in.
BUNDLE_SUFFIX = ".noust-release.tar.gz"

#: The most a manifest may be: it names a handful of facts, and a megabyte of one is an attack.
MAX_MANIFEST_BYTES = 64 * 1024

#: The most members a bundle may extract to. A built ``node_modules`` is hundreds of thousands of
#: files; a bundle comes from a server the operator chose, and this only stops a runaway.
MAX_ENTRIES = 2_000_000

#: The most bytes a bundle may extract to (16 GiB).
MAX_BYTES = 16 * 1024**3

#: How much of a file is read at a time when hashing it.
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Manifest:
    """
    What a bundle says about itself.

    Attributes:
        format: The layout's version.
        app_type: The application type the release was built for (``nodejs``): a release runs
            on the type it was built as.
        domain: The application it was built for.
        release_id: The id of the release, which is the directory name it takes on arrival.
        commit: The commit it was built from; None for a source that is not git.
        branch: The branch that was built, when there was one.
        app_root: The application's directory on the builder, which the tree may name in text
            files and which is rewritten to the receiver's on arrival.
        built_at: When it was packed, ISO 8601 UTC.
        builder: The machine that packed it.
        noust_version: The Noust that packed it.
        files: Regular files in the release.
        bytes: Their total size.
    """

    format: int
    app_type: str
    domain: str
    release_id: str
    commit: str | None
    branch: str | None
    app_root: str
    built_at: str
    builder: str
    noust_version: str
    files: int
    bytes: int

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The manifest as plain data, as it is written into the bundle.
        """
        return {
            "format": self.format,
            "app_type": self.app_type,
            "domain": self.domain,
            "release_id": self.release_id,
            "commit": self.commit,
            "branch": self.branch,
            "app_root": self.app_root,
            "built_at": self.built_at,
            "builder": self.builder,
            "noust_version": self.noust_version,
            "files": self.files,
            "bytes": self.bytes,
        }

    @classmethod
    def from_dict(cls, data: object) -> Manifest:
        """
        Read a manifest, refusing anything that is not exactly one.

        Args:
            data: What the bundle's first member held, parsed.

        Returns:
            The manifest.

        Raises:
            ValidationError: It is not an object, has a key it should not or lacks one, or a
                value is not of its type.
        """
        refusal = ValidationError(
            "This is not a Noust release bundle",
            details="Its manifest is missing or damaged. Make the bundle again with "
            "'noust releases bundle'.",
        )
        if not isinstance(data, dict) or set(data) != set(_FIELDS):
            raise refusal
        for key, kinds in _FIELDS.items():
            value = data[key]
            if isinstance(value, bool) or not isinstance(value, kinds):
                raise refusal
        if data["format"] != FORMAT:
            raise ValidationError(
                f"This bundle is format {data['format']}, and this Noust reads format {FORMAT}",
                details="Make it again with a Noust of the same major version, or update this one.",
            )
        return cls(**{key: data[key] for key in _FIELDS})


#: The manifest's keys and the types each value may have.
_FIELDS: dict[str, tuple[type, ...]] = {
    "format": (int,),
    "app_type": (str,),
    "domain": (str,),
    "release_id": (str,),
    "commit": (str, type(None)),
    "branch": (str, type(None)),
    "app_root": (str,),
    "built_at": (str,),
    "builder": (str,),
    "noust_version": (str,),
    "files": (int,),
    "bytes": (int,),
}


@dataclass(frozen=True)
class Bundle:
    """
    A bundle on disk.

    Attributes:
        path: The file.
        sha256: Its digest, which the receiver must be given.
        size: Its size in bytes.
        manifest: What it says about itself.
    """

    path: Path
    sha256: str
    size: int
    manifest: Manifest


def sha256_of(path: Path) -> str:
    """
    Hash a file.

    Args:
        path: The file.

    Returns:
        Its SHA-256 as 64 lower-case hex digits.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def verify(path: Path, expected: str) -> str:
    """
    Check a file against the digest the operator brought.

    Args:
        path: The bundle.
        expected: Its SHA-256, as 64 hex digits (any case).

    Returns:
        The digest, lower-case.

    Raises:
        ValidationError: The digest given is not a SHA-256, or the file is not the one it names.
    """
    wanted = (expected or "").strip().lower()
    if len(wanted) != 64 or any(char not in "0123456789abcdef" for char in wanted):
        raise ValidationError(
            "That is not a SHA-256",
            details="Give the 64 hex digits 'noust releases bundle' printed, or the .sha256 file "
            "beside the bundle.",
            field="sha256",
        )
    actual = sha256_of(path)
    if not hmac.compare_digest(actual, wanted):
        raise ValidationError(
            "The bundle is not the file the checksum names",
            details=f"It is {actual}; the checksum given is {wanted}. A transfer that was cut "
            "short or a file that was replaced looks the same: nothing was extracted.",
            field="sha256",
        )
    return actual


def _stats(root: Path) -> tuple[int, int]:
    """
    Args:
        root: A release.

    Returns:
        Its regular files and their total size; links are not counted.
    """
    files = 0
    total = 0
    for current, _dirs, names in os.walk(root):
        for name in names:
            entry = Path(current) / name
            if not entry.is_symlink() and entry.is_file():
                files += 1
                total += entry.stat().st_size
    return files, total


def _shared_link(path: Path, root: Path, shared: Path | None) -> bool:
    """
    Tell whether a link is one a release makes to the application's ``shared/`` directory.

    Args:
        path: The link.
        root: The release.
        shared: The application's ``shared/`` directory, if there is one.

    Returns:
        True when its target is inside ``shared/``, however it is spelled.
    """
    if shared is None:
        return False
    target = Path(os.path.normpath(path.parent / os.readlink(path)))
    return target == shared or shared in target.parents


def pack(
    release: Path,
    manifest: Manifest,
    destination: Path,
    *,
    shared: Path | None = None,
    fs: FileSystem | None = None,
) -> Bundle:
    """
    Pack a built release into a bundle, and write its checksum beside it.

    Args:
        release: The release directory, as it was built.
        manifest: What to say about it; ``files`` and ``bytes`` are counted here.
        destination: The file to write; ``<destination>.sha256`` is written beside it.
        shared: The application's ``shared/`` directory: a link into it is the builder's own
            (its ``.env``, its persistent paths) and is left out.
        fs: The filesystem seam the checksum file and a cleanup go through.

    Returns:
        The bundle.

    Raises:
        ValidationError: The release holds a link that points outside it (and not into
            ``shared/``), a device file, a socket or a pipe, or the destination exists.
    """
    root = Path(os.path.normpath(release))
    if destination.exists() or destination.is_symlink():
        raise ValidationError(
            f"{destination} exists already",
            details="Choose another file: a bundle is never written over another.",
            field="out",
        )
    for current, directories, names in os.walk(root):
        for name in [*directories, *names]:
            entry = Path(current) / name
            if entry.is_symlink():
                if _shared_link(entry, root, shared):
                    continue
                target = Path(os.path.normpath(entry.parent / os.readlink(entry)))
                if os.path.isabs(os.readlink(entry)) or (
                    target != root and root not in target.parents
                ):
                    raise ValidationError(
                        f"{entry.relative_to(root)} is a link that points outside the release",
                        details=f"It leads to {os.readlink(entry)}. A Python virtual environment "
                        "does this (its interpreter is a link to the system's), and so does any "
                        "tree built with absolute links: a bundle cannot carry it, and the "
                        "receiving server would refuse it. Build such an application where it runs.",
                    )
            elif not (entry.is_file() or entry.is_dir()):
                raise ValidationError(
                    f"{entry.relative_to(root)} is not a regular file, a directory or a link",
                    details="A device file, a socket or a pipe cannot be carried.",
                )
    files, total = _stats(root)
    full = Manifest(**{**manifest.to_dict(), "files": files, "bytes": total})
    document = json.dumps(full.to_dict(), indent=2, sort_keys=True).encode("utf-8")

    def keep(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        if info.issym() and _shared_link(root / info.name.removeprefix(f"{TREE}/"), root, shared):
            return None
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        info.mode &= 0o755
        return info

    fs = fs or get_fs()
    written = False
    try:
        with tarfile.open(destination, "w:gz", compresslevel=6) as archive:
            head = tarfile.TarInfo(MANIFEST_NAME)
            head.size = len(document)
            head.mtime = int(datetime.now(timezone.utc).timestamp())
            head.mode = 0o644
            archive.addfile(head, io.BytesIO(document))
            archive.add(root, arcname=TREE, recursive=True, filter=keep)
        written = True
    finally:
        # A half-written bundle is never left to be mistaken for one.
        if not written:
            fs.remove(destination)
    digest = sha256_of(destination)
    sidecar = destination.with_name(destination.name + ".sha256")
    fs.write_text(sidecar, f"{digest}  {destination.name}\n")
    return Bundle(destination, digest, destination.stat().st_size, full)


def read_manifest(path: Path) -> Manifest:
    """
    Read what a bundle says about itself without extracting anything.

    Args:
        path: The bundle.

    Returns:
        The manifest.

    Raises:
        ValidationError: The file is not a bundle: not a gzip tar, its first member is not
            the manifest, or the manifest is too large or damaged.
    """
    refusal = ValidationError(
        "This is not a Noust release bundle",
        details="A bundle is what 'noust releases bundle' writes. Make it again.",
    )
    try:
        with tarfile.open(path, "r:gz") as archive:
            first = archive.next()
            if first is None or first.name != MANIFEST_NAME or not first.isfile():
                raise refusal
            if first.size > MAX_MANIFEST_BYTES:
                raise refusal
            handle = archive.extractfile(first)
            if handle is None:
                raise refusal
            raw = handle.read(MAX_MANIFEST_BYTES + 1)
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise refusal from exc
    try:
        return Manifest.from_dict(json.loads(raw.decode("utf-8")))
    except (ValueError, UnicodeDecodeError) as exc:
        raise refusal from exc


def unpack(
    path: Path,
    releases: Path,
    *,
    sha256: str,
    check: Callable[[Manifest], None] | None = None,
    fs: FileSystem | None = None,
    max_entries: int = MAX_ENTRIES,
    max_bytes: int = MAX_BYTES,
) -> tuple[Manifest, Path]:
    """
    Check a bundle against its checksum and extract its release into a new directory.

    The manifest is read and judged before any member is extracted, so a bundle for another
    type, or one that names a path instead of a release id, is refused without being written.

    Args:
        path: The bundle.
        releases: The ``releases/`` directory of the application; the release takes the id the
            manifest names, which must not exist there.
        sha256: The digest the operator brought.
        check: Judges the manifest before anything is extracted; raises to refuse.
        fs: The filesystem seam.
        max_entries: The most members it may extract to.
        max_bytes: The most bytes it may extract to.

    Returns:
        The manifest and the directory the release is in.

    Raises:
        ValidationError: The checksum is wrong, the file is not a bundle, its release id is not
            one, the check refuses it, or the release exists.
        SourceError: A member is unsafe to extract (see
            :func:`~noust.managers.source_manager.extract_archive`); nothing is left behind.
    """
    filesystem = fs or get_fs()
    verify(path, sha256)
    manifest = read_manifest(path)
    if _match_release_id(manifest.release_id) is None:
        raise ValidationError(
            "The bundle names a release that is not a release id",
            details=f"{manifest.release_id!r} is not what a build makes.",
        )
    if check is not None:
        check(manifest)
    destination = releases / manifest.release_id
    if destination.exists() or destination.is_symlink():
        raise ValidationError(
            f"{destination.name} exists already", details=f"In {releases}.", field="release"
        )
    staging = releases / f".bundle-{manifest.release_id}"
    if staging.exists():
        filesystem.remove_tree(staging)
    filesystem.make_dir(staging.parent, parents=True)
    try:
        extract_archive(
            path,
            staging,
            archive_format="tar.gz",
            max_entries=max_entries,
            max_total_bytes=max_bytes,
            fs=filesystem,
        )
        tree = staging / TREE
        if not tree.is_dir() or tree.is_symlink():
            raise ValidationError(
                "This bundle holds no release",
                details="Its tree is missing. Make it again with 'noust releases bundle'.",
            )
        filesystem.move(tree, destination)
    finally:
        if staging.exists():
            filesystem.remove_tree(staging)
    return manifest, destination


def describe_builder() -> tuple[str, str]:
    """
    Returns:
        The name of this machine and the Noust running on it, as a manifest records them.
    """
    return socket.gethostname(), __version__
