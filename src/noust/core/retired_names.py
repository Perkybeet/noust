# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What 4.0 does about the names the product had before 3.0.

3.x moved a server that ran the product under its old name onto Noust's names the first time
root ran a command, and left links with the old ones (``/etc/wasm`` and the like). 4.0 moves
nothing. It does two things, both about not hurting a server that is already running:

- :func:`require_moved` refuses to start on a server whose data 3.x never finished moving.
  Every path in 4.0 means the new directory, and a store created empty there would answer
  "No applications deployed" about a full server: the trap that once made seventeen client
  sites look empty. The way out is to install 3.3.1, let it move the data, and upgrade.
- :func:`retire_links` removes the links 3.x left, and only the ones that are provably its own
  and that nothing uses. A link goes when it points exactly where the migration pointed it,
  its target is there, and neither the configuration, an nginx site nor a row of the store
  names it. A real file or directory is never touched: the name is also WebAssembly's, and a
  runtime's ``/var/lib/wasm`` is not ours to judge.

This is the one place that still spells the old names. It exists so that an upgrade cannot
break a server, and it can be deleted when no 3.x server is left to upgrade (4.x at the
earliest; the 3.3 line is supported until then).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from noust.core import paths
from noust.core.exceptions import ConfigError
from noust.core.fs import FileSystem, get_fs

logger = logging.getLogger(__name__)

#: The real system. Tests build a root under a temporary directory.
SYSTEM_ROOT = Path("/")

#: The name the product had until 3.0.
_OLD = "wasm"

#: Largest file an nginx scan reads: a site is a few kilobytes, and a log that landed in the
#: configuration tree by mistake is not worth reading whole.
_NGINX_FILE_LIMIT = 2 * 1024 * 1024

#: How long the store scan waits for a writer. The scan only reads.
_STORE_TIMEOUT = 5.0

LinkKind = Literal["data", "nginx", "command"]


@dataclass(frozen=True)
class CompatLink:
    """
    One link 3.x may have left.

    Attributes:
        link: Where the link is.
        target: Where the migration pointed it, exactly.
        shown: The path as the configuration, a site or a record would spell it.
        kind: ``data`` (a directory that holds Noust's data), ``nginx`` (the blue/green
            upstreams, which sites include) or ``command`` (the installer's alias).
    """

    link: Path
    target: Path
    shown: str
    kind: LinkKind


@dataclass(frozen=True)
class LinkOutcome:
    """
    What became, or would become, of a link.

    Attributes:
        link: The link.
        retired: Whether it was removed (or would be, under a rehearsal).
        reason: Why, in a sentence.
    """

    link: Path
    retired: bool
    reason: str


def old_data_roots() -> tuple[Path, ...]:
    """
    Name the old locations of the directories that hold Noust's configuration and secrets.

    A link under one of these names may still be standing (:func:`retire_links` keeps the ones
    something uses), and a bind mount that reaches Noust's configuration, store or backups
    through it reaches the real thing. Every guard that refuses the new names refuses these too,
    from this list.

    Returns:
        The configuration, state and backup directories under the names they had until 3.0.
    """
    return (
        Path("/etc") / _OLD,
        Path("/var/lib") / _OLD,
        Path("/var/backups") / _OLD,
    )


def old_log_dir() -> Path:
    """
    Name the old location of Noust's log directory.

    Returns:
        ``/var/log/<old name>``: a real directory, not a link, when 3.x left logs in it. A build
        must not open it, as it must not open the current one.
    """
    return Path("/var/log") / _OLD


def _home() -> Path:
    """
    Return the running user's home directory.

    Returns:
        The home directory; its own function so a test can name another.
    """
    return Path.home()


def _shown(path: Path, root: Path) -> str:
    """
    Spell a path the way the system would, without the root a test put it under.

    Args:
        path: The path.
        root: The root it is under.

    Returns:
        ``/etc/wasm`` for ``<root>/etc/wasm``; the path itself when it is not under the root.
    """
    try:
        return "/" + str(path.relative_to(root))
    except ValueError:
        return str(path)


def compat_links(root: Path | None = None, home: Path | None = None) -> list[CompatLink]:
    """
    List every link 3.x may have left, with where it pointed.

    Args:
        root: The filesystem root; the real system by default.
        home: The home directory of the user running; the process's own by default.

    Returns:
        The links, data first.
    """
    root = root or SYSTEM_ROOT
    home = home or _home()
    data = [
        (root / "etc" / _OLD, root / "etc" / paths.NAME),
        (root / "var/lib" / _OLD, root / "var/lib" / paths.NAME),
        (root / "var/backups" / _OLD, root / "var/backups" / paths.NAME),
        (home / ".local/share" / _OLD, home / ".local/share" / paths.NAME),
    ]
    found = [CompatLink(link, target, _shown(link, root), "data") for link, target in data]
    upstreams = root / "etc/nginx" / f"{_OLD}-upstreams"
    found.append(
        CompatLink(
            upstreams,
            root / "etc/nginx" / f"{paths.NAME}-upstreams",
            _shown(upstreams, root),
            "nginx",
        )
    )
    alias = root / "usr/local/bin" / _OLD
    found.append(CompatLink(alias, root / "opt/noust/bin" / _OLD, _shown(alias, root), "command"))
    return found


