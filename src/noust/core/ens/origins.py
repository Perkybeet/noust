# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where code may be deployed from (ENS G18: op.exp.5, mp.sw.1).

A deploy is the execution of code from somewhere else as this server. The
operator can name where that code may come from in ``security.allowed_sources``,
a list of:

- a host: ``git.example.com`` (any repository there), or ``*.example.com``
  (any subdomain);
- an organisation on a host: ``github.com/acme`` (every repository of
  ``acme``, not of ``acme-evil``);
- a repository: ``github.com/acme/shop``;
- ``local``: directories on this server, which are otherwise refused once a
  list is set, because a path says nothing about where its code came from;
- ``push``: code that arrives with ``git push`` to this server's own repositories
  (``noust push enable``), which are otherwise refused once a list is set for the same reason:
  whoever holds a pusher's key decides what is deployed;
- ``upload``: an archive somebody uploaded (``noust upload add``, the console), which is
  otherwise refused once a list is set: it says nothing about where its code came from either;
- ``recipes``: the code Noust prepares from a recipe of the catalogue, in a folder of its own
  beside the store (:mod:`noust.core.recipe_sources`). Without the word a server that names its
  repositories could install nothing from the catalogue. ``local`` also covers it, as it did before
  the word existed: a folder of this server is a directory of this server.

A port is not compared. An entry names a host and a path and cannot name a port (it is refused where
it is saved), and a source on ``git.example.com:8443`` is ``git.example.com``: the rule says which
machine code may come from, as it did for an ssh remote on another port, and the same machine's other
services are not told apart from it. What a port does decide is the credential a source is cloned
with (:func:`noust.core.repositories.repository_port`), which is never sent to another port.

An empty list allows everything, as every earlier version did. The list is a
security setting (``security.*``, changed by the ``security`` role), so an
administrator cannot allow a repository of their own to themselves.

The check runs where every source is classified before anything is fetched
(:func:`noust.managers.source_manager._validate_source_keeping_credentials`),
so a deploy, an update, a preview and a webhook all meet it (rule 4). A
refusal is an audit event, ``apps.source.denied``, naming the source.
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from noust.core.exceptions import SourceError
from noust.validators.origins import LOCAL, PUSH, RECIPES, UPLOAD

__all__ = [
    "CONFIG_KEY",
    "LOCAL",
    "PUSH",
    "RECIPE",
    "RECIPES",
    "UPLOAD",
    "Origin",
    "OriginNotAllowed",
    "allow_command",
    "allowed_sources",
    "check_origin",
    "describe_origin",
    "matches",
    "origin_kind",
    "recipes_allowed",
]

#: The configuration key holding the list.
CONFIG_KEY = "security.allowed_sources"

#: The kind of an origin that is the code Noust prepared from a recipe; the entry that allows it
#: is :data:`~noust.validators.origins.RECIPES`.
RECIPE = "recipe"


class OriginNotAllowed(SourceError):
    """A source is outside the origins this server allows code from."""


@dataclass(frozen=True)
class Origin:
    """
    Where a source is, without its credential.

    Attributes:
        kind: ``git``, ``archive``, ``local``, ``push``, ``upload`` or ``recipe``.
        host: The host, lower-case; None for a local directory.
        path: The path's segments on that host (organisation, repository),
            without a ``.git`` suffix; empty for a local directory.
        display: ``host/org/repo``, or the directory, for messages.
    """

    kind: str
    host: str | None
    path: tuple[str, ...]
    display: str


def _strip_credentials(url: str) -> str:
    """
    Drop the userinfo of a URL, whatever scheme.

    Args:
        url: A URL, possibly ``https://user:token@host/...``.

    Returns:
        The URL without ``user:token@``.
    """
    scheme, sep, rest = url.partition("://")
    if not sep:
        return url
    authority, slash, tail = rest.partition("/")
    if "@" in authority:
        authority = authority.rsplit("@", 1)[1]
    return f"{scheme}://{authority}{slash}{tail}"


