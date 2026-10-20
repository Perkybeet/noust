# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A monorepo's workspaces load a file of their own; this keeps it in step with the application's.

A monorepo runs one unit per workspace, and each unit loads ``<workspace>/.env.production`` (the
deployment writes it), not the ``.env`` at the root that ``noust env`` and the console edit. Left
alone, an edit of the application's variables, a rotated secret or a shared variable that changed
reached the root file and no unit: the one place the operator can see the variables was not the
place the processes read them. :func:`propagate` carries a change to every workspace's file,
changing only the names the change names, so what a workspace has of its own (its port, what its
``package.json`` asked for) is kept (checklist B-43, B-46, B-48).

The files of a release are the release's: on the release layout this writes the ones of the
release that serves, and the deployment that builds the next one carries the rest.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from pathlib import Path, PurePosixPath

from noust.core.config import Config
from noust.core.fs import SECRET_MODE, FileSystem, get_fs
from noust.core.logger import Logger
from noust.core.store import App, Service, get_store
from noust.deployers.helpers.env_manager import EnvManager
from noust.deployers.helpers.layout import CURRENT_LINK, code_path_for
from noust.deployers.helpers.permissions import escapes, hand_over_file
from noust.managers.app_identity import service_account

__all__ = ["ENV_FILE_NAME", "propagate", "workspace_paths"]

#: The file a workspace's unit loads, in the workspace's directory.
ENV_FILE_NAME = ".env.production"

#: What a workspace's file holds that the application's variables never carry: its own port and
#: the mode the unit sets. A change to the application's variables leaves them as they are.
_OWN = frozenset({"PORT", "NODE_ENV"})


def workspace_paths(app: App | None, units: list[Service] | None = None) -> list[str]:
    """
    List the directories of an application's workspaces, relative to its tree.

    The deployment records them as JSON beside the application's variables, and each workspace's
    unit runs in its directory, so both are read: the record may be missing on an application
    that was deployed by an old version, and the unit may not be recorded yet.

    Args:
        app: The application.
        units: The units the store records for it.

    Returns:
        Relative POSIX paths, each a plain path inside the tree (no ``..``, no root), without
        repeats, in the order they were recorded.
    """
    found: list[str] = []
    recorded = (app.env_vars or {}).get("_workspaces") if app is not None else None
    if recorded:
        try:
            for entry in json.loads(recorded):
                if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                    found.append(entry["path"])
        except (ValueError, TypeError):
            found = []
    root = Path(app.app_path) if app is not None and app.app_path else None
    for unit in units or []:
        if root is None or not unit.working_directory:
            continue
        directory = Path(unit.working_directory)
        for base in (root / CURRENT_LINK, root):
            if directory.is_relative_to(base):
                found.append(directory.relative_to(base).as_posix())
                break
    clean: list[str] = []
    for path in found:
        parts = PurePosixPath(path)
        if parts.is_absolute() or ".." in parts.parts or str(parts) in (".", ""):
            continue
        if str(parts) not in clean:
            clean.append(str(parts))
    return clean


def propagate(
    app: App,
    *,
    changed: Mapping[str, str],
    removed: Collection[str] = (),
    root: Path | None = None,
    manager: EnvManager | None = None,
    fs: FileSystem | None = None,
    logger: Logger | None = None,
) -> list[Path]:
    """
    Carry a change of an application's variables to the file every workspace loads.

    Only the names the change names are written; a name the workspace's file does not hold yet is
    added. A workspace that has no file (not deployed yet), a file that is a link and one that
    leaves the tree are left alone: a repository is untrusted input, and this runs as root.

    Args:
        app: A monorepo.
        changed: Names to the value they have now.
        removed: Names the application no longer has.
        root: The tree whose files are written; the one that runs by default.
        manager: Writer to use. Defaults to a new one.
        fs: Filesystem the writes and the hand-over go through.
        logger: Where a file that could not be handed over is reported.

    Returns:
        The files written.
    """
    names = {name for name in changed if name not in _OWN}
    gone = {name for name in removed if name not in _OWN}
    if not names and not gone:
        return []
    tree = root or code_path_for(app)
    writer = manager or EnvManager(fs=fs)
    units = [s for s in get_store().list_services() if app.id is not None and s.app_id == app.id]
    user, group = service_account(app, Config())
    written: list[Path] = []
    for relative in workspace_paths(app, units):
        path = tree / relative / ENV_FILE_NAME
        if path.is_symlink() or not path.is_file() or escapes(path, tree):
            continue
        current = writer.read_env_file(path)
        wanted = {
            **{n: v for n, v in current.items() if n not in gone},
            **{n: changed[n] for n in names},
        }
        if wanted == current:
            continue
        writer.write_env_file(path, wanted)
        # Written through a rename, it would be root's and the unit could not read it.
        hand_over_file(
            path,
            user=user,
            group=group,
            mode=SECRET_MODE,
            fs=fs or get_fs(),
            logger=logger or Logger(),
        )
        written.append(path)
    return written
