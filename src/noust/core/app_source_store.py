# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How an application's repository is fetched, and when a push deploys it.

One row per application in ``app_source`` (``schema_v14.SOURCES_SQL``), kept
apart from ``apps`` on purpose: a deploy writes the whole ``apps`` row it read
when it started, and a setting an operator changed meanwhile would be put back
(see :mod:`noust.core.app_build_store`). No row means the defaults, which are
what every application did before these options existed except for the
skip markers and watch paths, which only ever decline a push.

- ``submodules``: initialise the repository's submodules and put their files in
  the release (default on).
- ``lfs``: ``auto`` fetches Git LFS objects when the repository tracks files
  with LFS, ``on`` always does and ``off`` never does (default ``auto``).
- ``shallow``: fetch only the commit being deployed (default on); off keeps the
  whole history, which a build that reads ``git describe`` or the log needs.
- ``watch_paths``: globs a push must touch to deploy (see
  :mod:`noust.core.source_rules`); none means every push deploys.
- ``skip_markers``: honour ``[skip ci]`` / ``[skip cd]`` (default on).

This is the only module that reads or writes the table (rule 3); the single
setter validates, so the CLI, the API and ``noust apply`` cannot disagree on
what is acceptable (rule 4).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal, cast

from noust.core import audit
from noust.core.exceptions import NoustError, ValidationError
from noust.core.source_rules import normalize_watch_paths
from noust.core.store import NoustStore, get_store

#: What ``lfs`` can be.
LfsMode = Literal["auto", "on", "off"]
LFS_MODES: tuple[str, ...] = ("auto", "on", "off")

#: The keys a caller can change, in the order they are shown.
OPTION_KEYS: tuple[str, ...] = ("submodules", "lfs", "shallow", "watch_paths", "skip_markers")


@dataclass(frozen=True)
class SourceOptions:
    """
    An application's source options.

    Attributes:
        submodules: Initialise and export submodules.
        lfs: ``auto``, ``on`` or ``off``.
        shallow: Fetch only the latest commit.
        watch_paths: Globs a push must touch to deploy; empty for every push.
        skip_markers: Honour ``[skip ci]`` and ``[skip cd]``.
        customised: Whether a row exists, that is, an operator set something.
    """

    submodules: bool = True
    lfs: LfsMode = "auto"
    shallow: bool = True
    watch_paths: tuple[str, ...] = ()
    skip_markers: bool = True
    customised: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the options for the API and ``--json``.

        Returns:
            Every option, with watch paths as a list.
        """
        return {
            "submodules": self.submodules,
            "lfs": self.lfs,
            "shallow": self.shallow,
            "watch_paths": list(self.watch_paths),
            "skip_markers": self.skip_markers,
            "customised": self.customised,
        }


def get_options(domain: str, *, store: NoustStore | None = None) -> SourceOptions:
    """
    Read an application's source options.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        The options; the defaults for an application with no row or no
        application.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "SELECT s.submodules, s.lfs, s.shallow, s.watch_paths, s.skip_markers "
            "FROM app_source s JOIN apps a ON a.id = s.app_id WHERE a.domain = ?",
            (domain,),
        )
        row = cursor.fetchone()
    if row is None:
        return SourceOptions()
    try:
        paths = json.loads(row[3])
    except ValueError:
        paths = []
    return SourceOptions(
        submodules=bool(row[0]),
        lfs=cast(LfsMode, row[1]),
        shallow=bool(row[2]),
        watch_paths=tuple(str(item) for item in paths) if isinstance(paths, list) else (),
        skip_markers=bool(row[4]),
        customised=True,
    )


def _checked_flag(value: object, key: str) -> bool:
    """
    Args:
        value: What a caller gave for a switch.
        key: Its name, for the refusal.

    Returns:
        The switch.

    Raises:
        ValidationError: It is not true or false.
    """
    if not isinstance(value, bool):
        raise ValidationError(
            f"{key} must be true or false", details=f"Give {key} as on or off.", field=key
        )
    return value