def describe_origin(source: str, kind: str | None = None) -> Origin:
    """
    Say where a source is.

    Args:
        source: The source as given or stored: a git URL (HTTPS, SSH or the
            ``user@host:path`` form), an archive URL, a local path, or GitHub's
            ``owner/repo`` shorthand.
        kind: ``git``, ``archive``, ``local``, ``push``, ``upload`` or ``recipe`` when the caller
            already classified it; guessed from the text otherwise.

    Returns:
        The origin.
    """
    from noust.validators.source import (
        expand_github_shorthand,
        is_local_path,
        is_push_source,
        parse_git_url,
    )

    text = (source or "").strip().split("#", 1)[0]
    if kind == PUSH or (kind is None and is_push_source(text)):
        return Origin(PUSH, None, (), text)
    if kind == UPLOAD:
        return Origin(UPLOAD, None, (), text)
    if kind == RECIPE:
        return Origin(RECIPE, None, (), text)
    if kind is None:
        kind = "local" if is_local_path(text) else "git"
    if kind == "local":
        return Origin("local", None, (), text)
    text = _strip_credentials(expand_github_shorthand(text))
    host = ""
    segments: tuple[str, ...] = ()
    if kind == "git":
        parsed = parse_git_url(text)
        host = str(parsed.get("host") or "")
        # The owner of a GitLab subgroup is several segments (group/sub).
        segments = tuple(
            part
            for part in (*str(parsed.get("owner") or "").split("/"), parsed.get("repo"))
            if part
        )
    if not host:
        try:
            split = urlsplit(text)
        except ValueError:
            # Not an address (a bracket that is no IPv6 literal): no host, so no entry allows it.
            return Origin(kind, None, (), text)
        host = split.hostname or ""
        segments = tuple(part for part in split.path.split("/") if part)
    host = host.lower().rstrip(".")
    if "@" in host:
        host = host.rsplit("@", 1)[1]
    host = host.split(":", 1)[0]
    segments = tuple(segment.removesuffix(".git") for segment in segments)
    display = "/".join((host, *segments)) if host else text
    return Origin(kind, host or None, segments, display)


def _pattern_parts(pattern: str) -> tuple[str, tuple[str, ...]]:
    """
    Split an allowed-origin entry into its host and path.

    Args:
        pattern: ``host[/org[/repo]]``, optionally with a scheme.

    Returns:
        The host pattern, lower-case, and the path segments.
    """
    text = pattern.strip()
    if "://" in text:
        text = _strip_credentials(text).split("://", 1)[1]
    parts = [part for part in text.split("/") if part]
    if not parts:
        return "", ()
    host = parts[0].lower().split(":", 1)[0].rstrip(".")
    return host, tuple(part.removesuffix(".git").lower() for part in parts[1:])


def matches(pattern: str, origin: Origin) -> bool:
    """
    Report whether one allowed-origin entry covers a source.

    Args:
        pattern: An entry of ``security.allowed_sources``.
        origin: Where the source is.

    Returns:
        True when the entry allows it. Hosts match exactly, or ``*.domain``
        any subdomain of ``domain``; path segments match whole, so
        ``github.com/acme`` never allows ``github.com/acme-evil``.
    """
    entry = pattern.strip()
    if entry.lower() == LOCAL:
        # A recipe's folder is a directory of this server, and was one before it had a word.
        return origin.kind in ("local", RECIPE)
    if entry.lower() == PUSH:
        return origin.kind == PUSH
    if entry.lower() == UPLOAD:
        return origin.kind == UPLOAD
    if entry.lower() == RECIPES:
        return origin.kind == RECIPE
    if origin.host is None:
        return False
    host, path = _pattern_parts(entry)
    if not host:
        return False
    if host.startswith("*."):
        if not origin.host.endswith(host[1:]):
            return False
    elif origin.host != host:
        return False
    if len(path) > len(origin.path):
        return False
    return all(want == have.lower() for want, have in zip(path, origin.path, strict=False))


