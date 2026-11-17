# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What Docker will take a path of a compose file for.

A repository can hold links, and the daemon follows them: ``./host`` that points at ``/`` is
``/``. A path is therefore never judged by how it is spelled. :func:`reach` follows it the way
the kernel does, with every link on the way, and says where the application's own tree ends:
what a link inside it leads out of is the way out of the tree, whoever put the link there.

Nothing here guesses. A path that cannot be followed (a loop of links, a name that is not a
path, a directory the account may not enter) is reported as such and the guard refuses it:
what cannot be resolved is exactly what cannot be judged.

Two spellings of one path are looked at when it is absolute: the cleaned one (``a/../b`` is
``b``, which is how Compose hands a relative path to the daemon) and the raw one, followed link
by link (``a/../b`` is whatever ``..`` of the place ``a`` leads to is). The daemon may take
either, and a finding in one is a finding.
"""

from __future__ import annotations

import errno
import os
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

#: How many links one path may go through before it is a loop. The kernel's own limit is 40.
MAX_LINKS = 40

#: Container runtimes' APIs: a mount of one of these sockets is the Docker socket by another name.
RUNTIME_SOCKETS = ("docker.sock", "containerd.sock", "podman.sock", "crio.sock", "cri-dockerd.sock")

#: What is not a path of this server but something Docker fetches: a URL, a repository.
_REMOTE = re.compile(r"(?:[A-Za-z][A-Za-z0-9+.-]*://.*|git@.+|github\.com/.+)", re.DOTALL)

#: A build context that names an image or another service of the stack, not a path.
_NOT_A_PATH_CONTEXT = re.compile(r"(?:docker-image|target|service)[:].*", re.DOTALL)

#: What a build context or an include can be: a path of this server, something fetched from the
#: network, or an image or a service of the stack.
PATH, REMOTE, OTHER = "path", "remote", "other"


@dataclass(frozen=True)
class Reached:
    """
    Where a path of a compose file leads.

    Attributes:
        written: The path as the file spells it.
        places: Every absolute path the daemon may take it for: the cleaned one and where each
            link on it leads. Judged one by one: a finding in any is a finding.
        real: Where the kernel ends up for the cleaned spelling; None when it cannot be followed.
        inside: Whether it stays in the application's tree once every link is followed.
        link: When a link inside the tree is the way out, ``<link> leads to <where>``; else empty.
        link_at: That link's own absolute path; else empty.
        unresolved: It cannot be followed (a loop, a NUL byte, a directory that cannot be read).
    """

    written: str
    places: tuple[PurePosixPath, ...] = ()
    real: PurePosixPath | None = None
    inside: bool = True
    link: str = ""
    unresolved: bool = False
    link_at: str = ""


def kind_of(value: str) -> str:
    """
    Tell what a build context or an include names.

    Args:
        value: The context or the include as written.

    Returns:
        ``remote`` for a URL (``https://``, ``git://``, ``oci://``...), a ``git@`` address and the
        ``github.com/...`` shorthand, which the daemon or Compose would download; ``other`` for an
        image or a service of the stack given as a context (``docker-image://x``, ``service:x``),
        which is neither a path nor a download of this server's; ``path`` for the rest.
    """
    if _NOT_A_PATH_CONTEXT.fullmatch(value):
        return OTHER
    return REMOTE if _REMOTE.fullmatch(value) is not None else PATH


def _follow(path: str) -> str:
    """
    Follow every link of an absolute path, the way the kernel does.

    Args:
        path: An absolute path; what does not exist is kept as written.

    Returns:
        The path with no link left.

    Raises:
        OSError: There are more than :data:`MAX_LINKS` links on the way, or a directory cannot be
            read: neither is a path that can be judged.
    """
    done = "/"
    pending = path.split("/")
    hops = 0
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            done = posixpath.dirname(done)
            continue
        candidate = posixpath.join(done, part)
        try:
            target = os.readlink(candidate)
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.ENOTDIR, errno.EINVAL):
                done = candidate
                continue
            raise
        hops += 1
        if hops > MAX_LINKS:
            raise OSError(errno.ELOOP, "too many levels of symbolic links", path)
        if target.startswith("/"):
            done = "/"
        pending = [*target.split("/"), *pending]
    return done


def within(path: PurePosixPath, root: PurePosixPath) -> bool:
    """
    Args:
        path: An absolute path.
        root: A directory.

    Returns:
        Whether ``path`` is ``root`` or below it, by components.
    """
    return path == root or root in path.parents


def followed(path: str | Path) -> PurePosixPath | None:
    """
    Args:
        path: An absolute path.

    Returns:
        It with every link followed, or None when it cannot be (a loop, a directory that cannot
        be read).
    """
    try:
        return PurePosixPath(_follow(posixpath.normpath(str(path))))
    except (OSError, ValueError):
        return None


def _link_out(lexical: str, app: str, real_app: PurePosixPath) -> tuple[str, str]:
    """
    Find the link of the application's tree that carries a path out of it.

    Args:
        lexical: The cleaned path, which is inside ``app``.
        app: The application directory.
        real_app: Where the application directory really is.

    Returns:
        ``<link, relative to the application> leads to <where>`` and the link's absolute path, for
        the first link on the way whose destination is outside the tree; empty when none is (the
        way out is a link of the application's own path).
    """
    relative = PurePosixPath(lexical).relative_to(app)
    here = app
    for part in relative.parts:
        here = posixpath.join(here, part)
        if not os.path.islink(here):
            continue
        destination = followed(here)
        if destination is None or not within(destination, real_app):
            where = destination if destination is not None else "somewhere that cannot be followed"
            return f"./{PurePosixPath(here).relative_to(app)} leads to {where}", here
    return "", ""


def reach(source: str, base: Path | str, app_path: Path | str) -> Reached | None:
    """
    Follow a path of a compose file to where Docker will take it.

    Args:
        source: The path as the file spells it: absolute, relative to ``base`` or starting with
            ``~``.
        base: The directory a relative path is read from: the project directory.
        app_path: The application directory, the tree a path may stay in.

    Returns:
        Where it leads; None for a path made of variables, which the model Compose resolves
        says where it went.
    """
    if not source or "$" in source:
        return None
    expanded = os.path.expanduser(source) if source.startswith("~") else source
    joined = expanded if posixpath.isabs(expanded) else posixpath.join(str(base), expanded)
    lexical = PurePosixPath(posixpath.normpath(joined))
    app = PurePosixPath(posixpath.normpath(str(app_path)))
    try:
        if "\x00" in source or expanded.startswith("~"):
            raise ValueError(source)
        real = _follow(str(lexical))
        raw = _follow(joined) if posixpath.isabs(expanded) else real
        real_app = PurePosixPath(_follow(str(app)))
    except (OSError, ValueError):
        # What it looks to be is all that is left to say of it: a path that is lexically outside
        # the application is outside whatever else cannot be known of it.
        return Reached(source, (lexical,), None, within(lexical, app), "", unresolved=True)
    places = tuple(dict.fromkeys((lexical, PurePosixPath(real), PurePosixPath(raw))))
    inside = all(within(place, real_app) for place in (PurePosixPath(real), PurePosixPath(raw)))
    link, link_at = "", ""
    if not inside and within(lexical, app):
        link, link_at = _link_out(str(lexical), str(app), real_app)
    return Reached(source, places, PurePosixPath(real), inside, link, link_at=link_at)