def check_options(
    *,
    submodules: object = None,
    lfs: object = None,
    shallow: object = None,
    watch_paths: object = None,
    skip_markers: object = None,
) -> dict[str, Any]:
    """
    Validate the options someone gave, without storing anything.

    The one definition of what a source option may be: the setter below, the
    import of an export document and ``noust apply`` all ask it.

    Args:
        submodules: True or False.
        lfs: ``auto``, ``on`` or ``off``.
        shallow: True or False.
        watch_paths: A list or lines of globs (:func:`normalize_watch_paths`).
        skip_markers: True or False.

    Returns:
        The options that were given, as stored: watch paths as a tuple.

    Raises:
        ValidationError: A value is not acceptable; ``field`` names it.
    """
    changes: dict[str, Any] = {}
    if submodules is not None:
        changes["submodules"] = _checked_flag(submodules, "submodules")
    if shallow is not None:
        changes["shallow"] = _checked_flag(shallow, "shallow")
    if skip_markers is not None:
        changes["skip_markers"] = _checked_flag(skip_markers, "skip_markers")
    if lfs is not None:
        if lfs not in LFS_MODES:
            raise ValidationError(
                f"lfs must be one of {', '.join(LFS_MODES)}",
                details="auto fetches LFS files when the repository tracks any; on always does; "
                "off never does.",
                field="lfs",
            )
        changes["lfs"] = lfs
    if watch_paths is not None:
        if isinstance(watch_paths, str):
            changes["watch_paths"] = normalize_watch_paths(watch_paths)
        elif isinstance(watch_paths, (list, tuple)):
            changes["watch_paths"] = normalize_watch_paths([str(item) for item in watch_paths])
        else:
            raise ValidationError(
                "watch_paths must be a list of paths",
                details="Give one glob per line, such as apps/web/**.",
                field="watch_paths",
            )
    return changes


def set_options(
    domain: str,
    *,
    submodules: object = None,
    lfs: object = None,
    shallow: object = None,
    watch_paths: object = None,
    skip_markers: object = None,
    actor: str | None = None,
    store: NoustStore | None = None,
) -> SourceOptions:
    """
    Change some of an application's source options, keeping the others.

    A key left as None is not touched; ``watch_paths`` of an empty list clears
    them. Every value is validated here, whoever calls, and the change is
    recorded in the audit trail.

    Args:
        domain: The application's domain.
        submodules: True or False.
        lfs: ``auto``, ``on`` or ``off``.
        shallow: True or False.
        watch_paths: A list or lines of globs (:func:`normalize_watch_paths`).
        skip_markers: True or False.
        actor: Who changed it, for the record.
        store: The store; the process-wide one when None.

    Returns:
        The options now in force.

    Raises:
        NoustError: The application is unknown.
        ValidationError: A value is not acceptable; ``field`` names it.
    """
    target = store or get_store()
    current = get_options(domain, store=target)
    changes = check_options(
        submodules=submodules,
        lfs=lfs,
        shallow=shallow,
        watch_paths=watch_paths,
        skip_markers=skip_markers,
    )
    updated = replace(current, customised=True, **changes)
    now = datetime.now().isoformat()
    with target._transaction() as cursor:
        cursor.execute("SELECT id FROM apps WHERE domain = ?", (domain,))
        row = cursor.fetchone()
        if row is None:
            raise NoustError(
                f"Application not found: {domain}",
                details="Run 'noust list' to see what is deployed.",
            )
        cursor.execute(
            "INSERT INTO app_source (app_id, submodules, lfs, shallow, watch_paths, "
            "skip_markers, updated_by, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(app_id) DO UPDATE SET submodules = excluded.submodules, "
            "lfs = excluded.lfs, shallow = excluded.shallow, watch_paths = excluded.watch_paths, "
            "skip_markers = excluded.skip_markers, updated_by = excluded.updated_by, "
            "updated_at = excluded.updated_at",
            (
                row[0],
                int(updated.submodules),
                updated.lfs,
                int(updated.shallow),
                json.dumps(list(updated.watch_paths)),
                int(updated.skip_markers),
                actor,
                now,
            ),
        )
    audit.record(
        "apps.source_options",
        target=f"app:{domain}",
        details={
            key: (list(value) if isinstance(value, tuple) else value)
            for key, value in changes.items()
        },
    )
    return updated


def reset_options(
    domain: str, *, actor: str | None = None, store: NoustStore | None = None
) -> bool:
    """
    Forget an application's source options, back to the defaults.

    Args:
        domain: The application's domain.
        actor: Who did it, for the record.
        store: The store; the process-wide one when None.

    Returns:
        True when there was something to forget.
    """
    target = store or get_store()
    with target._transaction() as cursor:
        cursor.execute(
            "DELETE FROM app_source WHERE app_id = (SELECT id FROM apps WHERE domain = ?)",
            (domain,),
        )
        removed = cursor.rowcount > 0
    if removed:
        audit.record("apps.source_options", target=f"app:{domain}", details={"action": "reset"})
    return removed


__all__ = [
    "LFS_MODES",
    "OPTION_KEYS",
    "LfsMode",
    "SourceOptions",
    "check_options",
    "get_options",
    "reset_options",
    "set_options",
]