def allowed_sources(config: Any | None = None) -> list[str]:
    """
    Read the list this server allows code from.

    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        The entries, stripped; empty when none is set (anything allowed).
    """
    if config is None:
        from noust.core.config import Config

        config = Config()
    get = getattr(config, "get", None)
    raw = get(CONFIG_KEY, []) if callable(get) else []
    if isinstance(raw, str):
        raw = [part for part in raw.replace(",", " ").split() if part]
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(entry).strip() for entry in raw if str(entry).strip()]


def origin_kind(source_type: str, source: str) -> str:
    """
    Say which kind of origin a classified source is.

    The one place a source becomes a kind, so a deploy, an update, a preview, a webhook and a change
    of source all judge the same text the same way: code that arrives with ``git push``, an archive
    somebody uploaded, and the folder a recipe was rendered into are each a kind of their own, and a
    list of hosts says nothing about them.

    Args:
        source_type: What :func:`noust.validators.source.validate_source` made of it: ``git``,
            ``archive`` or ``local``.
        source: The source as it is recorded, already resolved.

    Returns:
        ``push``, ``upload``, ``recipe``, or ``source_type`` itself.
    """
    from noust.core.recipe_sources import is_recipe_tree
    from noust.validators.source import is_push_source, is_upload_tree

    if is_push_source(source):
        return PUSH
    if source_type == "local":
        if is_upload_tree(source):
            return UPLOAD
        if is_recipe_tree(source):
            return RECIPE
    return source_type


def allow_command(entries: Sequence[str], entry: str) -> str:
    """
    Write the command that adds one entry to the list.

    The list is written whole, so the command carries the entries the server has. The value is
    quoted for the shell and needs no brackets: ``noust config set`` reads a comma-separated list,
    and a test runs what this returns.

    Args:
        entries: The entries the list holds now.
        entry: The one to add.

    Returns:
        A command an operator can paste.
    """
    return f"noust config set {CONFIG_KEY} {shlex.quote(','.join([*entries, entry]))}"


def recipes_allowed(config: Any | None = None) -> bool:
    """
    Say whether this server lets the catalogue's recipes be deployed.

    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        True when no list is set (anything is allowed) or an entry covers the folder a recipe's
        code is prepared in (``recipes``, or ``local``, which always did).
    """
    entries = allowed_sources(config)
    if not entries:
        return True
    return any(matches(entry, Origin(RECIPE, None, (), RECIPE)) for entry in entries)


def _suggested_entry(origin: Origin) -> str:
    """
    Name the entry that would allow an origin.

    Args:
        origin: Where the refused source is.

    Returns:
        The word for a place without a host, or ``host/org/repo``: the narrowest entry that works.
    """
    words = {"local": LOCAL, PUSH: PUSH, UPLOAD: UPLOAD, RECIPE: RECIPES}
    return words.get(origin.kind, origin.display)


def _record_refusal(display: str, allowed: list[str]) -> None:
    """
    Put a refused source on record.

    Args:
        display: Where it was, without credentials.
        allowed: The list that refused it.
    """
    from noust.core.audit import record

    record(
        "apps.source.denied",
        target=f"source:{display}",
        outcome="denied",
        details={"reason": "origin not allowed", "allowed_sources": allowed},
    )


def check_origin(
    source: str, allowed: Sequence[str] | None = None, kind: str | None = None
) -> None:
    """
    Refuse a source outside the allowed origins.

    Args:
        source: The source as given or stored.
        allowed: The allowed entries; read from the configuration when None.
        kind: ``git``, ``archive``, ``local``, ``push``, ``upload`` or ``recipe`` when already
            classified.

    Raises:
        OriginNotAllowed: A list is set and no entry covers the source.
    """
    entries = list(allowed) if allowed is not None else allowed_sources()
    if not entries:
        return
    origin = describe_origin(source, kind)
    if any(matches(entry, origin) for entry in entries):
        return
    _record_refusal(origin.display, entries)
    raise OriginNotAllowed(
        f"Code from {origin.display} is not allowed on this server",
        details=(
            f"{CONFIG_KEY} allows: {', '.join(entries)}. Deploy from one of those, or ask the "
            "security officer to allow this one (the whole list is written again, so it keeps "
            f"the others):\n  {allow_command(entries, _suggested_entry(origin))}"
        ),
    )
