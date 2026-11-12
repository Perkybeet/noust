# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where on the central's disk a collected copy is (item 195).

``<backup directory>/.collected/<server>/<folder>/<kind>/<copy>/<file>``: by server, by application
(or by database, or ``noust``), by kind, and one folder for each copy, so a copy's files are never
mixed with another's and a whole server's can be taken off the disk by one folder. It sits below the
directory the central's own backups use, under a name no application can have (it starts with a
dot), so what lists that directory (the central's own backups, ``noust backup list``) never sees it.

Every name that makes a path was judged first (:func:`~noust.fleet.collect.names.safe_segment`) and is
judged again here, and a path that does not resolve inside the root is refused, so a bug elsewhere
cannot write outside it.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from noust.fleet.collect.names import safe_segment

__all__ = ["Layout", "copy_folder_name"]

#: The folder of the collected copies under the backup directory.
ROOT_NAME = ".collected"

_UNSAFE = re.compile(r"[^A-Za-z0-9._@+=,~$-]")


def copy_folder_name(item_id: str) -> str:
    """
    Name the folder of one copy.

    An identifier is what a server called a copy: printable, but anything. The folder is made of
    its safe characters and a digest of the whole, so two identifiers that differ never share a
    folder.

    Args:
        item_id: The copy's identifier on its server.

    Returns:
        A plain name.
    """
    plain = _UNSAFE.sub("_", item_id).lstrip(".-_") or "copy"
    return f"{plain[:80]}-{hashlib.sha256(item_id.encode('utf-8')).hexdigest()[:10]}"


class Layout:
    """
    The places of the collected copies.

    Args:
        root: The folder they are all under.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def default(cls) -> Layout:
        """
        Returns:
            The layout under the backup directory this central is configured with.
        """
        from noust.core.config import Config, resolve_backup_directory

        return cls(resolve_backup_directory(Config().get("backup.directory")) / ROOT_NAME)

    def _inside(self, path: Path) -> Path:
        """
        Args:
            path: A path built from names.

        Returns:
            The path.

        Raises:
            ValueError: It does not stay inside the root.
        """
        resolved = Path(os.path.normpath(path))
        if not resolved.is_relative_to(Path(os.path.normpath(self.root))):
            raise ValueError(f"{path} is outside {self.root}")
        return resolved

    def copy_dir(self, node: str, folder: str, kind: str, item_id: str) -> Path:
        """
        Args:
            node: The server.
            folder: The application (or database) folder.
            kind: The kind of copy.
            item_id: The copy's identifier on its server.

        Returns:
            Where the copy's files are.

        Raises:
            ValueError: A name is not a plain one.
        """
        for part in (node, folder, kind):
            if safe_segment(part) is None:
                raise ValueError(f"Not a plain name: {part!r}")
        return self._inside(self.root / node / folder / kind / copy_folder_name(item_id))

    def file_path(self, directory: Path, name: str) -> Path:
        """
        Args:
            directory: A copy's folder (:meth:`copy_dir`).
            name: A file's name.

        Returns:
            Where that file is.

        Raises:
            ValueError: The name is not a plain one.
        """
        if safe_segment(name) is None:
            raise ValueError(f"Not a plain name: {name!r}")
        return self._inside(directory / name)

    def node_dir(self, node: str) -> Path:
        """
        Args:
            node: The server.

        Returns:
            Its folder.

        Raises:
            ValueError: The name is not a plain one.
        """
        if safe_segment(node) is None:
            raise ValueError(f"Not a plain name: {node!r}")
        return self._inside(self.root / node)