# -- a server whose data was never moved -------------------------------------------------------


def _holds(directory: Path, marker: str) -> bool:
    """
    Tell whether a real directory holds the file that makes it Noust's.

    Args:
        directory: The directory.
        marker: The file that only the product kept there.

    Returns:
        True for a directory that is not a link and holds the file.
    """
    return (
        directory.is_dir()
        and not directory.is_symlink()
        and (directory / marker).is_file()
        and not (directory / marker).is_symlink()
    )


def unmoved_data(root: Path | None = None, home: Path | None = None) -> list[Path]:
    """
    Find the data 3.x never moved to Noust's names.

    Args:
        root: The filesystem root; the real system by default.
        home: The home directory of the user running.

    Returns:
        The directories, in the order they matter. A directory is listed only when it holds the
        file the product kept there (the configuration, the store): the old name is also
        WebAssembly's, and a runtime's directory is not ours.
    """
    root = root or SYSTEM_ROOT
    home = home or _home()
    candidates = [
        (root / "etc" / _OLD, "config.yaml"),
        (root / "var/lib" / _OLD, f"{_OLD}.db"),
        (home / ".local/share" / _OLD, f"{_OLD}.db"),
    ]
    found = [directory for directory, marker in candidates if _holds(directory, marker)]
    # A run stopped between renaming the store and renaming its directory leaves it here.
    new_state = root / "var/lib" / paths.NAME
    if (new_state / f"{_OLD}.db").is_file() and not (new_state / paths.STORE_NAME).exists():
        found.append(new_state)
    return found


def require_moved(root: Path | None = None, home: Path | None = None) -> None:
    """
    Refuse to run on a server whose data is still under the old names.

    Args:
        root: The filesystem root; the real system by default.
        home: The home directory of the user running.

    Raises:
        ConfigError: When :func:`unmoved_data` finds something. Nothing is changed.
    """
    if paths.DATA_DIR is not None:
        # The operator named where Noust lives; no old location is looked at.
        return
    found = unmoved_data(root, home)
    if not found:
        return
    raise ConfigError(
        "This server still keeps Noust's data where it was kept before 3.0: "
        + ", ".join(str(directory) for directory in found),
        details=(
            "Noust 4.0 does not move it, and starting here would create an empty store beside "
            "the real one. Nothing was changed. Install Noust 3.3.1, run any command there as "
            "root (it moves the data onto Noust's names), check that `noust list` shows your "
            "applications, then upgrade to 4.0."
        ),
    )


# -- the links 3.x left -------------------------------------------------------------------------


def _store_files(root: Path, home: Path) -> list[Path]:
    """
    Name the stores that may record a path.

    Args:
        root: The filesystem root.
        home: The home directory of the user running.

    Returns:
        The system store and the user's, whichever exist.
    """
    candidates = [
        root / "var/lib" / paths.NAME / paths.STORE_NAME,
        home / ".local/share" / paths.NAME / paths.STORE_NAME,
    ]
    return [path for path in candidates if path.is_file()]


def _quote(name: str) -> str:
    """
    Quote an identifier for SQLite.

    Args:
        name: A table or column name taken from the store itself.

    Returns:
        The name between double quotes.
    """
    return '"' + name.replace('"', '""') + '"'


def _mentioned_in_store(store: Path, needle: str) -> str | None:
    """
    Find the first row of a store that contains a text.

    Args:
        store: The store file; it is opened read-only.
        needle: The text, compared byte for byte (``instr``, not ``LIKE``).

    Returns:
        ``table.column`` of a row that contains it, or None.

    Raises:
        sqlite3.Error: When the store cannot be read.
    """
    connection = sqlite3.connect(f"{store.as_uri()}?mode=ro", uri=True, timeout=_STORE_TIMEOUT)
    try:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' AND sql NOT LIKE 'CREATE VIRTUAL%'"
        ).fetchall()
        for (table,) in tables:
            columns = connection.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
            for column in (row[1] for row in columns):
                # The identifiers are the store's own, read from its schema and quoted; the
                # text looked for is bound.
                found = connection.execute(
                    f"SELECT 1 FROM {_quote(table)} "  # noqa: S608
                    f"WHERE instr(CAST({_quote(column)} AS TEXT), ?) > 0 LIMIT 1",
                    (needle,),
                ).fetchone()
                if found:
                    return f"{table}.{column}"
    finally:
        connection.close()
    return None


