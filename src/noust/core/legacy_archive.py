# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one reader of the backups and exports written before 4.0.

A backup is data, and data outlives names. An archive taken by an earlier release keeps its
payload under the directory that release called it, and an application export says which
format it is and which release made it in the words of its time. Both are still restorable and
importable, as long as the file exists; what Noust writes now carries Noust's name. Every place
that opens one of these asks here, so there is one list of what an old file looks like.

The names themselves are in :mod:`noust.core.legacy_names`, with the rest of what is still
accepted; this module is what reads them.
"""

from __future__ import annotations

import copy
import tarfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from noust.core import legacy_names
from noust.core.versions import parse_version

__all__ = [
    "EXPORT_FORMAT",
    "EXPORT_FORMATS",
    "EXPORT_VERSION_KEY",
    "PAYLOAD_DIR",
    "PAYLOAD_DIRS",
    "export_for",
    "export_made_by",
    "export_with_legacy_names",
    "external_dump",
    "find_member",
    "is_export_document",
    "is_payload_dir",
    "payload_dir_in",
    "payload_prefixes",
]

#: The directory a backup archive keeps its payload (manifest, dumps, volumes) under.
PAYLOAD_DIR = "noust-backup"
#: Every directory an archive that can be restored here may keep it under: the current one
#: first, then the one before 4.0.
PAYLOAD_DIRS = (PAYLOAD_DIR, legacy_names.ARCHIVE_PAYLOAD_DIR)

#: What an application export says it is, and which release made it.
EXPORT_FORMAT = "noust-app"
EXPORT_VERSION_KEY = "noust_version"
EXPORT_FORMATS = (EXPORT_FORMAT, legacy_names.EXPORT_FORMAT)
_EXPORT_VERSION_KEYS = (EXPORT_VERSION_KEY, legacy_names.EXPORT_VERSION_KEY)


def is_payload_dir(name: str) -> bool:
    """
    Tell whether a name is the payload directory of an archive, under either spelling.

    Args:
        name: A directory name found at the top of an archive or beside it.

    Returns:
        True for the current name and for the one before 4.0.
    """
    return name in PAYLOAD_DIRS


def payload_dir_in(extracted: Path) -> str:
    """
    Find which payload directory an extracted archive uses.

    Args:
        extracted: The directory the archive was extracted into.

    Returns:
        The current name when the archive has it, else the older one when that is there, else
        the current name (an archive of 1.x has neither, and nothing is read from it).
    """
    for name in PAYLOAD_DIRS:
        candidate = extracted / name
        if candidate.is_dir() and not candidate.is_symlink():
            return name
    return PAYLOAD_DIR


def payload_prefixes(subdirectory: str) -> tuple[str, ...]:
    """
    Name the places in an archive where one kind of payload may be, under either spelling.

    Args:
        subdirectory: ``databases`` or ``volumes``.

    Returns:
        ``<payload dir>/<subdirectory>/`` for each spelling, for a check that a member is
        inside the payload (``str.startswith`` takes the tuple).
    """
    return tuple(f"{name}/{subdirectory}/" for name in PAYLOAD_DIRS)


def external_dump(entry: Mapping[str, Any], folder: Path, label: str) -> Path | None:
    """
    Find the dump an archive of 1.x kept outside itself, if it says one, and only if it is safe to read.

    An archive from before dumps travelled inside it names, in its manifest or its sidecar, where on this
    machine the dump was left. That entry is whatever the archive's maker wrote (a copy a central gives
    back brings its own), so the only file outside the archive it may name is one in the folder of this
    server's backups, where 1.x wrote them, links followed: anywhere else would be a restore asked to
    load a file of the machine into a database.

    Args:
        entry: The manifest's or the sidecar's entry for one dump.
        folder: The folder of this server's backups.
        label: What the entry describes, for the error.

    Returns:
        The file, resolved; None when the entry names none that exists.

    Raises:
        BackupError: The entry names a file that is not in ``folder``.
    """
    external = entry.get("backup_path") or entry.get("path")
    if not (isinstance(external, str) and external and Path(external).is_file()):
        return None
    real = Path(external).resolve()
    if real.is_relative_to(folder.resolve()):
        return real
    # Said only when it is refused, and in the catalog's words: this module is read by every restore.
    from noust.core.exceptions import BackupError
    from noust.core.server_text import say

    raise BackupError.from_text(
        say(
            "backup_manager.backup_entry_points_outside",
            exc=(
                f"{external} is not in {folder}: a dump outside the archive is only read from the "
                "folder of this server's backups"
            ),
            label=label,
        )
    )


def find_member(archive: tarfile.TarFile, relative: str) -> tarfile.TarInfo | None:
    """
    Find a member of the payload, wherever the archive that holds it put the payload.

    Args:
        archive: An open archive.
        relative: The member's path inside the payload directory (``manifest.json``).

    Returns:
        The member, or None when the archive has it under neither spelling.
    """
    for name in PAYLOAD_DIRS:
        try:
            return archive.getmember(f"{name}/{relative}")
        except KeyError:
            continue
    return None


def is_export_document(document: Mapping[str, Any]) -> bool:
    """
    Tell whether a document is an application export, in either format name.

    Args:
        document: A parsed JSON document.

    Returns:
        True when its ``format`` is the current name or the one before 4.0.
    """
    return document.get("format") in EXPORT_FORMATS


def export_made_by(document: Mapping[str, Any]) -> str | None:
    """
    Read which release made an export, under either key.

    Args:
        document: A parsed export document.

    Returns:
        The release, or None when the document does not say.
    """
    for key in _EXPORT_VERSION_KEYS:
        value = document.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def export_for(version: str | None, document: Mapping[str, Any]) -> dict[str, Any]:
    """
    Spell an export the way the server it is going to reads it.

    Chosen by the version the server declared, because what is sent to it has to be understood
    by it: 4.0 reads both spellings and is given the current one, anything older, or a server
    that has not said, is given the one it knows.

    Args:
        version: The release the destination last reported, or None.
        document: An export in the current spelling (or already in the old one).

    Returns:
        The document in the destination's spelling; a copy.
    """
    parsed = parse_version(version) if version else None
    if parsed is not None and parsed.major >= 4:
        return copy.deepcopy(dict(document))
    return export_with_legacy_names(document)


def export_with_legacy_names(document: Mapping[str, Any]) -> dict[str, Any]:
    """
    Spell an export the way a server before 4.0 reads it.

    A central moves an application between servers by exporting it on one and importing it on
    another; when the server that imports it is older than 4.0 it takes only the old format
    name. Nothing else about the document differs.

    Args:
        document: An export in the current spelling.

    Returns:
        A copy whose format and release key are the old ones.
    """
    converted = copy.deepcopy(dict(document))
    if converted.get("format") == EXPORT_FORMAT:
        converted["format"] = legacy_names.EXPORT_FORMAT
    if EXPORT_VERSION_KEY in converted:
        converted[legacy_names.EXPORT_VERSION_KEY] = converted.pop(EXPORT_VERSION_KEY)
    return converted
