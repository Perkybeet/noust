# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Code uploaded as an archive: a source for people who have no repository.

An application is deployed from a ``.zip``, ``.tar.gz``, ``.tar.bz2`` or ``.tar.xz`` of its
project that somebody picked in the console or gave to ``noust upload add``. The archive is
sent in chunks by the same resumable store the database dumps use
(:class:`~noust.managers.database.uploads.UploadStore` with the :data:`SOURCES` profile: owner
only, size capped, swept after a day), and when the last chunk is in it is *extracted once*, here,
into a directory of its own with the checks every archive gets
(:func:`~noust.managers.source_manager.extract_archive`: no absolute path, no ``..``, nothing
written through a link, no device node or setuid bit, caps on members and bytes). The archive
itself is then thrown away.

The result is a directory, and a directory is already a source: ``upload:<id>`` is that
directory's address (:func:`~noust.validators.source.validate_source` reads it as a local source
whose path nobody typed), so building, inspecting, redeploying and the allowed origins need
nothing new. The identifier is 128 random bits, which is what lets a person who holds it use it
and nobody else guess it; the extracted tree is root's alone (0700).

An application keeps being built from the tree its source names. A new version is a new upload
and a change of source (:mod:`noust.deployers.source_change`); the tree nothing names any more is
removed when its application goes, when the source moves on, and by :meth:`SourceUploads.prune`
after a week for the ones that never got used.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from noust.core import audit, paths
from noust.core.config import Config
from noust.core.exceptions import SourceError, ValidationError
from noust.core.fs import SECRET_DIR_MODE, SECRET_MODE, FileSystem, get_fs
from noust.core.store import NoustStore, get_store
from noust.managers.database.uploads import (
    Upload,
    UploadProfile,
    UploadStore,
    check_file_name,
)
from noust.managers.source_manager import (
    detect_archive_format,
    extract_archive,
    flatten_single_directory,
)
from noust.validators.source import (
    check_upload_id,
    is_upload_source,
    is_upload_tree,
    upload_source,
    upload_source_id,
    upload_tree,
)

__all__ = [
    "DOCKERFILE_MAX_BYTES",
    "DOCKERFILE_NAMES",
    "SOURCES",
    "TREE_TTL",
    "SourceUploads",
    "UploadedSource",
    "validate_dockerfile",
]

_log = logging.getLogger(__name__)

#: Archives people upload as the code of an application.
SOURCES = UploadProfile(
    extensions=("zip", "tar", "gz", "tgz", "bz2", "xz"),
    limit_key="uploads.source_max_bytes",
    default_max=512 * 1024**2,
    noun="a code archive",
    extension_lead="An archive's name ends in one of: ",
    name_details="Name the archive with a plain file name, without a path.",
    extension_hint="Zip the project (zip -r code.zip .) or tar it (tar czf code.tgz .) first.",
    limit_details="Raise uploads.source_max_bytes with 'noust config set', or deploy from a "
    "repository or a directory on the server.",
    event="source.upload",
    target="source:upload",
    directory="uploads",
)

#: The names a Dockerfile is given by the tools that build one.
DOCKERFILE_NAMES: tuple[str, ...] = ("Dockerfile", "Containerfile")

#: The most a pasted Dockerfile may be: a Dockerfile is a page, and a megabyte of one is a mistake.
DOCKERFILE_MAX_BYTES = 256 * 1024

#: An instruction that starts a stage; a Dockerfile without one builds nothing.
_FROM = re.compile(r"^\s*FROM\s+\S", re.IGNORECASE | re.MULTILINE)

#: How long an extracted tree that no application names is kept, in seconds: a week, so a
#: person can upload first and create the application when they are ready.
TREE_TTL = 7 * 24 * 3600