def _mentioned_in_nginx(root: Path, needle: str) -> Path | None:
    """
    Find the first nginx file that contains a text.

    Args:
        root: The filesystem root.
        needle: The text.

    Returns:
        The file, or None. Links are not followed into directories, and the files that enable a
        site (links to the one in sites-available) are read through, so a site is found by the
        name it is known by.
    """
    for directory, _subdirectories, names in os.walk(root / "etc/nginx", followlinks=False):
        for name in sorted(names):
            path = Path(directory) / name
            try:
                if path.stat().st_size > _NGINX_FILE_LIMIT:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if needle in text:
                return path
    return None


def _held_by(item: CompatLink, root: Path, home: Path) -> str | None:
    """
    Say what still names a link, when something does.

    Args:
        item: The link.
        root: The filesystem root.
        home: The home directory of the user running.

    Returns:
        A sentence naming what uses it, or None when nothing is known to.
    """
    if item.kind == "nginx":
        site = _mentioned_in_nginx(root, item.shown)
        if site is not None:
            return f"nginx still reads {item.shown}: {site} names it"
        return None
    config = root / "etc" / paths.NAME / "config.yaml"
    try:
        if config.is_file() and item.shown in config.read_text(encoding="utf-8", errors="replace"):
            return f"the configuration ({config}) names {item.shown}"
    except OSError as exc:
        return f"the configuration ({config}) could not be read ({exc}), so it cannot be ruled out"
    for store in _store_files(root, home):
        try:
            where = _mentioned_in_store(store, item.shown)
        except sqlite3.Error as exc:
            return f"the store {store} could not be read ({exc}), so what it records is unknown"
        if where is not None:
            return f"the store ({store}) records {item.shown} in {where}"
    return None


def _consider_command(item: CompatLink) -> LinkOutcome:
    """
    Judge the ``wasm`` entry the installer linked into ``/usr/local/bin``.

    Args:
        item: The link.

    Returns:
        Its outcome. It is Noust's when it points into a ``bin`` that holds ``noust`` or at a
        file that is gone (the venv dropped it with the old version): a program of the same name
        that something else installed is not.
    """
    link = item.link
    destination = Path(os.readlink(link))
    if not destination.is_absolute():
        return LinkOutcome(link, False, f"it points to {destination}, which is not the installer's")
    if destination.name != _OLD:
        return LinkOutcome(link, False, f"it points to {destination}, which is not the installer's")
    if not destination.exists() or (destination.parent / paths.NAME).exists():
        return LinkOutcome(
            link, True, "the command was renamed in 3.0 and this alias is no longer served"
        )
    return LinkOutcome(link, False, f"it points to {destination}, a program Noust did not install")


def _consider(item: CompatLink, root: Path, home: Path) -> LinkOutcome | None:
    """
    Judge one link.

    Args:
        item: The link.
        root: The filesystem root.
        home: The home directory of the user running.

    Returns:
        None when there is nothing at the name, else what becomes of it.
    """
    link = item.link
    if not os.path.lexists(link):
        return None
    if not link.is_symlink():
        return LinkOutcome(link, False, "it is not a link, so it is not something 3.x made")
    if item.kind == "command":
        return _consider_command(item)
    destination = os.readlink(link)
    if destination != str(item.target):
        return LinkOutcome(
            link,
            False,
            f"it points to {destination}, not to {item.target} where 3.x pointed it",
        )
    if not item.target.is_dir():
        return LinkOutcome(link, False, f"its target {item.target} is gone")
    held = _held_by(item, root, home)
    if held is not None:
        return LinkOutcome(link, False, held)
    return LinkOutcome(link, True, f"it points to {item.target}, which holds everything now")


def retire_links(
    *,
    root: Path | None = None,
    home: Path | None = None,
    fs: FileSystem | None = None,
) -> list[LinkOutcome]:
    """
    Remove the links 3.x left that are provably its own and unused.

    Args:
        root: The filesystem root; the real system by default.
        home: The home directory of the user running.
        fs: Removes the links; the process's own seam by default, so a rehearsal removes nothing
            and still says what it would.

    Returns:
        One outcome per name that has something at it. Names with nothing at them are left out,
        so a second run on a clean server returns an empty list.
    """
    if paths.DATA_DIR is not None:
        return []
    root = root or SYSTEM_ROOT
    home = home or _home()
    seam = fs or get_fs()
    outcomes: list[LinkOutcome] = []
    for item in compat_links(root, home):
        outcome = _consider(item, root, home)
        if outcome is None:
            continue
        if outcome.retired:
            try:
                seam.remove(item.link)
            except OSError as exc:
                outcome = LinkOutcome(item.link, False, f"it could not be removed: {exc}")
            else:
                logger.info("Retired %s: %s", item.link, outcome.reason)
        outcomes.append(outcome)
    return outcomes