@dataclass
class UploadedSource:
    """
    An archive that was uploaded and extracted.

    Attributes:
        id: Its identifier.
        source: What an application's source is set to: ``upload:<id>``.
        file_name: The name the client gave the archive.
        size: Bytes the archive had.
        sha256: The digest the client declared, if any.
        files: Files in the extracted project.
        bytes: Bytes in the extracted project.
        owner: Who uploaded it.
        created_at: When.
        in_use: Whether an application's source names it.
    """

    id: str
    source: str
    file_name: str
    size: int
    sha256: str | None
    files: int
    bytes: int
    owner: str
    created_at: str
    in_use: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The upload as plain data, for the API and ``--json``.
        """
        return asdict(self)


def validate_dockerfile(content: str) -> str:
    """
    Check that a pasted text can be a Dockerfile.

    Args:
        content: What was pasted.

    Returns:
        The text, with the line ends it came with.

    Raises:
        ValidationError: It is empty, too large, holds a NUL byte, or has no ``FROM``
            instruction. Docker would refuse the last one only after the transfer and a
            queue; saying it here puts the sentence beside the field.
    """
    if not isinstance(content, str) or not content.strip():
        raise ValidationError(
            "The Dockerfile is empty", details="Paste the Dockerfile's text.", field="content"
        )
    if len(content.encode("utf-8")) > DOCKERFILE_MAX_BYTES:
        raise ValidationError(
            "That Dockerfile is too large",
            details=f"A pasted Dockerfile may be at most {DOCKERFILE_MAX_BYTES // 1024} KiB. "
            "Put a larger project in an archive or a repository.",
            field="content",
        )
    if "\x00" in content:
        raise ValidationError(
            "A Dockerfile is text", details="It holds a NUL byte.", field="content"
        )
    if not _FROM.search(content):
        raise ValidationError(
            "No FROM instruction",
            details="A Dockerfile starts from an image: FROM node:22-alpine.",
            field="content",
        )
    return content


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _measure(tree: Path) -> tuple[int, int]:
    """
    Count what an extraction produced.

    Args:
        tree: The extracted project.

    Returns:
        The number of regular files and their bytes.
    """
    files = 0
    total = 0
    for entry in tree.rglob("*"):
        if entry.is_symlink() or not entry.is_file():
            continue
        files += 1
        total += entry.stat().st_size
    return files, total


class SourceUploads:
    """
    The archives uploaded as code, their sessions and their extracted trees.
    """

    def __init__(
        self,
        *,
        fs: FileSystem | None = None,
        config: Config | None = None,
        store: NoustStore | None = None,
        sessions: UploadStore | None = None,
    ) -> None:
        """
        Args:
            fs: The filesystem trees are made and removed through.
            config: The configuration the size limit is read from.
            store: The application store, to tell which trees are in use.
            sessions: The chunk store; one under the uploads directory by default.
        """
        self._fs = fs
        self._config = config
        self._store = store
        self._sessions = sessions

    @property
    def fs(self) -> FileSystem:
        """The filesystem trees are made and removed through."""
        return self._fs or get_fs()

    @property
    def sessions(self) -> UploadStore:
        """The resumable store the archive is sent into."""
        if self._sessions is None:
            self._sessions = UploadStore(
                paths.uploads_dir() / ".pending",
                fs=self._fs,
                config=self._config,
                profile=SOURCES,
            )
        return self._sessions

    # ------------------------------------------------------------ sending

    def start(self, file_name: str, size: int, *, owner: str, sha256: str | None = None) -> Upload:
        """
        Start an upload.

        Args:
            file_name: The archive's name; only its ending is read.
            size: Its size in bytes.
            owner: Who is uploading, as the audit trail names them.
            sha256: The digest the finished file must have, if the client has one.

        Returns:
            The session, with nothing received yet.

        Raises:
            SourceError: The name is not that of an archive format Noust extracts.
            ValidationError: The name, the size or the digest is not usable.
            UploadTooLargeError: The size is past the limit or the disk cannot hold it.
        """
        # Judged before a byte moves: the name decides how the archive is read, and an upload
        # that could never be extracted is not worth the transfer.
        check_file_name(file_name, SOURCES)
        detect_archive_format(file_name)
        self.prune()
        return self.sessions.create(file_name, size, owner=owner, sha256=sha256)

    def finish(self, upload_id: str, *, owner: str) -> UploadedSource:
        """
        Close an upload and extract it into the directory a source names.

        Args:
            upload_id: The upload.
            owner: Who is uploading.

        Returns:
            The extracted project.

        Raises:
            UploadOffsetError: Bytes are missing.
            ValidationError: The digest does not match (the data is discarded).
            SourceError: The archive is malformed, holds an unsafe member or is past the caps
                on members and bytes, or holds no files (nothing is kept).
        """
        session = self.sessions.complete(upload_id, owner=owner)
        data = self.sessions.path(upload_id)
        tree = upload_tree(upload_id)
        kept = False
        try:
            self.fs.make_dir(paths.uploads_dir(), mode=SECRET_DIR_MODE, parents=True)
            extract_archive(
                data, tree, archive_format=detect_archive_format(session.file_name), fs=self.fs
            )
            flatten_single_directory(tree, self.fs)
            files, total = _measure(tree)
            if files == 0:
                raise SourceError(
                    f"{session.file_name} holds no files",
                    details="Upload an archive of the project itself, with its files in it.",
                )
            self.fs.chmod(tree, SECRET_DIR_MODE)
            kept = True
        finally:
            # The archive is never kept, and a tree that did not come out whole is not either:
            # a refusal, a cancellation and a full disk all leave nothing behind.
            if not kept:
                self.forget(upload_id)
            self.sessions.discard(upload_id)
        uploaded = UploadedSource(
            id=upload_id,
            source=upload_source(upload_id),
            file_name=session.file_name,
            size=session.size,
            sha256=session.sha256,
            files=files,
            bytes=total,
            owner=session.owner,
            created_at=_now(),
        )
        self.fs.write_text(self._sidecar(upload_id), json.dumps(asdict(uploaded)), mode=SECRET_MODE)
        return uploaded

    def add_dockerfile(
        self, content: str, *, owner: str, name: str = "Dockerfile"
    ) -> UploadedSource:
        """
        Make a project of one file out of a Dockerfile that was pasted.

        There is no repository and no files beside it: the build context is that one file, so a
        ``COPY`` finds nothing and the Dockerfile fetches what it needs itself (``ADD`` of a URL,
        ``RUN git clone``, a base image that already holds the program). The result is an upload
        like any other, so the container deployer builds it from the directory its source names.

        Args:
            content: The Dockerfile's text.
            owner: Who pasted it.
            name: ``Dockerfile`` or ``Containerfile``.

        Returns:
            The project, whose ``source`` an application is set to.

        Raises:
            ValidationError: The text is not one a build could use, or the name is not a
                Dockerfile's.
        """
        text = validate_dockerfile(content)
        if name not in DOCKERFILE_NAMES:
            raise ValidationError(
                f"{name!r} is not the name of a Dockerfile",
                details="It is Dockerfile or Containerfile.",
                field="name",
            )
        data = text.encode("utf-8")
        upload_id = secrets.token_hex(16)
        tree = upload_tree(upload_id)
        kept = False
        try:
            self.fs.make_dir(paths.uploads_dir(), mode=SECRET_DIR_MODE, parents=True)
            self.fs.make_dir(tree, mode=SECRET_DIR_MODE)
            self.fs.write_text(tree / name, text)
            kept = True
        finally:
            if not kept:
                self.forget(upload_id)
        uploaded = UploadedSource(
            id=upload_id,
            source=upload_source(upload_id),
            file_name=name,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            files=1,
            bytes=len(data),
            owner=owner,
            created_at=_now(),
        )
        self.fs.write_text(self._sidecar(upload_id), json.dumps(asdict(uploaded)), mode=SECRET_MODE)
        audit.record(
            SOURCES.event,
            actor=audit.Actor.from_label(owner),
            target=f"{SOURCES.target}/{upload_id}",
            details={"file": name, "size": uploaded.size, "sha256": uploaded.sha256},
        )
        return uploaded

    def add_file(self, archive: Path, *, owner: str) -> UploadedSource:
        """
        Take an archive that is already on this server, as ``noust upload add`` does.

        The file goes through the same session, size limit and extraction as a browser's
        upload; nothing about the command line is trusted more.

        Args:
            archive: The archive.
            owner: Who is uploading.

        Returns:
            The extracted project.

        Raises:
            SourceError: The file is missing, not an archive, or unsafe to extract.
            ValidationError: The name or the size is not usable.
            UploadTooLargeError: It is past the limit.
        """
        if archive.is_symlink() or not archive.is_file():
            raise SourceError(
                f"{archive} is not a file",
                details="Give the path of a .zip, .tar.gz, .tar.bz2 or .tar.xz archive.",
            )
        if archive.name in DOCKERFILE_NAMES:
            # A Dockerfile given as it is: a project of one file, not an archive to extract.
            if archive.stat().st_size > DOCKERFILE_MAX_BYTES:
                raise ValidationError(
                    "That Dockerfile is too large",
                    details=f"A Dockerfile may be at most {DOCKERFILE_MAX_BYTES // 1024} KiB.",
                )
            try:
                text = archive.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise SourceError(f"Cannot read {archive}", details=str(exc)) from exc
            return self.add_dockerfile(text, owner=owner, name=archive.name)
        size = archive.stat().st_size
        session = self.start(archive.name, size, owner=owner)
        finished = False
        try:
            offset = 0
            with open(archive, "rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    self.sessions.append(session.id, offset, chunk, owner=owner)
                    offset += len(chunk)
            uploaded = self.finish(session.id, owner=owner)
            finished = True
            return uploaded
        finally:
            if not finished:
                self.sessions.discard(session.id)

    # ------------------------------------------------------------ reading

    def _sidecar(self, upload_id: str) -> Path:
        """
        Args:
            upload_id: The identifier.

        Returns:
            The file holding what is known about the upload, outside the tree so it is never
            copied into a release.
        """
        return paths.uploads_dir() / f"{upload_id}.json"

    def _used(self) -> set[str]:
        """
        Returns:
            The identifiers of the uploads some application's source names.
        """
        store = self._store or get_store()
        used: set[str] = set()
        for app in store.list_apps():
            text = (app.source or "").strip()
            if is_upload_source(text):
                try:
                    used.add(upload_source_id(text))
                except SourceError:
                    continue
            elif text and is_upload_tree(text):
                used.add(Path(text).name)
        return used

    def get(self, upload_id: str) -> UploadedSource | None:
        """
        Read what is known about an extracted upload.

        Args:
            upload_id: The identifier.

        Returns:
            The upload, or None when there is no such extracted tree.
        """
        if not upload_tree(check_upload_id(upload_id)).is_dir():
            return None
        try:
            data = json.loads(self._sidecar(upload_id).read_text(encoding="utf-8"))
            uploaded = UploadedSource(**{**data, "in_use": upload_id in self._used()})
        except (OSError, ValueError, TypeError):
            return None
        return uploaded

    def items(self) -> list[UploadedSource]:
        """
        Returns:
            Every extracted upload, newest first.
        """
        found: list[UploadedSource] = []
        try:
            entries = sorted(paths.uploads_dir().iterdir())
        except OSError:
            return found
        used = self._used()
        for entry in entries:
            if not (entry.is_dir() and is_upload_tree(entry)):
                continue
            uploaded = self.get(entry.name)
            if uploaded is not None:
                uploaded.in_use = entry.name in used
                found.append(uploaded)
        return sorted(found, key=lambda item: item.created_at, reverse=True)

    # ------------------------------------------------------------ removing

    def forget(self, upload_id: str) -> None:
        """
        Remove an extracted upload and what is known about it.

        Args:
            upload_id: The identifier.
        """
        check_upload_id(upload_id)
        for path in (upload_tree(upload_id), self._sidecar(upload_id)):
            try:
                if path.is_dir() and not path.is_symlink():
                    self.fs.remove_tree(path)
                else:
                    self.fs.remove(path)
            except OSError as exc:
                _log.warning("Could not remove %s: %s", path, exc)

    def release(self, source: str | None) -> bool:
        """
        Remove the tree a source named, once nothing names it.

        Called when an application is deleted and when its source moves on.

        Args:
            source: The source an application had.

        Returns:
            Whether a tree was removed.
        """
        text = (source or "").strip()
        if is_upload_source(text):
            try:
                upload_id = upload_source_id(text)
            except SourceError:
                return False
        elif text and is_upload_tree(text):
            upload_id = Path(text).name
        else:
            return False
        if upload_id in self._used() or not upload_tree(upload_id).is_dir():
            return False
        self.forget(upload_id)
        return True

    def prune(self, *, now: float | None = None) -> list[str]:
        """
        Remove what nobody needs: sessions nobody finished and trees nothing names.

        Args:
            now: The moment to judge at, as ``time.time()``; the clock by default.

        Returns:
            The identifiers of the trees removed.
        """
        self.sessions.sweep(now=now)
        horizon = (now if now is not None else time.time()) - TREE_TTL
        removed: list[str] = []
        used = self._used()
        try:
            entries = sorted(paths.uploads_dir().iterdir())
        except OSError:
            return removed
        for entry in entries:
            if not (entry.is_dir() and is_upload_tree(entry)) or entry.name in used:
                continue
            # The record is written when the extraction ends; the tree's own time says when
            # its files were last moved, which is the same moment or a flatten later.
            try:
                made = self._sidecar(entry.name)
                if (made if made.exists() else entry).stat().st_mtime >= horizon:
                    continue
            except OSError:
                continue
            self.forget(entry.name)
            removed.append(entry.name)
        return removed
